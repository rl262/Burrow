#!/usr/bin/env bash
# Burrow demo seeder — populate a Burrow install with believable FAKE data for
# screenshots / demos. Adds DNS records and a few allow/deny overrides; with
# --activity it also briefly generates blocked-query traffic so the Activity tab
# has something to show.
#
#   sudo bash examples/demo-seed.sh [--activity]
#
# NOT for production: it writes fake records into your local zone. Intended to be
# run on a throwaway demo box (ideally installed with LOCAL_DOMAIN=home.arpa and
# LAN_CIDR=192.168.1.0/24 so the seeded data matches). Idempotent — safe to re-run.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }

CONF=/etc/burrow/burrow.conf
[[ -r "$CONF" ]] || { echo "no $CONF — is Burrow installed?" >&2; exit 1; }
# shellcheck disable=SC1090
source "$CONF"
DOMAIN="${LOCAL_DOMAIN:?LOCAL_DOMAIN not set}"
PW="${ADMIN_PASSWORD:-}"
DASH="http://127.0.0.1:${DASHBOARD_PORT:-8088}"
REV="1.168.192.in-addr.arpa"
WITH_ACTIVITY=0; [[ "${1:-}" == "--activity" ]] && WITH_ACTIVITY=1

# host label -> last octet of 192.168.1.x
HOST_LIST="gateway:1 ns:2 nas:10 proxmox:11 truenas:12 jellyfin:20 grafana:21
  homeassistant:22 unifi:23 immich:24 vaultwarden:25 paperless:26 git:30 wiki:31
  printer:40 nvr:41 desktop:100 laptop:101 phone:102"

say() { printf ':: %s\n' "$*"; }

say "seeding forward records in $DOMAIN"
for hp in $HOST_LIST; do
  h="${hp%:*}"; o="${hp#*:}"
  pdnsutil replace-rrset "$DOMAIN" "$h" A 3600 "192.168.1.$o" >/dev/null
done
pdnsutil replace-rrset "$DOMAIN" jellyfin AAAA 3600 "fd00::20" >/dev/null
pdnsutil replace-rrset "$DOMAIN" media CNAME 3600 "jellyfin.$DOMAIN." >/dev/null
pdnsutil replace-rrset "$DOMAIN" dash  CNAME 3600 "grafana.$DOMAIN." >/dev/null
pdnsutil replace-rrset "$DOMAIN" "$DOMAIN" MX 3600 "10 mail.$DOMAIN." >/dev/null
pdnsutil replace-rrset "$DOMAIN" "$DOMAIN" TXT 3600 '"v=spf1 -all"' >/dev/null
pdnsutil replace-rrset "$DOMAIN" _imaps._tcp SRV 3600 "0 1 993 mail.$DOMAIN." >/dev/null

say "seeding reverse PTRs in $REV"
if pdnsutil list-all-zones | grep -Fxq "$REV"; then
  for hp in $HOST_LIST; do
    h="${hp%:*}"; o="${hp#*:}"
    pdnsutil replace-rrset "$REV" "$o" PTR 3600 "$h.$DOMAIN." >/dev/null
  done
else
  say "  ($REV not present — install with LAN_CIDR=192.168.1.0/24 for matching PTRs)"
fi
pdnsutil increase-serial "$DOMAIN" >/dev/null 2>&1 || true
pdnsutil increase-serial "$REV"    >/dev/null 2>&1 || true

say "seeding allow/deny overrides via the dashboard API"
JAR="$(mktemp)"
if [[ -n "$PW" ]] && curl -fsS -c "$JAR" --data-urlencode "password=$PW" --data-urlencode "next=/" "$DASH/login" >/dev/null 2>&1; then
  for d in ads.example.com telemetry.example.net tracker.example.org; do
    curl -fsS -b "$JAR" --data-urlencode "domain=$d" "$DASH/unbound/deny"  >/dev/null 2>&1 || true
  done
  curl -fsS -b "$JAR" --data-urlencode "domain=cdn.jsdelivr.net" "$DASH/unbound/allow" >/dev/null 2>&1 || true
else
  say "  (could not log in to the dashboard API — skipping overrides)"
fi
rm -f "$JAR"

if [[ "$WITH_ACTIVITY" == 1 ]]; then
  say "generating blocked-query traffic for the Activity tab (adds temporary IP aliases)"
  IFACE="$(ip -4 route show default | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1);exit}}')"
  CLIENTS="192.168.1.50 192.168.1.55 192.168.1.73 192.168.1.101 192.168.1.120 192.168.1.150"
  # Recognizable ad/tracker domains that the default blocklists carry.
  DOMAINS="doubleclick.net google-analytics.com googleadservices.com googlesyndication.com
    adservice.google.com ads.facebook.com an.facebook.com scorecardresearch.com
    app-measurement.com adnxs.com criteo.com taboola.com outbrain.com bat.bing.com
    ads.linkedin.com casalemedia.com pubmatic.com rubiconproject.com quantserve.com
    ads.youtube.com"
  ADDED=""
  for c in $CLIENTS; do ip addr add "$c/24" dev "$IFACE" 2>/dev/null && ADDED="$ADDED $c" || true; done
  # shellcheck disable=SC2064
  trap "for c in $ADDED; do ip addr del \"\$c/24\" dev \"$IFACE\" 2>/dev/null || true; done" EXIT
  for wave in 1 2 3; do
    for c in $CLIENTS; do
      for d in $DOMAINS; do dig +tries=1 +time=1 -b "$c" @"$c" "$d" >/dev/null 2>&1 || true; done
    done
    say "  wave $wave/3 sent"
    [[ "$wave" -lt 3 ]] && sleep 25
  done
  say "  activity traffic done (temporary IP aliases removed)"
fi

say "demo data seeded — open $DASH (login: admin)"
