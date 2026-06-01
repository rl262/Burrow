"""Unit tests for the pure record-canonicalization + reverse-PTR helpers in
dns_manager.pdns. These functions write into authoritative DNS, so a quoting or
trailing-dot bug silently corrupts zones — hence they're covered here."""

import pytest

from dns_manager.pdns import (
    PdnsClient,
    PdnsError,
    _normalize_caa,
    _quote_txt,
    normalize_content,
)


def test_a_aaaa_stay_bare():
    assert normalize_content("A", "  192.168.1.10  ") == "192.168.1.10"
    assert normalize_content("AAAA", "2001:db8::1") == "2001:db8::1"


def test_fqdn_types_get_one_trailing_dot():
    assert normalize_content("CNAME", "host.home.arpa") == "host.home.arpa."
    assert normalize_content("NS", "ns.home.arpa.") == "ns.home.arpa."  # idempotent
    assert normalize_content("PTR", "ns.home.arpa") == "ns.home.arpa."


def test_mx_dots_target_only():
    assert normalize_content("MX", "10 mail.home.arpa") == "10 mail.home.arpa."


def test_mx_malformed_raises():
    with pytest.raises(PdnsError):
        normalize_content("MX", "mail.home.arpa")  # missing preference


def test_srv_dots_target_only():
    assert normalize_content("SRV", "0 5 5060 sip.home.arpa") == "0 5 5060 sip.home.arpa."


def test_srv_malformed_raises():
    with pytest.raises(PdnsError):
        normalize_content("SRV", "0 5 sip.home.arpa")  # 3 fields, not 4


def test_txt_quoting():
    assert normalize_content("TXT", "hello world") == '"hello world"'
    assert normalize_content("TXT", '"already quoted"') == '"already quoted"'
    assert normalize_content("TXT", 'a"b') == '"a\\"b"'


def test_quote_txt_escapes_backslash():
    assert _quote_txt("a\\b") == '"a\\\\b"'


def test_caa_normalizes_and_quotes_value():
    assert normalize_content("CAA", "0 issue letsencrypt.org") == '0 issue "letsencrypt.org"'
    assert normalize_content("CAA", '0 issue "letsencrypt.org"') == '0 issue "letsencrypt.org"'


def test_caa_malformed_raises():
    with pytest.raises(PdnsError):
        _normalize_caa("0 issue")  # only 2 fields


def test_empty_content_passes_through():
    assert normalize_content("A", "   ") == ""


def test_ptr_fqdn_for_ip_valid():
    assert PdnsClient.ptr_fqdn_for_ip("192.168.1.10") == "10.1.168.192.in-addr.arpa."


def test_ptr_fqdn_for_ip_invalid():
    assert PdnsClient.ptr_fqdn_for_ip("not.an.ip") is None
    assert PdnsClient.ptr_fqdn_for_ip("256.1.1.1") is None
    assert PdnsClient.ptr_fqdn_for_ip("1.2.3") is None


def test_reverse_zone_for_ptr_picks_most_specific():
    client = PdnsClient()  # constructor only reads settings; no network
    zones = [
        {"name": "168.192.in-addr.arpa."},
        {"name": "1.168.192.in-addr.arpa."},
        {"name": "home.arpa."},
    ]
    ptr = "10.1.168.192.in-addr.arpa."
    assert client.reverse_zone_for_ptr(ptr, zones) == "1.168.192.in-addr.arpa."


def test_reverse_zone_for_ptr_none_when_uncovered():
    client = PdnsClient()
    assert client.reverse_zone_for_ptr("10.1.168.192.in-addr.arpa.", [{"name": "home.arpa."}]) is None
