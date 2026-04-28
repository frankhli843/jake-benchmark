"""Generate shareable failure reports from a v1 run artifact.

Outputs:
  - report.json (conforms to failure-report-v1.schema.json)
  - report.md   (human-readable, self-contained, no dashboard required)

Both can be sanitized in-line via the `sanitize_profile` argument.
'public' is required for any artifact that leaves Frank's machines.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from . import sanitize as _sanitize

REPORT_SCHEMA_VERSION = "1"
EXCERPT_MAX_LEN = 2048


def _excerpt_from_transcript(transcript: str, *, around: int = 0) -> dict | None:
    """Pick a compact excerpt centered on `around` (0 = start). Capped at 2 KB."""
    if not transcript:
        return None
    transcript = transcript.strip()
    if len(transcript) <= EXCERPT_MAX_LEN:
        return {"offset": 0, "length": len(transcript), "text": transcript}
    start = max(0, around - EXCERPT_MAX_LEN // 2)
    end = min(len(transcript), start + EXCERPT_MAX_LEN)
    return {"offset": start, "length": end - start, "text": transcript[start:end]}


def _repro_command(model_spec: dict, task_id: str) -> str:
    provider = model_spec.get("provider", "?")
    model_id = model_spec.get("model_id", "?")
    checkpoint = model_spec.get("checkpoint_id")
    spec_str = f"{provider}:{model_id}"
    if checkpoint:
        spec_str += f"@{checkpoint}"
    return f"jake-bench run --model '{spec_str}' --task {task_id}"


def _read_transcript(run_dir: Path, task: dict) -> str:
    rel = task.get("transcriptPath")
    if not rel:
        return ""
    p = run_dir / rel
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _failed(task: dict) -> bool:
    if task.get("error") is not None:
        return True
    return not bool(task.get("passed", False))


def build_report(
    run_artifact: dict,
    *,
    run_dir: Path | None = None,
    sanitize_profile: str = "public",
) -> dict:
    """Build a failure report dict from a parsed run artifact.

    `run_dir` is needed if you want transcript excerpts; without it the
    report still includes metadata, scores, and category counts.
    """
    if run_artifact.get("schemaVersion") != "1":
        raise ValueError("run artifact is not schemaVersion '1'")

    tasks = run_artifact.get("tasks", [])
    failed_tasks = [t for t in tasks if _failed(t)]
    failures: list[dict] = []
    counter: Counter[str] = Counter()

    for task in failed_tasks:
        err = task.get("error") or {}
        category = err.get("category") or "other"
        counter[category] += 1

        excerpt = None
        if run_dir is not None:
            transcript = _read_transcript(run_dir, task)
            excerpt = _excerpt_from_transcript(transcript)

        criteria_failed = [
            cr.get("criterion", "")
            for cr in task.get("criteriaResults", [])
            if not cr.get("passed", True)
        ]

        entry: dict[str, Any] = {
            "taskId": task.get("taskId"),
            "category": category,
            "score": task.get("score", 0),
            "maxScore": task.get("maxScore", 0),
            "errorMessage": err.get("message", ""),
            "criteriaFailed": criteria_failed,
            "reproCommand": _repro_command(
                run_artifact.get("modelSpec", {}), task.get("taskId", "?")
            ),
        }
        if task.get("name"):
            entry["name"] = task["name"]
        if excerpt is not None:
            entry["transcriptExcerpt"] = excerpt
        failures.append(entry)

    summary = {
        "tasksTotal": len(tasks),
        "tasksFailed": len(failed_tasks),
        "categoryCounts": dict(counter),
        "topCategory": counter.most_common(1)[0][0] if counter else "",
    }

    report = {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "runId": run_artifact.get("runId", ""),
        "generatedAt": _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "modelSpec": run_artifact.get("modelSpec", {}),
        "configHash": run_artifact.get("configHash", ""),
        "sanitized": sanitize_profile != "none",
        "sanitizationProfile": sanitize_profile,
        "summary": summary,
        "failures": failures,
        "environment": run_artifact.get("environment", {}),
    }

    if sanitize_profile != "none":
        report = _sanitize.sanitize_obj(report, sanitize_profile)

    return report


def render_markdown(report: dict) -> str:
    """Render a self-contained markdown view of the failure report."""
    lines: list[str] = []
    spec = report.get("modelSpec", {})
    summary = report.get("summary", {})
    lines.append(f"# Benchmark Failure Report: {report.get('runId', 'unknown')}")
    lines.append("")
    lines.append(f"Generated: {report.get('generatedAt', '')}")
    lines.append(f"Sanitization: `{report.get('sanitizationProfile', 'unknown')}`")
    lines.append("")
    lines.append("## Model")
    lines.append("")
    lines.append(f"- Provider: `{spec.get('provider', '?')}`")
    lines.append(f"- Model: `{spec.get('model_id', '?')}`")
    if spec.get("checkpoint_id"):
        lines.append(f"- Checkpoint: `{spec.get('checkpoint_id')}`")
    options = spec.get("options") or {}
    if options:
        lines.append(f"- Options: `{json.dumps(options, sort_keys=True)}`")
    if report.get("configHash"):
        lines.append(f"- Config hash: `{report['configHash'][:12]}...`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(
        f"- Tasks: {summary.get('tasksFailed', 0)} failed / {summary.get('tasksTotal', 0)} total"
    )
    cat_counts = summary.get("categoryCounts", {})
    if cat_counts:
        lines.append("- Failure categories:")
        for cat, count in sorted(cat_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"  - `{cat}`: {count}")
    lines.append("")
    failures = report.get("failures", [])
    if not failures:
        lines.append("_No failures._")
        return "\n".join(lines) + "\n"

    lines.append("## Failures")
    lines.append("")
    for f in failures:
        lines.append(f"### {f.get('taskId')}: {f.get('name', '')}")
        lines.append("")
        lines.append(
            f"- Category: `{f.get('category')}`  Score: {f.get('score', 0)}/{f.get('maxScore', 0)}"
        )
        if f.get("errorMessage"):
            lines.append(f"- Error: {f['errorMessage']}")
        criteria_failed = f.get("criteriaFailed") or []
        if criteria_failed:
            lines.append("- Criteria failed:")
            for c in criteria_failed:
                lines.append(f"  - {c}")
        excerpt = f.get("transcriptExcerpt")
        if excerpt and excerpt.get("text"):
            lines.append("")
            lines.append("Transcript excerpt:")
            lines.append("")
            lines.append("```")
            lines.append(excerpt["text"].rstrip())
            lines.append("```")
        if f.get("reproCommand"):
            lines.append("")
            lines.append(f"Repro: `{f['reproCommand']}`")
        lines.append("")

    return "\n".join(lines) + "\n"


def write_report(
    report: dict,
    out_dir: Path,
    *,
    json_name: str = "report.json",
    md_name: str = "report.md",
) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / json_name
    md_path = out_dir / md_name
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="Directory containing run.json")
    parser.add_argument("--out", type=Path, default=None, help="Output directory (default: run_dir)")
    parser.add_argument(
        "--sanitize",
        choices=["none", "internal", "public"],
        default="public",
    )
    args = parser.parse_args(argv)

    run_dir: Path = args.run_dir
    out_dir: Path = args.out or run_dir
    run_artifact = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    report = build_report(run_artifact, run_dir=run_dir, sanitize_profile=args.sanitize)
    json_path, md_path = write_report(report, out_dir)
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
