"""Corpus assembly: determinism, stratification, provenance, difficulty filtering."""

from __future__ import annotations

from typing import ClassVar

import pytest

from evald.corpus.assemble import (
    FilterMode,
    PilotResult,
    assign_splits,
    build_corpus,
    discriminative_slugs,
    filter_by_difficulty,
    hash_seed,
)
from evald.corpus.schema import GRADABLE_TASKS, CorpusItem, corpus_sha256
from evald.corpus.sources import RawItem


def raw(i: int, task: str = "math_word_problem", gt: str | None = "4") -> RawItem:
    return RawItem(
        source_dataset="openai/gsm8k",
        source_id=f"test-{i}",
        source_license="MIT",
        task_type=task,
        messages=[{"role": "user", "content": f"question number {i}"}],
        ground_truth=gt,
    )


POOL = {"math_word_problem": [raw(i) for i in range(100)]}


class TestSplits:
    def test_sizes_sum_exactly_to_n(self) -> None:
        for n in (1, 7, 99, 1200, 1201):
            assert len(assign_splits(n, seed=1)) == n

    def test_proportions_are_respected(self) -> None:
        labels = assign_splits(1000, seed=1)
        assert labels.count("calibration") == 200
        assert labels.count("dev") == 300
        assert labels.count("test") == 500

    def test_is_deterministic_for_a_given_seed(self) -> None:
        assert assign_splits(50, seed=7) == assign_splits(50, seed=7)

    def test_differs_across_seeds(self) -> None:
        assert assign_splits(50, seed=7) != assign_splits(50, seed=8)

    def test_rejects_weights_that_do_not_sum_to_one(self) -> None:
        with pytest.raises(ValueError, match=r"sum to 1\.0"):
            assign_splits(10, seed=1, weights={"calibration": 0.5, "dev": 0.2, "test": 0.2})

    def test_seed_derivation_is_stable_across_processes(self) -> None:
        # Python's built-in hash() is salted per process; ours must not be.
        assert hash_seed(42, "math_word_problem") == hash_seed(42, "math_word_problem")
        assert hash_seed(42, "a") != hash_seed(42, "b")


class TestBuildCorpus:
    def test_is_byte_identical_for_the_same_seed(self) -> None:
        a = build_corpus(POOL, {"math_word_problem": 30}, seed=99)
        b = build_corpus(POOL, {"math_word_problem": 30}, seed=99)
        assert corpus_sha256(a) == corpus_sha256(b)
        assert [i.slug for i in a] == [i.slug for i in b]
        assert [i.split for i in a] == [i.split for i in b]

    def test_changes_with_the_seed(self) -> None:
        a = build_corpus(POOL, {"math_word_problem": 30}, seed=1)
        b = build_corpus(POOL, {"math_word_problem": 30}, seed=2)
        assert corpus_sha256(a) != corpus_sha256(b)

    def test_stratifies_each_task_independently(self) -> None:
        pool = {
            "math_word_problem": [raw(i) for i in range(100)],
            "summarization": [
                raw(i, "summarization", None).__class__(
                    source_dataset="abisee/cnn_dailymail",
                    source_id=f"s-{i}",
                    source_license="Apache-2.0",
                    task_type="summarization",
                    messages=[{"role": "user", "content": f"article {i}"}],
                    ground_truth=None,
                )
                for i in range(100)
            ],
        }
        items = build_corpus(pool, {"math_word_problem": 50, "summarization": 50}, seed=3)
        for task in ("math_word_problem", "summarization"):
            subset = [i for i in items if i.task_type == task]
            assert len(subset) == 50
            assert sum(1 for i in subset if i.split == "test") == 25

    def test_marks_gradable_items_verifiable_with_a_verifier(self) -> None:
        items = build_corpus(POOL, {"math_word_problem": 5}, seed=1)
        for item in items:
            assert item.task_type in GRADABLE_TASKS
            assert item.verifiable
            assert item.verifier == "final_number"
            assert item.ground_truth is not None

    def test_records_provenance_on_every_item(self) -> None:
        for item in build_corpus(POOL, {"math_word_problem": 5}, seed=1):
            assert item.source_dataset
            assert item.source_id
            assert item.source_license

    def test_refuses_to_silently_underdeliver(self) -> None:
        with pytest.raises(ValueError, match="only 100 candidates"):
            build_corpus(POOL, {"math_word_problem": 500}, seed=1)

    def test_rejects_duplicate_prompts(self) -> None:
        # Duplicates would share one cached generation and be counted twice,
        # understating variance and narrowing every CI built on them.
        dupes = {"math_word_problem": [raw(1), raw(1)]}
        with pytest.raises(ValueError, match="duplicate prompt"):
            build_corpus(dupes, {"math_word_problem": 2}, seed=1)

    def test_adding_a_task_does_not_reshuffle_existing_ones(self) -> None:
        # Per-task seeds mean extending the corpus later cannot silently change
        # which items an earlier artifact was computed from.
        only_math = build_corpus(POOL, {"math_word_problem": 20}, seed=5)
        pool2 = dict(POOL)
        pool2["multiple_choice"] = [
            RawItem(
                source_dataset="TIGER-Lab/MMLU-Pro",
                source_id=f"mc-{i}",
                source_license="MIT",
                task_type="multiple_choice",
                messages=[{"role": "user", "content": f"mc question {i}"}],
                ground_truth="C",
            )
            for i in range(50)
        ]
        both = build_corpus(pool2, {"math_word_problem": 20, "multiple_choice": 20}, seed=5)
        math_from_both = [i for i in both if i.task_type == "math_word_problem"]
        assert [i.source_id for i in only_math] == [i.source_id for i in math_from_both]


class TestCorpusHash:
    def test_is_order_independent(self) -> None:
        items = build_corpus(POOL, {"math_word_problem": 10}, seed=1)
        assert corpus_sha256(items) == corpus_sha256(list(reversed(items)))

    def test_changes_if_any_item_changes(self) -> None:
        items = build_corpus(POOL, {"math_word_problem": 10}, seed=1)
        mutated = list(items)
        mutated[0] = mutated[0].model_copy(update={"ground_truth": "999"})
        assert corpus_sha256(items) != corpus_sha256(mutated)


class TestSchemaInvariants:
    def test_verifiable_items_require_ground_truth_and_a_verifier(self) -> None:
        with pytest.raises(ValueError, match="need ground_truth and verifier"):
            CorpusItem(
                slug="x",
                task_type="math_word_problem",
                split="test",
                verifiable=True,
                messages=[{"role": "user", "content": "q"}],
                source_dataset="d",
                source_id="1",
                source_license="MIT",
            )

    def test_free_form_items_must_not_carry_ground_truth(self) -> None:
        with pytest.raises(ValueError, match="must not carry ground truth"):
            CorpusItem(
                slug="x",
                task_type="summarization",
                split="test",
                verifiable=False,
                messages=[{"role": "user", "content": "q"}],
                ground_truth="leak",
                verifier="final_number",
                source_dataset="d",
                source_id="1",
                source_license="MIT",
            )


class TestDifficultyFilter:
    def test_keeps_only_items_the_models_disagreed_on(self) -> None:
        results = [
            PilotResult("all-pass", "m1", True),
            PilotResult("all-pass", "m2", True),
            PilotResult("all-fail", "m1", False),
            PilotResult("all-fail", "m2", False),
            PilotResult("split", "m1", True),
            PilotResult("split", "m2", False),
        ]
        assert discriminative_slugs(results) == {"split"}

    def test_ignores_items_with_too_few_pilot_models(self) -> None:
        assert discriminative_slugs([PilotResult("lonely", "m1", True)]) == set()

    def test_free_form_items_pass_through_untouched(self) -> None:
        items = [
            CorpusItem(
                slug="summarization-0000",
                task_type="summarization",
                split="test",
                verifiable=False,
                messages=[{"role": "user", "content": "article"}],
                source_dataset="abisee/cnn_dailymail",
                source_id="s1",
                source_license="Apache-2.0",
            )
        ]
        kept, report = filter_by_difficulty(items, results=[])
        assert kept == items
        assert report["gradable_before"] == 0

    def test_reports_the_pass_rate_that_justifies_the_filter(self) -> None:
        # The ceiling-effect claim must be a measured number, not an assertion.
        items = build_corpus(POOL, {"math_word_problem": 4}, seed=1)
        slugs = [i.slug for i in items]
        results = [
            PilotResult(slugs[0], "cheap", True),
            PilotResult(slugs[0], "strong", True),
            PilotResult(slugs[1], "cheap", False),
            PilotResult(slugs[1], "strong", True),
            PilotResult(slugs[2], "cheap", False),
            PilotResult(slugs[2], "strong", False),
            PilotResult(slugs[3], "cheap", True),
            PilotResult(slugs[3], "strong", True),
        ]
        kept, report = filter_by_difficulty(items, results)
        assert [i.slug for i in kept] == [slugs[1]]
        assert report["gradable_before"] == 4
        assert report["removed_as_uninformative"] == 3
        assert report["pass_rate_by_model"] == {"cheap": 0.5, "strong": 0.75}


class TestCheapOnlyPilotMode:
    """A pilot run on cheap models only can claim less, and must filter less."""

    RESULTS: ClassVar[list[PilotResult]] = [
        PilotResult("easy", "20b", True),
        PilotResult("easy", "120b", True),
        PilotResult("split", "20b", False),
        PilotResult("split", "120b", True),
        PilotResult("hard", "20b", False),
        PilotResult("hard", "120b", False),
    ]

    def test_discriminative_mode_drops_items_nobody_passed(self) -> None:
        assert discriminative_slugs(self.RESULTS) == {"split"}

    def test_drop_easy_mode_keeps_them(self) -> None:
        # Two small models both failing says nothing about whether a frontier
        # model would succeed — and that is exactly where cheap-vs-strong
        # routing gets decided. Discarding those items would throw away the
        # most informative part of the corpus.
        assert discriminative_slugs(self.RESULTS, mode="drop_easy") == {"split", "hard"}

    def test_both_modes_always_drop_the_too_easy_items(self) -> None:
        # The one-sided claim that holds whatever models were in the pilot.
        modes: list[FilterMode] = ["discriminative", "drop_easy"]
        for mode in modes:
            assert "easy" not in discriminative_slugs(self.RESULTS, mode=mode)

    def test_report_records_which_models_and_mode_produced_it(self) -> None:
        items = build_corpus(POOL, {"math_word_problem": 3}, seed=1)
        slugs = [i.slug for i in items]
        results = [
            PilotResult(slugs[0], "openai/gpt-oss-20b", True),
            PilotResult(slugs[0], "openai/gpt-oss-120b", True),
            PilotResult(slugs[1], "openai/gpt-oss-20b", False),
            PilotResult(slugs[1], "openai/gpt-oss-120b", False),
            PilotResult(slugs[2], "openai/gpt-oss-20b", False),
            PilotResult(slugs[2], "openai/gpt-oss-120b", True),
        ]
        kept, report = filter_by_difficulty(items, results, mode="drop_easy")
        assert report["filter_mode"] == "drop_easy"
        assert report["pilot_models"] == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
        assert {i.slug for i in kept} == {slugs[1], slugs[2]}


class TestEmbeddingText:
    """What gets embedded decides whether a cache threshold means anything."""

    @staticmethod
    def item(system: str, user: str) -> CorpusItem:
        return CorpusItem(
            slug="math_word_problem-0000",
            task_type="math_word_problem",
            split="test",
            verifiable=True,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            ground_truth="4",
            verifier="final_number",
            source_dataset="nlile/hendrycks-MATH-benchmark",
            source_id="1",
            source_license="MIT",
        )

    def test_excludes_the_system_instruction(self) -> None:
        # Every item of a task type carries the SAME instruction, so including
        # it drags all pairwise similarities toward 1.0 and destroys the
        # discrimination a cache threshold depends on.
        shared = "Solve the problem. " * 20
        text = self.item(shared, "What is 2+2?").user_prompt_text()
        assert text == "What is 2+2?"
        assert shared.strip() not in text

    def test_two_items_sharing_an_instruction_are_not_made_similar(self) -> None:
        shared = "Solve the problem and give the final answer as #### <answer>"
        a = self.item(shared, "How many divisors does 196 have?").user_prompt_text()
        b = self.item(shared, "What is the smallest perfect cube above 100?").user_prompt_text()
        assert a != b
        assert shared not in a and shared not in b

    def test_full_prompt_text_still_includes_everything(self) -> None:
        # The replay runner sends the whole thing; only EMBEDDING drops the system turn.
        full = self.item("INSTRUCTION", "QUESTION").prompt_text()
        assert "INSTRUCTION" in full and "QUESTION" in full

    def test_falls_back_when_there_are_no_user_turns(self) -> None:
        item = CorpusItem(
            slug="summarization-0000",
            task_type="summarization",
            split="test",
            verifiable=False,
            messages=[{"role": "system", "content": "only this"}],
            source_dataset="d",
            source_id="1",
            source_license="MIT",
        )
        # Better to embed something than the empty string, which would make
        # every such request a duplicate of every other.
        assert "only this" in item.user_prompt_text()
