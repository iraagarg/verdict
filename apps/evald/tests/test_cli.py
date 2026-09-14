"""CLI wiring: the projection gate must stand between a command and any spend."""

from __future__ import annotations

from pathlib import Path

import pytest

from evald.cli import _replicate_map, build_parser, load_corpus, write_corpus
from evald.corpus.schema import CorpusItem

CORPUS = Path("../../corpus/items.jsonl")


def item(slug: str) -> CorpusItem:
    return CorpusItem(
        slug=slug,
        task_type="summarization",
        split="test",
        verifiable=False,
        messages=[{"role": "user", "content": f"article {slug}"}],
        source_dataset="abisee/cnn_dailymail",
        source_id=slug,
        source_license="Apache-2.0",
    )


def test_cap_is_mandatory_on_every_spending_command() -> None:
    # No command that can spend money may default its cap.
    parser = build_parser()
    for argv in (["replay", "plan"], ["replay", "run"], ["pilot"]):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)


def test_cap_is_parsed_when_supplied() -> None:
    args = build_parser().parse_args(["replay", "plan", "--cap", "12.5"])
    assert args.cap == 12.5


def test_corpus_round_trips(tmp_path: Path) -> None:
    items = [item("a-1"), item("b-1")]
    path = tmp_path / "items.jsonl"
    write_corpus(items, path)
    assert load_corpus(path) == items


def test_missing_corpus_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="corpus build"):
        load_corpus(tmp_path / "nope.jsonl")


class TestReplicateMap:
    def test_applies_k_to_a_bounded_subset_only(self) -> None:
        items = [item(f"s-{i:03d}") for i in range(50)]
        reps = _replicate_map(items, subset=10, k=3)
        assert len(reps) == 10
        assert set(reps.values()) == {3}

    def test_is_deterministic(self) -> None:
        items = [item(f"s-{i:03d}") for i in range(50)]
        assert _replicate_map(items, 10, 3) == _replicate_map(items, 10, 3)

    def test_disabled_when_k_is_one(self) -> None:
        items = [item(f"s-{i:03d}") for i in range(10)]
        assert _replicate_map(items, 10, 1) == {}


class TestCommittedCorpus:
    """Guards the real corpus that P3-P5 will consume."""

    def test_exists_and_is_the_agreed_size(self) -> None:
        items = load_corpus(CORPUS)
        assert len(items) == 1500

    def test_splits_the_way_the_design_says(self) -> None:
        items = load_corpus(CORPUS)
        counts = {s: sum(1 for i in items if i.split == s) for s in ("calibration", "dev", "test")}
        assert counts == {"calibration": 300, "dev": 450, "test": 750}

    def test_is_1200_gradable_plus_300_free_form(self) -> None:
        # D-021: only the free-form slice costs judge money in P3.
        items = load_corpus(CORPUS)
        assert sum(1 for i in items if i.verifiable) == 1200
        assert sum(1 for i in items if not i.verifiable) == 300

    def test_every_item_cites_its_source_and_licence(self) -> None:
        for i in load_corpus(CORPUS):
            assert i.source_dataset and i.source_id and i.source_license

    def test_maths_comes_from_the_hard_source_not_gsm8k(self) -> None:
        # The pilot measured gpt-oss-120b at 98% on GSM8K: a ceiling that made
        # half the gradable slice worthless. D-030 replaced it.
        sources = {
            i.source_dataset for i in load_corpus(CORPUS) if i.task_type == "math_word_problem"
        }
        assert sources == {"nlile/hendrycks-MATH-benchmark"}
        assert "openai/gsm8k" not in {i.source_dataset for i in load_corpus(CORPUS)}

    def test_every_maths_answer_is_a_plain_number(self) -> None:
        # The final_number verifier compares numbers. A LaTeX ground truth would
        # fail every correct answer, which would look like a weak model.
        import re

        for i in load_corpus(CORPUS):
            if i.task_type == "math_word_problem":
                assert i.ground_truth is not None
                assert re.fullmatch(r"-?\d+(?:\.\d+)?", i.ground_truth), i.ground_truth

    def test_every_gradable_item_has_a_working_verifier(self) -> None:
        from evald.corpus.verifiers import REGISTRY

        for i in load_corpus(CORPUS):
            if i.verifiable:
                assert i.verifier in REGISTRY
                assert i.ground_truth

    def test_prompts_are_unique(self) -> None:
        texts = [i.prompt_text() for i in load_corpus(CORPUS)]
        assert len(set(texts)) == len(texts)

    def test_slugs_are_unique(self) -> None:
        slugs = [i.slug for i in load_corpus(CORPUS)]
        assert len(set(slugs)) == len(slugs)


class TestPilotSample:
    """A pilot that silently tests only one task type would mislabel its results."""

    def test_covers_every_gradable_task_type(self) -> None:
        from evald.cli import _pilot_sample

        items = load_corpus(CORPUS)
        sample = _pilot_sample(items, n=100, seed=1)
        tasks = {i.task_type for i in sample}
        # Slugs sort alphabetically, so a naive [:100] would be all maths.
        assert tasks == {"math_word_problem", "multiple_choice"}

    def test_is_balanced_across_task_types(self) -> None:
        from evald.cli import _pilot_sample

        sample = _pilot_sample(load_corpus(CORPUS), n=100, seed=1)
        counts = [
            sum(1 for i in sample if i.task_type == t)
            for t in sorted({i.task_type for i in sample})
        ]
        assert max(counts) - min(counts) <= 1

    def test_never_includes_a_free_form_item(self) -> None:
        from evald.cli import _pilot_sample

        assert all(i.verifiable for i in _pilot_sample(load_corpus(CORPUS), n=50, seed=1))

    def test_is_deterministic(self) -> None:
        from evald.cli import _pilot_sample

        items = load_corpus(CORPUS)
        assert [i.slug for i in _pilot_sample(items, 60, seed=3)] == [
            i.slug for i in _pilot_sample(items, 60, seed=3)
        ]

    def test_respects_the_requested_size(self) -> None:
        from evald.cli import _pilot_sample

        assert len(_pilot_sample(load_corpus(CORPUS), n=40, seed=1)) <= 40
