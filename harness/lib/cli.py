"""jake-bench CLI: validate, migrate, report, smoke, run, matrix, compare.

This is the stable user surface for the harness. All mutating operations
go through schema validation. The `run` and `matrix` subcommands route
through the runner abstraction (`lib.runner`) so the same CLI works for
mock testing, the OpenClaw adapter, or future runners (gemmaclaw, vllm).

The CLI is the docker entrypoint. Run it without args to see the help.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import (
    compare as _compare,
    failure_report,
    matrix as _matrix,
    migrate,
    mock_runner,
    sanitize,
    validate,
)
from .model_spec import ModelSpec, expand_matrix, load_matrix_file
from .runner import build_runner


def _cmd_validate(args: argparse.Namespace) -> int:
    instance = json.loads(Path(args.file).read_text(encoding="utf-8"))
    errors = validate.validate(instance, args.schema)
    if errors:
        for e in errors:
            print(f"INVALID: {e}", file=sys.stderr)
        return 1
    print(f"OK: {args.file} validates against {args.schema}")
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    pack = migrate.migrate_file(
        Path(args.src),
        Path(args.dst),
        pack_name=args.pack_name,
        pack_version=args.pack_version,
    )
    errors = validate.validate(pack, "task-pack-v1")
    if errors:
        for e in errors:
            print(f"INVALID after migration: {e}", file=sys.stderr)
        return 1
    print(
        f"Migrated {len(pack['tasks'])} tasks to {args.dst} "
        f"(pack='{pack['pack']}' v{pack['version']})."
    )
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    run_artifact = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    errors = validate.validate(run_artifact, "run-artifact-v1")
    if errors and not args.skip_validation:
        for e in errors:
            print(f"INVALID run artifact: {e}", file=sys.stderr)
        return 1
    report = failure_report.build_report(
        run_artifact, run_dir=run_dir, sanitize_profile=args.sanitize
    )
    out_dir = Path(args.out) if args.out else run_dir
    failure_report.write_report(report, out_dir)
    print(f"Wrote {out_dir/'report.json'}")
    print(f"Wrote {out_dir/'report.md'}")
    return 0


def _cmd_smoke(args: argparse.Namespace) -> int:
    pack_path = Path(args.pack)
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    pack_errors = validate.validate(pack, "task-pack-v1")
    if pack_errors:
        for e in pack_errors:
            print(f"INVALID pack: {e}", file=sys.stderr)
        return 1
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_spec = {
        "provider": "mock",
        "model_id": "mock:smoke",
        "options": {"context_length": 8192, "thinking": "off"},
    }
    run_artifact = mock_runner.run(pack, model_spec, out_dir)
    run_errors = validate.validate(run_artifact, "run-artifact-v1")
    if run_errors:
        for e in run_errors:
            print(f"INVALID run artifact: {e}", file=sys.stderr)
        return 1
    report = failure_report.build_report(
        run_artifact, run_dir=out_dir, sanitize_profile=args.sanitize
    )
    failure_report.write_report(report, out_dir)
    print(f"Smoke run OK. {out_dir/'run.json'}, {out_dir/'report.md'} written.")
    return 0


def _cmd_sanitize(args: argparse.Namespace) -> int:
    text = Path(args.file).read_text(encoding="utf-8")
    out = sanitize.sanitize(text, profile=args.profile)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(out, end="")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    """Run a single ModelSpec against a pack via the runner abstraction."""

    pack = json.loads(Path(args.pack).read_text(encoding="utf-8"))
    pack_errors = validate.validate(pack, "task-pack-v1")
    if pack_errors and not args.skip_validation:
        for e in pack_errors:
            print(f"INVALID pack: {e}", file=sys.stderr)
        return 1

    spec = _resolve_single_spec(args)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    runner = build_runner(
        args.runner,
        baseline_config=Path(args.baseline_config) if args.baseline_config else None,
        baseline_home=Path(args.baseline_home) if args.baseline_home else None,
        dispatch_cmd=args.dispatch_cmd,
        timeout_seconds=args.timeout,
    )
    ctx = runner.prepare(spec, out_dir / "scratch")
    artifact = runner.run_pack(pack, spec, ctx, out_dir=out_dir)
    runner.teardown(ctx)

    (out_dir / "run.json").write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if not args.skip_validation:
        run_errors = validate.validate(artifact, "run-artifact-v1")
        if run_errors:
            for e in run_errors:
                print(f"INVALID run artifact: {e}", file=sys.stderr)
            return 1

    print(f"Run OK. wrote {out_dir/'run.json'}")
    return 0


def _cmd_matrix(args: argparse.Namespace) -> int:
    """Run a matrix file (or expanded matrix) against a pack."""

    pack = json.loads(Path(args.pack).read_text(encoding="utf-8"))
    if args.matrix:
        specs = load_matrix_file(args.matrix)
    elif args.spec:
        specs = [_resolve_single_spec(args)]
    else:
        print("matrix: pass --matrix <file> or --spec <model-id>", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    def factory(_spec: ModelSpec):
        return build_runner(
            args.runner,
            baseline_config=Path(args.baseline_config) if args.baseline_config else None,
            baseline_home=Path(args.baseline_home) if args.baseline_home else None,
            dispatch_cmd=args.dispatch_cmd,
            timeout_seconds=args.timeout,
        )

    summary = _matrix.run_matrix(
        pack=pack,
        runner_factory=factory,
        specs=specs,
        out_dir=out_dir,
        matrix_file=args.matrix,
        skip_validation=args.skip_validation,
        on_progress=lambda s, r: print(
            f"  [{s.config_hash[:8]}] {s.slug}: "
            + ("OK" if r.error is None else f"FAILED ({r.error})")
        ),
    )

    failed = [r for r in summary.runs if r.error]
    print(f"Matrix done: {len(summary.runs)} runs, {len(failed)} failed.")
    print(f"Index: {out_dir/'matrix.json'}")
    return 1 if failed else 0


def _cmd_compare(args: argparse.Namespace) -> int:
    """Compare two run artifacts."""

    a_path = _resolve_run_path(Path(args.a))
    b_path = _resolve_run_path(Path(args.b))
    report = _compare.compare_run_files(a_path, b_path)
    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "compare.json").write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (out_dir / "compare.md").write_text(report.to_markdown(), encoding="utf-8")
        print(f"Wrote {out_dir/'compare.json'}")
        print(f"Wrote {out_dir/'compare.md'}")
    else:
        print(report.to_markdown())
    return 1 if report.regressions else 0


def _resolve_single_spec(args: argparse.Namespace) -> ModelSpec:
    if args.spec_json:
        raw = json.loads(Path(args.spec_json).read_text(encoding="utf-8"))
        return ModelSpec.from_dict(raw)
    if not args.spec:
        raise SystemExit("missing --spec or --spec-json")
    provider, _, model = args.spec.partition(":")
    if not model:
        raise SystemExit(f"--spec must be 'provider:model_id', got {args.spec!r}")
    options: dict = {}
    if args.thinking:
        options["thinking"] = args.thinking
    if args.context_length:
        options["context_length"] = int(args.context_length)
    return ModelSpec(
        provider=provider,
        model_id=model,
        checkpoint_id=args.checkpoint or None,
        options=options,
    )


def _resolve_run_path(p: Path) -> Path:
    if p.is_dir():
        return p / "run.json"
    return p


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jake-bench")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_validate = sub.add_parser("validate", help="Validate a JSON file against a schema.")
    p_validate.add_argument(
        "schema", choices=["task-pack-v1", "run-artifact-v1", "failure-report-v1"]
    )
    p_validate.add_argument("file")
    p_validate.set_defaults(func=_cmd_validate)

    p_migrate = sub.add_parser("migrate", help="Wrap legacy tasks.json into a v1 pack.")
    p_migrate.add_argument("src")
    p_migrate.add_argument("dst")
    p_migrate.add_argument("--pack-name", default=migrate.DEFAULT_PACK_NAME)
    p_migrate.add_argument("--pack-version", default=migrate.DEFAULT_PACK_VERSION)
    p_migrate.set_defaults(func=_cmd_migrate)

    p_report = sub.add_parser("report", help="Generate failure report from a run dir.")
    p_report.add_argument("run_dir")
    p_report.add_argument("--out", default=None)
    p_report.add_argument(
        "--sanitize", choices=["none", "internal", "public"], default="public"
    )
    p_report.add_argument("--skip-validation", action="store_true")
    p_report.set_defaults(func=_cmd_report)

    p_smoke = sub.add_parser("smoke", help="Run the MockRunner against a pack and emit reports.")
    p_smoke.add_argument("pack")
    p_smoke.add_argument("--out", required=True)
    p_smoke.add_argument(
        "--sanitize", choices=["none", "internal", "public"], default="public"
    )
    p_smoke.set_defaults(func=_cmd_smoke)

    p_sanitize = sub.add_parser("sanitize", help="Apply redaction to a text file.")
    p_sanitize.add_argument("file")
    p_sanitize.add_argument(
        "--profile", choices=["none", "internal", "public"], default="public"
    )
    p_sanitize.add_argument("--out", default=None)
    p_sanitize.set_defaults(func=_cmd_sanitize)

    p_run = sub.add_parser("run", help="Run a single ModelSpec against a pack.")
    p_run.add_argument("--pack", required=True)
    p_run.add_argument("--out", required=True)
    p_run.add_argument("--runner", choices=["mock", "openclaw"], default="mock")
    p_run.add_argument("--spec", help="provider:model_id (e.g. ollama:qwen3.5:27b).")
    p_run.add_argument("--spec-json", help="Path to a JSON ModelSpec file.")
    p_run.add_argument("--checkpoint", default=None)
    p_run.add_argument("--thinking", choices=["off", "low", "medium", "high"])
    p_run.add_argument("--context-length", type=int)
    p_run.add_argument("--baseline-config", default=None)
    p_run.add_argument("--baseline-home", default=None)
    p_run.add_argument("--dispatch-cmd", nargs="*", default=None)
    p_run.add_argument("--timeout", type=int, default=7200)
    p_run.add_argument("--skip-validation", action="store_true")
    p_run.set_defaults(func=_cmd_run)

    p_matrix = sub.add_parser("matrix", help="Run a matrix of ModelSpecs against a pack.")
    p_matrix.add_argument("--pack", required=True)
    p_matrix.add_argument("--out", required=True)
    p_matrix.add_argument("--matrix", help="Path to a matrix file.")
    p_matrix.add_argument("--runner", choices=["mock", "openclaw"], default="mock")
    p_matrix.add_argument("--spec", default=None)
    p_matrix.add_argument("--spec-json", default=None)
    p_matrix.add_argument("--checkpoint", default=None)
    p_matrix.add_argument("--thinking", choices=["off", "low", "medium", "high"])
    p_matrix.add_argument("--context-length", type=int)
    p_matrix.add_argument("--baseline-config", default=None)
    p_matrix.add_argument("--baseline-home", default=None)
    p_matrix.add_argument("--dispatch-cmd", nargs="*", default=None)
    p_matrix.add_argument("--timeout", type=int, default=7200)
    p_matrix.add_argument("--skip-validation", action="store_true")
    p_matrix.set_defaults(func=_cmd_matrix)

    p_compare = sub.add_parser("compare", help="Compare two run artifacts.")
    p_compare.add_argument("a", help="Path to run.json (or run dir) A")
    p_compare.add_argument("b", help="Path to run.json (or run dir) B")
    p_compare.add_argument("--out", default=None, help="Optional dir to write report files.")
    p_compare.set_defaults(func=_cmd_compare)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
