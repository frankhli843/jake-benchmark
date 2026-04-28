"""Deterministic MockRunner: emits a synthetic run artifact without GPUs.

Used by CI smoke tests and by `jake-bench` developers who want to
exercise the report/sanitize/CLI surface without a live model. The mock
is intentionally cheap (no I/O beyond writing the artifact dir) and
deterministic (seeded by task ids, not by clock).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path
from typing import Any

RUNNER_NAME = "mock"
RUNNER_VERSION = "0.1.0"

# Deterministic synthetic transcripts for smoke testing redaction.
_TRANSCRIPT_TEMPLATE = (
    "[user] Hello assistant.\n"
    "[assistant] Hello! I will execute the task using the gateway.\n"
    "[tool_call] jake_gog list_inbox\n"
    "[tool_result] 5 unread messages\n"
    "[assistant] Done.\n"
)


def _config_hash(model_spec: dict) -> str:
    canonical = json.dumps(model_spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _task_seed(task_id: str) -> int:
    return int(hashlib.sha256(task_id.encode("utf-8")).hexdigest(), 16)


def run(
    pack: dict,
    model_spec: dict,
    out_dir: Path,
    *,
    fail_task_ids: list[str] | None = None,
) -> dict:
    """Run the pack against the mock and write run.json + transcripts.

    `fail_task_ids` lets callers force specific task failures. By default,
    every task at index % 4 == 3 is marked failed (deterministic).
    """
    out_dir = Path(out_dir)
    transcripts_dir = out_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    fail_set = set(fail_task_ids or [])
    started_at = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    cfg_hash = _config_hash(model_spec)

    task_results: list[dict] = []
    total_score = 0.0
    max_total = 0.0
    passed_count = 0
    errored_count = 0

    for i, task in enumerate(pack["tasks"]):
        task_id = task["id"]
        max_score = (
            task["grading"].get("max_score")
            or task["grading"].get("maxScore")
            or 10
        )
        forced_fail = task_id in fail_set or (not fail_set and i % 4 == 3)

        seed = _task_seed(task_id)
        score = 0.0 if forced_fail else max_score
        passed = not forced_fail

        transcript_rel = f"transcripts/{task_id}.txt"
        (out_dir / transcript_rel).write_text(_TRANSCRIPT_TEMPLATE, encoding="utf-8")

        result: dict[str, Any] = {
            "taskId": task_id,
            "score": score,
            "maxScore": max_score,
            "passed": passed,
            "durationMs": 100 + (seed % 500),
            "tokensIn": 200 + (seed % 50),
            "tokensOut": 80 + (seed % 30),
            "tokensPerSecond": 25.0,
            "transcriptPath": transcript_rel,
            "criteriaResults": [],
        }

        if forced_fail:
            result["error"] = {
                "category": "tool_call_missing",
                "message": "Mock-injected failure for smoke test.",
            }
            errored_count += 1
        else:
            passed_count += 1

        total_score += score
        max_total += max_score
        task_results.append(result)

    completed_at = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)

    run_artifact = {
        "schemaVersion": "1",
        "runId": f"{pack['pack']}-mock-{cfg_hash[:8]}-{int(started_at.timestamp())}",
        "startedAt": started_at.isoformat().replace("+00:00", "Z"),
        "completedAt": completed_at.isoformat().replace("+00:00", "Z"),
        "pack": {
            "pack": pack["pack"],
            "version": pack["version"],
            "family": pack["family"],
        },
        "modelSpec": model_spec,
        "runner": {"name": RUNNER_NAME, "version": RUNNER_VERSION, "adapter": "mock"},
        "configHash": cfg_hash,
        "hardware": {"host_class": "ci"},
        "environment": {},
        "tasks": task_results,
        "summary": {
            "totalScore": total_score,
            "maxScore": max_total,
            "percentage": (100.0 * total_score / max_total) if max_total else 0.0,
            "tasksPassed": passed_count,
            "tasksTotal": len(task_results),
            "tasksErrored": errored_count,
            "avgTokensPerSecond": 25.0,
            "totalDurationMs": sum(t["durationMs"] for t in task_results),
        },
    }

    (out_dir / "run.json").write_text(
        json.dumps(run_artifact, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return run_artifact
