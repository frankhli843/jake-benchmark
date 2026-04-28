"""Tests for the validator + the schemas themselves."""

from __future__ import annotations

import json

import pytest

from harness.lib import validate


def test_valid_minimal_pack():
    pack = {
        "schemaVersion": "1",
        "pack": "core",
        "version": "1.0.0",
        "family": "tool-free",
        "tasks": [
            {
                "id": "list_reverse",
                "prompt": "Reverse [1,2,3,4,5]",
                "grading": {
                    "type": "exact_match",
                    "expected": ["5, 4, 3, 2, 1"],
                    "maxScore": 5,
                },
            }
        ],
    }
    assert validate.validate(pack, "task-pack-v1") == []


def test_pack_missing_required_fails():
    bad = {"schemaVersion": "1", "tasks": []}
    errors = validate.validate(bad, "task-pack-v1")
    assert errors, "expected validation errors"


def test_pack_wrong_schema_version_fails():
    bad = {
        "schemaVersion": "2",
        "pack": "x",
        "version": "1.0.0",
        "family": "agent",
        "tasks": [{"id": "x", "prompt": "p", "grading": {"type": "output_check", "max_score": 1}}],
    }
    errors = validate.validate(bad, "task-pack-v1")
    assert any("schemaVersion" in e or "const" in e for e in errors), errors


def test_pack_unknown_grading_type_fails():
    bad = {
        "schemaVersion": "1",
        "pack": "x",
        "version": "1.0.0",
        "family": "agent",
        "tasks": [
            {
                "id": "x",
                "prompt": "p",
                "grading": {"type": "fortune_cookie", "max_score": 1},
            }
        ],
    }
    errors = validate.validate(bad, "task-pack-v1")
    assert errors, "unknown grading type should fail"
