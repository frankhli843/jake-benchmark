"""Shared helpers for jake-dispatch JSONL resolution.

Extracted so they can be tested independently from the main dispatch loop.
"""

import json
import os


def _session_key(session_id: str) -> str:
    return f"agent:main:explicit:{session_id}"


def _load_sessions_index(sessions_dir: str) -> dict:
    path = os.path.join(sessions_dir, "sessions.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _resolve_session_uuid(session_id: str, sessions_dir: str, started_ms: int) -> str | None:
    """Look up the internal UUID for a session-id via sessions.json."""
    data = _load_sessions_index(sessions_dir)
    key = _session_key(session_id)
    entry = data.get(key)
    if not isinstance(entry, dict):
        return None

    updated_at = entry.get("updatedAt")
    if isinstance(updated_at, (int, float)) and updated_at + 5000 < started_ms:
        return None

    sid = entry.get("sessionId")
    if isinstance(sid, str) and sid:
        return sid
    return None


def resolve_jsonl_path(session_id: str, sessions_dir: str, started_ms: int) -> str | None:
    """Find the JSONL file for a session, trying both naming schemes.

    Returns the path if the file exists, None otherwise.
    Order: direct <session_id>.jsonl first, then UUID resolution via sessions.json.
    """
    # Direct path (newer OpenClaw versions).
    direct = os.path.join(sessions_dir, f"{session_id}.jsonl")
    if os.path.exists(direct):
        return direct

    # UUID resolution (older installs).
    uuid = _resolve_session_uuid(session_id, sessions_dir, started_ms)
    if uuid:
        uuid_path = os.path.join(sessions_dir, f"{uuid}.jsonl")
        if os.path.exists(uuid_path):
            return uuid_path

    return None


def has_active_lock(session_id: str, sessions_dir: str, uuid_jsonl_path: str | None) -> bool:
    """Check if a lock file exists for either naming scheme."""
    direct_lock = os.path.join(sessions_dir, f"{session_id}.jsonl.lock")
    if os.path.exists(direct_lock):
        return True
    if uuid_jsonl_path and os.path.exists(f"{uuid_jsonl_path}.lock"):
        return True
    return False
