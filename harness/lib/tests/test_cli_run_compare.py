"""CLI-level integration: run + matrix + compare via `jake-bench` argv."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from harness.lib import cli, migrate


REPO = Path(__file__).resolve().parents[3]
LEGACY_TASKS = REPO / "harness" / "tasks.json"


@pytest.fixture
def pack_path(tmp_path: Path) -> Path:
    legacy = json.loads(LEGACY_TASKS.read_text(encoding="utf-8"))
    pack = migrate.migrate(legacy)
    p = tmp_path / "pack.json"
    p.write_text(json.dumps(pack, indent=2), encoding="utf-8")
    return p


def test_cli_run_mock(pack_path: Path, tmp_path: Path):
    out = tmp_path / "run-a"
    rc = cli.main([
        "run",
        "--pack", str(pack_path),
        "--out", str(out),
        "--runner", "mock",
        "--spec", "mock:smoke",
        "--thinking", "off",
    ])
    assert rc == 0
    artifact = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert artifact["runner"]["name"] == "mock"


def test_cli_matrix_mock(pack_path: Path, tmp_path: Path):
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(
        json.dumps({
            "kind": "matrix",
            "provider": "mock",
            "model_id": "mock:smoke",
            "options_matrix": {"thinking": ["off", "low"]},
        }),
        encoding="utf-8",
    )
    out = tmp_path / "matrix-out"
    rc = cli.main([
        "matrix",
        "--pack", str(pack_path),
        "--out", str(out),
        "--runner", "mock",
        "--matrix", str(matrix_path),
    ])
    assert rc == 0
    index = json.loads((out / "matrix.json").read_text(encoding="utf-8"))
    assert index["kind"] == "matrix-index"
    assert len(index["runs"]) == 2


def test_cli_compare_two_runs(pack_path: Path, tmp_path: Path):
    a_dir = tmp_path / "a"
    b_dir = tmp_path / "b"
    rc = cli.main([
        "run", "--pack", str(pack_path), "--out", str(a_dir),
        "--runner", "mock", "--spec", "mock:smoke",
    ])
    assert rc == 0
    rc = cli.main([
        "run", "--pack", str(pack_path), "--out", str(b_dir),
        "--runner", "mock", "--spec", "mock:smoke",
    ])
    assert rc == 0
    out_compare = tmp_path / "compare"
    rc = cli.main([
        "compare", str(a_dir), str(b_dir), "--out", str(out_compare),
    ])
    # Two identical mock runs => no regressions => exit 0.
    assert rc == 0
    assert (out_compare / "compare.md").exists()
    cmp = json.loads((out_compare / "compare.json").read_text(encoding="utf-8"))
    assert cmp["regressionCount"] == 0
