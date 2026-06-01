"""Unit tests for dns_manager.auth — the constant-time password check and the
exempt-path logic that gate the DNS-rewriting dashboard."""

from dns_manager import auth
from dns_manager.config import settings


def test_password_ok_matches(monkeypatch):
    monkeypatch.setattr(settings, "app_password", "s3cret-pass")
    assert auth.password_ok("s3cret-pass") is True
    assert auth.password_ok("wrong") is False


def test_password_ok_empty_stored_password_denies_everyone(monkeypatch):
    # An empty stored password must NOT act as "no auth" — it denies all logins.
    monkeypatch.setattr(settings, "app_password", "")
    assert auth.password_ok("") is False
    assert auth.password_ok("anything") is False


def test_is_exempt_allows_health_static_login():
    assert auth.is_exempt("/healthz")
    assert auth.is_exempt("/api/health")
    assert auth.is_exempt("/static/style.css")
    assert auth.is_exempt("/login")
    assert auth.is_exempt("/logout")
    assert auth.is_exempt("/favicon.ico")


def test_is_exempt_gates_everything_else():
    assert not auth.is_exempt("/")
    assert not auth.is_exempt("/zones/home.arpa.")
    assert not auth.is_exempt("/unbound")
    assert not auth.is_exempt("/activity")
