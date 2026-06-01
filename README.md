<div align="center">

# 🐇 Burrow

**A self-hosted, full-stack DNS server with ad-blocking and a web dashboard — a more capable Pi-hole alternative.**

Recursive resolver · authoritative local DNS · ad/tracker blocklists · a records editor · a live blocked-request command center — one `install.sh`, no cloud, no telemetry.

</div>

> ⚠️ **Status: alpha (`v0.1.0-alpha.3`) — validated on Ubuntu 24.04.** Test on a throwaway VM before pointing real clients at it.

---

## Table of contents

- [What is Burrow?](#what-is-burrow)
- [Features](#features)
- [Architecture](#architecture)
- [Requirements & pre-flight](#requirements--pre-flight)
- [Install](#install)
- [Install-time prompts (the 6 settings)](#install-time-prompts-the-6-settings)
- [What the installer actually does](#what-the-installer-actually-does)
- [Post-install: point your network at Burrow & verify](#post-install-point-your-network-at-burrow--verify)
- [Accessing the dashboard](#accessing-the-dashboard)
- [Blocklists & per-domain allow/deny](#blocklists--per-domain-allowdeny)
- [Managing local DNS records](#managing-local-dns-records)
- [Re-running the installer & changing settings](#re-running-the-installer--changing-settings)
- [File & secret locations](#file--secret-locations)
- [Services, ports & troubleshooting](#services-ports--troubleshooting)
- [Uninstall](#uninstall)
- [Privacy & security](#privacy--security)
- [License](#license) · [Acknowledgements](#acknowledgements)

---

## What is Burrow?

Pi-hole is a DNS sinkhole. Burrow is that **plus a real DNS server**: it runs **Unbound** as a validating resolver (DNSSEC, ad/tracker blocklists, encrypted DNS-over-TLS to your chosen upstreams) *and* **PowerDNS** as an authoritative server for your own local zone — all managed from a single fast, keyboard-friendly web dashboard.

| | Pi-hole | **Burrow** |
|---|:---:|:---:|
| Network-wide ad/tracker blocking | ✅ | ✅ |
| Add/remove blocklists (adlists) | ✅ | ✅ |
| Live query/block activity view | ✅ | ✅ |
| DNSSEC-validating resolver | via add-on | ✅ Unbound |
| Encrypted upstreams (DNS-over-TLS) | ⚠️ | ✅ |
| **Authoritative local DNS + full records editor** | ⚠️ basic | ✅ PowerDNS (A/AAAA/CNAME/PTR/MX/TXT/SRV/NS/CAA) |
| Per-record cache flush · reverse-PTR helper | ❌ | ✅ |

## Features

- **Records** — full CRUD over your local zones via the PowerDNS HTTP API, all common record types, with a reverse-PTR auto-helper and per-record cache flush.
- **Unbound** — DNSSEC validation, DoT upstreams, cache controls, and **allow/deny overrides** that take effect instantly (no resolver restart).
- **Blocklists** — add/remove adlist source URLs and refresh on demand (Pi-hole's "adlists" + "gravity").
- **Activity** — a live, terminal-style command center: blocked-request feed, blocks-per-minute, top blocked domains and top clients.
- **Dashboard** — a fast, dependency-light web UI (FastAPI + HTMX) with a dark/light "phosphor" theme and password login.

## Architecture

```
                 ┌──────────────────────── this host ────────────────────────┐
 LAN clients ──▶ │  Unbound  :53   DNSSEC + ad-block + DoT upstreams (↑ :853) │
                 │     │           (stub-forwards your local zone ↓)          │
                 │     ▼                                                       │
                 │  PowerDNS  127.0.0.1:5300   authoritative for <domain> + PTR│
                 │     └─ HTTP API 127.0.0.1:8081  ◀── records editor          │
                 │  Dashboard :8088 (FastAPI)  ◀── (put your own TLS proxy here)│
                 └─────────────────────────────────────────────────────────────┘
```

- **Unbound** is the front door on `:53`. It validates DNSSEC, blocks ads via `local-zone … always_nxdomain` lists, forwards public queries to your upstreams over **DoT (port 853)**, and **stub-forwards** your local zone + reverse zones to PowerDNS on `127.0.0.1:5300`.
- **PowerDNS** (gmysql/MariaDB backend) is authoritative for *only* your local forward zone and the reverse (`in-addr.arpa`) zones derived from your LAN CIDR. The dashboard edits it through the HTTP API on `127.0.0.1:8081`.
- **The dashboard** binds **loopback by default** (`127.0.0.1:8088`) and speaks plain HTTP. Burrow does **not** install a TLS proxy — to expose the UI safely, front it with your own reverse proxy, or reach it over an SSH tunnel / VPN (see [Accessing the dashboard](#accessing-the-dashboard)).

## Requirements & pre-flight

- **OS:** **Validated in CI on Ubuntu 24.04 and Debian 13** (the current stable releases, `amd64`). Other Debian/Ubuntu releases (and arm64) may work but are untested — please [report issues](https://github.com/rl262/Burrow/issues). The installer hard-aborts on anything outside Debian/Ubuntu.
- **Root:** run via `sudo` — it installs packages, writes `/etc`, and manages systemd units.
- **A static IP** (strongly recommended) — your clients will point their DNS at this box, so its address must not change.
- **Resources:** ~512 MB RAM and ~2 GB disk (MariaDB + the blocklists; the default lists are ~840k domains).
- **Outbound network:** TCP **443** (to fetch the installer + blocklists from GitHub/adlist hosts) and TCP **853** (DNS-over-TLS to your upstream resolvers).
- **Port 53 must be free.** On Ubuntu, `systemd-resolved` listens on `127.0.0.53:53` by default — the installer handles this for you (see [What the installer actually does](#what-the-installer-actually-does)).

## Install

Burrow installs with one script. **The interactive prompts only appear when the script is run from a terminal** — piping `curl … | bash` makes stdin a pipe, so the installer runs **non-interactively with defaults** instead of prompting. Pick the form you want:

**A) Interactive (recommended for your first install)** — download, then run, so you get the prompts:

```bash
curl -fsSL https://raw.githubusercontent.com/rl262/Burrow/v0.1.0-alpha.3/install.sh -o /tmp/burrow-install.sh && sudo bash /tmp/burrow-install.sh
```

**B) One-liner, non-interactive** — runs straight through with all defaults (loopback dashboard + an auto-generated admin password printed at the end). You still see every step, just no prompts:

```bash
curl -fsSL https://raw.githubusercontent.com/rl262/Burrow/v0.1.0-alpha.3/install.sh | sudo bash
```

**C) Non-interactive but with your own settings** — set any of the prompt values as environment variables and they win over the defaults (no prompting):

```bash
curl -fsSL https://raw.githubusercontent.com/rl262/Burrow/v0.1.0-alpha.3/install.sh \
  | sudo LOCAL_DOMAIN=home.arpa LAN_CIDR=192.168.1.0/24 DASHBOARD_BIND=0.0.0.0 ADMIN_PASSWORD='choose-a-strong-one' bash
```

> **Pinned installs.** These commands fetch a specific release tag (`v0.1.0-alpha.3`), so what you install is reproducible — a push to `main` never silently changes a new install. To install the latest unreleased `main` instead, fetch `…/main/install.sh` **and** pass `BURROW_REF=main` (the installer pins the source tree it downloads to `BURROW_REF`, which defaults to the release tag). Released versions are on the [Releases page](https://github.com/rl262/Burrow/releases).

When it finishes, the installer prints the dashboard URL and (if it generated the password) the password. Then [point your clients at it](#post-install-point-your-network-at-burrow--verify).

## Install-time prompts (the 6 settings)

Every setting has a sensible default shown in `[brackets]`; press **Enter** to accept it. Each can also be supplied as an environment variable (shown below) to skip the prompt. **Precedence:** an explicit env var **>** the value saved from a previous install (`/etc/burrow/burrow.conf`) **>** what you type at the prompt **>** the built-in default.

Here's exactly what each one controls.

---

### 1. Local domain — `LOCAL_DOMAIN`

```
Local domain Burrow is authoritative for [home.arpa]:
```

**What it is:** the internal DNS domain that Burrow's PowerDNS half serves **authoritatively** for your LAN. Anything ending in this domain is answered from your own records; everything else recurses out to the internet. This is what lets you give machines stable private names like `nas.home.arpa` that resolve only inside your network.

**Default:** `home.arpa` (built-in).

**Accepted values:** a DNS domain — a single label (`lan`, `home`) or a dotted name (`home.arpa`, `lab.example.com`). **Do not add a trailing dot** — Burrow adds the canonical dot itself everywhere it's needed. ⚠️ This value is **not validated**, so a typo lands directly in your zone names.

**What it touches:** the PowerDNS forward zone is *named* exactly this (created with an SOA + `NS ns.<domain>` + an `A ns.<domain> → this host`); Unbound gets a `stub-zone` for it (plus `local-zone "<domain>." nodefault` + `domain-insecure` so local answers don't get shadowed or SERVFAIL); and it becomes the dashboard's landing zone and site title.

**How to choose:**
- **`home.arpa` (default)** — the IETF-reserved domain for home networks (RFC 8375). It can *never* collide with a real public domain, so it's the safe pick when in doubt.
- **A subdomain you own** (e.g. `lab.example.com`) — pretty names with zero collision risk and room to delegate from real DNS later. Recommended if you have a domain.
- **A short label** (`lan`, `home`) — works and reads nicely (`nas.lan`), but relies on that label never becoming a real TLD.
- **⚠️ Never use a domain you don't own** (`example.com`, `google.com`, …). Burrow becomes authoritative for it on your LAN and will **black-hole the real site** for every client.

> Changing this later is **additive, not a rename**: re-running with a new value creates a *second* forward zone and leaves the old one behind. Pick it once.

---

### 2. LAN subnet CIDR — `LAN_CIDR`

```
LAN subnet CIDR allowed to query (and reverse zone) [192.168.1.0/24]:
```

**What it is:** two things at once — (a) the **access-control gate**: which clients are allowed to send DNS queries to Burrow, and (b) the source for the **reverse (PTR) zones** Burrow auto-creates in PowerDNS.

**Default:** **auto-detected from this machine's NIC** — the installer reads the subnet on the interface that owns the default route and masks it to its network base. (This is computed live on the box; it is *not* hardcoded. Falls back to `192.168.1.0/24` only if detection fails.)

**Accepted values:** a single IPv4 CIDR like `192.168.1.0/24`. The host bits you type don't matter — `192.168.1.44/24` is normalized to `192.168.1.0/24`. ⚠️ An **invalid CIDR aborts the install** (this one *is* validated). IPv6 is not handled here.

**What it touches:**
- **Unbound access-control:** Unbound refuses `0.0.0.0/0` by default and explicitly allows only loopback **+ this CIDR**. Clients outside it get `REFUSED`.
- **Reverse zones:** by prefix length — `/24` (or longer) → one `in-addr.arpa` zone; **`/23`–`/20` → the 2–16 covering `/24` zones** (a `/23` deliberately becomes *two* `/24` zones, never one over-broad classful zone); **wider than `/20` → no reverse zones are auto-created** (a warning is printed; add them later from the dashboard).

**How to choose:** in almost all cases, accept the auto-detected value — it's your actual LAN. Only change it if the box is multi-homed and the default route isn't on the network you want to serve.

> ⚠️ **Never set this to `0.0.0.0/0`** — that turns Burrow into an open resolver, dangerous on any internet-reachable host.

---

### 3. Unbound listen IP — `LISTEN_IP`

```
IP Unbound binds for DNS (0.0.0.0 = all) [0.0.0.0]:
```

**What it is:** the local address Unbound binds its DNS listener (UDP/TCP **:53**) to — i.e. *which interface answers DNS queries*. This only opens the socket; **who is allowed to query is decided separately by `LAN_CIDR`.**

**Default:** `0.0.0.0` (all IPv4 interfaces). Not auto-detected.

**Accepted values:** a single address written verbatim into Unbound's `interface:` directive — `0.0.0.0` (all IPv4), a specific IP like `192.168.1.2` (one interface only), or `::` (all IPv6). ⚠️ Not validated — a typo surfaces later as an `unbound-checkconf` failure, not at the prompt.

**How to choose:** leave it `0.0.0.0` unless the box is multi-homed and you want DNS on one interface only.

> **Don't** set this to `127.0.0.1` to "secure" the resolver — that makes Burrow unreachable from your LAN. Restrict *clients* with `LAN_CIDR` instead. (Also: don't confuse this with `DASHBOARD_BIND`, which is the *web UI's* listener.) A concrete (non-wildcard) value here also becomes the seeded `ns.<domain>` A record + host PTR.

---

### 4. Upstream DoT resolvers — `UPSTREAMS`

```
Upstream DoT resolvers (comma-separated IPs) [1.1.1.1,1.0.0.1,9.9.9.9,149.112.112.112]:
```

**What it is:** the public resolvers Unbound forwards all non-local internet queries to, over **DNS-over-TLS (port 853)**. Burrow uses a forwarding model (encrypted to these providers) rather than recursing from the root servers.

**Default:** Cloudflare (`1.1.1.1`, `1.0.0.1`) + Quad9 (`9.9.9.9`, `149.112.112.112`).

**Accepted values:** a comma-separated list of resolver IPs, **no port** — the installer appends `@853` for you. Whitespace is trimmed; empty entries dropped. **Every address must run a DoT listener on :853** — plain-DNS-only resolvers will not work.

**How to choose:** the default (four targets across two providers) is fine for most homelabs and gives failover. Use Quad9 only (`9.9.9.9,149.112.112.112`) for upstream malware filtering, or Cloudflare only (`1.1.1.1,1.0.0.1`) for lowest latency.

> Notes: ⚠️ **don't** add `@853`/`:853` yourself — entering `1.1.1.1@853` produces a broken `…@853@853`. These upstreams *see* your queries (it's forwarding, encrypted in transit). DoT here is **opportunistic** — Unbound encrypts to the upstream but does not verify its TLS certificate chain. There is **no dashboard UI** for this; change it by re-running the installer (editing `burrow.conf` alone won't re-render the Unbound config).

---

### 5. Dashboard bind IP — `DASHBOARD_BIND`

```
Dashboard bind IP (127.0.0.1 = loopback only; 0.0.0.0 = LAN) [127.0.0.1]:
```

**What it is:** which interface the **web dashboard** (port **8088**, fixed) listens on. This is unrelated to `LISTEN_IP` (the DNS listener).

**Default:** `127.0.0.1` — **loopback only**, reachable only from the box itself (or via an SSH tunnel). Secure by default.

**Accepted values:** `127.0.0.1` (loopback), `0.0.0.0` (all interfaces / LAN-reachable), `::1` (IPv6 loopback), or a specific interface IP. ⚠️ Not validated — a bad value crashes `burrow-dashboard.service` on start.

**How to choose:**
- Keep **`127.0.0.1`** and reach the UI over an [SSH tunnel](#accessing-the-dashboard) — most secure, recommended.
- Use **`0.0.0.0`** for convenient LAN access — but the dashboard speaks **plaintext HTTP**, so your admin password and session cookie cross the network in the clear. The installer prints a loud warning in this case. Only do it on a trusted LAN, behind a TLS reverse proxy, or over a VPN.

> Exposing the dashboard is higher-stakes than a typical web UI: the `burrow` account can run `unbound-control` via sudo, so a compromised dashboard can redirect or blackhole DNS for your whole network.

---

### 6. Dashboard admin password — `ADMIN_PASSWORD`

```
Dashboard admin password (blank = generate):
```

**What it is:** the password for the single **`admin`** login of the dashboard (the username is fixed). The input is hidden as you type, with no confirm step.

**Default:** none — **leave it blank and Burrow generates a strong 20-character random password and prints it once** at the end of the install.

**Accepted values:** any string **except** one containing a newline or a single-quote (`'`) — those two are rejected (they'd break the systemd EnvironmentFile). Everything else, including `$ " \ # space`, is accepted verbatim. There is **no minimum length / strength check** on a password you supply.

**How to choose:** for most installs, leave it blank and save the generated password. Supply your own only if you have a strong one in mind (`ADMIN_PASSWORD='…'` for unattended installs).

> ⚠️ A **generated** password is shown **only once** (`Password : … (generated — save it)`). If you miss it, recover it with `sudo grep DNSMGR_APP_PASSWORD /etc/burrow/dashboard.env`. A password you **supply** is *not* echoed. **Re-running the installer does not rotate it** — to change it, pass `ADMIN_PASSWORD=` again (and restart the service). Non-interactive installs (`curl | bash`, no TTY) always auto-generate.

## What the installer actually does

Beyond the prompts, `install.sh` makes these changes to your system (all idempotent):

1. **Pre-flight:** confirms it's root and the OS is Debian/Ubuntu; otherwise aborts.
2. **Installs packages** via `apt`: `unbound`, `pdns-server` + `pdns-backend-mysql`, `mariadb-server`, `dns-root-data`, Python 3 (`venv`/`pip`), and small CLI deps. (`pdns` is masked during install so its pre-config start attempt doesn't error, then unmasked.)
3. **Creates a system user/group `burrow`** (added to `systemd-journal` so the dashboard can read the block log) and the directories under `/opt/burrow`, `/etc/burrow`, `/var/lib/burrow`.
4. **Generates secrets** (PowerDNS API key, dashboard session secret, and the admin password if blank) and writes `/etc/burrow/burrow.conf` (mode `0640 root:burrow`).
5. **Configures PowerDNS** (gmysql backend, authoritative on `127.0.0.1:5300`, HTTP API on `:8081`) and **bootstraps the database + zones**: creates the forward zone for your domain, the reverse `in-addr.arpa` zone(s) for your CIDR, and seeds the host's own A + PTR records.
6. **Configures Unbound** as the `:53` front door: moves aside the distro's own drop-ins (so its trust-anchor/remote-control config doesn't clash), renders Burrow's config from your answers, and seeds the DNSSEC root key.
7. **Frees port 53** — on systems with `systemd-resolved`, writes `DNSStubListener=no` and restarts it so Unbound can bind `:53`, then **points the box's own `/etc/resolv.conf` at `127.0.0.1`**.
8. **Seeds the blocklists** (first download of the default adlists — ~840k domains) and enables a **nightly refresh timer**.
9. **Installs the dashboard:** a Python venv under `/opt/burrow`, the `burrow-dashboard` systemd service (listening per `DASHBOARD_BIND`), a least-privilege sudoers rule, and the uninstaller at `/opt/burrow/uninstall.sh`.
10. **Prints a summary** with the dashboard URL, the password (if generated), and test commands.

## Post-install: point your network at Burrow & verify

Installing Burrow doesn't change anything for your clients until you repoint DNS at it.

**Option A — whole network (recommended):** in your router/DHCP server, set the **DNS server** handed to clients to Burrow's IP. Every device then uses it on its next DHCP lease.

**Option B — a single device:** set that device's DNS server to Burrow's IP manually.

**Verify it works** (replace `<burrow-ip>` and `<domain>`):

```bash
dig @<burrow-ip> example.com +short           # normal recursion → real IPs
dig @<burrow-ip> doubleclick.net              # ad domain → status: NXDOMAIN (blocked)
dig @<burrow-ip> ns.<domain> +short           # your local zone → the Burrow host IP
dig @<burrow-ip> -x <burrow-ip> +short        # reverse PTR → ns.<domain>.
```

If `example.com` resolves, `doubleclick.net` is `NXDOMAIN`, and the local/PTR lookups answer, you're blocking ads network-wide and serving local DNS.

## Accessing the dashboard

The dashboard listens on **port 8088**. How you reach it depends on `DASHBOARD_BIND`:

- **Loopback (default, `127.0.0.1`)** — open an SSH tunnel from your laptop, then browse locally:
  ```bash
  ssh -L 8088:127.0.0.1:8088 <user>@<burrow-ip>
  # then open http://127.0.0.1:8088/ in your browser
  ```
- **LAN (`0.0.0.0`)** — open `http://<burrow-ip>:8088/` directly. ⚠️ This is **plaintext HTTP** — only do it on a trusted network, behind your own TLS reverse proxy, or over a VPN.

Log in as **`admin`** with the password you set (or the generated one from the install summary / `sudo grep DNSMGR_APP_PASSWORD /etc/burrow/dashboard.env`).

## Blocklists & per-domain allow/deny

Burrow ships with reputable default adlists (StevenBlack, OISD, HaGeZi — ~840k domains) and refreshes them on a **nightly timer**. Two distinct mechanisms:

- **Blocklists (adlists)** — *sources* of many domains. Manage them on the dashboard's **Unbound** tab: add/remove source URLs and hit **refresh now** to download + apply them. (Source URLs you add in the GUI live in `/etc/unbound/blocklist.d/sources.d/gui.txt`; the base lists are installer-managed and read-only in the UI.)
- **Allow / deny overrides** — *single domains* you force open or force blocked. These apply **instantly** at runtime (no resolver restart) and persist across blocklist refreshes. Use **allow** to un-block a domain a list caught by mistake, **deny** to block one manually.

The **Activity** tab shows blocks live: a feed of blocked requests, blocks-per-minute, and the top blocked domains/clients.

## Managing local DNS records

On the dashboard's **records** view you edit your local forward zone (and reverse zones) through the PowerDNS API: add/edit/delete `A`, `AAAA`, `CNAME`, `PTR`, `MX`, `TXT`, `SRV`, `NS`, `CAA`. When you add an `A` record you can tick the **reverse-PTR helper** to create the matching `PTR` automatically (if the reverse zone exists). Each record has a **flush** action to drop it from the Unbound + PowerDNS caches immediately.

## Re-running the installer & changing settings

Re-running is **safe and idempotent**. On a re-run:

- **Generated secrets are reused** (PowerDNS API key, session secret) — they are not rotated, so the dashboard doesn't log everyone out and the DB user keeps working.
- **Your previous answers are reused** from `/etc/burrow/burrow.conf` as the new defaults. Override any of them with an **env var** (which wins), or change them at the prompt.
- **The admin password is not rotated** unless you pass `ADMIN_PASSWORD=`.
- **Changing `LOCAL_DOMAIN` is additive** — it creates a new forward zone rather than renaming the old one. Clean up the stale zone from the dashboard.
- **Changes to `UPSTREAMS` / `LISTEN_IP` / `DASHBOARD_BIND` take effect only via the installer**, which re-renders the config and restarts the service — editing `burrow.conf` by hand is not enough.

## File & secret locations

| Path | Purpose | Mode |
|---|---|---|
| `/etc/burrow/burrow.conf` | Install answers + generated secrets (source of truth) | `0640 root:burrow` |
| `/etc/burrow/dashboard.env` | Dashboard env (API key, session secret, admin password) | `0640 root:burrow` |
| `/opt/burrow/` | App code, Python venv, `bin/`, `uninstall.sh` | — |
| `/etc/powerdns/pdns.conf` | PowerDNS config (gmysql, `:5300`, API `:8081`) | `0640` |
| `/etc/unbound/unbound.conf.d/burrow-*.conf` | Unbound server, blocklist, overrides, remote-control drop-ins | — |
| `/etc/unbound/blocklist.d/` | `sources.txt`, `allowlist.txt`, generated `blocklist.conf`, `sources.d/gui.txt`, `status` | — |
| `/var/lib/unbound/burrow/overrides.conf` | App-owned allow/deny include (daemon-readable) | `0644` |
| `/usr/local/sbin/burrow-update-blocklist` | Blocklist refresh script | `0755` |
| `/etc/systemd/system/burrow-*.{service,timer}` | Dashboard service + nightly blocklist refresh | — |
| `/etc/sudoers.d/burrow` | Least-privilege `unbound-control` access for the `burrow` user | `0440` |

Secrets are generated per install and never leave the box. Reading them requires root (`sudo`).

## Services, ports & troubleshooting

**Services:** `unbound`, `pdns`, `mariadb`, `burrow-dashboard`, `burrow-blocklist-refresh.timer`.

**Ports:** Unbound `:53` (UDP/TCP, on `LISTEN_IP`) · PowerDNS `127.0.0.1:5300` (auth) + `127.0.0.1:8081` (HTTP API) · dashboard `:8088` (on `DASHBOARD_BIND`) · DoT `:853` outbound.

Quick checks:

```bash
systemctl status unbound pdns burrow-dashboard          # are the daemons up?
sudo unbound-checkconf                                  # is the Unbound config valid?
journalctl -u burrow-dashboard -n 50 --no-pager         # dashboard errors
ss -lntup | grep ':53'                                  # what holds port 53
```

> **Most common gotcha — `systemd-resolved` and port 53 (Ubuntu).** systemd-resolved listens on `127.0.0.53:53` by default and will fight Unbound for the port. The installer disables its stub listener (`DNSStubListener=no`) and restarts it. If Unbound fails to bind `:53`, confirm nothing else holds it (`ss -lntup | grep :53`) and that `/etc/systemd/resolved.conf.d/burrow.conf` exists, then `sudo systemctl restart systemd-resolved unbound`.

## Uninstall

```bash
sudo /opt/burrow/uninstall.sh            # remove Burrow; keep the unbound/pdns/mariadb packages + DNS data
sudo /opt/burrow/uninstall.sh --purge    # also drop the PowerDNS database and remove the packages
```

The uninstaller removes Burrow's services, configs, drop-ins, user, and state, and **hands DNS back to `systemd-resolved`** — it stops Unbound/PowerDNS first so resolved can reclaim its `127.0.0.53:53` stub, waits for resolution to come back, and falls back to a public resolver if needed, so the box is never left without DNS. Without `--purge`, your local DNS records (the PowerDNS database) and the installed packages are left in place.

## Privacy & security

No telemetry, no phone-home. Resolution happens on your box; public queries are forwarded **encrypted (DoT)** to the upstreams you choose. A few things worth knowing before you expose anything:

- **Plaintext HTTP dashboard.** The UI speaks HTTP. If you bind it beyond loopback, put a TLS reverse proxy in front (or keep it on a VPN/SSH tunnel) so the admin password and session cookie aren't sent in the clear. The dashboard also has a same-origin (CSRF) check on state-changing requests.
- **The dashboard can rewrite DNS.** The `burrow` service account is granted (via `/etc/sudoers.d/burrow`) the `unbound-control` verbs needed to add/remove local-zone overrides and flush caches — by design, it's a DNS management UI. That means compromising the dashboard process equals the ability to redirect or blackhole any domain for your whole network. Keep it authenticated and off untrusted networks.
- **Upstream visibility.** Burrow forwards public queries to your chosen DoT resolvers — encrypted in transit, but those providers do see the queries. Pick upstreams you trust (or reconfigure Unbound for full recursion).
- **Secrets** (PowerDNS API key, session secret, admin password) live in `/etc/burrow/*.conf`, mode `0640 root:burrow`, generated per install and never committed.

## Roadmap

See [ROADMAP.md](ROADMAP.md) for planned work, sequenced toward Pi-hole parity — secondary/failover, persistent query stats, per-client groups, "pause blocking", regex rules, arm64, backup/restore, Authentik SSO, and more.

## License

[MIT](LICENSE) © Ron Lima. Built on the excellent [Unbound](https://nlnetlabs.nl/projects/unbound/) and [PowerDNS](https://www.powerdns.com/) projects.

## Acknowledgements

Inspired by [Pi-hole](https://pi-hole.net/). Blocklists from community projects (StevenBlack, OISD, HaGeZi, …) — please support them.
