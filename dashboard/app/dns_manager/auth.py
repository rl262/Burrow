"""Authentication helpers for dns_manager.

Three modes (settings.auth_mode), enforced by a middleware in main.py:

  none      -- no app auth; trust the upstream reverse proxy / trusted LAN.
  password  -- a single shared password; a signed session cookie marks the
               browser as authenticated (login form at /login).
  authentik -- trust an upstream Authentik forward-auth proxy: read the
               X-authentik-username header. If absent, the request did not
               pass through the outpost, so deny (defense in depth).

Paths that must never require auth (health checks, the login form, static
assets) are listed in EXEMPT_PREFIXES.

NOTE: the installer currently wires up only `password`. The `none` and
`authentik` modes are implemented here but not yet configured by install.sh —
Authentik SSO is planned (see ROADMAP.md), not dead code.
"""

from __future__ import annotations

import hmac

from starlette.requests import Request

from .config import settings

EXEMPT_PREFIXES = ("/healthz", "/api/health", "/static/", "/login", "/logout", "/favicon")


def password_ok(provided: str) -> bool:
    if not settings.app_password:
        return False
    return hmac.compare_digest(provided.encode(), settings.app_password.encode())


def is_exempt(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in EXEMPT_PREFIXES)


def current_user(request: Request) -> str:
    """Best-effort display name for the authenticated principal."""
    if settings.auth_mode == "authentik":
        return request.headers.get(settings.authentik_user_header) or "authentik-user"
    if settings.auth_mode == "password":
        return request.session.get("user", "admin")
    return "anonymous"
