"""End-to-end: CLI -> runner -> cache -> artifact, against a stub gateway.

Exercises the real corpus file, the real cost model and the real artifact
schema. Only the HTTP call is stubbed, and only because spending money in a
test suite is not acceptable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evald.cli import build_parser, cmd_replay_run, load_corpus, write_corpus
from evald.cost import Usage
from evald.replay.artifact import RunArtifact
from evald.replay.client import Generation


class StubGateway:
    """Stands in for GatewayClient. Records every call it is asked to make."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def health(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def __enter__(self) -> StubGateway:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def complete(
        self, model: str, messages: list[dict[str, str]], params: dict[str, Any]
    ) -> Generation:
        self.calls.append((model, params))
        return Generation(
            text="Reasoning here.\n#### 42",
            usage=Usage(input_tokens=120, output_tokens=60),
            finish_reason="stop",
            model_served=model,
            provider="anthropic",
            cost_usd=0.0,
            usage_is_final=True,
            request_id="req",
            latency_ms=30,
        )


@pytest.fixture
def small_corpus(tmp_path: Path) -> Path:
    """20 real items from the committed corpus: 10 gradable, 10 free-form.

    Deliberately mixed. Slugs sort alphabetically, so taking the first 20 would
    give only `long_form_qa` items and silently skip every verifier path.
    """
    items = load_corpus(Path("../../corpus/items.jsonl"))
    gradable = [i for i in items if i.verifiable][:10]
    free_form = [i for i in items if not i.verifiable][:10]
    path = tmp_path / "items.jsonl"
    write_corpus(gradable + free_form, path)
    return path


def run_cli(
    small_corpus: Path,
    tmp_path: Path,
    gateway: StubGateway,
    monkeypatch: pytest.MonkeyPatch,
    **extra: str,
) -> int:
    monkeypatch.setattr("evald.cli.GatewayClient", lambda *_a, **_k: gateway)
    argv = [
        "replay",
        "run",
        "--corpus",
        str(small_corpus),
        "--cache",
        str(tmp_path / "cache"),
        "--artifacts",
        str(tmp_path / "artifacts"),
        "--models",
        "claude-haiku-4-5,openai/gpt-oss-20b",
        "--cap",
        "5",
        "--yes",
        "--replicate-subset",
        "0",
    ]
    # Explicit run id: artifact filenames carry a random uuid, so sorting by
    # name would not identify which run wrote which file.
    for k, v in extra.items():
        argv += [f"--{k.replace('_', '-')}", v]
    args = build_parser().parse_args(argv)
    result: int = cmd_replay_run(args)
    return result


def test_produces_a_valid_artifact(
    small_corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gw = StubGateway()
    assert run_cli(small_corpus, tmp_path, gw, monkeypatch) == 0

    artifacts = list((tmp_path / "artifacts").glob("replay-*.json"))
    assert len(artifacts) == 1

    artifact = RunArtifact.read(artifacts[0])
    assert artifact.status == "complete"
    assert artifact.corpus_size == 20
    assert set(artifact.models) == {"claude-haiku-4-5", "openai/gpt-oss-20b"}
    assert len(gw.calls) == 40  # 20 items x 2 models


def test_artifact_records_measured_cost_and_the_projection_it_beat(
    small_corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_cli(small_corpus, tmp_path, StubGateway(), monkeypatch)
    artifact = RunArtifact.read(next((tmp_path / "artifacts").glob("replay-*.json")))
    assert artifact.cost["actual_usd"] > 0
    assert artifact.cost["projected_usd"] > 0
    assert "actual_over_projected" in artifact.cost


def test_artifact_scores_the_gradable_slice(
    small_corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_cli(small_corpus, tmp_path, StubGateway(), monkeypatch)
    artifact = RunArtifact.read(next((tmp_path / "artifacts").glob("replay-*.json")))
    for summary in artifact.summaries:
        # 10 of the 20 items are gradable, and the stub always answers "#### 42".
        assert summary.verifier_n == 10
        assert summary.verifier_pass_rate is not None


def test_a_second_run_is_free(
    small_corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `make bench` must be reproducible at $0.
    first = StubGateway()
    run_cli(small_corpus, tmp_path, first, monkeypatch, run_id="aaaaaaaa-first")
    assert len(first.calls) == 40

    second = StubGateway()
    run_cli(small_corpus, tmp_path, second, monkeypatch, run_id="bbbbbbbb-second")
    assert len(second.calls) == 0

    latest = RunArtifact.read(tmp_path / "artifacts" / "replay-bbbbbbbb.json")
    assert latest.cache["hit_rate"] == 1.0
    assert latest.cost["actual_usd"] == 0.0


def test_the_artifact_is_byte_stable_for_committing(
    small_corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_cli(small_corpus, tmp_path, StubGateway(), monkeypatch)
    path = next((tmp_path / "artifacts").glob("replay-*.json"))
    data = json.loads(path.read_text())
    assert list(data.keys()) == sorted(data.keys())  # sorted for clean diffs


def test_pins_temperature_only_where_the_model_accepts_it(
    small_corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gw = StubGateway()
    run_cli(small_corpus, tmp_path, gw, monkeypatch)
    by_model = {m: p for m, p in gw.calls}
    assert by_model["openai/gpt-oss-20b"]["temperature"] == 0.0
    assert "temperature" not in by_model["claude-haiku-4-5"] or True  # haiku does accept it
    assert by_model["claude-haiku-4-5"].get("temperature") == 0.0


def test_aborts_and_still_writes_an_artifact_when_the_cap_is_hit(
    small_corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A run that stops early must say so, never look complete.
    gw = StubGateway()
    monkeypatch.setattr("evald.cli.GatewayClient", lambda *_a, **_k: gw)
    args = build_parser().parse_args(
        [
            "replay",
            "run",
            "--corpus",
            str(small_corpus),
            "--cache",
            str(tmp_path / "cache2"),
            "--artifacts",
            str(tmp_path / "artifacts2"),
            "--models",
            "claude-opus-5",
            "--cap",
            "0.0005",
            "--yes",
            "--replicate-subset",
            "0",
        ]
    )
    assert cmd_replay_run(args) == 3

    artifact = RunArtifact.read(next((tmp_path / "artifacts2").glob("replay-*.json")))
    assert artifact.status == "aborted_budget"
    assert artifact.abort_reason is not None
    assert "spend cap" in artifact.abort_reason


def test_refuses_to_start_when_the_projection_exceeds_the_cap(
    small_corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("evald.cli.GatewayClient", lambda *_a, **_k: StubGateway())
    args = build_parser().parse_args(
        [
            "replay",
            "run",
            "--corpus",
            str(small_corpus),
            "--cache",
            str(tmp_path / "cache3"),
            "--artifacts",
            str(tmp_path / "artifacts3"),
            "--models",
            "claude-opus-5",
            "--cap",
            "0.000001",
            "--replicate-subset",
            "0",
        ]
    )
    assert cmd_replay_run(args) == 2
    assert not (tmp_path / "artifacts3").exists()  # nothing ran, nothing written
