<div align="center">

# 🐇 Burrow

**A self-hosted, full-stack DNS server with ad-blocking and a web dashboard — a more capable Pi-hole alternative.**

Recursive resolver · authoritative local DNS · ad/tracker blocklists · a records editor · a live blocked-request command center — one `install.sh`, no cloud, no telemetry.

</div>

> ⚠️ **Status: alpha / work in progress.** The installer is being built and hardened. Test on a throwaway VM first.

---

## What is Burrow?

Pi-hole is a DNS sinkhole. Burrow is that **plus a real DNS server**: it runs **Unbound** as a validating, recursive resolver (with DNS-over-TLS upstreams and ad/tracker blocklists) *and* **PowerDNS** as an authoritative server for your own local zone — all managed from a single fast, keyboard-friendly web dashboard.

| | Pi-hole | **Burrow** |
|---|:---:|:---:|
| Network-wide ad/tracker blocking | ✅ | ✅ |
| Add/remove blocklists (adlists) | ✅ | ✅ |
| Live query/block activity view | ✅ | ✅ |
| Recursive resolver (no upstream snooping) | via add-on | ✅ Unbound |
| DNS-over-TLS upstreams | ⚠️ | ✅ |
| **Authoritative local DNS + full records editor** | ⚠️ basic | ✅ PowerDNS (A/AAAA/CNAME/PTR/MX/TXT/SRV/NS/CAA) |
| Per-record cache flush, reverse-PTR helper | ❌ | ✅ |

## Features

- **Records** — full CRUD over your local zones via the PowerDNS HTTP API, all record types, with a reverse-PTR auto-helper and per-record cache flush.
- **Unbound** — recursive resolution, DoT upstreams, cache controls, and **allow/deny overrides** that take effect instantly.
- **Blocklists** — add/remove adlist source URLs and refresh on demand (Pi-hole's "adlists" + "gravity").
- **Activity** — a live, terminal-style command center: blocked-request feed, blocks-per-minute, top blocked domains and top clients.
- **Dashboard** — a fast, dependency-light web UI (FastAPI + HTMX) with a dark/light "phosphor" theme; password login; optional TLS.

## Quick start

On a fresh **Debian 12/13** or **Ubuntu 22.04/24.04** box (run as root):

```bash
curl -sSL https://raw.githubusercontent.com/rl262/Burrow/main/install.sh | sudo bash
```

The installer asks for a few things (with sensible defaults), then sets everything up:

- **Local domain** — the zone Burrow is authoritative for (default `home.arpa`)
- **LAN CIDR** — who may query, and the reverse zone to create (e.g. `192.168.1.0/24`)
- **Upstreams** — DoT resolvers for recursion fallback (default Cloudflare + Quad9)
- **Admin password** — for the dashboard

When it finishes it prints the dashboard URL. Point your router's DNS (or a client) at this machine's IP and you're blocking ads network-wide.

## Architecture

```
                 ┌──────────────────────── this host ────────────────────────┐
 LAN clients ──▶ │  Unbound  :53   recursive + DoT upstreams + ad-block       │
                 │     │           (stub-forwards your local zone ↓)          │
                 │     ▼                                                       │
                 │  PowerDNS  :5300   authoritative for <your domain> + PTR    │
                 │     └─ HTTP API :8081  ◀── records editor                   │
                 │  Dashboard :8088 (FastAPI)  ◀── nginx :443 (optional TLS)   │
                 └─────────────────────────────────────────────────────────────┘
```

- **Unbound** is the front door on `:53` — it resolves recursively, blocks ads via `local-zone` lists, and forwards your local zone + reverse zones to PowerDNS.
- **PowerDNS** (gmysql backend) is authoritative for your local zone; the dashboard edits it through the HTTP API.
- The **dashboard** binds loopback; the installer can optionally put nginx + a self-signed cert in front of it.

## Requirements

- Debian 12/13 or Ubuntu 22.04/24.04, `systemd`, `amd64`/`arm64`
- Root, and a (recommended) static IP
- ~512 MB RAM, ~2 GB disk (MariaDB + the blocklists)

## Uninstall

```bash
sudo /opt/burrow/uninstall.sh
```

## Configuration

Install-time answers are saved to `/etc/burrow/burrow.conf`. Re-running the installer is safe (idempotent) — it keeps your generated secrets and prior answers, and only changes what you pass via env vars or the prompts. Blocklist sources live in `/etc/unbound/blocklist.d/`.

By default the dashboard binds **loopback only** (`127.0.0.1:8088`). To reach it from your LAN, either tunnel over SSH (`ssh -L 8088:127.0.0.1:8088 your-host`) or re-install with `DASHBOARD_BIND=0.0.0.0` — but the login is then sent over **plaintext HTTP**, so prefer a TLS reverse proxy or a VPN on untrusted networks.

## Privacy & security

No telemetry, no phone-home. Recursion is done locally by Unbound (your queries aren't handed to a third party by default; DoT is only the fallback path you configure). The dashboard is password-protected and binds to **loopback only** by default (see *Configuration* to expose it).

A few things worth knowing before you expose it:

- **Plaintext HTTP.** The dashboard speaks HTTP. If you bind it beyond loopback, put a TLS reverse proxy in front (or keep it on a VPN) so the admin password and session cookie don't cross the network in the clear.
- **The dashboard can rewrite DNS.** The `burrow` service account is granted (via `/etc/sudoers.d/burrow`) the `unbound-control` verbs needed to add/remove local-zone overrides and flush caches. That's by design — it's a DNS management UI — but it means compromise of the dashboard process equals the ability to redirect or blackhole any domain for your whole network. Keep it behind auth and off untrusted networks.
- **Secrets** (PowerDNS API key, session secret, admin password) live in `/etc/burrow/*.conf` and `dashboard.env`, mode `0640 root:burrow`. They're generated per install and never committed.

## License

[MIT](LICENSE) © Ron Lima. Built on the excellent [Unbound](https://nlnetlabs.nl/projects/unbound/) and [PowerDNS](https://www.powerdns.com/) projects.

## Acknowledgements

Inspired by [Pi-hole](https://pi-hole.net/). Blocklists from community projects (StevenBlack, OISD, HaGeZi, …) — please support them.
