#!/usr/bin/env bash
# /opt/burrow/bin/burrow-pdns-bootstrap.sh
#
# Idempotent PowerDNS (gmysql) bootstrap for Burrow.
#   - create MariaDB db + user (pdns)
#   - load the Debian gmysql schema if not already loaded
#   - create the forward zone ${LOCAL_DOMAIN} (Native) with SOA + NS
#   - derive + create reverse in-addr.arpa zone(s) from ${LAN_CIDR}
#   - seed the Burrow host's own A + PTR records
#
# Safe to run repeatedly. Sources install-time parameters from
# /etc/burrow/burrow.conf (LOCAL_DOMAIN, LAN_CIDR, LISTEN_IP, PDNS_DB_PASSWORD).
set -euo pipefail

BURROW_CONF="${BURROW_CONF:-/etc/burrow/burrow.conf}"
SCHEMA_FILE="/usr/share/pdns-backend-mysql/schema/schema.mysql.sql"
PDNS_DB="pdns"
PDNS_DB_USER="pdns"
NS_LABEL="ns"   # produces ns.${LOCAL_DOMAIN} as the primary nameserver name

log() { printf '[burrow-pdns] %s\n' "$*"; }
die() { printf '[burrow-pdns] ERROR: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "must run as root"
[[ -r "$BURROW_CONF" ]] || die "cannot read $BURROW_CONF"
# shellcheck disable=SC1090
source "$BURROW_CONF"

: "${LOCAL_DOMAIN:?LOCAL_DOMAIN not set in $BURROW_CONF}"
: "${LAN_CIDR:?LAN_CIDR not set in $BURROW_CONF}"
: "${PDNS_DB_PASSWORD:?PDNS_DB_PASSWORD not set in $BURROW_CONF}"
LISTEN_IP="${LISTEN_IP:-0.0.0.0}"

# The nameserver host's own IP. If LISTEN_IP is a wildcard, fall back to the
# primary route source address so the seeded A record points somewhere real.
NS_IP="$LISTEN_IP"
if [[ "$NS_IP" == "0.0.0.0" || "$NS_IP" == "::" || -z "$NS_IP" ]]; then
  NS_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
  NS_IP="${NS_IP:-127.0.0.1}"
fi

# pdnsutil talks to PowerDNS through its own gmysql config, so point it at the
# pdns.conf is rendered to the standard /etc/powerdns, so pdnsutil's default
# config-dir works.
PDNSUTIL=(pdnsutil)

##############################################################################
# 1. MariaDB database + user (idempotent).
##############################################################################
# Uses unix_socket / root auth available to the local root user. mariadb-server
# on Debian/Ubuntu authorises the system root via unix_socket by default.
mysql_root() { mariadb --protocol=socket -u root "$@"; }

log "ensuring database '${PDNS_DB}' and user '${PDNS_DB_USER}'@'127.0.0.1'"
mysql_root <<SQL
CREATE DATABASE IF NOT EXISTS \`${PDNS_DB}\` CHARACTER SET latin1;
CREATE USER IF NOT EXISTS '${PDNS_DB_USER}'@'127.0.0.1' IDENTIFIED BY '${PDNS_DB_PASSWORD}';
ALTER USER '${PDNS_DB_USER}'@'127.0.0.1' IDENTIFIED BY '${PDNS_DB_PASSWORD}';
GRANT ALL PRIVILEGES ON \`${PDNS_DB}\`.* TO '${PDNS_DB_USER}'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL

##############################################################################
# 2. Load schema only if the 'domains' table does not yet exist.
##############################################################################
has_domains="$(mysql_root -N -B -e \
  "SELECT COUNT(*) FROM information_schema.tables \
   WHERE table_schema='${PDNS_DB}' AND table_name='domains';")"
if [[ "$has_domains" -eq 0 ]]; then
  [[ -r "$SCHEMA_FILE" ]] || die "schema file not found: $SCHEMA_FILE (is pdns-backend-mysql installed?)"
  log "loading gmysql schema from $SCHEMA_FILE"
  mysql_root "$PDNS_DB" < "$SCHEMA_FILE"
else
  log "schema already present (domains table exists) — skipping load"
fi

# NOTE: no pdns restart here — pdnsutil talks to the gmysql backend directly, so
# it does not need a running pdns. install.sh restarts pdns once after this
# bootstrap completes (avoids a redundant mid-bootstrap restart).

##############################################################################
# Helpers: zone existence + creation (Native zones, explicit SOA + NS).
##############################################################################
zone_exists() { "${PDNSUTIL[@]}" list-all-zones 2>/dev/null | grep -Fxq "$1"; }

create_zone() {
  local zone="$1"
  if zone_exists "$zone"; then
    log "zone '$zone' already exists — skipping create"
    return 0
  fi
  log "creating native zone '$zone'"
  "${PDNSUTIL[@]}" create-zone "$zone" "${NS_LABEL}.${LOCAL_DOMAIN}."
  # Explicit SOA: <primary-ns> <hostmaster> <serial> <refresh> <retry> <expire> <minimum>
  "${PDNSUTIL[@]}" replace-rrset "$zone" "$zone" SOA 3600 \
    "${NS_LABEL}.${LOCAL_DOMAIN}. hostmaster.${LOCAL_DOMAIN}. 1 10800 3600 604800 3600"
  "${PDNSUTIL[@]}" replace-rrset "$zone" "$zone" NS 3600 "${NS_LABEL}.${LOCAL_DOMAIN}."
}

##############################################################################
# Derive reverse in-addr.arpa zone NAME(S) from a CIDR. PowerDNS reverse zones
# are per-/24, so a CIDR maps to the SET of /24 zones covering it (NOT one
# over-broad classful zone — a /23 must not collapse to a /16, which would
# shadow PTR recursion for the rest of 192.168.x). Kept in lockstep with
# lib/burrow-reverse-zones.sh (the unbound stub-zone side).
#   192.168.1.0/24 -> 1.168.192.in-addr.arpa
#   192.168.0.0/23 -> 0.168.192.in-addr.arpa  1.168.192.in-addr.arpa
# Prefixes wider than /20 are not auto-enumerated (too many /24s); a warning is
# printed and the operator creates specific reverse zones from the dashboard.
# Emits one zone name per line; rc=1 (no output) when too wide.
##############################################################################
reverse_zone_names() {
  local cidr="$1" ip prefix o1 o2 o3 _o4 count base i
  ip="${cidr%/*}"; prefix="${cidr#*/}"
  IFS='.' read -r o1 o2 o3 _o4 <<<"$ip"
  if   (( prefix >= 24 )); then
    echo "${o3}.${o2}.${o1}.in-addr.arpa"
  elif (( prefix >= 20 )); then
    count=$(( 1 << (24 - prefix) )); base=$(( o3 - (o3 % count) ))
    for (( i = 0; i < count; i++ )); do echo "$(( base + i )).${o2}.${o1}.in-addr.arpa"; done
  else
    log "WARN: LAN_CIDR /$prefix is wider than /20; reverse zone(s) not auto-created (create them from the dashboard)"
    return 1
  fi
}

##############################################################################
# 3. Forward zone.
##############################################################################
create_zone "$LOCAL_DOMAIN"

# Seed the Burrow host's own A record (ns.<domain>) so resolution + the NS target work.
if ! "${PDNSUTIL[@]}" list-zone "$LOCAL_DOMAIN" 2>/dev/null \
     | grep -qiE "^${NS_LABEL}\.${LOCAL_DOMAIN}\.?[[:space:]].*[[:space:]]A[[:space:]]"; then
  log "seeding A record ${NS_LABEL}.${LOCAL_DOMAIN} -> ${NS_IP}"
  "${PDNSUTIL[@]}" add-record "$LOCAL_DOMAIN" "$NS_LABEL" A 3600 "$NS_IP"
fi

##############################################################################
# 4. Reverse zone(s) derived from LAN_CIDR (each is a /24; CIDR may be a list).
##############################################################################
# The Burrow host's own /24 reverse zone (for seeding its PTR).
IFS='.' read -r _n1 n2 n3 n4 <<<"$NS_IP"
host_rz="${n3}.${n2}.${_n1}.in-addr.arpa"
for cidr in ${LAN_CIDR//,/ }; do
  while IFS= read -r rz; do
    [[ -n "$rz" ]] || continue
    create_zone "$rz"
    # Seed the PTR for the Burrow host in whichever /24 zone contains it.
    if [[ "$rz" == "$host_rz" ]] \
       && ! "${PDNSUTIL[@]}" list-zone "$rz" 2>/dev/null \
            | grep -qiE "^${n4}\.${rz}\.?[[:space:]].*[[:space:]]PTR[[:space:]]"; then
      log "seeding PTR ${n4}.${rz} -> ${NS_LABEL}.${LOCAL_DOMAIN}."
      "${PDNSUTIL[@]}" add-record "$rz" "$n4" PTR 3600 "${NS_LABEL}.${LOCAL_DOMAIN}."
    fi
  done < <(reverse_zone_names "$cidr" 2>/dev/null || true)
done

##############################################################################
# 5. Bump serials + rectify so the data is consistent.
##############################################################################
"${PDNSUTIL[@]}" increase-serial "$LOCAL_DOMAIN" >/dev/null 2>&1 || true
for cidr in ${LAN_CIDR//,/ }; do
  while IFS= read -r rz; do
    [[ -n "$rz" ]] && "${PDNSUTIL[@]}" increase-serial "$rz" >/dev/null 2>&1 || true
  done < <(reverse_zone_names "$cidr" 2>/dev/null || true)
done

log "bootstrap complete: forward=${LOCAL_DOMAIN} reverse(from ${LAN_CIDR}) host=${NS_IP}"
