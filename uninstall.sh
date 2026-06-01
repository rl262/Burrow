#!/usr/bin/env bash
# Burrow uninstaller. Removes Burrow's services, configs, user, and state, and
# restores systemd-resolved. By default it leaves the unbound/pdns/mariadb
# packages and the PowerDNS database in place; pass --purge to remove those too.
set -euo pipefail

PURGE=0; [[ "${1:-}" == "--purge" ]] && PURGE=1
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
say() { printf ':: %s\n' "$*"; }

say "stopping Burrow services…"
systemctl disable --now burrow-dashboard.service 2>/dev/null || true
systemctl disable --now burrow-blocklist-refresh.timer 2>/dev/null || true
systemctl disable --now burrow-blocklist-refresh.service 2>/dev/null || true

say "removing units, sudoers, scripts…"
rm -f /etc/systemd/system/burrow-dashboard.service \
      /etc/systemd/system/burrow-blocklist-refresh.service \
      /etc/systemd/system/burrow-blocklist-refresh.timer \
      /etc/sudoers.d/burrow \
      /usr/local/sbin/burrow-update-blocklist
systemctl daemon-reload

say "removing Burrow unbound drop-ins + restoring distro drop-ins + unbound.conf…"
rm -f /etc/unbound/unbound.conf.d/burrow-server.conf \
      /etc/unbound/unbound.conf.d/burrow-blocklist.conf \
      /etc/unbound/unbound.conf.d/zz-burrow-overrides.conf \
      /etc/unbound/unbound.conf.d/burrow-remote-control.conf
# Re-enable any distro drop-ins the installer moved aside (root-auto-trust-anchor-file.conf, etc.)
for f in /etc/unbound/unbound.conf.d/*.burrow-disabled; do
  [[ -e "$f" ]] && mv -f "$f" "${f%.burrow-disabled}"
done
rm -rf /etc/unbound/blocklist.d /var/lib/unbound/burrow
[[ -f /etc/unbound/unbound.conf.burrow-orig ]] && mv -f /etc/unbound/unbound.conf.burrow-orig /etc/unbound/unbound.conf

say "restoring PowerDNS config…"
if [[ -f /etc/powerdns/pdns.conf.burrow-orig ]]; then
  mv -f /etc/powerdns/pdns.conf.burrow-orig /etc/powerdns/pdns.conf
fi

say "stopping unbound + pdns so systemd-resolved can reclaim port 53…"
# Burrow ran unbound as the resolver on 0.0.0.0:53 (which covers 127.0.0.53). If
# it is still listening when we (re)start systemd-resolved, resolved loses the
# race for its 127.0.0.53:53 stub and comes up WITHOUT it -- then glibc/curl get
# no DNS even though `resolvectl` still works via D-Bus. Stop + disable both so
# DNS goes cleanly back to systemd-resolved (packages stay installed unless --purge).
systemctl disable --now unbound 2>/dev/null || true
systemctl disable --now pdns 2>/dev/null || true

say "restoring systemd-resolved + resolv.conf…"
rm -f /etc/systemd/resolved.conf.d/burrow.conf
if systemctl list-unit-files systemd-resolved.service >/dev/null 2>&1; then
  systemctl restart systemd-resolved 2>/dev/null || true
  # Point resolv.conf back at the resolved stub, then wait (up to ~8s) for the box
  # to actually resolve -- both the stub listener AND a DHCP/link uplink need a moment.
  [[ -e /run/systemd/resolve/stub-resolv.conf ]] && \
    ln -sf ../run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
  for _ in $(seq 1 16); do
    getent hosts deb.debian.org >/dev/null 2>&1 && break
    sleep 0.5
  done
fi

say "removing Burrow files + user…"
rm -rf /opt/burrow /etc/burrow /var/lib/burrow
id burrow >/dev/null 2>&1 && userdel burrow 2>/dev/null || true
getent group burrow >/dev/null 2>&1 && groupdel burrow 2>/dev/null || true

if [[ "$PURGE" == "1" ]]; then
  say "--purge: dropping PowerDNS database + removing packages…"
  mariadb --protocol=socket -u root -e "DROP DATABASE IF EXISTS pdns; DROP USER IF EXISTS 'pdns'@'127.0.0.1';" 2>/dev/null || true
  export DEBIAN_FRONTEND=noninteractive
  apt-get purge -y -qq unbound pdns-server pdns-backend-mysql mariadb-server 2>/dev/null || true
  apt-get autoremove -y -qq 2>/dev/null || true
fi

# Final guarantee: never leave the box with broken DNS. If it still can't resolve
# (no systemd-resolved, the stub didn't come up, or package removal disturbed it),
# drop a static public resolver so the operator isn't stranded.
if ! getent hosts deb.debian.org >/dev/null 2>&1; then
  rm -f /etc/resolv.conf
  printf 'nameserver 1.1.1.1\nnameserver 9.9.9.9\n' >/etc/resolv.conf
fi

say "Burrow removed. (Re-check /etc/resolv.conf points where you expect.)"
