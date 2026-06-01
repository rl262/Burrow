# Burrow roadmap

A rough, non-binding plan — Burrow is alpha (`0.x`). See the
[releases](https://github.com/rl262/Burrow/releases) for what's actually shipped,
and [CONTRIBUTING](CONTRIBUTING.md) if you'd like to help.

## Authentication

- **Authentik SSO (OIDC / forward-auth) — _planned, not yet wired._** The dashboard
  already contains the code paths for an `authentik` auth mode (trusting an upstream
  Authentik forward-auth proxy via the `X-authentik-username` header), but the
  installer does **not** configure it yet — password auth is the default and the
  only installer-wired mode. Full integration is planned as a separate **dev-branch
  project** to be merged into `main`.

## Toward beta

- Dashboard **screenshots** in the README (the live "command center" is the headline
  feature over Pi-hole).
- **Login throttling / lockout** on the password form (rate-limit failed attempts).

## Toward 1.0

- **Edit upstream DoT resolvers from the dashboard** (currently installer-only).
- **IPv6 for LAN clients** — serve `AAAA`, derive `ip6.arpa` reverse zones, optional
  dual-bind.
- **Multiple LAN subnets** — comma-separated `LAN_CIDR`, per-subnet access-control +
  reverse zones.
- **Backup & restore** — dump/restore the PowerDNS DB + `/etc/burrow` config, with a
  documented disaster-recovery procedure.
- **Upgrade path + CHANGELOG** — record an installed-version marker and run
  migrations on re-install.
- **Authenticated DoT** — verify the upstream resolver's TLS certificate (currently
  opportunistic / unverified).
- **Wider OS coverage** — beyond the CI-validated Ubuntu 24.04 + Debian 13
  (e.g. Debian 12, Ubuntu 22.04, arm64).

## Later / ideas

- A first-class TLS story for non-loopback dashboards (reverse-proxy recipe; set the
  session-cookie `Secure` flag).
- Hash the admin password at rest instead of storing it in the env file.
- Signed release artifacts (checksums + signature verification).
- Optional full-recursion mode (root hints) instead of DoT forwarding.
- Conditional / per-domain forwarders (split-horizon).
- DHCP-lease hostname import.
