# Burrow roadmap

A rough, non-binding plan — Burrow is alpha (`0.x`). See the
[releases](https://github.com/rl262/Burrow/releases) for what's actually shipped,
and [CONTRIBUTING](CONTRIBUTING.md) if you'd like to help.

The post-beta milestones below are sequenced by impact: first making Burrow
dependable enough to be your only resolver, then everyday usability, then broader
reach, then advanced features.

## Now — finishing beta

- **Dashboard screenshots** in the README (the live "command center" is a great
  showcase). This is the last item before dropping the alpha label.

## Authentication

- **Authentik SSO (OIDC / forward-auth) — _planned, dev-branch._** The dashboard
  already has the `authentik` code path (trusting an upstream forward-auth proxy
  via `X-authentik-username`), but the installer doesn't configure it yet —
  password auth is the default. Full integration will be a separate dev-branch
  project merged into `main`.

## v0.2 — run it as your only DNS

The features that decide whether Burrow is dependable enough to be someone's
primary resolver.

- **Secondary / failover support** *(the most-requested capability).* No
  clustering needed — a documented **two-box pattern** + a `burrow-sync` helper
  (rsync `/etc/burrow` + blocklists, `pdnsutil` zone export/import) gets most of
  the value. Later: a `ROLE=primary|secondary` installer flag where the secondary
  slaves the forward/reverse zones (PowerDNS AXFR or native MySQL replication) and
  pulls the same blocklist sources, so DHCP can hand out both IPs.
- **Backup & restore.** `burrow-backup` / `burrow-restore` (mysqldump the pdns DB
  + `pdnsutil` zone export + tar `/etc/burrow`) and a short disaster-recovery
  procedure. Infra nobody can recover isn't infra anyone adopts.
- **Login throttling + password hashing at rest.** Rate-limit failed logins
  (in-memory token bucket keyed by client IP) and store an argon2/bcrypt hash
  instead of the plaintext password in `dashboard.env`.

## v0.3 — everyday usability

The things people reach for day to day.

- **Persistent query/block log + long-term stats** *(the most-missed feature).*
  Today `blocked.py` tails the journal into a live-only ring buffer that resets on
  restart. Add a SQLite (or MariaDB) table of queries/blocks (timestamp, client,
  domain, action) and build "queries over 24h / 7d, top clients, top domains"
  views on top.
- **"Disable blocking for N minutes."** A common need — pause blocking so a smart
  TV or a checkout page loads. Implement as a temporary
  `local-zone … always_transparent` override (or swapped include) with a timer
  that auto-reverts.
- **Regex / wildcard block + allow rules.** Expose user-defined wildcard
  (`*.example.com` → `local-zone "example.com." always_nxdomain`) and regex rules
  in the dashboard — building on the wildcard normalization the refresh script
  already does.

## v0.4 — broader reach

- **arm64 support.** Many self-hosters run on a Raspberry Pi or other ARM SBC. Add
  `linux/arm64` to the smoke-test matrix (QEMU / `docker buildx`) and drop the
  implicit amd64 assumption — the stack is already packaged for arm64.
- **Per-client / group blocking.** Let the dashboard assign clients (by IP/subnet)
  to groups with different blocklists/allowlists, via Unbound views /
  `access-control-tag`.
- **Conditional forwarding for client hostnames.** So reverse lookups of DHCP
  clients resolve to names — forward the client subnet's reverse lookups to the
  router's DNS, or import DHCP leases → PTR.
- **IPv6 for LAN clients** — serve `AAAA`, derive `ip6.arpa` reverse zones,
  optional dual-bind. (A 2026 resolver that refuses every dual-stack client's IPv6
  query is a surprise.)
- **Multiple LAN subnets** — comma-separated `LAN_CIDR`, per-subnet access-control
  + reverse zones (pairs naturally with per-client groups).
- **Edit upstream DoT resolvers from the dashboard** (currently installer-only).
- **Upgrade path + CHANGELOG** — installed-version marker + migrations on
  re-install.

## v1.0 and beyond — advanced features

- **Built-in DHCP (optional).** Wire `kea`/`dnsmasq` and feed leases into PowerDNS
  as A/PTR for automatic client names. Big scope — only after the above land.
- **Prometheus `/metrics` exporter** (Unbound + PowerDNS) for Grafana.
- **Authenticated DoT** — verify the upstream resolver's TLS certificate
  (currently opportunistic / unverified). A credibility item.
- A first-class **TLS story** for non-loopback dashboards (reverse-proxy recipe;
  `Secure` session cookie).
- **Signed release artifacts** (checksums + signature verification).
- Optional **full-recursion mode** (root hints) instead of DoT forwarding.

---

*The two highest-impact items: **secondary/failover** (makes Burrow deployable as
your only resolver) and **persistent query stats** (the feature people miss most).*
