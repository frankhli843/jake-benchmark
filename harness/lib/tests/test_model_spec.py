"""ModelSpec semantics: serialization round-trip, hashing stability, slugging,
and matrix expansion."""

from __future__ import annotations

import pytest

from harness.lib.model_spec import ModelSpec, expand_matrix


def test_round_trip_minimal_spec():
    spec = ModelSpec(provider="ollama", model_id="qwen3.5:27b")
    out = spec.to_dict()
    assert out == {"provider": "ollama", "model_id": "qwen3.5:27b"}
    rt = ModelSpec.from_dict(out)
    assert rt.config_hash == spec.config_hash


def test_round_trip_with_options_and_checkpoint():
    spec = ModelSpec(
        provider="ollama",
        model_id="qwen3.5:27b",
        checkpoint_id="q4_K_M",
        options={"thinking": "medium", "context_length": 32768},
    )
    out = spec.to_dict()
    rt = ModelSpec.from_dict(out)
    assert rt.config_hash == spec.config_hash
    assert rt.options == spec.options


def test_config_hash_is_deterministic_across_option_order():
    a = ModelSpec(
        provider="ollama",
        model_id="qwen3.5:27b",
        options={"thinking": "low", "context_length": 8192},
    )
    b = ModelSpec(
        provider="ollama",
        model_id="qwen3.5:27b",
        options={"context_length": 8192, "thinking": "low"},
    )
    assert a.config_hash == b.config_hash


def test_config_hash_changes_when_checkpoint_changes():
    a = ModelSpec(provider="ollama", model_id="x", checkpoint_id="q4")
    b = ModelSpec(provider="ollama", model_id="x", checkpoint_id="q5")
    assert a.config_hash != b.config_hash


def test_config_hash_format():
    spec = ModelSpec(provider="ollama", model_id="x")
    assert len(spec.config_hash) == 64
    int(spec.config_hash, 16)  # hex


def test_slug_filesystem_safe():
    spec = ModelSpec(
        provider="ollama",
        model_id="qwen3.5:27b",
        checkpoint_id="q4_K_M",
        options={"thinking": "high"},
    )
    slug = spec.slug
    for ch in slug:
        assert ch.isalnum() or ch in {"-", "_"}, slug
    assert "qwen3" in slug
    assert "q4_K_M" in slug
    assert "think-high" in slug


def test_from_dict_requires_provider_and_model_id():
    with pytest.raises(ValueError):
        ModelSpec.from_dict({"model_id": "x"})
    with pytest.raises(ValueError):
        ModelSpec.from_dict({"provider": "x"})


def test_expand_matrix_single_dimension():
    matrix = {
        "provider": "ollama",
        "model_id": "qwen3.5:27b",
        "options_matrix": {"thinking": ["off", "low", "medium"]},
    }
    specs = expand_matrix(matrix)
    assert len(specs) == 3
    thinkings = sorted(s.options["thinking"] for s in specs)
    assert thinkings == ["low", "medium", "off"]


def test_expand_matrix_cartesian():
    matrix = {
        "provider": "ollama",
        "model_id": "qwen3.5:27b",
        "checkpoint_ids": ["q4_K_M", "q5_K_M"],
        "options_matrix": {
            "thinking": ["off", "low"],
            "context_length": [8192, 32768],
        },
    }
    specs = expand_matrix(matrix)
    # 2 checkpoints * 2 thinkings * 2 contexts = 8
    assert len(specs) == 8
    # All hashes unique
    assert len({s.config_hash for s in specs}) == 8


def test_expand_matrix_fixed_options_merge():
    matrix = {
        "provider": "ollama",
        "model_id": "x",
        "options": {"temperature": 0.0},
        "options_matrix": {"thinking": ["off", "low"]},
    }
    specs = expand_matrix(matrix)
    assert len(specs) == 2
    for s in specs:
        assert s.options["temperature"] == 0.0
    assert sorted(s.options["thinking"] for s in specs) == ["low", "off"]


def test_expand_matrix_requires_provider_and_model_id():
    with pytest.raises(ValueError):
        expand_matrix({"provider": "ollama"})


def test_expand_matrix_no_options_matrix_yields_one_spec():
    matrix = {"provider": "ollama", "model_id": "x"}
    specs = expand_matrix(matrix)
    assert len(specs) == 1
    assert specs[0].provider == "ollama"
