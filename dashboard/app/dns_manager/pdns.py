"""PowerDNS Authoritative HTTP API client (v4.9.x).

Endpoints used (PowerDNS Authoritative v4.9.x API):
  * Base: {pdns_url}/api/v1/servers/{server_id}   header: X-API-Key
  * GET  /zones                         -> list (no rrsets)
  * GET  /zones/{zone_id}               -> zone detail incl. rrsets[]
  * PATCH /zones/{zone_id}              -> {"rrsets":[...]}  (REPLACE / DELETE)
  * PUT  /cache/flush?domain=FQDN.      -> flush PDNS auth cache (NOT Unbound)

Important semantics:
  * REPLACE is a FULL-SET replace of an rrset (matched on name+type): you must
    send the complete desired records[] list, or existing records are wiped.
  * PATCH returns HTTP 204 on success (empty body).
  * SOA serial auto-increments (soa_edit_api defaults to DEFAULT); we never
    touch the SOA rrset ourselves.
  * Zone ids and most record content (CNAME/NS/PTR/MX/SRV targets) carry a
    trailing dot; A/AAAA/TXT content do not.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import settings

# Record types the GUI offers. SOA is intentionally excluded from editing.
EDITABLE_TYPES = ["A", "AAAA", "CNAME", "PTR", "MX", "TXT", "SRV", "NS", "CAA"]
# Types whose content must be a trailing-dot FQDN target.
FQDN_CONTENT_TYPES = {"CNAME", "NS", "PTR"}


class PdnsError(RuntimeError):
    """Raised when the PowerDNS API returns an error (carries a clean message)."""


@dataclass
class RRSet:
    name: str
    type: str
    ttl: int
    records: list[str]  # plain content strings (disabled records dropped)
    comment: str = ""


def _fqdn(name: str) -> str:
    name = name.strip()
    return name if not name or name.endswith(".") else name + "."


def _quote_txt(content: str) -> str:
    """Wrap TXT content in double quotes (PowerDNS requires quoted rdata)."""
    if len(content) >= 2 and content.startswith('"') and content.endswith('"'):
        return content
    escaped = content.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _normalize_caa(content: str) -> str:
    """Normalise CAA to '<flags> <tag> "<value>"', quoting the value if needed."""
    parts = content.split(None, 2)
    if len(parts) != 3:
        raise PdnsError('CAA content must be "<flags> <tag> <value>"')
    flags, tag, value = parts
    if not (len(value) >= 2 and value.startswith('"') and value.endswith('"')):
        value = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"{flags} {tag} {value}"


def normalize_content(rtype: str, content: str) -> str:
    """Canonicalise a single record's content for the given type.

    A/AAAA stay bare; CNAME/NS/PTR get a trailing dot; MX/SRV trailing-dot only
    their target field; TXT/CAA get quoted. Malformed compound types raise.
    """
    content = content.strip()
    if not content:
        return content
    if rtype in FQDN_CONTENT_TYPES:
        return _fqdn(content)
    if rtype == "MX":
        parts = content.split()
        if len(parts) != 2:
            raise PdnsError('MX content must be "<preference> <target>"')
        return f"{parts[0]} {_fqdn(parts[1])}"
    if rtype == "SRV":
        parts = content.split()
        if len(parts) != 4:
            raise PdnsError('SRV content must be "<priority> <weight> <port> <target>"')
        return f"{parts[0]} {parts[1]} {parts[2]} {_fqdn(parts[3])}"
    if rtype == "TXT":
        return _quote_txt(content)
    if rtype == "CAA":
        return _normalize_caa(content)
    return content


class PdnsClient:
    def __init__(self) -> None:
        self._base = f"{settings.pdns_url}/api/v1/servers/{settings.pdns_server_id}"
        self._headers = {
            "X-API-Key": settings.pdns_api_key,
            "Accept": "application/json",
        }
        self._timeout = settings.pdns_timeout

    def _client(self) -> httpx.Client:
        return httpx.Client(headers=self._headers, timeout=self._timeout)

    @staticmethod
    def _raise_for(resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        msg = f"HTTP {resp.status_code}"
        try:
            body = resp.json()
            if isinstance(body, dict) and body.get("error"):
                msg = body["error"]
                if body.get("errors"):
                    msg += " (" + "; ".join(str(e) for e in body["errors"]) + ")"
        except Exception:  # noqa: BLE001 - body may not be JSON
            if resp.text:
                msg = resp.text[:300]
        raise PdnsError(msg)

    def _do(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Execute a request, converting connection errors into PdnsError."""
        try:
            with self._client() as c:
                resp = c.request(method, f"{self._base}{path}", **kwargs)
        except httpx.RequestError as exc:
            raise PdnsError(f"PowerDNS unreachable: {exc}") from exc
        self._raise_for(resp)
        return resp

    # --- reads --------------------------------------------------------------
    def server_info(self) -> dict:
        return self._do("GET", "").json()

    def list_zones(self) -> list[dict]:
        zones = self._do("GET", "/zones").json()
        zones.sort(key=lambda z: (not z["name"].endswith("in-addr.arpa."), z["name"]))
        return zones

    def get_zone(self, zone_id: str) -> dict:
        return self._do("GET", f"/zones/{_fqdn(zone_id)}").json()

    def get_rrsets(self, zone_id: str) -> list[RRSet]:
        """Return editable rrsets for a zone, SOA first then sorted by name."""
        zone = self.get_zone(zone_id)
        out: list[RRSet] = []
        for rr in zone.get("rrsets", []):
            records = [
                rec["content"]
                for rec in rr.get("records", [])
                if not rec.get("disabled")
            ]
            comment = ""
            if rr.get("comments"):
                comment = rr["comments"][0].get("content", "")
            out.append(
                RRSet(
                    name=rr["name"],
                    type=rr["type"],
                    ttl=rr.get("ttl", 3600),
                    records=records,
                    comment=comment,
                )
            )
        out.sort(key=lambda r: (r.type != "SOA", r.type != "NS", r.name, r.type))
        return out

    def find_rrset(self, zone_id: str, name: str, rtype: str) -> RRSet | None:
        name = _fqdn(name)
        for rr in self.get_rrsets(zone_id):
            if rr.name == name and rr.type == rtype:
                return rr
        return None

    # --- writes -------------------------------------------------------------
    def replace_rrset(
        self,
        zone_id: str,
        name: str,
        rtype: str,
        ttl: int,
        contents: list[str],
        comment: str | None = None,
    ) -> None:
        """REPLACE (upsert) the full rrset for name+type with the given records."""
        name = _fqdn(name)
        clean = [normalize_content(rtype, c) for c in contents if c.strip()]
        if not clean:
            raise PdnsError("at least one record value is required")
        rrset: dict = {
            "name": name,
            "type": rtype,
            "ttl": int(ttl),
            "changetype": "REPLACE",
            "records": [{"content": c, "disabled": False} for c in clean],
        }
        if comment is not None:
            rrset["comments"] = (
                [{"content": comment, "account": "dns_manager"}] if comment else []
            )
        self._patch(zone_id, [rrset])

    def delete_rrset(self, zone_id: str, name: str, rtype: str) -> None:
        rrset = {"name": _fqdn(name), "type": rtype, "changetype": "DELETE"}
        self._patch(zone_id, [rrset])

    def _patch(self, zone_id: str, rrsets: list[dict]) -> None:
        self._do(
            "PATCH",
            f"/zones/{_fqdn(zone_id)}",
            json={"rrsets": rrsets},
            headers={"Content-Type": "application/json"},
        )

    def flush_cache(self, domain: str) -> int:
        """Flush the PDNS authoritative cache for a name. Returns count flushed.

        NOTE: this is the PowerDNS auth cache only -- the Unbound recursive
        cache on :53 is separate and must be flushed via UnboundControl.
        """
        r = self._do("PUT", "/cache/flush", params={"domain": _fqdn(domain)})
        try:
            return int(r.json().get("count", 0))
        except Exception:  # noqa: BLE001
            return 0

    # --- reverse-PTR helper -------------------------------------------------
    @staticmethod
    def ptr_fqdn_for_ip(ip: str) -> str | None:
        """Return the in-addr.arpa PTR name for an IPv4 address, or None."""
        parts = ip.strip().split(".")
        if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            return None
        return ".".join(reversed(parts)) + ".in-addr.arpa."

    def reverse_zone_for_ptr(self, ptr_name: str, zones: list[dict] | None = None) -> str | None:
        """Find the most-specific existing reverse zone covering a PTR name."""
        if zones is None:
            zones = self.list_zones()
        candidates = [
            z["name"]
            for z in zones
            if z["name"].endswith(".in-addr.arpa.") and ptr_name.endswith("." + z["name"])
        ]
        if not candidates:
            return None
        return max(candidates, key=len)  # longest suffix == most specific
