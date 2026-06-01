"""Unbound resolver control for dns_manager.

Two channels are used:

  1. `unbound-control` (run via passwordless sudo on specific subcommands) for
     cache flushes, reload, status/stats, and listing forwarders. We need sudo
     because parsing the live config pulls in the 0600 root-owned blocklist.conf,
     so a non-root user cannot use unbound-control directly.

  2. An app-owned include file (settings.unbound_overrides_file, default
     /var/lib/unbound/burrow/overrides.conf) that we rewrite atomically.
     It is pulled in by /etc/unbound/unbound.conf.d/zz-burrow-overrides.conf,
     which (being alphabetically last) loads AFTER the blocklist files, so a
     `local-zone "<d>." transparent` line here overrides an upstream
     `always_nxdomain` (un-block), and `always_nxdomain` here adds a manual
     block. A `reload` re-reads the include, so entries persist across the
     nightly blocklist refresh (which itself reloads Unbound).

Burrow blocks domains with `local-zone "<d>." always_nxdomain` lines (generated
into the blocklist include), so flipping a domain to `transparent` in the
override file is sufficient to un-block it.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from .config import settings

# Allowed unbound-control subcommands (mirrors the sudoers allowlist).
ALLOW_TRANSPARENT = "transparent"
DENY_NXDOMAIN = "always_nxdomain"

_LOCAL_ZONE_RE = re.compile(
    r'^\s*local-zone:\s*"(?P<name>[^"]+)"\s+(?P<action>\S+)', re.IGNORECASE
)

# Strict hostname (incl. trailing dot; covers in-addr.arpa/ip6.arpa reverse
# names). Rejects quotes/whitespace/newlines so a domain field can never break
# out of the quoted local-zone token and inject arbitrary Unbound directives.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,254}$)(?!-)[A-Za-z0-9_-]{1,63}(?<!-)(\.[A-Za-z0-9_-]{1,63})*\.$"
)


class UnboundError(RuntimeError):
    """Raised when an unbound-control command or config write fails."""


@dataclass
class Override:
    name: str  # FQDN with trailing dot
    action: str  # "transparent" (allow) or "always_nxdomain" (deny)

    @property
    def kind(self) -> str:
        return "allow" if self.action == ALLOW_TRANSPARENT else "deny"


@dataclass
class Blocklist:
    url: str
    source: str  # "base" (installer sources.txt) or "gui" (sources.d/gui.txt)


def _valid_url(url: str) -> bool:
    if any(c in url for c in " \t\r\n\"'"):
        return False
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


def _rel_time(epoch: int | None) -> str:
    if not epoch:
        return "never"
    d = int(time.time()) - int(epoch)
    if d < 0:
        return "just now"
    if d < 60:
        return f"{d}s ago"
    if d < 3600:
        return f"{d // 60}m ago"
    if d < 86400:
        return f"{d // 3600}h ago"
    return f"{d // 86400}d ago"


def _fqdn(name: str) -> str:
    name = name.strip().lower().rstrip(".")
    if not name:
        return ""
    return name + "."


def _run(args: list[str]) -> str:
    cmd = list(args)
    if settings.unbound_use_sudo:
        cmd = ["sudo", "-n", *cmd]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.cmd_timeout,
            check=False,
        )
    except FileNotFoundError as exc:  # binary missing
        raise UnboundError(f"command not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise UnboundError(f"timed out: {' '.join(args)}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise UnboundError(detail or f"exit {proc.returncode}: {' '.join(args)}")
    return proc.stdout


def _run_query(args: list[str]) -> tuple[int, str]:
    """Run a read-only command WITHOUT sudo (e.g. systemctl is-active/show).

    Never raises on non-zero exit (is-active returns non-zero when inactive).
    """
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=settings.cmd_timeout, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 1, ""
    return proc.returncode, proc.stdout.strip()


class UnboundControl:
    def __init__(self) -> None:
        self.ctl = settings.unbound_control
        self.checkconf = settings.unbound_checkconf
        self.overrides_file = settings.unbound_overrides_file
        self.refresh_script = settings.unbound_refresh_script

    # --- read-only ----------------------------------------------------------
    def status(self) -> dict[str, str]:
        out = _run([self.ctl, "status"])
        info: dict[str, str] = {}
        for line in out.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                info[k.strip()] = v.strip()
        return info

    def stats(self) -> dict[str, str]:
        out = _run([self.ctl, "stats_noreset"])
        info: dict[str, str] = {}
        for line in out.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                info[k.strip()] = v.strip()
        return info

    def list_forwards(self) -> list[str]:
        out = _run([self.ctl, "list_forwards"])
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    # --- cache --------------------------------------------------------------
    def flush_name(self, name: str) -> None:
        _run([self.ctl, "flush", _fqdn(name)])

    def flush_zone(self, name: str) -> None:
        _run([self.ctl, "flush_zone", _fqdn(name)])

    def flush_negative(self) -> None:
        _run([self.ctl, "flush_negative"])

    def reload(self) -> None:
        _run([self.ctl, "reload"])

    # --- allow / deny overrides --------------------------------------------
    def read_overrides(self) -> list[Override]:
        out: list[Override] = []
        try:
            with open(self.overrides_file, encoding="utf-8") as fh:
                for line in fh:
                    m = _LOCAL_ZONE_RE.match(line)
                    if m:
                        out.append(
                            Override(
                                name=m.group("name").lower(),
                                action=m.group("action").lower(),
                            )
                        )
        except FileNotFoundError:
            return []
        out.sort(key=lambda o: (o.kind, o.name))
        return out

    def _write_overrides(self, overrides: list[Override]) -> None:
        header = (
            "# Managed by Burrow -- DO NOT EDIT BY HAND.\n"
            "# Included (last) from /etc/unbound/unbound.conf.d/"
            "zz-burrow-overrides.conf so these win over the blocklist.\n"
            '#   transparent     = allow (un-block) a domain\n'
            '#   always_nxdomain = manual block a domain\n'
        )
        lines = [header]
        for o in overrides:
            lines.append(f'local-zone: "{o.name}" {o.action}\n')
        # Defensive: never emit a name that could break out of the quoted token.
        for o in overrides:
            if not _HOSTNAME_RE.match(o.name):
                raise UnboundError(f"refusing to write invalid domain: {o.name!r}")
        data = "".join(lines)

        directory = os.path.dirname(self.overrides_file) or "."
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".burrow-ovr-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(data)
            # mkstemp forces 0600; the unbound daemon (user 'unbound', not in
            # the burrow group) must read this include on reload -> 0644.
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.overrides_file)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def set_override(self, name: str, action: str) -> None:
        name = _fqdn(name)
        if not name:
            raise UnboundError("a domain is required")
        if not _HOSTNAME_RE.match(name):
            raise UnboundError(f"invalid domain name: {name!r}")
        if action not in (ALLOW_TRANSPARENT, DENY_NXDOMAIN):
            raise UnboundError(f"invalid action: {action}")
        # 1) Persist to the include file (re-applied on every unbound reload).
        # 2) Apply instantly at runtime via `unbound-control local_zone`, which
        #    is an in-memory change with NO resolver downtime -- unlike
        #    `unbound-control reload`, which re-parses the full blocklist and
        #    briefly bounces the daemon (a short DNS blip).
        current = self.read_overrides()
        kept = [o for o in current if o.name != name]
        kept.append(Override(name=name, action=action))
        self._write_overrides(kept)
        _run([self.ctl, "local_zone", name, action])

    def allow(self, name: str) -> None:
        self.set_override(name, ALLOW_TRANSPARENT)

    def deny(self, name: str) -> None:
        self.set_override(name, DENY_NXDOMAIN)

    def remove_override(self, name: str) -> None:
        name = _fqdn(name)
        current = self.read_overrides()
        kept = [o for o in current if o.name != name]
        if len(kept) == len(current):
            return  # nothing to do
        self._write_overrides(kept)
        # Drop the runtime local-zone instantly. NOTE: if this was an "allow" for
        # a blocklisted domain, it resolves normally until the next reload
        # re-applies the blocklist's always_nxdomain.
        _run([self.ctl, "local_zone_remove", name])

    # --- blocklist (adlists) ------------------------------------------------
    @staticmethod
    def _read_urls(path: str) -> list[str]:
        try:
            with open(path, encoding="utf-8") as fh:
                return [
                    ln.strip()
                    for ln in fh
                    if ln.strip() and not ln.strip().startswith("#")
                ]
        except OSError:
            return []

    def _write_urls(self, path: str, urls: list[str]) -> None:
        header = (
            "# Managed by Burrow (GUI-added blocklist sources).\n"
            "# Merged with sources.txt by burrow-update-blocklist. One URL per line.\n"
        )
        data = header + "".join(f"{u}\n" for u in urls)
        directory = os.path.dirname(path) or "."
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".gui-sources-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(data)
            os.chmod(tmp, 0o644)  # mkstemp makes 0600; refresh (root) reads it fine either way
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def list_blocklists(self) -> list[Blocklist]:
        base = self._read_urls(settings.blocklist_sources_file)
        gui = self._read_urls(settings.blocklist_gui_file)
        seen = set(base)
        out = [Blocklist(url=u, source="base") for u in base]
        for u in gui:
            if u not in seen:
                out.append(Blocklist(url=u, source="gui"))
                seen.add(u)
        return out

    def add_blocklist(self, url: str) -> None:
        url = url.strip()
        if not _valid_url(url):
            raise UnboundError("enter a valid http(s) blocklist URL")
        base = self._read_urls(settings.blocklist_sources_file)
        gui = self._read_urls(settings.blocklist_gui_file)
        if url in base:
            raise UnboundError("that list is already in the installer-managed base set")
        if url in gui:
            raise UnboundError("that list is already added")
        gui.append(url)
        self._write_urls(settings.blocklist_gui_file, gui)

    def remove_blocklist(self, url: str) -> None:
        url = url.strip()
        gui = self._read_urls(settings.blocklist_gui_file)
        kept = [u for u in gui if u != url]
        if len(kept) == len(gui):
            raise UnboundError("only GUI-added lists can be removed")
        self._write_urls(settings.blocklist_gui_file, kept)

    def refresh_blocklist(self) -> None:
        """Trigger the existing refresh service in the background (non-blocking)."""
        _run([settings.systemctl, "start", "--no-block", settings.blocklist_refresh_service])

    def blocklist_status(self) -> dict:
        st: dict = {"domains": None, "sources": None, "updated": None}
        try:
            with open(settings.blocklist_status_file, encoding="utf-8") as fh:
                for line in fh:
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip()
                    if k in st and v.isdigit():
                        st[k] = int(v)
        except OSError:
            pass
        if st["updated"] is None:
            try:
                st["updated"] = int(os.stat(settings.blocklist_dir + "/blocklist.conf").st_mtime)
            except OSError:
                pass
        rc, state = _run_query([settings.systemctl, "is-active", settings.blocklist_refresh_service])
        st["refreshing"] = state == "activating"
        _, nxt = _run_query(
            [settings.systemctl, "show", settings.blocklist_refresh_service.replace(".service", ".timer"),
             "-p", "NextElapseUSecRealtime", "--value"]
        )
        st["next_run"] = nxt or ""
        st["updated_rel"] = _rel_time(st["updated"])
        return st
