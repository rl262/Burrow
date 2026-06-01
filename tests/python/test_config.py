"""Unit tests for the dns_manager.config parsing helpers."""

from dns_manager.config import _bool, _fqdn


def test_bool_truthy_values():
    for v in ("1", "true", "True", "YES", "on", "  on  "):
        assert _bool(v) is True


def test_bool_falsy_values():
    for v in ("0", "false", "no", "off", "nope"):
        assert _bool(v) is False


def test_bool_empty_or_none_uses_default():
    assert _bool("") is False
    assert _bool(None) is False
    assert _bool("", True) is True
    assert _bool(None, True) is True


def test_fqdn_adds_exactly_one_dot():
    assert _fqdn("home.arpa") == "home.arpa."
    assert _fqdn("home.arpa.") == "home.arpa."
    assert _fqdn("  lab  ") == "lab."
    assert _fqdn("") == ""
