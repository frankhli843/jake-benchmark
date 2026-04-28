"""Matrix runs: execute the same task pack against many ModelSpecs.

Reads a matrix file, expands it into N concrete `ModelSpec`s, and runs
each in its own scratch workspace. Writes one run.json per spec under
`<out_dir>/<spec_slug>__<config_hash8>/run.json`. Also writes a top-level
`<out_dir>/matrix.json` index that records which spec produced which dir.

The actual model execution is delegated to a `Runner` instance, so the
matrix module is fully decoupled from MockRunner / OpenclawRunner.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import validate
from .model_spec import ModelSpec, expand_matrix, load_matrix_file
from .runner import Runner, RunnerError


@dataclass
class MatrixRunResult:
    spec: ModelSpec
    out_dir: Path
    run_artifact: dict | None = None
    error: str | None = None


@dataclass
class MatrixRunSummary:
    matrix_file: str | None
    started_at: str
    completed_at: str
    out_dir: str
    runs: list[MatrixRunResult] = field(default_factory=list)

    def to_index_dict(self) -> dict:
        return {
            "schemaVersion": "1",
            "kind": "matrix-index",
            "matrixFile": self.matrix_file,
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
            "outDir": str(self.out_dir),
            "runs": [
                {
                    "spec": r.spec.to_dict(),
                    "configHash": r.spec.config_hash,
                    "slug": r.spec.slug,
                    "outDir": str(r.out_dir),
                    "ok": r.error is None,
                    "error": r.error,
                    "summary": (
                        r.run_artifact.get("summary") if r.run_artifact else None
                    ),
                }
                for r in self.runs
            ],
        }


def run_matrix(
    *,
    pack: dict,
    runner_factory: Callable[[ModelSpec], Runner],
    specs: list[ModelSpec],
    out_dir: Path,
    matrix_file: str | None = None,
    on_progress: Callable[[ModelSpec, MatrixRunResult], None] | None = None,
    skip_validation: bool = False,
) -> MatrixRunSummary:
    """Execute `specs` against `pack`. Each spec gets its own scratch dir.

    `runner_factory(spec)` returns a fresh runner for that spec. Errors in
    one spec do not abort the matrix; they are recorded on the run result.
    """

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    started = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    summary = MatrixRunSummary(
        matrix_file=matrix_file,
        started_at=started.isoformat().replace("+00:00", "Z"),
        completed_at="",
        out_dir=str(out_dir),
    )

    if not skip_validation:
        pack_errors = validate.validate(pack, "task-pack-v1")
        if pack_errors:
            raise ValueError(
                "Pack failed schema validation: " + "; ".join(pack_errors)
            )

    for spec in specs:
        run_dir_name = f"{spec.slug}__{spec.config_hash[:8]}"
        run_dir = out_dir / run_dir_name
        run_dir.mkdir(parents=True, exist_ok=True)
        result = MatrixRunResult(spec=spec, out_dir=run_dir)
        try:
            runner = runner_factory(spec)
            ctx = runner.prepare(spec, run_dir / "scratch")
            artifact = runner.run_pack(pack, spec, ctx, out_dir=run_dir)
            runner.teardown(ctx)
            if not skip_validation:
                errs = validate.validate(artifact, "run-artifact-v1")
                if errs:
                    raise RunnerError(
                        "Run artifact failed schema validation: "
                        + "; ".join(errs)
                    )
            result.run_artifact = artifact
        except Exception as exc:  # noqa: BLE001 - surface every failure
            result.error = f"{type(exc).__name__}: {exc}"
        summary.runs.append(result)
        if on_progress:
            on_progress(spec, result)

    completed = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    summary.completed_at = completed.isoformat().replace("+00:00", "Z")
    (out_dir / "matrix.json").write_text(
        json.dumps(summary.to_index_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return summary


def load_matrix(path: str | Path) -> list[ModelSpec]:
    return load_matrix_file(str(path))


__all__ = [
    "MatrixRunResult",
    "MatrixRunSummary",
    "expand_matrix",
    "load_matrix",
    "run_matrix",
]
