"""Redaction library for benchmark artifacts.

Three profiles:
  - "none":     pass through unchanged
  - "internal": redact secrets (tokens, keys), keep paths/IPs (debug-friendly)
  - "public":   redact everything that could leak topology, identity, or auth

Profiles are additive: 'public' includes everything 'internal' does.

Public mode is required for any artifact that leaves Frank's machines.
The redaction sweep is deterministic and idempotent: redact(redact(x)) == redact(x).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Pattern


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: Pattern[str]
    replacement: str
    profiles: frozenset


_INTERNAL_HOSTNAMES = (
    "frank-pc",
    "frank-wsl",
    "frankpi",
    "DESKTOP-DDEC81D",
    "clawed-nina",
    "clawed-peter",
    "clawed-adamas",
    "clawed-george",
    "clawed-jason_wwsa",
    "clawed-wwsa",
)


def _build_rules() -> tuple[Rule, ...]:
    secret = frozenset({"internal", "public"})
    public_only = frozenset({"public"})

    rules: list[Rule] = []

    # --- Secrets (always redacted, even in 'internal' mode) ---
    rules.append(Rule(
        name="anthropic_key",
        pattern=re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
        replacement="<REDACTED:anthropic_key>",
        profiles=secret,
    ))
    rules.append(Rule(
        name="openai_key",
        pattern=re.compile(r"sk-[A-Za-z0-9]{20,}"),
        replacement="<REDACTED:openai_key>",
        profiles=secret,
    ))
    rules.append(Rule(
        name="aws_access_key",
        pattern=re.compile(r"AKIA[0-9A-Z]{16}"),
        replacement="<REDACTED:aws_key>",
        profiles=secret,
    ))
    rules.append(Rule(
        name="bearer_token",
        pattern=re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.=]{16,}"),
        replacement="bearer <REDACTED:token>",
        profiles=secret,
    ))
    rules.append(Rule(
        name="oauth_refresh",
        pattern=re.compile(r"1//[A-Za-z0-9_\-]{30,}"),
        replacement="<REDACTED:oauth_refresh>",
        profiles=secret,
    ))

    # --- Public-only: topology, identity, paths ---
    # Tailscale CGNAT range (100.64.0.0/10). Must run BEFORE generic IPv4 to give
    # this its own label.
    rules.append(Rule(
        name="tailscale_ip",
        pattern=re.compile(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"),
        replacement="<REDACTED:tailscale_ip>",
        profiles=public_only,
    ))
    rules.append(Rule(
        name="ipv4",
        pattern=re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        replacement="<REDACTED:ipv4>",
        profiles=public_only,
    ))
    rules.append(Rule(
        name="ipv6",
        # Two alternatives: full 8-group form, OR a zero-compressed form
        # that MUST contain a literal '::'. Requiring '::' for the compressed
        # form prevents the pattern from grabbing 'ED:e' fragments inside
        # already-redacted placeholders like '<REDACTED:email>'.
        pattern=re.compile(
            r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"
            r"|\b[0-9a-fA-F]{0,4}::(?:[0-9a-fA-F]{0,4}:?){0,6}[0-9a-fA-F]{1,4}\b"
        ),
        replacement="<REDACTED:ipv6>",
        profiles=public_only,
    ))
    rules.append(Rule(
        name="email",
        pattern=re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        replacement="<REDACTED:email>",
        profiles=public_only,
    ))
    # E.164 + North American phone numbers. Must avoid stripping inside
    # transcript noise like timestamps "12:34:56-07:00".
    rules.append(Rule(
        name="phone_e164",
        pattern=re.compile(r"\+\d{10,15}\b"),
        replacement="<REDACTED:phone>",
        profiles=public_only,
    ))
    rules.append(Rule(
        name="phone_na",
        # Require explicit phone-number formatting (parens or delimiters)
        # to avoid swallowing 10-digit Unix timestamps and other long
        # numbers that appear inside identifiers like run-IDs.
        pattern=re.compile(
            r"(?:"
            r"\(\d{3}\)[\s\-]?\d{3}[\s\-]\d{4}"           # (647) 802-3321 / (647)802-3321
            r"|\b(?:1[\s\-])?\d{3}[\s\-]\d{3}[\s\-]\d{4}\b"  # 647-802-3321 / 1-647-802-3321
            r")"
        ),
        replacement="<REDACTED:phone>",
        profiles=public_only,
    ))
    # Internal hostnames. Compile a single alternation, word-bounded.
    host_alt = "|".join(re.escape(h) for h in _INTERNAL_HOSTNAMES)
    rules.append(Rule(
        name="internal_hostname",
        pattern=re.compile(r"\b(?:" + host_alt + r")\b"),
        replacement="<REDACTED:hostname>",
        profiles=public_only,
    ))
    # Absolute home/root paths. Capture the path so we can keep the basename context.
    rules.append(Rule(
        name="home_path",
        pattern=re.compile(r"/(?:home|Users|root|var/lib)/[A-Za-z0-9._\-/]+"),
        replacement="<REDACTED:path>",
        profiles=public_only,
    ))

    return tuple(rules)


_RULES = _build_rules()


def _select_rules(profile: str) -> Iterable[Rule]:
    if profile == "none":
        return ()
    return tuple(r for r in _RULES if profile in r.profiles)


def sanitize(text: str, profile: str = "public") -> str:
    """Redact text using the named profile.

    Profiles: 'none' | 'internal' | 'public'.
    Idempotent: applying twice yields the same output.
    """
    if profile not in {"none", "internal", "public"}:
        raise ValueError(f"unknown profile: {profile!r}")
    if not text:
        return text
    out = text
    for rule in _select_rules(profile):
        out = rule.pattern.sub(rule.replacement, out)
    return out


def sanitize_obj(obj, profile: str = "public"):
    """Recursively redact strings in dicts/lists/tuples. Other types pass through."""
    if isinstance(obj, str):
        return sanitize(obj, profile)
    if isinstance(obj, dict):
        return {k: sanitize_obj(v, profile) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_obj(v, profile) for v in obj]
    if isinstance(obj, tuple):
        return tuple(sanitize_obj(v, profile) for v in obj)
    return obj


def audit(text: str) -> list[dict]:
    """Return a list of leak findings. Used by tests to fail builds when
    sensitive patterns survive a sanitization pass.
    """
    findings: list[dict] = []
    for rule in _RULES:
        for m in rule.pattern.finditer(text):
            findings.append({
                "rule": rule.name,
                "match": m.group(0),
                "start": m.start(),
                "end": m.end(),
            })
    return findings


def rule_names(profile: str = "public") -> list[str]:
    return [r.name for r in _select_rules(profile)]
