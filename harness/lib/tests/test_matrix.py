"""Matrix runs end-to-end: expand a matrix file, run via MockRunner, validate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.lib import migrate, validate
from harness.lib.matrix import run_matrix
from harness.lib.model_spec import ModelSpec, expand_matrix
from harness.lib.runner import MockRunner


REPO = Path(__file__).resolve().parents[3]
LEGACY_TASKS = REPO / "harness" / "tasks.json"


@pytest.fixture
def pack() -> dict:
    legacy = json.loads(LEGACY_TASKS.read_text(encoding="utf-8"))
    return migrate.migrate(legacy)


def test_run_matrix_writes_index_and_per_spec_runs(pack: dict, tmp_path: Path):
    matrix = {
        "provider": "mock",
        "model_id": "mock:smoke",
        "options_matrix": {"thinking": ["off", "low"]},
    }
    specs = expand_matrix(matrix)
    summary = run_matrix(
        pack=pack,
        runner_factory=lambda _spec: MockRunner(),
        specs=specs,
        out_dir=tmp_path / "matrix",
    )

    assert len(summary.runs) == 2
    assert all(r.error is None for r in summary.runs)

    index_path = tmp_path / "matrix" / "matrix.json"
    assert index_path.exists()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["kind"] == "matrix-index"
    assert len(index["runs"]) == 2
    config_hashes = {r["configHash"] for r in index["runs"]}
    assert len(config_hashes) == 2

    # Each per-spec run dir contains a valid run.json
    for run_entry in index["runs"]:
        run_dir = Path(run_entry["outDir"])
        assert run_dir.exists()
        artifact = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        errs = validate.validate(artifact, "run-artifact-v1")
        assert errs == [], errs


def test_run_matrix_records_failures_per_spec(pack: dict, tmp_path: Path):
    """If one runner factory raises, the matrix records the failure but
    continues with remaining specs."""

    specs = [
        ModelSpec(provider="mock", model_id="ok"),
        ModelSpec(provider="mock", model_id="bad"),
    ]

    class FailingRunner(MockRunner):
        def run_pack(self, pack, model_spec, ctx, *, out_dir):
            raise RuntimeError("boom")

    def factory(spec: ModelSpec):
        if spec.model_id == "bad":
            return FailingRunner()
        return MockRunner()

    summary = run_matrix(
        pack=pack,
        runner_factory=factory,
        specs=specs,
        out_dir=tmp_path / "matrix",
    )
    ok = [r for r in summary.runs if r.error is None]
    failed = [r for r in summary.runs if r.error]
    assert len(ok) == 1
    assert len(failed) == 1
    assert "boom" in failed[0].error


def test_run_matrix_validates_pack(tmp_path: Path):
    bad_pack = {"not": "a real pack"}
    with pytest.raises(ValueError):
        run_matrix(
            pack=bad_pack,
            runner_factory=lambda _spec: MockRunner(),
            specs=[ModelSpec(provider="mock", model_id="x")],
            out_dir=tmp_path,
        )


def test_run_matrix_per_spec_dir_naming(pack: dict, tmp_path: Path):
    spec = ModelSpec(
        provider="mock",
        model_id="qwen3.5:27b",
        checkpoint_id="q4_K_M",
        options={"thinking": "off"},
    )
    summary = run_matrix(
        pack=pack,
        runner_factory=lambda _spec: MockRunner(),
        specs=[spec],
        out_dir=tmp_path / "matrix",
    )
    run_dir = summary.runs[0].out_dir
    assert spec.config_hash[:8] in run_dir.name
    assert "qwen3" in run_dir.name
