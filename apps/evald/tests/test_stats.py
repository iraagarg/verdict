"""The verdict layer.

Two independent kinds of check, because either alone is weak:

  1. **Against scipy.** Our McNemar is implemented from first principles, so
     scipy is a genuinely independent oracle. Testing a scipy call against scipy
     would prove nothing.
  2. **Against synthetic data with a known ground-truth effect.** A test can
     agree with scipy and still be wired up wrong — comparing the wrong arms,
     losing the pairing, or reporting the effect with the sign flipped. Data
     built with a known answer catches that; scipy cannot.
"""

from __future__ import annotations

import math
import random

import pytest
from scipy import stats as scipy_stats

from evald.stats.bootstrap import PairedDataError, bootstrap_rate_ci, paired_bootstrap
from evald.stats.mcnemar import chi2_p, exact_p, mcnemar, tabulate
from evald.stats.multiple import benjamini_hochberg, significant
from evald.stats.verdict import DEFAULT_MARGIN, classify, compare_runs, required_pairs


# ─────────────────────────────────────────────────────────────────────────────
# McNemar against scipy
# ─────────────────────────────────────────────────────────────────────────────
class TestMcNemarAgainstScipy:
    """scipy has no mcnemar in `stats`, but the exact test IS a binomial test at
    p = 0.5 on the discordant pairs, and scipy.stats.binomtest is exact."""

    @pytest.mark.parametrize(
        ("b", "c"),
        [(10, 0), (0, 10), (5, 5), (12, 3), (3, 12), (1, 0), (7, 2), (20, 9), (1, 1), (30, 14)],
    )
    def test_exact_p_matches_scipy_binomtest(self, b: int, c: int) -> None:
        ours = exact_p(b, c)
        theirs = scipy_stats.binomtest(b, b + c, 0.5, alternative="two-sided").pvalue
        assert ours == pytest.approx(theirs, rel=1e-12)

    @pytest.mark.parametrize(("b", "c"), [(30, 10), (50, 30), (100, 70), (40, 25)])
    def test_chi2_p_matches_scipy_chi2_survival(self, b: int, c: int) -> None:
        statistic, ours = chi2_p(b, c, continuity=True)
        expected_stat = (abs(b - c) - 1) ** 2 / (b + c)
        assert statistic == pytest.approx(expected_stat)
        assert ours == pytest.approx(scipy_stats.chi2.sf(expected_stat, df=1), rel=1e-10)

    def test_exact_matches_scipy_across_a_sweep(self) -> None:
        for b in range(0, 16):
            for c in range(0, 16):
                if b + c == 0:
                    continue
                expected = scipy_stats.binomtest(b, b + c, 0.5, alternative="two-sided").pvalue
                assert exact_p(b, c) == pytest.approx(expected, rel=1e-12), (b, c)


class TestMcNemarProperties:
    def test_tabulates_the_four_cells(self) -> None:
        a = [True, True, False, False, True]
        b = [True, False, True, False, False]
        counts = tabulate(a, b)
        assert (counts.n11, counts.c, counts.b, counts.n00) == (1, 2, 1, 1)
        assert counts.n == 5
        assert counts.discordant == 3

    def test_is_symmetric_in_the_arms(self) -> None:
        a = [True] * 20 + [False] * 30
        b = [False] * 25 + [True] * 25
        assert mcnemar(a, b).p_value == pytest.approx(mcnemar(b, a).p_value)

    def test_identical_arms_give_p_of_one(self) -> None:
        arm = [True, False, True, True, False] * 20
        result = mcnemar(arm, arm)
        assert result.p_value == 1.0
        assert result.n_discordant == 0

    def test_concordant_pairs_carry_no_evidence(self) -> None:
        # Adding items both arms get right must not change the p-value. This is
        # the defining property of McNemar and the reason it suits paired data.
        a = [True] * 5 + [False] * 5
        b = [False] * 5 + [True] * 5
        p_small = mcnemar(a, b).p_value
        p_padded = mcnemar(a + [True] * 500, b + [True] * 500).p_value
        assert p_small == pytest.approx(p_padded)

    def test_uses_the_exact_test_when_discordant_counts_are_small(self) -> None:
        a = [True] * 10 + [False] * 90
        b = [False] * 10 + [False] * 90
        assert mcnemar(a, b).method == "exact"

    def test_switches_to_chi2_when_discordant_counts_are_large(self) -> None:
        a = [True] * 60 + [False] * 60
        b = [False] * 60 + [True] * 60
        assert mcnemar(a, b).method == "chi2"

    def test_rejects_unpaired_input(self) -> None:
        with pytest.raises(ValueError, match="same length"):
            mcnemar([True, False], [True])


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap
# ─────────────────────────────────────────────────────────────────────────────
class TestPairedBootstrap:
    def test_recovers_a_known_effect(self) -> None:
        # Ground truth built in: baseline 80%, candidate 60%, so effect = -0.20.
        base = [True] * 80 + [False] * 20
        cand = [True] * 60 + [False] * 40
        r = paired_bootstrap(
            [1.0 if x else 0.0 for x in base], [1.0 if x else 0.0 for x in cand], iterations=4000
        )
        assert r.effect == pytest.approx(-0.20, abs=1e-9)
        assert r.ci_low < -0.20 < r.ci_high

    def test_identical_arms_give_a_zero_effect_and_a_zero_width_interval(self) -> None:
        arm = [1.0, 0.0, 1.0, 1.0] * 25
        r = paired_bootstrap(arm, arm, iterations=2000)
        assert r.effect == 0.0
        # Pairing means every resample cancels exactly. An UNPAIRED bootstrap
        # would report spurious width here — this is the test that proves the
        # pairing is actually wired up.
        assert r.ci_low == 0.0
        assert r.ci_high == 0.0

    def test_pairing_is_tighter_than_treating_arms_independently(self) -> None:
        # Shared item difficulty is the biggest variance source and it cancels
        # inside a pair. If it did not, this interval would be much wider.
        rng = random.Random(3)
        difficulty = [rng.random() for _ in range(200)]
        base = [1.0 if d < 0.7 else 0.0 for d in difficulty]
        cand = [1.0 if d < 0.65 else 0.0 for d in difficulty]  # strictly nested
        paired = paired_bootstrap(base, cand, iterations=3000)
        assert paired.ci_width < 0.12

    def test_ci_covers_the_truth_about_95_percent_of_the_time(self) -> None:
        # The property a confidence interval is defined by. Run many independent
        # experiments with a known effect and count coverage.
        rng = random.Random(11)
        true_base, true_cand = 0.70, 0.60
        covered = 0
        trials = 100
        for t in range(trials):
            base = [1.0 if rng.random() < true_base else 0.0 for _ in range(250)]
            cand = [1.0 if rng.random() < true_cand else 0.0 for _ in range(250)]
            r = paired_bootstrap(base, cand, iterations=300, seed=t)
            if r.ci_low <= (true_cand - true_base) <= r.ci_high:
                covered += 1
        assert 0.88 <= covered / trials <= 1.0

    def test_more_items_give_a_tighter_interval(self) -> None:
        small = paired_bootstrap([1.0, 0.0] * 15, [1.0, 1.0] * 15, iterations=2000)
        large = paired_bootstrap([1.0, 0.0] * 300, [1.0, 1.0] * 300, iterations=2000)
        assert large.ci_width < small.ci_width

    def test_is_deterministic_for_a_seed(self) -> None:
        a, b = [1.0, 0.0] * 50, [1.0, 1.0] * 50
        first = paired_bootstrap(a, b, iterations=1500, seed=42)
        second = paired_bootstrap(a, b, iterations=1500, seed=42)
        assert (first.ci_low, first.ci_high) == (second.ci_low, second.ci_high)

    def test_sign_convention_is_candidate_minus_baseline(self) -> None:
        # A flipped sign here would invert every verdict in the project.
        worse = paired_bootstrap([1.0] * 50, [0.0] * 50, iterations=500)
        better = paired_bootstrap([0.0] * 50, [1.0] * 50, iterations=500)
        assert worse.effect == -1.0
        assert better.effect == 1.0

    def test_rejects_mismatched_or_empty_input(self) -> None:
        with pytest.raises(PairedDataError, match="same length"):
            paired_bootstrap([1.0], [1.0, 0.0])
        with pytest.raises(PairedDataError, match="empty"):
            paired_bootstrap([], [])

    def test_rate_ci_brackets_the_observed_rate(self) -> None:
        rate, lo, hi = bootstrap_rate_ci([True] * 70 + [False] * 30, iterations=3000)
        assert rate == pytest.approx(0.70)
        assert lo < 0.70 < hi

    def test_rate_ci_matches_the_normal_approximation_at_large_n(self) -> None:
        # Independent cross-check: at n=2000 the bootstrap CI should land close
        # to the textbook normal-approximation interval.
        rng = random.Random(5)
        data = [rng.random() < 0.6 for _ in range(2000)]
        rate, lo, hi = bootstrap_rate_ci(data, iterations=2000)
        se = math.sqrt(rate * (1 - rate) / len(data))
        assert lo == pytest.approx(rate - 1.96 * se, abs=0.015)
        assert hi == pytest.approx(rate + 1.96 * se, abs=0.015)


# ─────────────────────────────────────────────────────────────────────────────
# Verdicts
# ─────────────────────────────────────────────────────────────────────────────
class TestClassify:
    @pytest.mark.parametrize(
        ("lo", "hi", "expected"),
        [
            (-0.20, -0.06, "REGRESSION"),
            (0.08, 0.20, "IMPROVEMENT"),
            (-0.02, 0.02, "EQUIVALENT"),
            (-0.05, 0.05, "EQUIVALENT"),
            (-0.30, 0.30, "INCONCLUSIVE"),
            (-0.20, -0.02, "INCONCLUSIVE"),
            (0.02, 0.20, "INCONCLUSIVE"),
            (-0.06, 0.01, "INCONCLUSIVE"),
        ],
    )
    def test_maps_intervals_to_verdicts(self, lo: float, hi: float, expected: str) -> None:
        assert classify(lo, hi, margin=0.05) == expected

    def test_a_significant_but_trivial_difference_is_EQUIVALENT_not_a_regression(self) -> None:
        # The case a p-value gets wrong: a real but tiny effect, measured very
        # precisely. Statistically significant; practically irrelevant.
        assert classify(-0.012, -0.004, margin=0.05) == "EQUIVALENT"

    def test_a_wide_interval_is_never_EQUIVALENT_however_small_the_estimate(self) -> None:
        # Absence of evidence must not be reported as evidence of absence.
        assert classify(-0.40, 0.41, margin=0.05) == "INCONCLUSIVE"

    def test_rejects_a_malformed_interval(self) -> None:
        with pytest.raises(ValueError, match="malformed"):
            classify(0.5, -0.5, margin=0.05)

    def test_rejects_a_negative_margin(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            classify(-0.1, 0.1, margin=-0.01)

    def test_a_zero_margin_reduces_to_plain_significance_testing(self) -> None:
        assert classify(-0.20, -0.01, margin=0.0) == "REGRESSION"
        assert classify(-0.01, 0.01, margin=0.0) == "INCONCLUSIVE"


class TestCompareRuns:
    def test_detects_a_planted_regression(self) -> None:
        base = [True] * 90 + [False] * 10
        cand = [True] * 60 + [False] * 40
        v = compare_runs(base, cand, margin=0.05, iterations=3000)
        assert v.outcome == "REGRESSION"
        assert v.effect == pytest.approx(-0.30)
        assert v.mcnemar_p < 0.001

    def test_detects_a_planted_improvement(self) -> None:
        base = [True] * 50 + [False] * 50
        cand = [True] * 85 + [False] * 15
        v = compare_runs(base, cand, margin=0.05, iterations=3000)
        assert v.outcome == "IMPROVEMENT"
        assert v.effect > 0

    def test_calls_identical_arms_EQUIVALENT(self) -> None:
        arm = ([True] * 7 + [False] * 3) * 40
        v = compare_runs(arm, list(arm), margin=0.03, iterations=2000)
        assert v.outcome == "EQUIVALENT"
        assert v.effect == 0.0
        assert v.mcnemar_p == 1.0

    def test_calls_a_tiny_sample_INCONCLUSIVE_rather_than_EQUIVALENT(self) -> None:
        # Eight items cannot demonstrate anything. Reporting "no significant
        # difference" here is how an underpowered run gets mistaken for a pass.
        v = compare_runs(
            [True] * 6 + [False] * 2, [True] * 5 + [False] * 3, margin=0.03, iterations=2000
        )
        assert v.outcome == "INCONCLUSIVE"
        assert v.can_demonstrate_equivalence is False

    def test_a_large_sample_with_a_real_but_trivial_effect_is_EQUIVALENT(self) -> None:
        rng = random.Random(17)
        # 60.0% vs 60.5%: real, tiny, and only visible at this n.
        base = [rng.random() < 0.600 for _ in range(6000)]
        cand = [rng.random() < 0.605 for _ in range(6000)]
        v = compare_runs(base, cand, margin=0.05, iterations=1500)
        assert v.outcome == "EQUIVALENT"

    def test_separates_statistical_from_practical_significance(self) -> None:
        rng = random.Random(23)
        base = [rng.random() < 0.50 for _ in range(20000)]
        cand = [rng.random() < 0.515 for _ in range(20000)]
        v = compare_runs(base, cand, margin=0.05, iterations=800)
        # Detectably different, but by less than anyone cares about.
        assert v.outcome == "EQUIVALENT"
        assert v.statistically_significant is True

    def test_reports_n_and_the_discordant_split(self) -> None:
        base = [True] * 40 + [False] * 60
        cand = [True] * 55 + [False] * 45
        v = compare_runs(base, cand, iterations=1000)
        assert v.n == 100
        assert v.n_discordant + v.n_concordant == 100

    def test_summary_names_the_outcome_and_never_says_no_difference(self) -> None:
        v = compare_runs([True] * 4 + [False] * 4, [True] * 5 + [False] * 3, iterations=800)
        text = v.summary()
        assert "INCONCLUSIVE" in text
        assert "Do NOT read this as 'no difference'" in text

    def test_default_margin_is_documented(self) -> None:
        assert DEFAULT_MARGIN == 0.03


class TestRequiredPairs:
    def test_smaller_effects_need_more_items(self) -> None:
        assert required_pairs(0.02, 0.30) > required_pairs(0.10, 0.30)

    def test_more_disagreement_needs_more_items_for_a_fixed_absolute_effect(self) -> None:
        # Counter-intuitive but correct, and worth being able to explain.
        # effect = (b - c)/n and discordant_rate = (b + c)/n. Holding the effect
        # fixed while raising the discordant rate makes the split LESS lopsided:
        #   rate 0.10, effect 0.05 -> b=0.075n, c=0.025n -> a 75/25 split
        #   rate 0.50, effect 0.05 -> b=0.275n, c=0.225n -> a 55/45 split
        # A 55/45 split is much harder to distinguish from 50/50, so it needs
        # more items. More disagreement is not more signal when the signal is
        # spread more thinly across it.
        assert required_pairs(0.05, 0.50) > required_pairs(0.05, 0.10)

    def test_rejects_nonsense_input(self) -> None:
        with pytest.raises(ValueError):
            required_pairs(0.0, 0.3)
        with pytest.raises(ValueError):
            required_pairs(0.05, 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Multiple comparisons
# ─────────────────────────────────────────────────────────────────────────────
class TestBenjaminiHochberg:
    def test_matches_scipy_false_discovery_control(self) -> None:
        ps = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
        ours = benjamini_hochberg(ps)
        theirs = scipy_stats.false_discovery_control(ps, method="bh")
        for mine, ref in zip(ours, theirs, strict=True):
            assert mine == pytest.approx(ref, abs=1e-8)

    def test_matches_scipy_on_unsorted_input(self) -> None:
        ps = [0.216, 0.001, 0.074, 0.039, 0.212, 0.008, 0.042, 0.06, 0.041, 0.205]
        ours = benjamini_hochberg(ps)
        theirs = scipy_stats.false_discovery_control(ps, method="bh")
        for mine, ref in zip(ours, theirs, strict=True):
            assert mine == pytest.approx(ref, abs=1e-8)

    def test_is_less_conservative_than_bonferroni(self) -> None:
        # The reason BH was chosen over Bonferroni (D-012): with 16 tests,
        # Bonferroni would reject almost nothing and the router would never route.
        ps = [0.001, 0.004, 0.01, 0.02, 0.03] + [0.5] * 11
        assert sum(significant(ps)) > sum(p <= 0.05 / len(ps) for p in ps)

    def test_controls_false_discoveries_under_a_global_null(self) -> None:
        # With every null true, BH should reject roughly none.
        rng = random.Random(9)
        ps = [rng.random() for _ in range(500)]
        assert sum(significant(ps, alpha=0.05)) <= 5

    def test_preserves_input_order(self) -> None:
        q = benjamini_hochberg([0.5, 0.01, 0.2])
        assert q[1] < q[2] < q[0] or q[1] <= q[2] <= q[0]

    def test_q_values_are_monotonic_in_p(self) -> None:
        ps = sorted(random.Random(4).random() for _ in range(40))
        q = benjamini_hochberg(ps)
        assert all(q[i] <= q[i + 1] + 1e-12 for i in range(len(q) - 1))

    def test_handles_empty_and_rejects_invalid(self) -> None:
        assert benjamini_hochberg([]) == []
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            benjamini_hochberg([0.5, 1.4])
