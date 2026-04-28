"""ModelSpec: typed wrapper around the model_spec dict used in run artifacts.

The dict shape is fixed by `schemas/run-artifact-v1.schema.json` ($defs/ModelSpec).
This module gives the harness a small typed surface for building specs,
canonicalizing them for hashing, and expanding matrix definitions into a
list of concrete specs.

A ModelSpec is uniquely identified by its `config_hash`, which is the sha256
over the canonical-JSON form of the spec. That hash is the join key that
lets the dashboard and the comparator line up runs of the same model across
different runners, dates, and machines.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
from dataclasses import dataclass, field
from typing import Any, Iterator


CANONICAL_OPTION_KEYS = (
    "context_length",
    "thinking",
    "temperature",
    "top_p",
)


@dataclass(frozen=True)
class ModelSpec:
    """Concrete model + runtime options for a single benchmark run.

    Use `to_dict()` for serialization into a run artifact. Use `config_hash`
    as the join key across runs.
    """

    provider: str
    model_id: str
    checkpoint_id: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict) -> "ModelSpec":
        if not isinstance(raw, dict):
            raise ValueError(f"ModelSpec must be a dict, got {type(raw).__name__}")
        if not raw.get("provider"):
            raise ValueError("ModelSpec.provider is required")
        if not raw.get("model_id"):
            raise ValueError("ModelSpec.model_id is required")
        return cls(
            provider=str(raw["provider"]),
            model_id=str(raw["model_id"]),
            checkpoint_id=(
                str(raw["checkpoint_id"]) if raw.get("checkpoint_id") else None
            ),
            options=copy.deepcopy(raw.get("options") or {}),
        )

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "provider": self.provider,
            "model_id": self.model_id,
        }
        if self.checkpoint_id:
            out["checkpoint_id"] = self.checkpoint_id
        if self.options:
            out["options"] = copy.deepcopy(self.options)
        return out

    @property
    def slug(self) -> str:
        """Filesystem-safe identifier. Stable across processes."""

        parts = [self.provider, self.model_id]
        if self.checkpoint_id:
            parts.append(self.checkpoint_id)
        thinking = (self.options or {}).get("thinking")
        if thinking:
            parts.append(f"think-{thinking}")
        joined = "_".join(parts)
        return _slugify(joined)

    @property
    def config_hash(self) -> str:
        """sha256 over canonical-JSON of the spec. Used as cross-run join key."""

        canonical = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _slugify(value: str) -> str:
    safe = []
    for ch in value:
        if ch.isalnum() or ch in {"-", "_"}:
            safe.append(ch)
        else:
            safe.append("-")
    out = "".join(safe).strip("-_")
    while "--" in out:
        out = out.replace("--", "-")
    return out or "spec"


def expand_matrix(matrix: dict) -> list[ModelSpec]:
    """Expand a matrix definition into a list of concrete ModelSpecs.

    Matrix shape (YAML/JSON):

        provider: ollama
        model_id: qwen3.5:27b
        checkpoint_ids: [q4_K_M, q5_K_M]      # optional
        options_matrix:                        # optional
          thinking: [off, low, medium]
          context_length: [8192, 32768]

    The Cartesian product of `checkpoint_ids` x options_matrix is taken.
    `options` (singular) merges into every produced spec as fixed keys.
    """

    if not isinstance(matrix, dict):
        raise ValueError(f"matrix must be a dict, got {type(matrix).__name__}")
    provider = matrix.get("provider")
    model_id = matrix.get("model_id")
    if not provider or not model_id:
        raise ValueError("matrix requires provider + model_id")

    checkpoints = matrix.get("checkpoint_ids") or [matrix.get("checkpoint_id")]
    if not checkpoints:
        checkpoints = [None]
    fixed_options = matrix.get("options") or {}
    options_matrix = matrix.get("options_matrix") or {}

    if options_matrix:
        keys = sorted(options_matrix.keys())
        value_lists = [options_matrix[k] for k in keys]
        option_combos: Iterator[tuple] = itertools.product(*value_lists)
        option_dicts = [dict(zip(keys, combo)) for combo in option_combos]
    else:
        option_dicts = [{}]

    specs: list[ModelSpec] = []
    for checkpoint in checkpoints:
        for option_overlay in option_dicts:
            merged: dict[str, Any] = {}
            merged.update(fixed_options)
            merged.update(option_overlay)
            specs.append(
                ModelSpec(
                    provider=provider,
                    model_id=model_id,
                    checkpoint_id=checkpoint or None,
                    options=merged,
                )
            )
    return specs


def load_matrix_file(path: str) -> list[ModelSpec]:
    """Load a matrix or list-of-specs file. JSON only (YAML is optional dep)."""

    raw = json.loads(open(path, encoding="utf-8").read())
    if isinstance(raw, list):
        return [ModelSpec.from_dict(item) for item in raw]
    if isinstance(raw, dict) and raw.get("kind") == "matrix":
        return expand_matrix(raw)
    if isinstance(raw, dict) and raw.get("kind") == "specs":
        return [ModelSpec.from_dict(item) for item in raw.get("specs", [])]
    if isinstance(raw, dict):
        # Bare single spec.
        return [ModelSpec.from_dict(raw)]
    raise ValueError("unrecognized matrix file shape")
