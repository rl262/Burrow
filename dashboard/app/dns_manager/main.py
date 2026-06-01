"""dns_manager FastAPI application.

Server-rendered (Jinja2) with HTMX for live refresh/filtering. All mutations
use POST-redirect-GET with flash messages;
HTMX is used only for read-side refresh. Binds to 127.0.0.1 by default and is
fronted by nginx (TLS + optional Authentik forward-auth).
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import __version__, auth
from .blocked import monitor as blocked_monitor
from .config import settings
from .pdns import EDITABLE_TYPES, PdnsClient, PdnsError, normalize_content
from .unbound import UnboundControl, UnboundError

# Fail fast rather than silently minting an ephemeral per-process signing key
# (which would log everyone out on every restart).
if settings.auth_mode == "password" and not settings.session_secret:
    raise RuntimeError(
        "auth_mode=password requires a stable session secret "
        "(DNSMGR_SESSION_SECRET) — the installer generates one."
    )

APP_DIR = Path(__file__).resolve().parent

app = FastAPI(title=f"{settings.site_title} manager", version=__version__)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
templates.env.globals["app_version"] = __version__
templates.env.globals["site_title"] = settings.site_title
templates.env.globals["auth_mode"] = settings.auth_mode
templates.env.globals["unbound_enabled"] = settings.unbound_enabled
_blocked_enabled = settings.unbound_enabled and settings.blocked_enabled
templates.env.globals["blocked_enabled"] = _blocked_enabled

pdns = PdnsClient()
unbound = UnboundControl()

# Start the live blocked-request tailer (background thread) once at import.
if _blocked_enabled:
    blocked_monitor.start()


# --------------------------------------------------------------------------
# flash helpers
# --------------------------------------------------------------------------
def flash(request: Request, message: str, category: str = "ok") -> None:
    request.session.setdefault("_flashes", []).append({"category": category, "message": message})


def pop_flashes(request: Request) -> list[dict]:
    flashes = request.session.pop("_flashes", [])
    return flashes


def ctx(request: Request, **kw) -> dict:
    base = {
        "request": request,
        "flashes": pop_flashes(request),
        "user": auth.current_user(request),
        "editable_types": EDITABLE_TYPES,
    }
    base.update(kw)
    return base


def back(request: Request, fallback: str = "/") -> RedirectResponse:
    target = request.headers.get("referer") or fallback
    return RedirectResponse(target, status_code=303)


def safe_next(target: str) -> str:
    """Constrain a post-login redirect to a same-origin relative path."""
    if not target.startswith("/") or target.startswith("//") or target.startswith("/\\"):
        return "/"
    return target


def to_record_name(raw: str, zone_id: str) -> str:
    raw = raw.strip().rstrip(".")
    zid = zone_id.rstrip(".")
    if raw in ("", "@"):
        return zid + "."
    if raw == zid or raw.endswith("." + zid):
        return raw + "."
    return f"{raw}.{zid}."


# --------------------------------------------------------------------------
# auth middleware
# --------------------------------------------------------------------------
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _same_origin(request: Request) -> bool:
    """CSRF defense: a state-changing request must originate same-site.

    Browsers always attach Origin (and almost always Referer) on cross-site
    form POSTs, so a mismatch is rejected. Requests with neither header are
    allowed (curl/scripts) — those still require a valid session cookie, and a
    cross-site browser attack cannot suppress both headers.
    """
    host = request.headers.get("host", "")
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if value:
            return urlsplit(value).netloc == host
    return True


async def enforce_auth(request: Request, call_next):
    path = request.url.path
    if settings.auth_mode == "none" or auth.is_exempt(path):
        return await call_next(request)
    if settings.auth_mode == "authentik":
        if settings.trusted_proxy_token and not hmac.compare_digest(
            request.headers.get("x-proxy-token", ""), settings.trusted_proxy_token
        ):
            return JSONResponse(
                {"error": "forbidden: missing/invalid proxy token"}, status_code=403
            )
        if not request.headers.get(settings.authentik_user_header):
            return JSONResponse(
                {"error": "forbidden: request did not pass Authentik forward-auth"},
                status_code=403,
            )
        return await call_next(request)
    # password mode
    if request.session.get("authed"):
        if request.method not in _SAFE_METHODS and not _same_origin(request):
            return JSONResponse({"error": "cross-origin request blocked"}, status_code=403)
        return await call_next(request)
    if request.method == "GET":
        return RedirectResponse(f"/login?next={quote(path, safe='/')}", status_code=303)
    return JSONResponse({"error": "authentication required"}, status_code=401)


# Middleware order matters: add_middleware PREPENDS, so the LAST added is the
# OUTERMOST. SessionMiddleware must be outermost so request.session is populated
# before enforce_auth (which reads it) runs.
app.add_middleware(BaseHTTPMiddleware, dispatch=enforce_auth)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret or os.urandom(32).hex(),
    same_site="lax",
    https_only=False,  # TLS is terminated upstream by nginx
)


# --------------------------------------------------------------------------
# health (no auth)
# --------------------------------------------------------------------------
@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/api/health")
def api_health() -> JSONResponse:
    out: dict = {"status": "ok", "version": __version__, "pdns": False}
    try:
        info = pdns.server_info()
        out["pdns"] = True
        out["pdns_version"] = info.get("version")
    except Exception as exc:  # noqa: BLE001
        out["status"] = "degraded"
        out["pdns_error"] = str(exc)
    return JSONResponse(out)


# --------------------------------------------------------------------------
# login / logout (password mode)
# --------------------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/") -> Response:
    if settings.auth_mode != "password":
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", ctx(request, next=safe_next(next)))


@app.post("/login")
def login_submit(
    request: Request, password: str = Form(""), next: str = Form("/")
) -> Response:
    if settings.auth_mode != "password":
        return RedirectResponse("/", status_code=303)
    nxt = safe_next(next)
    if auth.password_ok(password):
        request.session["authed"] = True
        request.session["user"] = "admin"
        return RedirectResponse(nxt, status_code=303)
    flash(request, "Incorrect password.", "error")
    return RedirectResponse(f"/login?next={quote(nxt, safe='/')}", status_code=303)


@app.get("/logout")
def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# --------------------------------------------------------------------------
# zones + records
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> Response:
    try:
        zones = pdns.list_zones()
    except PdnsError as exc:
        return templates.TemplateResponse(
            "index.html", ctx(request, zones=[], selected=None, rrsets=[], error=str(exc))
        )
    default = settings.forward_zone
    target = next((z for z in zones if z["name"] == default), zones[0] if zones else None)
    if not target:
        return templates.TemplateResponse(
            "index.html", ctx(request, zones=[], selected=None, rrsets=[], error="no zones")
        )
    return RedirectResponse(f"/zones/{target['name']}", status_code=303)


@app.get("/zones/{zone_id}", response_class=HTMLResponse)
def zone_detail(request: Request, zone_id: str, edit: str = "", q: str = "") -> Response:
    edit_rr = None
    rrsets = []
    error = ""
    try:
        zones = pdns.list_zones()
    except PdnsError as exc:
        return templates.TemplateResponse(
            "index.html",
            ctx(request, zones=[], selected=None, zone_id=zone_id, rrsets=[], q=q, error=str(exc)),
        )
    selected = next((z for z in zones if z["name"] == zone_id), None)
    try:
        rrsets = pdns.get_rrsets(zone_id)
    except PdnsError as exc:
        error = str(exc)
    if edit and ":" in edit:
        ename, _, etype = edit.partition(":")
        edit_rr = next(
            (r for r in rrsets if r.name == ename and r.type == etype), None
        )
    return templates.TemplateResponse(
        "index.html",
        ctx(
            request,
            zones=zones,
            selected=selected,
            zone_id=zone_id,
            rrsets=rrsets,
            edit_rr=edit_rr,
            q=q,
            error=error,
        ),
    )


@app.get("/_partial/records/{zone_id}", response_class=HTMLResponse)
def records_partial(request: Request, zone_id: str, q: str = "") -> Response:
    rrsets = []
    error = ""
    try:
        rrsets = pdns.get_rrsets(zone_id)
    except PdnsError as exc:
        error = str(exc)
    return templates.TemplateResponse(
        "partials/records.html",
        {"request": request, "rrsets": rrsets, "zone_id": zone_id, "q": q, "error": error},
    )


@app.post("/zones/{zone_id}/rrsets")
def upsert_rrset(
    request: Request,
    zone_id: str,
    name: str = Form(...),
    rtype: str = Form(...),
    ttl: int = Form(3600),
    content: str = Form(""),
    comment: str = Form(""),
    create_ptr: str = Form(""),
    is_edit: str = Form(""),
) -> Response:
    if rtype not in EDITABLE_TYPES:
        flash(request, f"Unsupported record type {rtype!r}.", "error")
        return RedirectResponse(f"/zones/{zone_id}", status_code=303)
    fqdn = to_record_name(name, zone_id)
    contents = [ln.strip() for ln in content.replace(",", "\n").splitlines() if ln.strip()]
    try:
        if is_edit:
            # Edit flow: the textarea held the complete rrset, so a full-set
            # REPLACE is intended (this is how you remove a value).
            pdns.replace_rrset(zone_id, fqdn, rtype, ttl, contents, comment=comment or None)
            msg = f"Updated {rtype} {fqdn}"
        else:
            # Add flow: MERGE with any existing rrset of this name+type so a
            # second A / MX / TXT does not silently wipe the first.
            existing = pdns.find_rrset(zone_id, fqdn, rtype)
            if existing:
                new_norm = [normalize_content(rtype, c) for c in contents]
                merged = list(existing.records) + [
                    c for c in new_norm if c not in existing.records
                ]
                pdns.replace_rrset(
                    zone_id, fqdn, rtype, existing.ttl, merged, comment=comment or None
                )
                msg = f"Added to {rtype} {fqdn} (now {len(merged)} value(s))"
            else:
                pdns.replace_rrset(zone_id, fqdn, rtype, ttl, contents, comment=comment or None)
                msg = f"Created {rtype} {fqdn}"
    except PdnsError as exc:
        flash(request, f"PowerDNS error: {exc}", "error")
        return RedirectResponse(f"/zones/{zone_id}", status_code=303)

    # reverse-PTR auto-helper
    if create_ptr and rtype == "A":
        made, warned = _create_ptrs(zone_id, fqdn, contents)
        if made:
            msg += f"; created PTR in {', '.join(made)}"
        for w in warned:
            flash(request, w, "warn")

    flash(request, msg, "ok")
    return RedirectResponse(f"/zones/{zone_id}", status_code=303)


def _create_ptrs(forward_zone_id: str, fqdn: str, ips: list[str]) -> tuple[list[str], list[str]]:
    """Create PTRs for the given IPs pointing at fqdn. Returns (zones_done, warnings)."""
    made: list[str] = []
    warnings: list[str] = []
    zones = pdns.list_zones()
    for ip in ips:
        ptr_name = pdns.ptr_fqdn_for_ip(ip)
        if not ptr_name:
            warnings.append(f"{ip} is not an IPv4 address; skipped PTR")
            continue
        rev = pdns.reverse_zone_for_ptr(ptr_name, zones)
        if not rev:
            warnings.append(
                f"No reverse zone for {ip} (PTR {ptr_name}); create the in-addr.arpa zone first"
            )
            continue
        try:
            existing = pdns.find_rrset(rev, ptr_name, "PTR")
            ttl = existing.ttl if existing else 3600
            pdns.replace_rrset(rev, ptr_name, "PTR", ttl, [fqdn])
            made.append(rev)
        except PdnsError as exc:
            warnings.append(f"PTR for {ip} failed: {exc}")
    return made, warnings


@app.post("/zones/{zone_id}/rrsets/delete")
def delete_rrset(
    request: Request, zone_id: str, name: str = Form(...), rtype: str = Form(...)
) -> Response:
    apex = zone_id.rstrip(".") + "."
    target = to_record_name(name, zone_id)
    if rtype == "SOA" or (rtype == "NS" and target == apex):
        flash(
            request,
            f"Refusing to delete the zone {rtype} record ({target}) — that would break the zone.",
            "error",
        )
        return RedirectResponse(f"/zones/{zone_id}", status_code=303)
    try:
        pdns.delete_rrset(zone_id, name, rtype)
        flash(request, f"Deleted {rtype} {name}", "ok")
    except PdnsError as exc:
        flash(request, f"PowerDNS error: {exc}", "error")
    return RedirectResponse(f"/zones/{zone_id}", status_code=303)


@app.post("/zones/{zone_id}/flush")
def flush_record(request: Request, zone_id: str, name: str = Form(...)) -> Response:
    notes = []
    try:
        n = pdns.flush_cache(name)
        notes.append(f"PowerDNS cache flushed ({n})")
    except PdnsError as exc:
        flash(request, f"PowerDNS flush error: {exc}", "error")
    if settings.unbound_enabled:
        try:
            unbound.flush_zone(name)
            notes.append("Unbound cache flushed")
        except UnboundError as exc:
            flash(request, f"Unbound flush error: {exc}", "error")
    if notes:
        flash(request, "; ".join(notes), "ok")
    return RedirectResponse(f"/zones/{zone_id}", status_code=303)


# --------------------------------------------------------------------------
# unbound
# --------------------------------------------------------------------------
@app.get("/unbound", response_class=HTMLResponse)
def unbound_page(request: Request) -> Response:
    if not settings.unbound_enabled:
        flash(request, "Unbound management is disabled.", "warn")
        return RedirectResponse("/", status_code=303)
    status = {}
    stats = {}
    forwards: list[str] = []
    overrides = []
    error = ""
    try:
        status = unbound.status()
        stats = unbound.stats()
        forwards = unbound.list_forwards()
        overrides = unbound.read_overrides()
    except UnboundError as exc:
        error = str(exc)
    blocklists = []
    bl = {}
    if settings.blocklist_enabled:
        try:
            blocklists = unbound.list_blocklists()
            bl = unbound.blocklist_status()
        except Exception as exc:  # noqa: BLE001 - don't let blocklist read break the page
            error = error or str(exc)
    return templates.TemplateResponse(
        "unbound.html",
        ctx(
            request,
            status=status,
            stats=stats,
            forwards=forwards,
            overrides=overrides,
            blocklist_enabled=settings.blocklist_enabled,
            blocklists=blocklists,
            bl=bl,
            error=error,
        ),
    )


@app.get("/_partial/unbound_stats", response_class=HTMLResponse)
def unbound_stats_partial(request: Request) -> Response:
    stats = {}
    try:
        stats = unbound.stats()
    except UnboundError:
        pass
    return templates.TemplateResponse(
        "partials/unbound_stats.html", {"request": request, "stats": stats}
    )


@app.get("/_partial/blocklist_status", response_class=HTMLResponse)
def blocklist_status_partial(request: Request) -> Response:
    bl = {}
    try:
        bl = unbound.blocklist_status()
    except Exception:  # noqa: BLE001
        pass
    return templates.TemplateResponse(
        "partials/blocklist_status.html", {"request": request, "bl": bl}
    )


@app.post("/unbound/blocklist/add")
def blocklist_add(request: Request, url: str = Form(...)) -> Response:
    try:
        unbound.add_blocklist(url)
        flash(request, f"Added blocklist {url} — click 'refresh now' to download + apply it.", "ok")
    except UnboundError as exc:
        flash(request, f"Blocklist error: {exc}", "error")
    return RedirectResponse("/unbound", status_code=303)


@app.post("/unbound/blocklist/remove")
def blocklist_remove(request: Request, url: str = Form(...)) -> Response:
    try:
        unbound.remove_blocklist(url)
        flash(request, f"Removed blocklist {url} — 'refresh now' to drop its domains.", "ok")
    except UnboundError as exc:
        flash(request, f"Blocklist error: {exc}", "error")
    return RedirectResponse("/unbound", status_code=303)


@app.post("/unbound/blocklist/refresh")
def blocklist_refresh(request: Request) -> Response:
    try:
        unbound.refresh_blocklist()
        flash(request, "Blocklist refresh started — downloading sources + reloading in the background.", "ok")
    except UnboundError as exc:
        flash(request, f"Blocklist error: {exc}", "error")
    return RedirectResponse("/unbound", status_code=303)


@app.post("/unbound/allow")
def unbound_allow(request: Request, domain: str = Form(...)) -> Response:
    try:
        unbound.allow(domain)
        flash(request, f"Allowed (un-blocked) {domain}", "ok")
    except UnboundError as exc:
        flash(request, f"Unbound error: {exc}", "error")
    return RedirectResponse("/unbound", status_code=303)


@app.post("/unbound/deny")
def unbound_deny(request: Request, domain: str = Form(...)) -> Response:
    try:
        unbound.deny(domain)
        flash(request, f"Blocked {domain}", "ok")
    except UnboundError as exc:
        flash(request, f"Unbound error: {exc}", "error")
    return RedirectResponse("/unbound", status_code=303)


@app.post("/unbound/override/remove")
def unbound_override_remove(request: Request, domain: str = Form(...)) -> Response:
    try:
        unbound.remove_override(domain)
        flash(request, f"Removed override for {domain}", "ok")
    except UnboundError as exc:
        flash(request, f"Unbound error: {exc}", "error")
    return RedirectResponse("/unbound", status_code=303)


@app.post("/unbound/flush")
def unbound_flush(request: Request, name: str = Form(...)) -> Response:
    try:
        unbound.flush_zone(name)
        flash(request, f"Flushed Unbound cache for {name}", "ok")
    except UnboundError as exc:
        flash(request, f"Unbound error: {exc}", "error")
    return RedirectResponse("/unbound", status_code=303)


@app.post("/unbound/flush_negative")
def unbound_flush_negative(request: Request) -> Response:
    try:
        unbound.flush_negative()
        flash(request, "Flushed negative (NXDOMAIN) cache", "ok")
    except UnboundError as exc:
        flash(request, f"Unbound error: {exc}", "error")
    return RedirectResponse("/unbound", status_code=303)


@app.post("/unbound/reload")
def unbound_reload(request: Request) -> Response:
    try:
        unbound.reload()
        flash(request, "Reloaded Unbound (cache flushed)", "ok")
    except UnboundError as exc:
        flash(request, f"Unbound error: {exc}", "error")
    return RedirectResponse("/unbound", status_code=303)


# --------------------------------------------------------------------------
# activity (blocked-request command center)
# --------------------------------------------------------------------------
@app.get("/activity", response_class=HTMLResponse)
def activity_page(request: Request) -> Response:
    if not _blocked_enabled:
        flash(request, "The blocked-request monitor is disabled.", "warn")
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        "activity.html",
        ctx(request, stats=blocked_monitor.stats(), recent=blocked_monitor.recent(200)),
    )


@app.get("/_partial/blocked_stats", response_class=HTMLResponse)
def blocked_stats_partial(request: Request) -> Response:
    return templates.TemplateResponse(
        "partials/blocked_stats.html",
        {"request": request, "stats": blocked_monitor.stats()},
    )


@app.get("/_partial/blocked_feed", response_class=HTMLResponse)
def blocked_feed_partial(request: Request) -> Response:
    return templates.TemplateResponse(
        "partials/blocked_feed.html",
        {
            "request": request,
            "recent": blocked_monitor.recent(200),
            "stats": blocked_monitor.stats(),
        },
    )
