# Security Policy

Burrow is a network-facing DNS appliance installed as **root**, and its dashboard can rewrite DNS for your whole LAN. Security reports are taken seriously — thank you for helping.

## Reporting a vulnerability

**Please do not open a public issue for security problems.** Report privately via GitHub's private vulnerability reporting:

1. Go to the repo's **Security** tab → **Report a vulnerability** ([open advisory form](https://github.com/rl262/Burrow/security/advisories/new)).
2. Include the affected version (`cat /etc/burrow/VERSION`), your OS/arch, and clear reproduction steps.

You'll get an acknowledgement and a coordination timeline. Please allow a reasonable disclosure window before disclosing publicly.

## Supported versions

Burrow is pre-1.0; only the **latest tagged release** receives security fixes. Check your version with `cat /etc/burrow/VERSION`, and upgrade by re-running the installer for the newest tag.

| Version | Supported |
|---|---|
| latest `0.x` tag | ✅ |
| older tags / `main` | ⚠️ best-effort |

## Threat model (what to keep in mind)

- **Installed as root** via `curl | sudo bash`. Pin to a release tag (the default) so installs are reproducible, and review the script before running it.
- **The dashboard can rewrite DNS** for the whole LAN — the `burrow` service account holds scoped `sudo unbound-control` rights by design. It binds **loopback by default**; exposing it (`DASHBOARD_BIND=0.0.0.0`) sends the admin password and session cookie over **plaintext HTTP**, so put a TLS reverse proxy in front or keep it on a trusted network / VPN.
- **Secrets** (PowerDNS API key, session secret, admin password) live in `/etc/burrow/*.conf`, mode `0640 root:burrow`, generated per install.
- **Upstreams** see your forwarded queries — encrypted in transit via DNS-over-TLS, but currently *opportunistic* (the upstream certificate chain is not verified).
