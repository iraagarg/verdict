"""Semantic cache: exact bounds, hard negatives, and refusing to loosen on luck."""

from __future__ import annotations

import random
from pathlib import Path
from typing import ClassVar

import pytest
from scipy import stats as scipy_stats

from evald.cache.calibrate import CalibrationCurve, calibrate, evaluate_threshold
from evald.cache.pairs import (
    LabelledPair,
    cosine,
    hardest_negatives,
    paraphrase_positives,
    read_pairs,
    write_pairs,
)
from evald.stats.proportion import (
    clopper_pearson,
    clopper_pearson_lower,
    clopper_pearson_upper,
    min_trials_for_upper_bound,
)


def pair(label: str, sim: float, i: int = 0) -> LabelledPair:
    return LabelledPair(
        pair_id=f"{label}{i}",
        left_slug="a",
        right_slug="b",
        left_text="x",
        right_text="y",
        label=label,  # type: ignore[arg-type]
        source="paraphrase" if label == "duplicate" else "hard_negative",
        similarity=sim,
    )


class TestClopperPearsonAgainstScipy:
    """Implemented from first principles, so scipy is an independent oracle."""

    @pytest.mark.parametrize(
        ("k", "n"), [(0, 150), (0, 30), (1, 150), (3, 100), (50, 100), (99, 100), (150, 150)]
    )
    def test_upper_bound_matches_scipy(self, k: int, n: int) -> None:
        ours = clopper_pearson_upper(k, n)
        theirs = scipy_stats.beta.ppf(0.975, k + 1, n - k) if k < n else 1.0
        assert ours == pytest.approx(theirs, abs=1e-7)

    @pytest.mark.parametrize(("k", "n"), [(1, 150), (3, 100), (50, 100), (99, 100)])
    def test_lower_bound_matches_scipy(self, k: int, n: int) -> None:
        assert clopper_pearson_lower(k, n) == pytest.approx(
            scipy_stats.beta.ppf(0.025, k, n - k + 1), abs=1e-7
        )

    def test_zero_events_gives_a_NON_zero_upper_bound(self) -> None:
        # The bug this module exists to fix. A percentile bootstrap resampling
        # 0-of-150 sees zero every time and reports [0, 0], which would let a
        # cache threshold be called "provably 0% false hits" when it had merely
        # not been tested hard enough.
        rate, lo, hi = clopper_pearson(0, 150)
        assert rate == 0.0
        assert lo == 0.0
        assert hi == pytest.approx(0.0243, abs=1e-3)

    def test_zero_events_matches_the_rule_of_three(self) -> None:
        # The familiar approximation: ~3/n at 95%.
        for n in (100, 300, 1000):
            assert clopper_pearson_upper(0, n) == pytest.approx(3.0 / n, rel=0.25)

    def test_bounds_tighten_as_evidence_grows(self) -> None:
        assert clopper_pearson_upper(0, 1000) < clopper_pearson_upper(0, 100)

    def test_all_events_pins_the_upper_bound_at_one(self) -> None:
        assert clopper_pearson_upper(50, 50) == 1.0

    def test_interval_always_contains_the_point_estimate(self) -> None:
        for k, n in [(0, 50), (1, 50), (25, 50), (49, 50), (50, 50)]:
            rate, lo, hi = clopper_pearson(k, n)
            assert lo <= rate <= hi

    def test_rejects_impossible_input(self) -> None:
        with pytest.raises(ValueError):
            clopper_pearson_upper(5, 3)
        with pytest.raises(ValueError):
            clopper_pearson_upper(0, 0)
        with pytest.raises(ValueError):
            clopper_pearson_upper(0, 10, alpha=1.5)


class TestMinTrials:
    def test_tolerance_sets_a_floor_on_sample_size(self) -> None:
        # A safety bound is limited by n, not by results. No number of clean
        # observations below this n can demonstrate the target.
        assert min_trials_for_upper_bound(0.01) == 368
        assert min_trials_for_upper_bound(0.05) == 72

    def test_a_stricter_tolerance_needs_more_evidence(self) -> None:
        assert min_trials_for_upper_bound(0.005) > min_trials_for_upper_bound(0.01)

    def test_the_answer_actually_achieves_the_target(self) -> None:
        n = min_trials_for_upper_bound(0.01)
        assert clopper_pearson_upper(0, n) <= 0.01
        assert clopper_pearson_upper(0, n - 1) > 0.01


class TestCosine:
    def test_identical_vectors_are_one(self) -> None:
        assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_are_zero(self) -> None:
        assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_magnitude_does_not_matter(self) -> None:
        assert cosine([1.0, 2.0], [10.0, 20.0]) == pytest.approx(1.0)

    def test_a_dimension_mismatch_is_an_error_not_a_silent_truncation(self) -> None:
        # Comparing two different embedding spaces would return a plausible
        # number, which is worse than failing.
        with pytest.raises(ValueError, match="dimension mismatch"):
            cosine([1.0, 2.0], [1.0, 2.0, 3.0])

    def test_a_zero_vector_does_not_divide_by_zero(self) -> None:
        assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


class TestHardNegatives:
    EMB: ClassVar[dict[str, list[float]]] = {
        "a": [1.0, 0.0, 0.0],
        "b": [0.95, 0.05, 0.0],  # nearest to a
        "c": [0.0, 1.0, 0.0],  # far from a
    }
    TEXTS: ClassVar[dict[str, str]] = {"a": "A", "b": "B", "c": "C"}

    def test_picks_the_most_similar_different_item(self) -> None:
        # Random negatives are trivially separable and would flatter any
        # threshold. The pairs that matter sit near the boundary.
        pairs = hardest_negatives(["a", "b", "c"], self.TEXTS, self.EMB, per_item=1)
        for_a = next(p for p in pairs if p.left_slug == "a")
        assert for_a.right_slug == "b"

    def test_never_pairs_an_item_with_itself(self) -> None:
        pairs = hardest_negatives(["a", "b", "c"], self.TEXTS, self.EMB)
        assert all(p.left_slug != p.right_slug for p in pairs)

    def test_labels_every_pair_as_different(self) -> None:
        # Corpus items are distinct prompts by construction (D-025), so a hit
        # between any two is a false hit — no human labelling needed.
        pairs = hardest_negatives(["a", "b", "c"], self.TEXTS, self.EMB)
        assert {p.label for p in pairs} == {"different"}
        assert {p.source for p in pairs} == {"hard_negative"}

    def test_records_the_similarity_it_found(self) -> None:
        pairs = hardest_negatives(["a", "b", "c"], self.TEXTS, self.EMB)
        assert all(p.similarity is not None for p in pairs)


class TestParaphrasePositives:
    def test_pairs_an_item_with_its_reworded_self(self) -> None:
        pairs = paraphrase_positives(
            {"a": "reworded A"}, {"a": "A"}, {"a": [1.0, 0.0]}, {"a": [0.99, 0.1]}
        )
        assert len(pairs) == 1
        assert pairs[0].label == "duplicate"
        assert pairs[0].similarity is not None and pairs[0].similarity > 0.9

    def test_skips_items_with_no_embedding_rather_than_guessing(self) -> None:
        assert paraphrase_positives({"a": "x"}, {"a": "A"}, {}, {}) == []


class TestPairPersistence:
    def test_round_trips(self, tmp_path: Path) -> None:
        pairs = [pair("duplicate", 0.95, 1), pair("different", 0.80, 2)]
        assert read_pairs(write_pairs(pairs, tmp_path / "pairs.jsonl")) == pairs

    def test_missing_file_is_empty_not_an_error(self, tmp_path: Path) -> None:
        assert read_pairs(tmp_path / "nope.jsonl") == []


class TestCalibration:
    @staticmethod
    def curve(n_dup: int = 150, n_neg: int = 400, seed: int = 7) -> CalibrationCurve:
        rng = random.Random(seed)
        pairs = [pair("duplicate", min(0.999, rng.gauss(0.94, 0.03)), i) for i in range(n_dup)] + [
            pair("different", min(0.999, rng.gauss(0.86, 0.04)), i) for i in range(n_neg)
        ]
        return calibrate(pairs, iterations=400)

    def test_a_looser_threshold_raises_both_rates(self) -> None:
        c = self.curve()
        loose = next(p for p in c.points if p.threshold == 0.85)
        tight = next(p for p in c.points if p.threshold == 0.975)
        assert loose.hit_rate > tight.hit_rate
        assert loose.false_hit_rate > tight.false_hit_rate

    def test_chooses_the_best_hit_rate_it_can_prove_is_safe(self) -> None:
        c = self.curve()
        chosen = c.chosen()
        assert chosen is not None
        assert chosen.acceptable
        better = [p for p in c.acceptable() if p.hit_rate > chosen.hit_rate]
        assert better == []

    def test_judges_safety_on_the_upper_bound_not_the_point_estimate(self) -> None:
        # A threshold with 0 observed false hits is NOT automatically acceptable.
        point = evaluate_threshold(
            [pair("duplicate", 0.99, i) for i in range(20)],
            [pair("different", 0.50, i) for i in range(20)],  # zero hits at 0.9
            threshold=0.9,
            max_false_hit_rate=0.01,
            iterations=200,
        )
        assert point.false_hit_rate == 0.0
        assert point.false_hit_ci_high > 0.01  # 0/20 cannot prove 1%
        assert point.acceptable is False

    def test_refuses_when_the_sample_is_too_small_to_prove_the_tolerance(self) -> None:
        c = self.curve(n_dup=50, n_neg=50)
        assert c.chosen() is None
        assert "SAMPLE SIZE limit" in c.render()

    def test_refuses_a_threshold_that_is_safe_but_never_hits(self) -> None:
        # Duplicates and negatives at identical similarity: any threshold that
        # excludes the negatives also excludes every duplicate. Such a setting
        # is "safe" and completely useless — it still costs an embedding call
        # and a vector search on every request.
        pairs = [pair("duplicate", 0.99, i) for i in range(100)] + [
            pair("different", 0.99, i) for i in range(400)
        ]
        c = calibrate(pairs, iterations=200)
        assert c.chosen() is None
        assert "SAFE but not USEFUL" in c.render()

    def test_says_so_when_no_threshold_is_even_safe(self) -> None:
        # Negatives at similarity exactly 1.0: they hit at EVERY threshold on
        # the grid, including 1.0, so nothing is acceptable at any setting.
        # This is the case of two genuinely different prompts with identical
        # embeddings, which no threshold can separate.
        pairs = [pair("duplicate", 0.90, i) for i in range(100)] + [
            pair("different", 1.0, i) for i in range(400)
        ]
        c = calibrate(pairs, iterations=200)
        assert c.chosen() is None
        assert c.acceptable() == []
        assert "every threshold fails it" in c.render()

    def test_reports_both_rates_always(self) -> None:
        # The whole point: a cache that returns wrong answers fast is worse than
        # no cache, so the hit rate may never be shown without the false-hit rate.
        text = self.curve().render()
        assert "hit%" in text
        assert "false-hit%" in text

    def test_needs_both_kinds_of_pair(self) -> None:
        with pytest.raises(ValueError, match="both kinds of pair"):
            calibrate([pair("duplicate", 0.9, i) for i in range(10)])

    def test_is_deterministic(self) -> None:
        a, b = self.curve(seed=3), self.curve(seed=3)
        assert [p.threshold for p in a.points] == [p.threshold for p in b.points]
        assert [p.false_hit_ci_high for p in a.points] == [p.false_hit_ci_high for p in b.points]
