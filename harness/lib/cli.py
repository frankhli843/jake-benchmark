"""jake-bench CLI: validate, migrate, report, smoke.

This is the stable user surface. The heavy `run` subcommand depends on
the runner abstraction, which is a follow-up subtask. The current
subcommands are sufficient to validate packs, migrate the legacy
tasks.json, exercise the MockRunner, and generate sanitized failure
reports end-to-end.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import failure_report, migrate, mock_runner, sanitize, validate


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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
