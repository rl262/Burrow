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

say "restoring systemd-resolved + resolv.conf…"
rm -f /etc/systemd/resolved.conf.d/burrow.conf
# resolv.conf currently points at 127.0.0.1 (Burrow's unbound), which we're
# removing — so leave a WORKING resolver behind. Prefer handing DNS back to
# systemd-resolved if it actually comes up as the manager; otherwise drop a
# public-resolver fallback so the box can still resolve after uninstall.
restored=0
if systemctl list-unit-files systemd-resolved.service >/dev/null 2>&1; then
  systemctl restart systemd-resolved 2>/dev/null || true
  if systemctl is-active --quiet systemd-resolved && [[ -e /run/systemd/resolve/stub-resolv.conf ]]; then
    ln -sf ../run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
    restored=1
  fi
fi
if [[ "$restored" == "0" ]] && grep -q '127.0.0.1' /etc/resolv.conf 2>/dev/null; then
  printf 'nameserver 1.1.1.1\nnameserver 9.9.9.9\n' >/etc/resolv.conf
fi

say "restarting unbound + pdns with restored config…"
systemctl restart unbound 2>/dev/null || true
systemctl restart pdns 2>/dev/null || true

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

say "Burrow removed. (Re-check /etc/resolv.conf points where you expect.)"
