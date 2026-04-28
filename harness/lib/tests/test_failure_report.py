"""End-to-end test: MockRunner -> run.json -> failure report -> sanitization audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.lib import failure_report, migrate, mock_runner, sanitize, validate


REPO = Path(__file__).resolve().parents[3]
LEGACY_TASKS = REPO / "harness" / "tasks.json"


@pytest.fixture
def pack(tmp_path: Path) -> dict:
    legacy = json.loads(LEGACY_TASKS.read_text(encoding="utf-8"))
    return migrate.migrate(legacy)


@pytest.fixture
def model_spec() -> dict:
    return {
        "provider": "mock",
        "model_id": "mock:smoke",
        "options": {"context_length": 8192, "thinking": "off"},
    }


def test_smoke_run_produces_valid_artifact(tmp_path: Path, pack: dict, model_spec: dict):
    run_artifact = mock_runner.run(pack, model_spec, tmp_path)
    errors = validate.validate(run_artifact, "run-artifact-v1")
    assert errors == [], f"run artifact does not validate: {errors}"
    assert (tmp_path / "run.json").exists()
    assert run_artifact["summary"]["tasksTotal"] == len(pack["tasks"])


def test_failure_report_round_trip_validates(tmp_path: Path, pack: dict, model_spec: dict):
    run_artifact = mock_runner.run(pack, model_spec, tmp_path, fail_task_ids=[pack["tasks"][0]["id"]])
    report = failure_report.build_report(run_artifact, run_dir=tmp_path, sanitize_profile="public")
    errors = validate.validate(report, "failure-report-v1")
    assert errors == [], f"failure report does not validate: {errors}"
    assert report["summary"]["tasksFailed"] >= 1


def test_failure_report_markdown_contains_failure_section(tmp_path: Path, pack: dict, model_spec: dict):
    run_artifact = mock_runner.run(pack, model_spec, tmp_path, fail_task_ids=[pack["tasks"][0]["id"]])
    report = failure_report.build_report(run_artifact, run_dir=tmp_path, sanitize_profile="public")
    md = failure_report.render_markdown(report)
    assert "## Failures" in md
    assert pack["tasks"][0]["id"] in md
    assert "Repro:" in md


def test_failure_report_passes_sanitization_audit(tmp_path: Path, pack: dict, model_spec: dict):
    """A 'public' report must contain no surviving sensitive patterns."""
    run_artifact = mock_runner.run(pack, model_spec, tmp_path, fail_task_ids=[pack["tasks"][0]["id"]])
    # Sprinkle sensitive content into the transcript files so the audit has something to find
    # if sanitization fails.
    for t in run_artifact["tasks"]:
        path = tmp_path / t["transcriptPath"]
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\nleak: lifrank1994@gmail.com  192.168.1.42  /home/frank/secret\n",
            encoding="utf-8",
        )
    report = failure_report.build_report(run_artifact, run_dir=tmp_path, sanitize_profile="public")
    md = failure_report.render_markdown(report)
    blob = json.dumps(report) + "\n" + md
    findings = sanitize.audit(blob)
    leak_rules = [f for f in findings if f["rule"] in {"email", "ipv4", "home_path"}]
    assert leak_rules == [], f"sanitized report still leaks: {leak_rules}"


def test_failure_report_writes_files(tmp_path: Path, pack: dict, model_spec: dict):
    run_artifact = mock_runner.run(pack, model_spec, tmp_path, fail_task_ids=[pack["tasks"][0]["id"]])
    report = failure_report.build_report(run_artifact, run_dir=tmp_path, sanitize_profile="public")
    json_p, md_p = failure_report.write_report(report, tmp_path)
    assert json_p.exists()
    assert md_p.exists()
