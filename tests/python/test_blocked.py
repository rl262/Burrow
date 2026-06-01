"""Unit tests for the block-line parser + dedup + stats in dns_manager.blocked.
Guards against silent breakage of the Activity feed if the Unbound log format or
the high-water-mark dedup logic regresses."""

import json
import time

from dns_manager.blocked import _BLOCK_RE, BlockedMonitor


def _line(domain, client="192.168.1.50", qtype="A", ts_us=None):
    msg = f"[1] info: {domain}. always_nxdomain {client}@54321 {domain}. {qtype} IN"
    entry = {"MESSAGE": msg}
    if ts_us is not None:
        entry["__REALTIME_TIMESTAMP"] = str(ts_us)
    return json.dumps(entry)


def test_block_regex_extracts_fields():
    m = _BLOCK_RE.search("foo.com. always_nxdomain 10.0.0.1@5 foo.com. A IN")
    assert m is not None
    assert m.group("client") == "10.0.0.1"
    assert m.group("domain") == "foo.com."
    assert m.group("qtype") == "A"


def test_ingest_strips_trailing_dot_and_lowercases():
    mon = BlockedMonitor()
    mon._ingest(_line("Ads.Example.COM", client="10.0.0.5", ts_us=1_000_000))
    recent = mon.recent()
    assert len(recent) == 1
    assert recent[0].domain == "ads.example.com"
    assert recent[0].client == "10.0.0.5"


def test_ingest_dedups_by_high_water_mark():
    mon = BlockedMonitor()
    mon._ingest(_line("a.com", ts_us=2_000_000))
    mon._ingest(_line("a.com", ts_us=2_000_000))  # equal ts -> dropped
    mon._ingest(_line("b.com", ts_us=1_000_000))  # older than hwm -> dropped
    mon._ingest(_line("c.com", ts_us=3_000_000))  # newer -> kept
    assert sorted(e.domain for e in mon.recent()) == ["a.com", "c.com"]


def test_ingest_ignores_bad_json_and_non_block_lines():
    mon = BlockedMonitor()
    mon._ingest("not json at all")
    mon._ingest(json.dumps({"MESSAGE": "info: a normal query, nothing blocked"}))
    mon._ingest(json.dumps({"NOT_MESSAGE": "x"}))
    assert mon.recent() == []


def test_stats_shape_and_counts():
    mon = BlockedMonitor()
    now_us = int(time.time() * 1_000_000)
    for i in range(3):
        mon._ingest(_line(f"d{i}.example", client=f"10.0.0.{i}", ts_us=now_us + i))
    s = mon.stats()
    assert s["window_total"] == 3
    assert s["unique_domains"] == 3
    assert s["unique_clients"] == 3
    assert len(s["per_minute"]) == 30
    assert isinstance(s["top_domains"], list)
    assert s["blocked_10m"] == 3
