"""Compare two run artifacts: regressions, improvements, JSON + markdown."""

from __future__ import annotations

from harness.lib.compare import compare_runs


def _artifact(run_id: str, tasks: list[dict]) -> dict:
    summary = {
        "totalScore": sum(t.get("score", 0) for t in tasks),
        "maxScore": sum(t.get("maxScore", 0) for t in tasks),
        "percentage": 0.0,
        "tasksPassed": sum(1 for t in tasks if t.get("passed")),
        "tasksTotal": len(tasks),
    }
    if summary["maxScore"]:
        summary["percentage"] = 100.0 * summary["totalScore"] / summary["maxScore"]
    return {
        "runId": run_id,
        "configHash": "h-" + run_id,
        "tasks": tasks,
        "summary": summary,
    }


def test_compare_detects_regression():
    a = _artifact("a", [
        {"taskId": "t1", "score": 10, "maxScore": 10, "passed": True},
        {"taskId": "t2", "score": 10, "maxScore": 10, "passed": True},
    ])
    b = _artifact("b", [
        {"taskId": "t1", "score": 10, "maxScore": 10, "passed": True},
        {"taskId": "t2", "score": 0, "maxScore": 10, "passed": False},
    ])
    rep = compare_runs(a, b)
    assert len(rep.regressions) == 1
    assert rep.regressions[0].task_id == "t2"
    assert len(rep.improvements) == 0


def test_compare_detects_improvement():
    a = _artifact("a", [
        {"taskId": "t1", "score": 0, "maxScore": 10, "passed": False},
    ])
    b = _artifact("b", [
        {"taskId": "t1", "score": 10, "maxScore": 10, "passed": True},
    ])
    rep = compare_runs(a, b)
    assert len(rep.improvements) == 1
    assert len(rep.regressions) == 0


def test_compare_handles_missing_tasks():
    a = _artifact("a", [{"taskId": "t1", "score": 10, "maxScore": 10, "passed": True}])
    b = _artifact("b", [{"taskId": "t2", "score": 10, "maxScore": 10, "passed": True}])
    rep = compare_runs(a, b)
    task_ids = {d.task_id for d in rep.task_deltas}
    assert task_ids == {"t1", "t2"}


def test_compare_to_dict_and_markdown():
    a = _artifact("a", [{"taskId": "t1", "score": 10, "maxScore": 10, "passed": True}])
    b = _artifact("b", [{"taskId": "t1", "score": 0, "maxScore": 10, "passed": False}])
    rep = compare_runs(a, b)
    d = rep.to_dict()
    assert d["kind"] == "compare-report"
    assert d["regressionCount"] == 1
    md = rep.to_markdown()
    assert "Regressions" in md
    assert "t1" in md
