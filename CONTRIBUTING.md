# Contributing to Burrow

Thanks for helping! Burrow is a solo-maintained homelab project; issues and PRs are welcome. A few ground rules keep it safe to install on other people's networks.

## Before you start

- Burrow installs system packages, writes `/etc`, and manages systemd units **as root** — always develop and test against a **throwaway VM or container**, never your daily resolver.
- Targets: Ubuntu 24.04 and Debian 13 (`amd64`) — what CI validates. Other Debian/Ubuntu releases may work but aren't tested.
- No secrets and no private data in code, comments, or commits — examples use `home.arpa` / `192.168.x` only.

## Test loop (throwaway VM)

From your branch checkout on a fresh Debian/Ubuntu VM:

```bash
sudo BURROW_REF=main bash install.sh             # install your working tree (or download+run for the prompts)
dig @127.0.0.1 example.com +short                # recursion works
dig @127.0.0.1 doubleclick.net | grep status     # -> NXDOMAIN (blocked)
dig @127.0.0.1 -x <vm-ip> +short                 # reverse PTR resolves
curl -s http://127.0.0.1:8088/healthz            # dashboard is up
sudo /opt/burrow/uninstall.sh --purge            # and confirm DNS is restored afterward
```

> When testing the installer from a local checkout, `BURROW_REF=main` (or any value) is ignored for the source tree because a local checkout is used directly — it only matters for the `curl | bash` download path.

**Re-running must stay idempotent.** Run `install.sh` twice and confirm it does not rotate secrets, duplicate zones, or needlessly restart services.

### Containerized smoke test (what CI runs)

CI (`.github/workflows/smoke.yml`) runs the full install → resolve → block → dashboard → uninstall cycle inside a systemd-enabled container on each target distro. You can run the same thing locally with Docker (Burrow needs real systemd, so the container boots `/sbin/init` as PID 1):

```bash
docker run -d --name burrow --privileged --cgroupns=host \
  --tmpfs /run -v /sys/fs/cgroup:/sys/fs/cgroup:rw -v "$PWD:/src:ro" \
  debian:13 bash -c 'apt-get update -qq && apt-get install -y -qq systemd systemd-sysv >/dev/null && exec /sbin/init'
sleep 10
docker exec burrow bash -c 'apt-get install -y -qq curl >/dev/null'
docker exec -e ASSUME_YES=1 burrow bash -c 'cd /src && bash install.sh'

docker exec burrow dig @127.0.0.1 example.com +short            # recursion
docker exec burrow bash -c "dig @127.0.0.1 doubleclick.net | grep status:"  # -> NXDOMAIN
docker exec burrow curl -fsS http://127.0.0.1:8088/healthz       # dashboard

docker exec burrow /opt/burrow/uninstall.sh --purge
docker rm -f burrow
```

## Checks

- **Shell** must pass `shellcheck`:
  ```bash
  shellcheck install.sh uninstall.sh bin/* lib/*
  ```
  Keep scripts `set -euo pipefail`, prefer idempotent guards over blind commands, and quote variables.
- **Python (dashboard)** must pass `ruff` and stay dependency-light (no build step):
  ```bash
  ruff check dashboard/app
  ```
- Match the surrounding style; keep commits small and focused with clear messages.

## Demo data (for screenshots)

[`examples/demo-seed.sh`](examples/demo-seed.sh) populates an install with believable **fake** data for screenshots/demos — a fake local zone (`nas`, `jellyfin`, `grafana`, …), reverse PTRs, a couple of allow/deny overrides, and (with `--activity`) some blocked-query traffic so the Activity tab has data. Run it on a throwaway demo box (ideally installed with `LOCAL_DOMAIN=home.arpa` and `LAN_CIDR=192.168.1.0/24` so the data matches):

```bash
sudo bash examples/demo-seed.sh --activity
```

It's idempotent and **not for production** (it writes fake records into your local zone). `--activity` adds temporary `192.168.1.x` IP aliases to generate varied client traffic, then removes them.

## Submitting

Open a PR against `main` describing **what** you changed and **how you tested it** (which OS/arch). For anything security-sensitive, see [SECURITY.md](SECURITY.md) — report privately, don't open a public PR/issue.
