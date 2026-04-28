"""Sanitization tests with golden fixtures.

Build fails if a known sensitive pattern survives a 'public' redaction
pass, OR if a 'safe' fixture is altered (false-positive redaction).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from harness.lib import sanitize


FIX = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def sensitive_text() -> str:
    return (FIX / "sensitive_input.txt").read_text(encoding="utf-8")


@pytest.fixture
def safe_text() -> str:
    return (FIX / "safe_input.txt").read_text(encoding="utf-8")


# --- Profile sanity ---


def test_profile_none_is_passthrough(sensitive_text):
    assert sanitize.sanitize(sensitive_text, "none") == sensitive_text


def test_profile_unknown_raises():
    with pytest.raises(ValueError):
        sanitize.sanitize("hi", "weird")


def test_internal_redacts_secrets_only(sensitive_text):
    out = sanitize.sanitize(sensitive_text, "internal")
    # Secrets gone:
    assert "sk-ant-" not in out
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "abcdef1234567890ABCDEF1234567890" not in out
    # But topology kept:
    assert "192.168.1.42" in out
    assert "frankpi" in out
    assert "lifrank1994@gmail.com" in out


# --- Public profile redacts everything sensitive ---


def test_public_redacts_emails(sensitive_text):
    out = sanitize.sanitize(sensitive_text, "public")
    assert "lifrank1994@gmail.com" not in out
    assert "wsfccorp@gmail.com" not in out


def test_public_redacts_ipv4(sensitive_text):
    out = sanitize.sanitize(sensitive_text, "public")
    assert "192.168.1.42" not in out


def test_public_redacts_tailscale_ip(sensitive_text):
    out = sanitize.sanitize(sensitive_text, "public")
    assert "100.108.252.124" not in out
    assert "tailscale_ip" in out  # tagged differently from generic ipv4


def test_public_redacts_internal_hostnames(sensitive_text):
    out = sanitize.sanitize(sensitive_text, "public")
    for h in ("frankpi", "frank-pc", "clawed-nina"):
        assert h not in out


def test_public_redacts_home_paths(sensitive_text):
    out = sanitize.sanitize(sensitive_text, "public")
    assert "/home/frank/" not in out
    assert "/Users/charlotte/" not in out


def test_public_redacts_phones(sensitive_text):
    out = sanitize.sanitize(sensitive_text, "public")
    assert "+16478023321" not in out
    assert "(647) 802-3321" not in out


def test_public_redacts_ipv6(sensitive_text):
    out = sanitize.sanitize(sensitive_text, "public")
    assert "fe80::1ff:fe23:4567:890a" not in out


# --- Safe content not damaged ---


def test_safe_content_passes_through_public(safe_text):
    out = sanitize.sanitize(safe_text, "public")
    # Common words should stay intact
    for keep in ("prime numbers", "tokens per second", "Wikipedia", "RFC 8259", "RFC 5321"):
        assert keep in out, f"benign substring '{keep}' was redacted unexpectedly"
    # Plain numbers unrelated to PII keep their digits
    assert "1234" in out


# --- Idempotence ---


def test_sanitize_idempotent_public(sensitive_text):
    once = sanitize.sanitize(sensitive_text, "public")
    twice = sanitize.sanitize(once, "public")
    assert once == twice


# --- Final audit sweep: nothing sensitive may survive a public pass ---


def test_audit_finds_no_leaks_after_public_sanitize(sensitive_text):
    out = sanitize.sanitize(sensitive_text, "public")
    findings = sanitize.audit(out)
    # Filter to rules that should be redacted in public mode.
    leak_rules = [
        f for f in findings
        if f["rule"] in {
            "anthropic_key", "openai_key", "aws_access_key", "bearer_token",
            "oauth_refresh", "tailscale_ip", "ipv4", "ipv6", "email",
            "phone_e164", "phone_na", "internal_hostname", "home_path",
        }
    ]
    assert leak_rules == [], f"leaks survived public sanitize: {leak_rules}"


# --- Recursive sanitization on dicts/lists ---


def test_sanitize_obj_recurses():
    obj = {
        "a": "user@example.com",
        "b": ["x@y.io", {"nested": "frankpi rocks"}],
        "c": 42,
        "d": True,
    }
    out = sanitize.sanitize_obj(obj, "public")
    assert "user@example.com" not in out["a"]
    assert "x@y.io" not in out["b"][0]
    assert "frankpi" not in out["b"][1]["nested"]
    assert out["c"] == 42
    assert out["d"] is True


# --- Spot-check that we don't redact innocuous numeric runs ---


def test_does_not_redact_random_numbers():
    text = "The score was 42 out of 100 in 5 trials."
    out = sanitize.sanitize(text, "public")
    assert out == text


# --- Regex on output: spot-checks on tagged labels ---

LABEL_RE = re.compile(r"<REDACTED:[a-z0-9_]+>")


def test_redaction_inserts_labels(sensitive_text):
    out = sanitize.sanitize(sensitive_text, "public")
    labels = set(LABEL_RE.findall(out))
    expected = {
        "<REDACTED:email>",
        "<REDACTED:ipv4>",
        "<REDACTED:tailscale_ip>",
        "<REDACTED:hostname>",
        "<REDACTED:path>",
        "<REDACTED:anthropic_key>",
        "<REDACTED:openai_key>",
        "<REDACTED:aws_key>",
        "<REDACTED:phone>",
        "<REDACTED:ipv6>",
        "<REDACTED:oauth_refresh>",
    }
    missing = expected - labels
    assert not missing, f"expected labels missing: {missing}"
