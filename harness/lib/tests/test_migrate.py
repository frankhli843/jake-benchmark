"""Tests for the bare-list -> v1 task pack migration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.lib import migrate, validate


REPO = Path(__file__).resolve().parents[3]
LEGACY_TASKS = REPO / "harness" / "tasks.json"


def test_legacy_tasks_file_exists():
    assert LEGACY_TASKS.exists(), f"missing legacy fixture: {LEGACY_TASKS}"


def test_migrate_real_legacy_pack_validates():
    legacy = json.loads(LEGACY_TASKS.read_text(encoding="utf-8"))
    pack = migrate.migrate(legacy)
    errors = validate.validate(pack, "task-pack-v1")
    assert errors == [], f"migrated pack does not validate: {errors}"
    assert pack["family"] == "agent"
    assert pack["schemaVersion"] == "1"
    assert len(pack["tasks"]) == len(legacy)


def test_migrate_idempotent():
    legacy = json.loads(LEGACY_TASKS.read_text(encoding="utf-8"))
    once = migrate.migrate(legacy)
    twice = migrate.migrate(once)
    assert once == twice


def test_migrate_rejects_unknown_input():
    with pytest.raises(ValueError):
        migrate.migrate({"random": "object", "no": "tasks"})


def test_migrate_preserves_task_order_and_ids():
    legacy = json.loads(LEGACY_TASKS.read_text(encoding="utf-8"))
    pack = migrate.migrate(legacy)
    assert [t["id"] for t in pack["tasks"]] == [t["id"] for t in legacy]


def test_migrate_file_round_trip(tmp_path: Path):
    src = tmp_path / "legacy.json"
    src.write_text(LEGACY_TASKS.read_text(encoding="utf-8"), encoding="utf-8")
    dst = tmp_path / "pack.json"
    pack = migrate.migrate_file(src, dst)
    assert dst.exists()
    parsed = json.loads(dst.read_text(encoding="utf-8"))
    assert parsed == pack
    errors = validate.validate(parsed, "task-pack-v1")
    assert errors == []
