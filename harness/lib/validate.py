"""Schema validation for task packs, run artifacts, and failure reports.

Uses jsonschema if available; falls back to a small built-in validator
that covers the constraints this codebase actually uses (required props,
type checks, const, enum, pattern, oneOf discriminator). The fallback
keeps the harness usable in lightweight environments without pip.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"


def load_schema(name: str) -> dict:
    path = SCHEMAS_DIR / f"{name}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def validate(instance: Any, schema_name: str) -> list[str]:
    """Validate `instance` against the named schema.

    Returns a list of human-readable error messages. Empty list means valid.
    """
    schema = load_schema(schema_name)

    try:
        import jsonschema  # type: ignore
    except Exception:
        return _fallback_validate(instance, schema, "$")

    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    return [f"{'/'.join(str(p) for p in e.path) or '$'}: {e.message}" for e in errors]


# --- Built-in fallback validator ---


def _fallback_validate(instance: Any, schema: dict, path: str) -> list[str]:
    errors: list[str] = []

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: expected one of {schema['enum']!r}, got {instance!r}")

    t = schema.get("type")
    if t == "object":
        if not isinstance(instance, dict):
            return errors + [f"{path}: expected object, got {type(instance).__name__}"]
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(f"{path}: missing required property {req!r}")
        props = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for k, v in instance.items():
            if k in props:
                errors.extend(_fallback_validate(v, props[k], f"{path}.{k}"))
            elif additional is False:
                errors.append(f"{path}: unexpected property {k!r}")
    elif t == "array":
        if not isinstance(instance, list):
            return errors + [f"{path}: expected array, got {type(instance).__name__}"]
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: expected at least {schema['minItems']} items")
        items = schema.get("items")
        if items:
            for i, v in enumerate(instance):
                errors.extend(_fallback_validate(v, items, f"{path}[{i}]"))
    elif t == "string":
        if not isinstance(instance, str):
            return errors + [f"{path}: expected string, got {type(instance).__name__}"]
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: does not match pattern {schema['pattern']!r}")
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
    elif t == "number":
        if not isinstance(instance, (int, float)) or isinstance(instance, bool):
            return errors + [f"{path}: expected number, got {type(instance).__name__}"]
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
    elif t == "integer":
        if not isinstance(instance, int) or isinstance(instance, bool):
            return errors + [f"{path}: expected integer, got {type(instance).__name__}"]
    elif t == "boolean":
        if not isinstance(instance, bool):
            errors.append(f"{path}: expected boolean")

    if "oneOf" in schema:
        sub_errors = [_fallback_validate(instance, s, path) for s in schema["oneOf"]]
        passing = [s for s in sub_errors if not s]
        if len(passing) != 1:
            errors.append(
                f"{path}: oneOf expected exactly one match, got {len(passing)}"
            )

    if "$defs" in schema:
        # local refs handled minimally below
        pass

    if "$ref" in schema:
        ref = schema["$ref"]
        if ref.startswith("#/$defs/"):
            top = _ref_root_for(path)
            if top is not None:
                defs = top.get("$defs", {})
                key = ref.split("/")[-1]
                if key in defs:
                    errors.extend(_fallback_validate(instance, defs[key], path))
        # external refs (other schema files) are skipped in the fallback validator.

    return errors


# Track the top-level schema for $defs lookup. The fallback only supports
# in-file $defs and external refs are tolerated.
_TOP_SCHEMA: dict | None = None


def _ref_root_for(_path: str) -> dict | None:
    return _TOP_SCHEMA


def _set_top(schema: dict) -> None:
    global _TOP_SCHEMA
    _TOP_SCHEMA = schema


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("schema", choices=["task-pack-v1", "run-artifact-v1", "failure-report-v1"])
    parser.add_argument("file", type=Path)
    args = parser.parse_args(argv)

    instance = json.loads(args.file.read_text(encoding="utf-8"))
    errors = validate(instance, args.schema)
    if errors:
        for e in errors:
            print(f"INVALID: {e}")
        return 1
    print(f"OK: {args.file} validates against {args.schema}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
