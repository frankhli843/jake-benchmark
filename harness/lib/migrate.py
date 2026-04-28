"""Deterministic migration: legacy bare-list tasks.json -> task-pack v1.

The original `harness/tasks.json` is a bare JSON array of 23 tasks with
agent-style grading (output_check, multi_check, artifact_check, etc.).
v1 wraps it as a versioned pack with `family: "agent"`. The migration
preserves all original fields verbatim; it only adds metadata.

Idempotent: passing an already-migrated pack returns it unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1"
DEFAULT_PACK_NAME = "jake-agent"
DEFAULT_PACK_VERSION = "1.0.0"


def is_v1_pack(obj: Any) -> bool:
    return (
        isinstance(obj, dict)
        and obj.get("schemaVersion") == SCHEMA_VERSION
        and "tasks" in obj
        and "family" in obj
    )


def migrate(
    legacy: Any,
    *,
    pack_name: str = DEFAULT_PACK_NAME,
    pack_version: str = DEFAULT_PACK_VERSION,
    description: str = "Jake agent benchmark task pack (migrated from bare-list tasks.json).",
) -> dict:
    """Wrap a legacy bare-list tasks.json into a v1 pack.

    Accepts:
      - bare list of task dicts (legacy jake format) -> wraps as agent pack
      - already-v1 pack -> returns unchanged

    Raises ValueError on unrecognized input.
    """
    if is_v1_pack(legacy):
        return legacy

    if not isinstance(legacy, list):
        raise ValueError(
            "Unrecognized task pack: expected a bare list of tasks or a v1 pack object."
        )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "pack": pack_name,
        "version": pack_version,
        "family": "agent",
        "description": description,
        "tasks": list(legacy),
    }


def migrate_file(src: Path, dst: Path, **kwargs) -> dict:
    """Read a legacy file, migrate, write the v1 pack to `dst`. Returns the pack."""
    src = Path(src)
    dst = Path(dst)
    legacy = json.loads(src.read_text(encoding="utf-8"))
    pack = migrate(legacy, **kwargs)
    dst.write_text(json.dumps(pack, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return pack


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src", type=Path, help="Path to legacy tasks.json")
    parser.add_argument("dst", type=Path, help="Output path for the v1 pack")
    parser.add_argument("--pack-name", default=DEFAULT_PACK_NAME)
    parser.add_argument("--pack-version", default=DEFAULT_PACK_VERSION)
    args = parser.parse_args(argv)

    pack = migrate_file(
        args.src,
        args.dst,
        pack_name=args.pack_name,
        pack_version=args.pack_version,
    )
    print(
        f"Migrated {len(pack['tasks'])} tasks into pack '{pack['pack']}' "
        f"v{pack['version']} (family={pack['family']}). Wrote {args.dst}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
