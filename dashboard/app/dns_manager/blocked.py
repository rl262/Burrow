"""Live blocked-request monitor for the command center ('activity' tab).

A background thread tails the Unbound journal, filtered server-side to block
events via `journalctl --grep always_nxdomain`, and parses each local-action
line into (timestamp, client, domain, qtype). Events are kept in a rolling
in-memory ring buffer -- no persistence (use your own log pipeline for long-term
history). The burrow service user reads the journal via systemd-journal group
membership; no sudo, no writes.

Example block line MESSAGE (Unbound, log-local-actions: yes):
  [tid:0] info: <qname>. always_nxdomain <client>@<port> <qname>. <TYPE> <CLASS>
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass

from .config import settings

_BLOCK_RE = re.compile(
    r"always_nxdomain\s+(?P<client>[0-9a-fA-F.:]+)@\d+\s+(?P<domain>\S+)\s+(?P<qtype>\S+)"
)


@dataclass
class BlockEvent:
    ts: float  # epoch seconds
    client: str
    domain: str  # no trailing dot
    qtype: str

    @property
    def hms(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.ts))


class BlockedMonitor:
    def __init__(self) -> None:
        self._buf: deque[BlockEvent] = deque(maxlen=settings.blocked_buffer_size)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.started_at = time.time()
        self.alive = False
        # Highest __REALTIME_TIMESTAMP (µs) ingested. journalctl is relaunched
        # with `-n <seed>` on every restart, re-emitting historical block lines;
        # this high-water mark (touched only by the tailer thread) dedups them.
        self._hwm_us = 0

    # --- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="blocked-monitor", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        # Re-launch journalctl if it ever exits (log rotation, transient error).
        while not self._stop.is_set():
            try:
                self._tail_once()
            except Exception:  # noqa: BLE001 - keep the monitor resilient
                pass
            self.alive = False
            self._stop.wait(3.0)

    def _tail_once(self) -> None:
        cmd = [
            settings.journalctl,
            "-u", settings.blocked_journal_unit,
            "-o", "json",
            "-n", str(settings.blocked_seed_lines),
            "--grep", "always_nxdomain",
            "--no-pager",
            "-f",
        ]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1
        )
        self.alive = True
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if self._stop.is_set():
                    break
                self._ingest(line)
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                proc.kill()

    def _ingest(self, line: str) -> None:
        try:
            entry = json.loads(line)
        except (ValueError, TypeError):
            return
        msg = entry.get("MESSAGE")
        if not isinstance(msg, str):
            return
        m = _BLOCK_RE.search(msg)
        if not m:
            return
        raw = entry.get("__REALTIME_TIMESTAMP")
        try:
            ts_us = int(raw) if raw is not None else None
        except (ValueError, TypeError):
            ts_us = None
        if ts_us is not None:
            if ts_us <= self._hwm_us:
                return  # already ingested (re-emitted seed line on relaunch)
            self._hwm_us = ts_us
            ts = ts_us / 1_000_000
        else:
            ts = time.time()
        ev = BlockEvent(
            ts=ts,
            client=m.group("client"),
            domain=m.group("domain").rstrip(".").lower(),
            qtype=m.group("qtype"),
        )
        with self._lock:
            self._buf.append(ev)

    # --- reads --------------------------------------------------------------
    def recent(self, limit: int = 200) -> list[BlockEvent]:
        with self._lock:
            items = list(self._buf)
        items.reverse()  # newest first
        return items[:limit]

    def stats(self) -> dict:
        now = time.time()
        with self._lock:
            items = list(self._buf)
        n10 = sum(1 for e in items if e.ts >= now - 600)
        n60 = sum(1 for e in items if e.ts >= now - 3600)
        window = [e for e in items if e.ts >= now - 3600] or items
        dom = Counter(e.domain for e in window)
        cli = Counter(e.client for e in window)
        # per-minute counts for the last 30 minutes (oldest -> newest)
        buckets = [0] * 30
        for e in window:
            age = now - e.ts
            if 0 <= age < 1800:
                buckets[29 - int(age // 60)] += 1
        return {
            "alive": self.alive,
            "blocked_10m": n10,
            "blocked_1h": n60,
            "window_total": len(items),
            "buffer_max": self._buf.maxlen,
            "unique_domains": len(dom),
            "unique_clients": len(cli),
            "top_domains": dom.most_common(10),
            "top_clients": cli.most_common(10),
            "per_minute": buckets,
            "per_minute_max": max(buckets) if buckets else 0,
        }


monitor = BlockedMonitor()
