"""Runner abstraction tests: MockRunner end-to-end + OpenclawRunner isolation."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from harness.lib import migrate, validate
from harness.lib.model_spec import ModelSpec
from harness.lib.runner import (
    MockRunner,
    OpenclawRunner,
    RunnerError,
    build_runner,
    detect_hardware,
)


REPO = Path(__file__).resolve().parents[3]
LEGACY_TASKS = REPO / "harness" / "tasks.json"


@pytest.fixture
def pack() -> dict:
    legacy = json.loads(LEGACY_TASKS.read_text(encoding="utf-8"))
    return migrate.migrate(legacy)


@pytest.fixture
def spec() -> ModelSpec:
    return ModelSpec(
        provider="mock",
        model_id="mock:smoke",
        options={"context_length": 8192, "thinking": "off"},
    )


def test_mock_runner_protocol(pack: dict, spec: ModelSpec, tmp_path: Path):
    runner = MockRunner()
    ctx = runner.prepare(spec, tmp_path / "scratch")
    assert ctx.workspace.exists()
    out_dir = tmp_path / "run"
    artifact = runner.run_pack(pack, spec, ctx, out_dir=out_dir)
    runner.teardown(ctx)

    assert artifact["runner"]["name"] == "mock"
    assert artifact["runner"]["adapter"] == "mock"
    assert artifact["modelSpec"] == spec.to_dict()
    assert artifact["configHash"] == spec.config_hash
    assert "summary" in artifact

    errors = validate.validate(artifact, "run-artifact-v1")
    assert errors == [], errors


def test_mock_runner_force_failures(pack: dict, spec: ModelSpec, tmp_path: Path):
    forced_id = pack["tasks"][0]["id"]
    runner = MockRunner(fail_task_ids=[forced_id])
    ctx = runner.prepare(spec, tmp_path / "scratch")
    artifact = runner.run_pack(pack, spec, ctx, out_dir=tmp_path / "run")
    runner.teardown(ctx)
    failures = [t for t in artifact["tasks"] if not t["passed"]]
    assert any(t["taskId"] == forced_id for t in failures)


def test_build_runner_factory(pack: dict, spec: ModelSpec, tmp_path: Path):
    runner = build_runner("mock")
    assert isinstance(runner, MockRunner)
    runner_oc = build_runner(
        "openclaw",
        baseline_config=tmp_path / "missing-config",
        baseline_home=tmp_path / "missing-home",
    )
    assert isinstance(runner_oc, OpenclawRunner)
    with pytest.raises(ValueError):
        build_runner("nope")


def test_openclaw_runner_isolation(pack: dict, spec: ModelSpec, tmp_path: Path):
    """Adapter must seed an isolated OPENCLAW_HOME and never touch the real one."""

    baseline_home = tmp_path / "baseline-home"
    baseline_home.mkdir()
    (baseline_home / "openclaw.json").write_text(
        json.dumps({"agents": {"defaults": {}}}, indent=2),
        encoding="utf-8",
    )
    workspace_dir = baseline_home / "workspace"
    workspace_dir.mkdir()
    sentinel = workspace_dir / "MEMORY.md"
    sentinel.write_text("# baseline memory\n", encoding="utf-8")

    fake_real_openclaw = tmp_path / "real-home" / ".openclaw"
    fake_real_openclaw.mkdir(parents=True)
    real_config = fake_real_openclaw / "openclaw.json"
    real_config.write_text(
        json.dumps({"agents": {"defaults": {"sentinel": "must-not-mutate"}}}),
        encoding="utf-8",
    )

    # Dispatch script: writes a minimal valid run.json proving env isolation.
    script = tmp_path / "fake-dispatch.py"
    script.write_text(
        '#!/usr/bin/env python3\n'
        'import json, os, hashlib, datetime\n'
        'spec_path = os.environ["JAKE_MODEL_SPEC_JSON"]\n'
        'pack_path = os.environ["JAKE_PACK_PATH"]\n'
        'out_dir = os.environ["JAKE_OUT_DIR"]\n'
        'home = os.environ["OPENCLAW_HOME"]\n'
        'cfg = os.environ["OPENCLAW_CONFIG_PATH"]\n'
        'spec = json.load(open(spec_path))\n'
        'pack = json.load(open(pack_path))\n'
        'os.makedirs(out_dir, exist_ok=True)\n'
        'with open(os.path.join(out_dir, "env.json"), "w") as f:\n'
        '    json.dump({"home": home, "config": cfg}, f)\n'
        'canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"))\n'
        'h = hashlib.sha256(canonical.encode("utf-8")).hexdigest()\n'
        'now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)\n'
        'iso = now.isoformat().replace("+00:00", "Z")\n'
        'task_results = []\n'
        'total_max = 0.0\n'
        'for t in pack["tasks"]:\n'
        '    max_score = t["grading"].get("max_score") or t["grading"].get("maxScore") or 10\n'
        '    total_max += max_score\n'
        '    task_results.append({\n'
        '        "taskId": t["id"], "score": max_score, "maxScore": max_score,\n'
        '        "passed": True, "durationMs": 100,\n'
        '    })\n'
        'artifact = {\n'
        '    "schemaVersion": "1",\n'
        '    "runId": pack["pack"] + "-fake-" + h[:8] + "-" + str(int(now.timestamp())),\n'
        '    "startedAt": iso, "completedAt": iso,\n'
        '    "pack": {"pack": pack["pack"], "version": pack["version"], "family": pack["family"]},\n'
        '    "modelSpec": spec,\n'
        '    "runner": {"name": "openclaw", "version": "fake", "adapter": "fake-dispatch"},\n'
        '    "configHash": h,\n'
        '    "hardware": {"host_class": "ci"},\n'
        '    "environment": {},\n'
        '    "tasks": task_results,\n'
        '    "summary": {\n'
        '        "totalScore": total_max, "maxScore": total_max,\n'
        '        "percentage": 100.0,\n'
        '        "tasksPassed": len(task_results), "tasksTotal": len(task_results),\n'
        '        "tasksErrored": 0, "avgTokensPerSecond": 25.0,\n'
        '        "totalDurationMs": 100 * len(task_results),\n'
        '    },\n'
        '}\n'
        'with open(os.path.join(out_dir, "run.json"), "w") as f:\n'
        '    json.dump(artifact, f, indent=2)\n'
    )
    script.chmod(0o755)

    runner = OpenclawRunner(
        baseline_config=baseline_home / "openclaw.json",
        baseline_home=baseline_home,
        dispatch_cmd=[sys.executable, str(script)],
    )
    ctx = runner.prepare(spec, tmp_path / "scratch")
    out_dir = tmp_path / "run"
    artifact = runner.run_pack(pack, spec, ctx, out_dir=out_dir)
    runner.teardown(ctx)

    # 1. Real OPENCLAW config is untouched.
    real_after = json.loads(real_config.read_text(encoding="utf-8"))
    assert real_after == {"agents": {"defaults": {"sentinel": "must-not-mutate"}}}

    # 2. Scratch home was seeded with baseline contents.
    scratch_home = Path(ctx.extras["scratch_home"])
    assert scratch_home.exists()
    assert (scratch_home / "openclaw.json").exists()
    assert (scratch_home / "workspace" / "MEMORY.md").read_text() == "# baseline memory\n"

    # 3. Dispatch script saw isolated env vars.
    env_capture = json.loads((out_dir / "env.json").read_text(encoding="utf-8"))
    assert env_capture["home"] == str(scratch_home)
    assert env_capture["config"] == str(scratch_home / "openclaw.json")

    # 4. Runner re-stamped runner identity.
    assert artifact["runner"]["name"] == "openclaw"
    assert artifact["runner"]["adapter"] == "subprocess"

    # 5. Schema validates.
    errors = validate.validate(artifact, "run-artifact-v1")
    assert errors == [], errors


def test_openclaw_runner_dispatch_failure(spec: ModelSpec, tmp_path: Path):
    bad_script = tmp_path / "bad.sh"
    bad_script.write_text("#!/usr/bin/env bash\nexit 7\n")
    bad_script.chmod(0o755)
    runner = OpenclawRunner(
        baseline_config=None,
        baseline_home=None,
        dispatch_cmd=["bash", str(bad_script)],
    )
    ctx = runner.prepare(spec, tmp_path / "scratch")
    pack = {
        "schemaVersion": "1",
        "pack": "test", "version": "0.0.1", "family": "agent",
        "tasks": [],
    }
    with pytest.raises(RunnerError):
        runner.run_pack(pack, spec, ctx, out_dir=tmp_path / "run")


def test_openclaw_runner_missing_artifact(spec: ModelSpec, tmp_path: Path):
    """If dispatch_cmd succeeds but does not write run.json, raise clearly."""

    silent = tmp_path / "silent.sh"
    silent.write_text("#!/usr/bin/env bash\nexit 0\n")
    silent.chmod(0o755)
    runner = OpenclawRunner(
        baseline_config=None,
        baseline_home=None,
        dispatch_cmd=["bash", str(silent)],
    )
    ctx = runner.prepare(spec, tmp_path / "scratch")
    pack = {
        "schemaVersion": "1",
        "pack": "test", "version": "0.0.1", "family": "agent",
        "tasks": [],
    }
    with pytest.raises(RunnerError) as exc:
        runner.run_pack(pack, spec, ctx, out_dir=tmp_path / "run")
    assert "did not produce" in str(exc.value)


def test_detect_hardware_returns_dict():
    info = detect_hardware()
    assert isinstance(info, dict)
    assert "host_class" in info
