"""The run artifact is the only place a number is allowed to exist."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evald.models_config import load_model_config
from evald.replay.artifact import (
    ARTIFACT_VERSION,
    ModelSummary,
    RunArtifact,
    now_iso,
    pricing_snapshot,
)
from evald.replay.plan import compare_projection

CFG = load_model_config("../../config/models.yaml")


def make_artifact(**overrides: object) -> RunArtifact:
    base: dict[str, object] = {
        "run_id": "run-1",
        "created_at": now_iso(),
        "git_sha": "abc123",
        "seed": 7,
        "corpus_sha256": "f" * 64,
        "corpus_size": 1500,
        "corpus_by_split": {"calibration": 300, "dev": 450, "test": 750},
        "corpus_by_task": {"math_word_problem": 600},
        "models": ["claude-haiku-4-5"],
        "pricing_snapshot": pricing_snapshot(["claude-haiku-4-5"], CFG),
    }
    return RunArtifact.model_validate({**base, **overrides})


def test_round_trips_through_disk(tmp_path: Path) -> None:
    artifact = make_artifact()
    path = artifact.write(tmp_path / "replay-x.json")
    assert RunArtifact.read(path) == artifact


def test_is_versioned_so_a_schema_change_is_detectable() -> None:
    assert make_artifact().artifact_version == ARTIFACT_VERSION


def test_records_the_prices_used_not_just_the_model_names() -> None:
    # A later price change must not silently rewrite what this run cost.
    snap = make_artifact().pricing_snapshot["claude-haiku-4-5"]
    assert snap["input"] == 1.0
    assert snap["output"] == 5.0


def test_records_provenance_needed_to_reproduce_the_run() -> None:
    a = make_artifact()
    for field in ("git_sha", "seed", "corpus_sha256"):
        assert getattr(a, field)


def test_an_aborted_run_says_so(tmp_path: Path) -> None:
    # A truncated run must never be mistakable for a complete one.
    a = make_artifact(status="aborted_budget", abort_reason="spend cap reached: $5.00")
    loaded = RunArtifact.read(a.write(tmp_path / "a.json"))
    assert loaded.status == "aborted_budget"
    assert loaded.abort_reason is not None


def test_defaults_to_complete() -> None:
    assert make_artifact().status == "complete"


def test_rejects_an_unknown_field() -> None:
    with pytest.raises(ValueError):
        make_artifact(totally_made_up=1)


def test_json_is_deterministic_for_diffable_commits(tmp_path: Path) -> None:
    a = make_artifact()
    first = a.write(tmp_path / "1.json").read_text()
    second = a.write(tmp_path / "2.json").read_text()
    assert first == second
    assert json.loads(first)["artifact_version"] == 1


def test_model_summary_carries_the_verifier_result() -> None:
    s = ModelSummary(
        model="claude-haiku-4-5",
        rung="cheap",
        calls_attempted=10,
        calls_succeeded=9,
        calls_failed=1,
        cache_hits=0,
        input_tokens=100,
        output_tokens=200,
        cost_usd=0.01,
        unconfirmed_usage_calls=1,
        verifier_pass_rate=0.75,
        verifier_n=8,
    )
    assert s.verifier_pass_rate == 0.75
    assert s.unconfirmed_usage_calls == 1


def test_compare_projection_reports_the_ratio() -> None:
    # Recording projected vs actual is what makes the estimate improve instead
    # of staying quietly wrong.
    out = compare_projection(projected_usd=10.0, actual_usd=7.5)
    assert out["actual_over_projected"] == 0.75


def test_compare_projection_survives_a_zero_projection() -> None:
    assert compare_projection(0.0, 0.0)["actual_over_projected"] == 0.0
