#!/usr/bin/env python3
"""Send a message to Jake and collect everything he does.

Usage: jake-dispatch.py "message" [session-id] [timeout-seconds]

Completion detection:
- Watch JSONL line count. When it stops growing for IDLE_SECONDS after at least one
  assistant message exists, consider the task done.
- Also detect compaction (definitive completion) as an early exit.

Writes to /tmp/jake-response-<session-id>.json

JSONL resolution:
Some OpenClaw versions store the session log as <session-id>.jsonl (the human-provided
label), while others use an internal UUID resolved via sessions.json.  We try the
direct <session-id>.jsonl path first, then fall back to UUID resolution.
"""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime

from jake_dispatch_helpers import (
    _session_key,
    has_active_lock,
    resolve_jsonl_path,
)

OPENCLAW = "/home/linuxbrew/.linuxbrew/bin/openclaw"
SESSIONS_DIR = os.path.expanduser("~/.openclaw/agents/main/sessions")
IDLE_SECONDS = int(os.environ.get("JAKE_IDLE_SECONDS", 300))  # override via env var; default 5 min
# Smoke mode: exit as soon as we see the first complete assistant response (stopReason=stop).
# Avoids waiting IDLE_SECONDS for a simple "say hello" prompt.
SMOKE_MODE = os.environ.get("JAKE_SMOKE_MODE", "0").strip() in {"1", "true", "yes"}


def main():
    message = sys.argv[1] if len(sys.argv) > 1 else "Hello Jake"
    session_id = sys.argv[2] if len(sys.argv) > 2 else f"bench-{int(time.time())}"
    timeout = int(sys.argv[3]) if len(sys.argv) > 3 else 600

    thinking_level = (os.environ.get("THINKING_LEVEL") or os.environ.get("THINKING") or "").strip()
    if thinking_level and thinking_level not in {"off", "minimal", "low", "medium", "high", "xhigh"}:
        # Ignore unexpected values rather than breaking the run.
        thinking_level = ""

    outfile = f"/tmp/jake-response-{session_id}.json"

    write_result(
        outfile,
        {
            "status": "running",
            "session_id": session_id,
            "session_key": _session_key(session_id),
            "started": datetime.now().isoformat(),
            "message": message,
        },
    )

    env = os.environ.copy()
    env["PATH"] = f"/home/linuxbrew/.linuxbrew/bin:{env.get('PATH', '')}"

    started_ms = int(time.time() * 1000)

    args = [
        OPENCLAW,
        "agent",
        "--local",
        "--session-id",
        session_id,
        "--message",
        message,
        "--timeout",
        str(timeout),
    ]
    if thinking_level:
        args.extend(["--thinking", thinking_level])

    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        preexec_fn=os.setsid,
    )

    # How long to wait for observable session activity.
    # Note: large Ollama models (e.g., gemma4:31b) can take >120s just to load,
    # and OpenClaw may not emit JSONL entries until the first token arrives.
    # Default to 5 minutes to avoid false-aborts.
    NO_ACTIVITY_ABORT = int(os.environ.get("JAKE_NO_ACTIVITY_ABORT", 300))
    # How long to wait for the first assistant message after JSONL starts growing.
    # Covers the gap where system/user messages appear in JSONL but the model never
    # responds (e.g. adapter bug, model hang during thinking). Without this, the
    # task runs until the full dispatch timeout (900-1800s).
    NO_RESPONSE_ABORT = int(os.environ.get("JAKE_NO_RESPONSE_ABORT", 180))

    start = time.time()
    poll_interval = 2  # Fast polling: 2s for quicker detection of responses and failures
    last_line_count = 0
    last_change_time = time.time()
    has_any_response = False  # assistant with stopReason=stop AND non-empty content
    has_any_assistant = False  # any assistant message, even without stopReason=stop
    completed_naturally = False

    session_uuid = None
    jsonl_path = None

    while time.time() - start < timeout:
        time.sleep(poll_interval)

        # Try to locate the JSONL file if we haven't found it yet.
        if not jsonl_path or not os.path.exists(jsonl_path):
            found = resolve_jsonl_path(session_id, SESSIONS_DIR, started_ms)
            if found:
                jsonl_path = found

        if not jsonl_path or not os.path.exists(jsonl_path):
            # Treat the presence of a lock file (for either naming scheme) as
            # evidence that the run is alive, even if the JSONL hasn't appeared yet.
            _has_lock = has_active_lock(session_id, SESSIONS_DIR, jsonl_path)

            # Early abort: no JSONL *and* no lock after NO_ACTIVITY_ABORT seconds
            if (time.time() - start) >= NO_ACTIVITY_ABORT and not _has_lock:
                print(f"⚠️ No session activity after {NO_ACTIVITY_ABORT}s, aborting early")
                break
            if proc.poll() is not None and (time.time() - start) > 10:
                break
            continue

        lines = read_jsonl(jsonl_path)
        current_count = len(lines)

        # Early abort: JSONL exists but still empty after NO_ACTIVITY_ABORT seconds.
        # If the lock file is present, the run is still active, so keep waiting.
        _has_lock = has_active_lock(session_id, SESSIONS_DIR, jsonl_path)
        if current_count == 0 and (time.time() - start) >= NO_ACTIVITY_ABORT and not _has_lock:
            print(f"⚠️ Session JSONL empty after {NO_ACTIVITY_ABORT}s, aborting early")
            break

        if any(l.get("type") == "compaction" for l in lines):
            completed_naturally = True
            break

        if not has_any_response:
            for l in lines:
                if (l.get("type") == "message"
                    and l.get("message", {}).get("role") == "assistant"
                    and l.get("message", {}).get("stopReason") == "stop"):
                    # Require actual content: at least one text, thinking, or toolCall block.
                    # Some models (gemma4:26b) emit empty content arrays with stopReason=stop.
                    content = l.get("message", {}).get("content", [])
                    if isinstance(content, list) and len(content) > 0:
                        has_any_response = True
                        break
                    elif not isinstance(content, list) and content:
                        has_any_response = True
                        break

        if not has_any_assistant:
            has_any_assistant = any(
                l.get("type") == "message"
                and l.get("message", {}).get("role") == "assistant"
                for l in lines
            )

        # Smoke mode: exit immediately once we see a complete response.
        # No need to wait for idle timeout on a simple prompt.
        if SMOKE_MODE and has_any_response:
            completed_naturally = True
            break

        if current_count != last_line_count:
            last_line_count = current_count
            last_change_time = time.time()
        else:
            idle_time = time.time() - last_change_time
            if has_any_response and idle_time >= IDLE_SECONDS:
                completed_naturally = True
                break
            # Stale abort: JSONL stopped growing for IDLE_SECONDS even without a
            # clean stop. The model is stuck (e.g. thinking loop). Exit so we
            # don't burn the full timeout.
            if has_any_assistant and idle_time >= IDLE_SECONDS:
                print(f"⚠️ JSONL stale for {IDLE_SECONDS}s (no stopReason=stop), exiting")
                break

        # No-response abort: JSONL has entries (system/user messages) but NO
        # assistant message has appeared after NO_RESPONSE_ABORT seconds.
        # This catches the case where the adapter/model never produces output
        # but the session keeps emitting non-assistant entries.
        if current_count > 0 and not has_any_assistant and (time.time() - start) >= NO_RESPONSE_ABORT:
            print(f"⚠️ JSONL has {current_count} entries but 0 assistant messages after {NO_RESPONSE_ABORT}s, aborting")
            break

    elapsed = time.time() - start

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

    time.sleep(2)

    lines = read_jsonl(jsonl_path) if jsonl_path and os.path.exists(jsonl_path) else []
    all_responses = extract_all_responses(lines)
    final_response = all_responses[-1] if all_responses else ""

    write_result(
        outfile,
        {
            "status": "collected",
            "session_id": session_id,
            "session_key": _session_key(session_id),
            "session_uuid": session_uuid,
            "session_jsonl_path": jsonl_path,
            "finished": datetime.now().isoformat(),
            "elapsed_seconds": round(elapsed, 1),
            "completed_naturally": completed_naturally,
            "responses": all_responses,
            # Back-compat: some collectors and dashboard scripts expect a single response string.
            "response": final_response,
            "final_response": final_response,
            "tool_calls": extract_tool_calls(lines),
            "messages": extract_messages(lines),
            "has_compaction": any(l.get("type") == "compaction" for l in lines),
            "total_entries": len(lines),
        },
    )

    n_resp = len(all_responses)
    n_tools = len(extract_tool_calls(lines))
    icon = "✅" if completed_naturally else "⏰"
    print(f"{icon} Collected ({elapsed:.0f}s) - {n_resp} responses, {n_tools} tool calls, natural={completed_naturally}")


def read_jsonl(path):
    lines = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    lines.append(json.loads(line.strip()))
                except Exception:
                    pass
    except Exception:
        pass
    return lines


def extract_messages(lines):
    msgs = []
    for e in lines:
        if e.get("type") != "message":
            continue
        msg = e.get("message", {})
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content", [])
        if isinstance(content, list):
            # Some models (for example gemma4) may emit only thinking blocks.
            text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
            thinking_parts = [c.get("thinking", "") for c in content if c.get("type") == "thinking"]
            text = " ".join(text_parts) or " ".join(thinking_parts)
        else:
            text = str(content)
        msgs.append({"role": role, "text": text.strip()[:10000], "timestamp": e.get("timestamp", "")})
    return msgs


def extract_all_responses(lines):
    responses = []
    for e in lines:
        if e.get("type") == "message" and e.get("message", {}).get("role") == "assistant":
            content = e["message"].get("content", [])
            if isinstance(content, list):
                # Capture both text and thinking content (thinking models like gemma4
                # may produce only thinking blocks with no text blocks)
                text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
                thinking_parts = [c.get("thinking", "") for c in content if c.get("type") == "thinking"]
                text = " ".join(text_parts) or " ".join(thinking_parts)
            else:
                text = str(content)
            if text.strip():
                responses.append(text.strip())
    return responses


def extract_tool_calls(lines):
    calls = []
    for e in lines:
        if e.get("type") != "message":
            continue
        content = e.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for c in content:
            if c.get("type") == "toolCall":
                calls.append({"tool": c.get("name", "?"), "args": str(c.get("arguments", {}))[:1000]})
    return calls


def write_result(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


if __name__ == "__main__":
    main()
