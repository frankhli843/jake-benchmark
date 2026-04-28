"""Compare two run artifacts (or a matrix index).

Outputs a small comparison report: per-task delta, summary delta, common
metadata. Used by `jake-bench compare <run-a> <run-b>` and by the matrix
post-processor to surface regressions across ModelSpecs.

Comparator is schema-agnostic for missing fields: it compares what is
present in both artifacts and reports the rest as "only in A" or "only
in B".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TaskDelta:
    task_id: str
    score_a: float | None
    score_b: float | None
    passed_a: bool | None
    passed_b: bool | None

    @property
    def regressed(self) -> bool:
        return bool(self.passed_a and not self.passed_b)

    @property
    def improved(self) -> bool:
        return bool((not self.passed_a) and self.passed_b)


@dataclass
class CompareReport:
    a_run_id: str
    b_run_id: str
    a_config_hash: str | None
    b_config_hash: str | None
    summary_a: dict
    summary_b: dict
    task_deltas: list[TaskDelta] = field(default_factory=list)

    @property
    def regressions(self) -> list[TaskDelta]:
        return [d for d in self.task_deltas if d.regressed]

    @property
    def improvements(self) -> list[TaskDelta]:
        return [d for d in self.task_deltas if d.improved]

    def to_dict(self) -> dict:
        return {
            "schemaVersion": "1",
            "kind": "compare-report",
            "a": {
                "runId": self.a_run_id,
                "configHash": self.a_config_hash,
                "summary": self.summary_a,
            },
            "b": {
                "runId": self.b_run_id,
                "configHash": self.b_config_hash,
                "summary": self.summary_b,
            },
            "regressionCount": len(self.regressions),
            "improvementCount": len(self.improvements),
            "tasks": [
                {
                    "taskId": d.task_id,
                    "scoreA": d.score_a,
                    "scoreB": d.score_b,
                    "passedA": d.passed_a,
                    "passedB": d.passed_b,
                    "regressed": d.regressed,
                    "improved": d.improved,
                }
                for d in self.task_deltas
            ],
        }

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append(f"# Compare {self.a_run_id} vs {self.b_run_id}")
        lines.append("")
        lines.append(f"- A run: `{self.a_run_id}` (config `{self.a_config_hash or '?'}`)")
        lines.append(f"- B run: `{self.b_run_id}` (config `{self.b_config_hash or '?'}`)")
        lines.append("")
        lines.append("## Summary delta")
        lines.append("")
        a_pct = self.summary_a.get("percentage")
        b_pct = self.summary_b.get("percentage")
        a_pass = self.summary_a.get("tasksPassed")
        b_pass = self.summary_b.get("tasksPassed")
        lines.append(
            f"- Percentage: A {a_pct} -> B {b_pct} (delta "
            f"{_safe_delta(b_pct, a_pct)})"
        )
        lines.append(
            f"- Tasks passed: A {a_pass} -> B {b_pass} (delta "
            f"{_safe_delta(b_pass, a_pass)})"
        )
        lines.append("")
        lines.append(
            f"- Regressions: {len(self.regressions)}; "
            f"Improvements: {len(self.improvements)}"
        )
        lines.append("")
        if self.regressions:
            lines.append("## Regressions")
            lines.append("")
            for d in self.regressions:
                lines.append(
                    f"- `{d.task_id}`: A {d.score_a} pass=true -> "
                    f"B {d.score_b} pass=false"
                )
            lines.append("")
        if self.improvements:
            lines.append("## Improvements")
            lines.append("")
            for d in self.improvements:
                lines.append(
                    f"- `{d.task_id}`: A {d.score_a} pass=false -> "
                    f"B {d.score_b} pass=true"
                )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def compare_runs(a: dict, b: dict) -> CompareReport:
    """Build a `CompareReport` from two run artifacts (parsed dicts)."""

    a_tasks = {t.get("taskId"): t for t in (a.get("tasks") or [])}
    b_tasks = {t.get("taskId"): t for t in (b.get("tasks") or [])}
    all_ids = sorted(set(a_tasks) | set(b_tasks))
    deltas: list[TaskDelta] = []
    for tid in all_ids:
        ta = a_tasks.get(tid) or {}
        tb = b_tasks.get(tid) or {}
        deltas.append(
            TaskDelta(
                task_id=str(tid),
                score_a=_num(ta.get("score")),
                score_b=_num(tb.get("score")),
                passed_a=_bool(ta.get("passed")),
                passed_b=_bool(tb.get("passed")),
            )
        )
    return CompareReport(
        a_run_id=str(a.get("runId") or "?"),
        b_run_id=str(b.get("runId") or "?"),
        a_config_hash=a.get("configHash"),
        b_config_hash=b.get("configHash"),
        summary_a=a.get("summary") or {},
        summary_b=b.get("summary") or {},
        task_deltas=deltas,
    )


def compare_run_files(a_path: Path | str, b_path: Path | str) -> CompareReport:
    a = json.loads(Path(a_path).read_text(encoding="utf-8"))
    b = json.loads(Path(b_path).read_text(encoding="utf-8"))
    return compare_runs(a, b)


def _num(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _bool(v: Any) -> bool | None:
    if v is None:
        return None
    return bool(v)


def _safe_delta(b: Any, a: Any) -> str:
    bn = _num(b)
    an = _num(a)
    if bn is None or an is None:
        return "n/a"
    return f"{bn - an:+.2f}"
