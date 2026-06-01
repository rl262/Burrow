"""Runtime configuration for the Burrow dashboard.

All configuration is supplied via environment variables (set by the systemd
unit's EnvironmentFile, rendered by the Burrow installer). No secrets live in the
source tree. Reading config at import time keeps the rest of the app simple.
"""

from __future__ import annotations

import os


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _fqdn(name: str) -> str:
    """Return name with exactly one trailing dot (PowerDNS canonical form)."""
    name = name.strip()
    if not name:
        return name
    return name if name.endswith(".") else name + "."


class Settings:
    def __init__(self) -> None:
        # --- PowerDNS HTTP API --------------------------------------------
        self.pdns_url: str = os.environ.get(
            "DNSMGR_PDNS_URL", "http://127.0.0.1:8081"
        ).rstrip("/")
        self.pdns_api_key: str = os.environ.get("DNSMGR_PDNS_API_KEY", "")
        self.pdns_server_id: str = os.environ.get("DNSMGR_PDNS_SERVER_ID", "localhost")
        self.pdns_timeout: float = float(os.environ.get("DNSMGR_PDNS_TIMEOUT", "10"))

        # Forward zone used by the PTR auto-helper to suggest A<->PTR pairs.
        self.forward_zone: str = _fqdn(
            os.environ.get("DNSMGR_FORWARD_ZONE", "home.arpa")
        )

        # --- Unbound control ----------------------------------------------
        self.unbound_enabled: bool = _bool(
            os.environ.get("DNSMGR_UNBOUND_ENABLED"), True
        )
        self.unbound_control: str = os.environ.get(
            "DNSMGR_UNBOUND_CONTROL", "/usr/sbin/unbound-control"
        )
        self.unbound_checkconf: str = os.environ.get(
            "DNSMGR_UNBOUND_CHECKCONF", "/usr/sbin/unbound-checkconf"
        )
        # App-owned include file referenced by the zz-burrow-overrides.conf
        # drop-in. We rewrite it atomically (we own it) and then reload Unbound.
        self.unbound_overrides_file: str = os.environ.get(
            "DNSMGR_UNBOUND_OVERRIDES_FILE",
            "/var/lib/unbound/burrow/overrides.conf",
        )
        # Path to the canonical blocklist refresh script (run on allowlist sync).
        self.unbound_refresh_script: str = os.environ.get(
            "DNSMGR_UNBOUND_REFRESH_SCRIPT", "/usr/local/sbin/burrow-update-blocklist"
        )
        self.unbound_use_sudo: bool = _bool(
            os.environ.get("DNSMGR_UNBOUND_USE_SUDO"), True
        )
        self.cmd_timeout: float = float(os.environ.get("DNSMGR_CMD_TIMEOUT", "30"))

        # --- Blocklist (Pi-hole-style adlists) ----------------------------
        self.blocklist_enabled: bool = _bool(
            os.environ.get("DNSMGR_BLOCKLIST_ENABLED"), True
        )
        self.blocklist_dir: str = os.environ.get(
            "DNSMGR_BLOCKLIST_DIR", "/etc/unbound/blocklist.d"
        )
        # Installer-managed base list (shown read-only in the GUI).
        self.blocklist_sources_file: str = os.environ.get(
            "DNSMGR_BLOCKLIST_SOURCES_FILE", f"{self.blocklist_dir}/sources.txt"
        )
        # GUI-owned overlay; the refresh script reads sources.txt + sources.d/*.txt.
        self.blocklist_gui_file: str = os.environ.get(
            "DNSMGR_BLOCKLIST_GUI_FILE", f"{self.blocklist_dir}/sources.d/gui.txt"
        )
        # World-readable status the refresh script writes (sources/domains/updated).
        self.blocklist_status_file: str = os.environ.get(
            "DNSMGR_BLOCKLIST_STATUS_FILE", f"{self.blocklist_dir}/status"
        )
        self.blocklist_refresh_service: str = os.environ.get(
            "DNSMGR_BLOCKLIST_REFRESH_SERVICE", "unbound-blocklist-refresh.service"
        )
        self.systemctl: str = os.environ.get("DNSMGR_SYSTEMCTL", "/usr/bin/systemctl")

        # --- Blocked-request monitor (command center) ---------------------
        self.blocked_enabled: bool = _bool(
            os.environ.get("DNSMGR_BLOCKED_ENABLED"), True
        )
        self.blocked_journal_unit: str = os.environ.get(
            "DNSMGR_BLOCKED_JOURNAL_UNIT", "unbound.service"
        )
        self.blocked_buffer_size: int = int(
            os.environ.get("DNSMGR_BLOCKED_BUFFER_SIZE", "5000")
        )
        self.blocked_seed_lines: int = int(
            os.environ.get("DNSMGR_BLOCKED_SEED_LINES", "2000")
        )
        self.journalctl: str = os.environ.get("DNSMGR_JOURNALCTL", "/usr/bin/journalctl")

        # --- Auth ----------------------------------------------------------
        # none      -> no app auth (trust the upstream proxy / LAN)
        # password  -> shared password, signed session cookie
        # authentik -> trust X-authentik-* headers from an upstream forward-auth
        self.auth_mode: str = os.environ.get("DNSMGR_AUTH_MODE", "password").lower()
        self.app_password: str = os.environ.get("DNSMGR_APP_PASSWORD", "")
        self.session_secret: str = os.environ.get("DNSMGR_SESSION_SECRET", "")
        self.authentik_user_header: str = os.environ.get(
            "DNSMGR_AUTHENTIK_USER_HEADER", "x-authentik-username"
        ).lower()
        self.authentik_email_header: str = os.environ.get(
            "DNSMGR_AUTHENTIK_EMAIL_HEADER", "x-authentik-email"
        ).lower()
        # Optional shared secret the upstream forward-auth proxy injects as
        # X-Proxy-Token; when set, authentik mode rejects requests without it
        # (defends the loopback bind against locally-forged auth headers).
        self.trusted_proxy_token: str = os.environ.get("DNSMGR_TRUSTED_PROXY_TOKEN", "")

        # Cosmetic
        self.site_title: str = os.environ.get("DNSMGR_SITE_TITLE", "Burrow DNS")


settings = Settings()
