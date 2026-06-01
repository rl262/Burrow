"""Burrow dashboard -- a single-host web GUI for managing PowerDNS records and
the local Unbound resolver (records, blocklists, allow/deny, cache, and a live
blocked-request activity feed).

The package version is used for the FastAPI title and for cache-busting the
static assets (/static/style.css?v=__version__). Bump it whenever the bundled
templates/CSS/JS change.
"""

__version__ = "0.1.0-alpha.1"
