#!/usr/bin/env bash
# Burrow installer — a self-hosted, full-stack DNS server with ad-blocking and a
# web dashboard (recursive Unbound + authoritative PowerDNS + the dashboard).
#
#   curl -fsSL https://raw.githubusercontent.com/rl262/Burrow/v0.1.0-alpha.2/install.sh -o /tmp/burrow.sh \
#     && sudo bash /tmp/burrow.sh
#
# Piping `curl ... | sudo bash` works too, but runs NON-INTERACTIVELY: stdin is
# the pipe, so the prompts are skipped and defaults are used. Download-then-run
# (above) or pass values as env vars to choose settings. See the README.
#
# Idempotent: safe to re-run. Validated end-to-end on Ubuntu 24.04; Debian 12/13,
# Ubuntu 22.04, and arm64 are supported but NOT yet fully validated.
set -euo pipefail

# ---------------------------------------------------------------------------
# constants / layout
# ---------------------------------------------------------------------------
BURROW_USER=burrow
BURROW_GROUP=burrow
PREFIX=/opt/burrow
ETC=/etc/burrow
STATE=/var/lib/burrow
BLOCKLIST_DIR=/etc/unbound/blocklist.d
UNBOUND_OVERRIDE_DIR=/var/lib/unbound/burrow
UNBOUND_OVERRIDES_FILE="$UNBOUND_OVERRIDE_DIR/overrides.conf"
BLOCKLIST_REFRESH_SCRIPT=/usr/local/sbin/burrow-update-blocklist
UNBOUND_CONTROL=/usr/sbin/unbound-control
UNBOUND_CHECKCONF=/usr/sbin/unbound-checkconf
PDNS_API_URL=http://127.0.0.1:8081
DASHBOARD_PORT=8088
# Version + the git ref the installer fetches its source tree from. Defaults to
# the pinned release tag so `curl | bash` installs are reproducible (a push to
# main never silently changes what new installs get). Override with
# BURROW_REF=main (or any tag/branch/SHA) for bleeding-edge.
BURROW_VERSION="0.1.0-alpha.2"   # keep in sync with dashboard/app/dns_manager/__init__.py
BURROW_REF="${BURROW_REF:-v${BURROW_VERSION}}"
REPO_TARBALL="https://github.com/rl262/Burrow/archive/${BURROW_REF}.tar.gz"

# Parameters resolve with precedence: explicit env > existing burrow.conf (re-run)
# > interactive prompt > built-in default. Capture explicit env overrides FIRST,
# before defaults or a prior install's config load, so a re-run never silently
# clobbers a value the operator set this run.
for _v in LOCAL_DOMAIN LAN_CIDR LISTEN_IP DASHBOARD_BIND UPSTREAMS ADMIN_PASSWORD; do
  printf -v "_ENV_${_v}" '%s' "${!_v:-}"
done
ASSUME_YES="${ASSUME_YES:-0}"

C_B=$'\e[1m'; C_G=$'\e[32m'; C_Y=$'\e[33m'; C_R=$'\e[31m'; C_0=$'\e[0m'
say()  { printf '%s\n' "${C_G}::${C_0} $*"; }
warn() { printf '%s\n' "${C_Y}!!${C_0} $*" >&2; }
die()  { printf '%s\n' "${C_R}xx${C_0} $*" >&2; exit 1; }
hr()   { printf '%s\n' "------------------------------------------------------------"; }
gen()    { openssl rand -hex 24; }                                   # generated secrets
gen_pw() { openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | cut -c1-20; }  # admin password (≥20 chars)
# mask_cidr() is provided by lib/burrow-net.sh, sourced once the source tree is located.

# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------
[[ $EUID -eq 0 ]] || die "run as root (e.g. 'sudo bash install.sh')."
. /etc/os-release 2>/dev/null || die "cannot read /etc/os-release"
case "${ID:-} ${ID_LIKE:-}" in
  *debian*|*ubuntu*) : ;;
  *) die "unsupported OS '${PRETTY_NAME:-?}'. Burrow needs Debian or Ubuntu." ;;
esac
export DEBIAN_FRONTEND=noninteractive
say "Burrow installer — ${PRETTY_NAME}"

# locate the source tree (local checkout, else download the repo tarball)
SELF="$(readlink -f "${BASH_SOURCE[0]:-$0}" 2>/dev/null || true)"
SRC="$(dirname "${SELF:-/nonexistent}")"
CLEANUP_SRC=""
if [[ ! -d "$SRC/config/unbound" || ! -d "$SRC/dashboard/app" ]]; then
  say "fetching Burrow sources…"
  command -v curl >/dev/null || { apt-get update -qq && apt-get install -y -qq curl; }
  command -v tar  >/dev/null || { apt-get update -qq && apt-get install -y -qq tar; }
  SRC="$(mktemp -d)"; CLEANUP_SRC="$SRC"
  curl -fsSL "$REPO_TARBALL" | tar xz -C "$SRC" --strip-components=1 \
    || die "failed to download Burrow from $REPO_TARBALL"
fi
[[ -f "$SRC/dashboard/app/dns_manager/main.py" ]] || die "source tree incomplete at $SRC"
# `return 0` is load-bearing: this is the EXIT trap, and without it a local-checkout
# install (CLEANUP_SRC empty) ends on a false `[[ -n "" ]]`, making the whole script
# exit 1 on success.
cleanup() { [[ -n "$CLEANUP_SRC" ]] && rm -rf "$CLEANUP_SRC"; return 0; }
trap cleanup EXIT
# shellcheck source=/dev/null
source "$SRC/lib/burrow-net.sh"
source "$SRC/lib/burrow-reverse-zones.sh"

# ---------------------------------------------------------------------------
# gather parameters
# ---------------------------------------------------------------------------
ask() { # ask VAR "prompt"  — the current value of VAR is the default; non-tty
        # / ASSUME_YES keep it unchanged (no clobber).
  local __v="$1" __p="$2" __d="${!1:-}" __in
  if [[ "$ASSUME_YES" == "1" || ! -t 0 ]]; then return; fi
  read -r -p "$__p [$__d]: " __in </dev/tty || true
  printf -v "$__v" '%s' "${__in:-$__d}"
}

# best-effort LAN CIDR autodetect from the default route, masked to network base
detect_cidr() {
  local dev cidr
  dev="$(ip -4 route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"
  cidr="$(ip -4 -o addr show "${dev:-x}" 2>/dev/null | awk '{print $4; exit}')"
  [[ -n "$cidr" ]] && mask_cidr "$cidr" || echo ""
}

hr
say "Configuration (press Enter to accept defaults)"

# Reuse a prior install's settings + generated secrets so re-runs are idempotent.
# Source burrow.conf in a SUBSHELL and export only OLD_* back, so it can never
# clobber a value the operator set this run (it just provides the defaults).
OLD_PDNS_DB_PASSWORD=""; OLD_PDNS_API_KEY=""; OLD_DASHBOARD_SESSION_SECRET=""
if [[ -f "$ETC/burrow.conf" ]]; then
  say "found existing $ETC/burrow.conf — reusing prior settings + secrets"
  eval "$(
    . "$ETC/burrow.conf" 2>/dev/null || true
    for k in LOCAL_DOMAIN LAN_CIDR LISTEN_IP DASHBOARD_BIND UPSTREAMS ADMIN_PASSWORD \
             PDNS_DB_PASSWORD PDNS_API_KEY DASHBOARD_SESSION_SECRET; do
      printf 'OLD_%s=%q\n' "$k" "${!k:-}"
    done
  )"
fi

# precedence: explicit env > prior install > built-in default
LOCAL_DOMAIN="${_ENV_LOCAL_DOMAIN:-${OLD_LOCAL_DOMAIN:-home.arpa}}"
LISTEN_IP="${_ENV_LISTEN_IP:-${OLD_LISTEN_IP:-0.0.0.0}}"
DASHBOARD_BIND="${_ENV_DASHBOARD_BIND:-${OLD_DASHBOARD_BIND:-127.0.0.1}}"
UPSTREAMS="${_ENV_UPSTREAMS:-${OLD_UPSTREAMS:-1.1.1.1,1.0.0.1,9.9.9.9,149.112.112.112}}"
LAN_CIDR="${_ENV_LAN_CIDR:-${OLD_LAN_CIDR:-}}"
[[ -z "$LAN_CIDR" ]] && LAN_CIDR="$(detect_cidr)"
[[ -z "$LAN_CIDR" ]] && LAN_CIDR="192.168.1.0/24"
ADMIN_PASSWORD="${_ENV_ADMIN_PASSWORD:-${OLD_ADMIN_PASSWORD:-}}"

ask LOCAL_DOMAIN   "Local domain Burrow is authoritative for"
ask LAN_CIDR       "LAN subnet CIDR allowed to query (and reverse zone)"
ask LISTEN_IP      "IP Unbound binds for DNS (0.0.0.0 = all)"
ask UPSTREAMS      "Upstream DoT resolvers (comma-separated IPs)"
ask DASHBOARD_BIND "Dashboard bind IP (127.0.0.1 = loopback only; 0.0.0.0 = LAN)"

# admin password: env/prior install wins; else prompt (interactive) or generate
if [[ -z "$ADMIN_PASSWORD" ]]; then
  if [[ "$ASSUME_YES" == "1" || ! -t 0 ]]; then
    ADMIN_PASSWORD="$(gen_pw)"; GENERATED_PW=1
  else
    read -rs -p "Dashboard admin password (blank = generate): " ADMIN_PASSWORD </dev/tty || true; echo
    [[ -z "$ADMIN_PASSWORD" ]] && { ADMIN_PASSWORD="$(gen_pw)"; GENERATED_PW=1; }
  fi
fi

# validate + normalize. Single-quote/newline in the password would break the
# systemd EnvironmentFile (we write it single-quoted), so reject them up front.
[[ "$ADMIN_PASSWORD" == *$'\n'* ]] && die "admin password must not contain a newline"
[[ "$ADMIN_PASSWORD" == *\'* ]]    && die "admin password must not contain a single quote (')"
LAN_CIDR="$(mask_cidr "$LAN_CIDR")"
[[ "$LAN_CIDR" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$ ]] || die "LAN_CIDR '$LAN_CIDR' is not a valid IPv4 CIDR"

# ---------------------------------------------------------------------------
# packages
# ---------------------------------------------------------------------------
hr; say "Installing packages (this can take a minute)…"
# pdns's apt post-install tries to start it before we've configured a backend;
# mask it during install so the expected pre-config failure doesn't alarm.
systemctl mask pdns >/dev/null 2>&1 || true
apt-get update -qq
apt-get install -y -qq \
  unbound dns-root-data ca-certificates \
  pdns-server pdns-backend-mysql mariadb-server \
  python3 python3-venv python3-pip \
  curl gettext-base openssl iproute2 dnsutils sudo
# sudo is required at runtime: the dashboard escalates to run unbound-control via
# /etc/sudoers.d/burrow. It's pre-installed on most VMs but absent from minimal
# images, so install it explicitly.

# ---------------------------------------------------------------------------
# user, group, directories
# ---------------------------------------------------------------------------
getent group "$BURROW_GROUP" >/dev/null || groupadd --system "$BURROW_GROUP"
getent passwd "$BURROW_USER" >/dev/null || \
  useradd --system --gid "$BURROW_GROUP" --home-dir "$PREFIX" --shell /usr/sbin/nologin "$BURROW_USER"
# the dashboard tails the journal for the live blocked-request feed
usermod -aG systemd-journal "$BURROW_USER"

install -d -o root -g root -m 0755 "$ETC"
install -d -o "$BURROW_USER" -g "$BURROW_GROUP" -m 0750 "$STATE"
install -d -o root -g root -m 0755 "$PREFIX" "$PREFIX/bin"
install -d -o root -g root -m 0755 "$BLOCKLIST_DIR"
install -d -o "$BURROW_USER" -g "$BURROW_GROUP" -m 0775 "$BLOCKLIST_DIR/sources.d"
install -d -o "$BURROW_USER" -g "$BURROW_GROUP" -m 0755 "$UNBOUND_OVERRIDE_DIR"

# ---------------------------------------------------------------------------
# secrets + /etc/burrow/burrow.conf
# ---------------------------------------------------------------------------
# Reuse prior generated secrets (captured into OLD_* above) so a re-run never
# rotates them and logs everyone out / breaks the pdns DB user.
PDNS_DB_PASSWORD="${OLD_PDNS_DB_PASSWORD:-$(gen)}"
PDNS_API_KEY="${OLD_PDNS_API_KEY:-$(gen)}"
DASHBOARD_SESSION_SECRET="${OLD_DASHBOARD_SESSION_SECRET:-$(gen)}"

umask 077
cat >"$ETC/burrow.conf" <<EOF
# Burrow install-time configuration + generated secrets. Mode 0640 root:burrow.
# Sourced by burrow scripts (e.g. burrow-pdns-bootstrap.sh). Do not commit.
LOCAL_DOMAIN=$(printf '%q' "$LOCAL_DOMAIN")
LAN_CIDR=$(printf '%q' "$LAN_CIDR")
LISTEN_IP=$(printf '%q' "$LISTEN_IP")
UPSTREAMS=$(printf '%q' "$UPSTREAMS")
DASHBOARD_BIND=$(printf '%q' "$DASHBOARD_BIND")
PDNS_DB_PASSWORD=$(printf '%q' "$PDNS_DB_PASSWORD")
PDNS_API_KEY=$(printf '%q' "$PDNS_API_KEY")
DASHBOARD_SESSION_SECRET=$(printf '%q' "$DASHBOARD_SESSION_SECRET")
ADMIN_PASSWORD=$(printf '%q' "$ADMIN_PASSWORD")
EOF
chgrp "$BURROW_GROUP" "$ETC/burrow.conf"; chmod 0640 "$ETC/burrow.conf"
umask 022
# Stamp the installed version + source ref (check with: cat /etc/burrow/VERSION).
printf 'burrow %s (ref %s)\n' "$BURROW_VERSION" "$BURROW_REF" >"$ETC/VERSION"
export LOCAL_DOMAIN LAN_CIDR LISTEN_IP UPSTREAMS DASHBOARD_BIND \
       PDNS_DB_PASSWORD PDNS_API_KEY DASHBOARD_SESSION_SECRET ADMIN_PASSWORD \
       PDNS_API_URL UNBOUND_CONTROL UNBOUND_CHECKCONF UNBOUND_OVERRIDES_FILE \
       BLOCKLIST_REFRESH_SCRIPT BLOCKLIST_DIR DASHBOARD_PORT

# ---------------------------------------------------------------------------
# PowerDNS (authoritative on 127.0.0.1:5300)
# ---------------------------------------------------------------------------
hr; say "Configuring PowerDNS…"
[[ -f /etc/powerdns/pdns.conf && ! -f /etc/powerdns/pdns.conf.burrow-orig ]] && \
  cp -a /etc/powerdns/pdns.conf /etc/powerdns/pdns.conf.burrow-orig
envsubst '${PDNS_DB_PASSWORD} ${PDNS_API_KEY} ${LOCAL_DOMAIN}' \
  <"$SRC/config/powerdns/pdns.conf.tmpl" >/etc/powerdns/pdns.conf
chgrp pdns /etc/powerdns/pdns.conf 2>/dev/null || true
chmod 0640 /etc/powerdns/pdns.conf
install -o root -g "$BURROW_GROUP" -m 0750 "$SRC/bin/burrow-pdns-bootstrap.sh" "$PREFIX/bin/burrow-pdns-bootstrap.sh"
systemctl enable --now mariadb >/dev/null 2>&1 || systemctl restart mariadb
systemctl unmask pdns >/dev/null 2>&1 || true
systemctl enable pdns >/dev/null 2>&1 || true
say "bootstrapping database + zones…"
"$PREFIX/bin/burrow-pdns-bootstrap.sh"
systemctl restart pdns

# ---------------------------------------------------------------------------
# Unbound (recursive + ad-block front door on :53)
# ---------------------------------------------------------------------------
hr; say "Configuring Unbound…"
REVERSE_ZONES_STUB="$(burrow_reverse_zones "$LAN_CIDR")"
UPSTREAMS_FORWARD_ADDRS="$(printf '%s' "$UPSTREAMS" | tr ',' '\n' | sed '/^$/d;s/^[[:space:]]*//;s/[[:space:]]*$//;s#^#    forward-addr: #;s#$#@853#')"
export REVERSE_ZONES_STUB UPSTREAMS_FORWARD_ADDRS

[[ -f /etc/unbound/unbound.conf && ! -f /etc/unbound/unbound.conf.burrow-orig ]] && \
  cp -a /etc/unbound/unbound.conf /etc/unbound/unbound.conf.burrow-orig
cp -a "$SRC/config/unbound/unbound.conf.tmpl" /etc/unbound/unbound.conf
install -d -m 0755 /etc/unbound/unbound.conf.d
# Burrow owns unbound.conf.d. Move aside any distro drop-ins so their directives
# don't collide with Burrow's — notably the package's own
# root-auto-trust-anchor-file.conf and remote-control.conf, which would declare a
# SECOND auto-trust-anchor-file pointing at the same root.key and make
# unbound-checkconf fail with "trust anchor presented twice". Restored on uninstall.
for f in /etc/unbound/unbound.conf.d/*.conf; do
  [[ -e "$f" ]] || continue
  case "$(basename "$f")" in
    burrow-*|zz-burrow-*) ;;                      # ours — keep
    *) say "disabling distro unbound drop-in $(basename "$f")"; mv -f "$f" "$f.burrow-disabled" ;;
  esac
done
envsubst '${LISTEN_IP} ${LAN_CIDR} ${LOCAL_DOMAIN} ${REVERSE_ZONES_STUB} ${UPSTREAMS_FORWARD_ADDRS}' \
  <"$SRC/config/unbound/unbound.conf.d/burrow-server.conf.tmpl" >/etc/unbound/unbound.conf.d/burrow-server.conf
cp -a "$SRC/config/unbound/unbound.conf.d/burrow-blocklist.conf" \
      "$SRC/config/unbound/unbound.conf.d/zz-burrow-overrides.conf" \
      "$SRC/config/unbound/unbound.conf.d/burrow-remote-control.conf" /etc/unbound/unbound.conf.d/

# blocklist data + override include (seed so includes never fail before 1st refresh)
[[ -f "$BLOCKLIST_DIR/sources.txt" ]]   || cp -a "$SRC/config/unbound/blocklist.d/sources.txt"   "$BLOCKLIST_DIR/sources.txt"
[[ -f "$BLOCKLIST_DIR/allowlist.txt" ]] || cp -a "$SRC/config/unbound/blocklist.d/allowlist.txt" "$BLOCKLIST_DIR/allowlist.txt"
[[ -f "$BLOCKLIST_DIR/blocklist.conf" ]] || cp -a "$SRC/config/unbound/blocklist.d/blocklist.conf.seed" "$BLOCKLIST_DIR/blocklist.conf"
# blocklist.conf must be world-readable: unbound re-reads includes as the
# dropped-privilege unbound user on reload (0600 would crash it). Corrects any
# 0600 file left by an earlier buggy refresh.
chmod 0644 "$BLOCKLIST_DIR/blocklist.conf" 2>/dev/null || true
[[ -f "$UNBOUND_OVERRIDES_FILE" ]] || {
  printf '# Burrow runtime allow/deny overrides (managed by the dashboard).\n' >"$UNBOUND_OVERRIDES_FILE"
  chown "$BURROW_USER:$BURROW_GROUP" "$UNBOUND_OVERRIDES_FILE"; chmod 0644 "$UNBOUND_OVERRIDES_FILE"
}
install -o root -g root -m 0755 "$SRC/bin/burrow-update-blocklist" "$BLOCKLIST_REFRESH_SCRIPT"
install -o root -g root -m 0644 "$SRC/systemd/burrow-blocklist-refresh.service" /etc/systemd/system/
install -o root -g root -m 0644 "$SRC/systemd/burrow-blocklist-refresh.timer"   /etc/systemd/system/

# free :53 from systemd-resolved (Ubuntu) BEFORE starting unbound. Guard on unit
# PRESENCE, not is-active: resolved can be enabled-but-inactive at install time
# yet grab 127.0.0.53:53 on the next boot and collide with unbound.
if systemctl list-unit-files systemd-resolved.service >/dev/null 2>&1; then
  say "freeing port 53 from systemd-resolved…"
  install -d -m 0755 /etc/systemd/resolved.conf.d
  printf '[Resolve]\nDNSStubListener=no\n' >/etc/systemd/resolved.conf.d/burrow.conf
  systemctl restart systemd-resolved 2>/dev/null || true
fi

# DNSSEC root trust anchor. The unbound package already seeds + maintains
# /var/lib/unbound/root.key (RFC 5011, via its own auto-trust-anchor-file drop-in
# which we disabled above). Only seed it if it is somehow missing — do NOT
# overwrite a maintained key, and do NOT run unbound-anchor on top of an existing
# one (that appends a second anchor and unbound-checkconf then rejects
# "trust anchor presented twice").
if [[ ! -s /var/lib/unbound/root.key ]]; then
  if [[ -r /usr/share/dns/root.key ]]; then
    install -o unbound -g unbound -m 0644 /usr/share/dns/root.key /var/lib/unbound/root.key
  elif command -v unbound-anchor >/dev/null 2>&1; then
    unbound-anchor -a /var/lib/unbound/root.key || true   # exit 1 == anchor written (normal)
    chown unbound:unbound /var/lib/unbound/root.key 2>/dev/null || true
  fi
fi
# override include: readable by the unbound daemon, writable by the dashboard user
chown "$BURROW_USER:$BURROW_GROUP" "$UNBOUND_OVERRIDE_DIR" "$UNBOUND_OVERRIDES_FILE"
unbound-checkconf >/dev/null || die "unbound-checkconf failed — config not applied"
systemctl enable unbound >/dev/null 2>&1 || true
# Restart explicitly tolerant of failure so we can report WHAT holds :53 instead
# of aborting opaquely under 'set -e' if resolved hasn't released the port yet.
if ! systemctl restart unbound; then
  warn "unbound failed to start. Listeners currently on :53:"
  ss -lntup 'sport = :53' 2>/dev/null || true
  journalctl -u unbound -n 20 --no-pager 2>/dev/null || true
  die "unbound did not start. If systemd-resolved still holds :53, reboot and re-run."
fi
sleep 1
# Point the host's own resolver at Burrow. rm first so a systemd-resolved symlink
# is replaced by a real file; tolerate failure (e.g. a bind-mounted /etc/resolv.conf
# inside a container) and fall through to a truncate-write.
if [[ -L /etc/resolv.conf || -f /etc/resolv.conf ]]; then
  rm -f /etc/resolv.conf 2>/dev/null || true
  printf 'nameserver 127.0.0.1\noptions edns0 trust-ad\n' >/etc/resolv.conf 2>/dev/null \
    || warn "could not write /etc/resolv.conf — point this host's resolver at 127.0.0.1 manually."
fi
say "seeding blocklist (first refresh; downloads adlists)…"
"$BLOCKLIST_REFRESH_SCRIPT" || warn "blocklist refresh failed — you can re-run: $BLOCKLIST_REFRESH_SCRIPT"
systemctl enable --now burrow-blocklist-refresh.timer >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
hr; say "Installing the dashboard…"
rm -rf "$PREFIX/dns_manager"
cp -a "$SRC/dashboard/app/dns_manager" "$PREFIX/dns_manager"
cp -a "$SRC/dashboard/app/requirements.txt" "$PREFIX/requirements.txt"
chown -R "$BURROW_USER:$BURROW_GROUP" "$PREFIX/dns_manager" "$PREFIX/requirements.txt"
[[ -x "$PREFIX/venv/bin/python" ]] || python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/venv/bin/pip" install --quiet -r "$PREFIX/requirements.txt"
chown -R "$BURROW_USER:$BURROW_GROUP" "$PREFIX/venv"

umask 077
envsubst '${PDNS_API_URL} ${PDNS_API_KEY} ${LOCAL_DOMAIN} ${UNBOUND_CONTROL} ${UNBOUND_CHECKCONF} ${UNBOUND_OVERRIDES_FILE} ${BLOCKLIST_REFRESH_SCRIPT} ${BLOCKLIST_DIR} ${DASHBOARD_SESSION_SECRET}' \
  <"$SRC/dashboard/deploy/dashboard.env.tmpl" >"$ETC/dashboard.env"
# Append the admin password directly, single-quoted, instead of via envsubst:
# systemd strips matching outer single quotes and does NO further interpretation
# inside them, so a password with $ " \ space # survives verbatim. (Validated to
# contain no single-quote or newline above, which are the only chars that break
# single-quoting.) envsubst would have left such chars unescaped and corrupted.
printf "DNSMGR_APP_PASSWORD='%s'\n" "$ADMIN_PASSWORD" >>"$ETC/dashboard.env"
chown root:"$BURROW_GROUP" "$ETC/dashboard.env"; chmod 0640 "$ETC/dashboard.env"
umask 022

envsubst '${DASHBOARD_BIND} ${DASHBOARD_PORT}' \
  <"$SRC/dashboard/deploy/burrow-dashboard.service.tmpl" >/etc/systemd/system/burrow-dashboard.service
install -d -m 0755 /etc/sudoers.d   # ensure the drop-in dir exists (sudo just installed)
install -o root -g root -m 0440 "$SRC/dashboard/deploy/sudoers.burrow" /etc/sudoers.d/burrow
visudo -cf /etc/sudoers.d/burrow >/dev/null || die "sudoers validation failed"

# self-contained uninstaller at the documented path (sudo /opt/burrow/uninstall.sh)
install -o root -g root -m 0755 "$SRC/uninstall.sh" "$PREFIX/uninstall.sh"

systemctl daemon-reload
systemctl enable --now burrow-dashboard.service >/dev/null 2>&1 || systemctl restart burrow-dashboard.service

# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------
HOST_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
HOST_IP="${HOST_IP:-<this-host-ip>}"
hr
say "${C_B}Burrow ${BURROW_VERSION} is installed.${C_0}"
echo
case "$DASHBOARD_BIND" in
  127.0.0.1|::1|localhost)
    echo "  Dashboard : http://127.0.0.1:${DASHBOARD_PORT}/   (login: admin — loopback only)"
    echo "              Bound to loopback. To reach it from your LAN, run an SSH tunnel,"
    echo "              put a TLS reverse proxy in front, or re-install with"
    echo "              DASHBOARD_BIND=0.0.0.0 (plaintext HTTP — prefer a proxy/VPN)."
    ;;
  *)
    echo "  Dashboard : http://${HOST_IP}:${DASHBOARD_PORT}/   (login: admin)"
    ;;
esac
[[ "${GENERATED_PW:-0}" == "1" ]] && echo "  Password  : ${C_B}${ADMIN_PASSWORD}${C_0}   (generated — save it)"
echo "  DNS       : point your router/clients' DNS at  ${HOST_IP}"
echo "  Local zone: ${LOCAL_DOMAIN}   ·   blocklists refresh nightly"
echo
case "$DASHBOARD_BIND" in
  127.0.0.1|::1|localhost) : ;;
  *) warn "Dashboard is bound to ${DASHBOARD_BIND}:${DASHBOARD_PORT} over PLAINTEXT HTTP — the admin"
     warn "password + session cookie cross the network in the clear. Put a TLS reverse proxy"
     warn "in front, or keep it on a trusted LAN/VPN only." ;;
esac
echo "  Test it:   dig @${HOST_IP} example.com +short"
echo "             dig @${HOST_IP} doubleclick.net      # should be NXDOMAIN (blocked)"
echo "  Uninstall: sudo ${PREFIX}/uninstall.sh"
hr
