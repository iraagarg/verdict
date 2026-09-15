"""The router: cascade economics, split discipline, and policy fitting."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from evald.router.artifact import ParetoArtifact, PolicyArtifact
from evald.router.cascade import (
    CascadeConfig,
    all_cheap_baseline,
    all_strong_baseline,
    confidence_check_cost,
    simulate,
)
from evald.router.fit import FIT_SPLIT, REPORT_SPLIT, FitOutput, fit_and_report
from evald.router.offline import fit_offline_policy, policy_cost
from evald.router.records import ItemRecord, SplitLeakError, assert_disjoint, select_split
from evald.router.sweep import sweep

CHEAP, MID, STRONG = "cheap", "mid", "strong"
LADDER = [CHEAP, MID, STRONG]
COSTS = {CHEAP: 170, MID: 3000, STRONG: 13750}


def record(
    i: int,
    split: str = "dev",
    task: str = "math_word_problem",
    conf: float = 0.9,
    cheap_ok: bool = True,
    mid_ok: bool = True,
) -> ItemRecord:
    return ItemRecord(
        slug=f"s{i}",
        task_type=task,
        split=split,
        success={CHEAP: cheap_ok, MID: mid_ok, STRONG: True},
        cost_nano=dict(COSTS),
        agreement={CHEAP: conf},
        verifier_confidence={CHEAP: conf},
    )


def world(n: int, split: str, seed: int, predictive: float = 0.7) -> list[ItemRecord]:
    """A synthetic world where confidence predicts cheap-model correctness."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        task = "math_word_problem" if i % 2 else "summarization"
        conf = rng.random()
        cheap_ok = rng.random() < (1 - predictive) + predictive * conf
        out.append(
            ItemRecord(
                slug=f"{split}-{i}",
                task_type=task,
                split=split,
                success={CHEAP: cheap_ok, MID: cheap_ok or rng.random() < 0.6, STRONG: True},
                cost_nano=dict(COSTS),
                agreement={CHEAP: conf},
                verifier_confidence={CHEAP: conf},
            )
        )
    return out


class TestConfidenceSignal:
    def test_gradable_items_use_self_consistency(self) -> None:
        r = record(1, task="math_word_problem", conf=0.66)
        assert r.gradable is True
        assert r.confidence(CHEAP) == 0.66

    def test_free_form_items_use_the_verifier_score(self) -> None:
        r = ItemRecord(
            slug="s",
            task_type="summarization",
            split="dev",
            success={CHEAP: True},
            cost_nano=dict(COSTS),
            agreement={CHEAP: 0.1},  # must be ignored
            verifier_confidence={CHEAP: 0.9},
        )
        assert r.gradable is False
        assert r.confidence(CHEAP) == 0.9

    def test_a_missing_signal_is_an_error_not_a_default(self) -> None:
        # Defaulting to 0 or 1 would silently escalate everything or nothing.
        r = ItemRecord(
            slug="s",
            task_type="summarization",
            split="dev",
            success={CHEAP: True},
            cost_nano=dict(COSTS),
        )
        with pytest.raises(KeyError, match="no verifier_confidence"):
            r.confidence(CHEAP)


class TestCascadeCost:
    def test_gradable_items_pay_for_k_cheap_samples(self) -> None:
        config = CascadeConfig(CHEAP, STRONG, k_samples=3)
        assert confidence_check_cost(record(1, task="math_word_problem"), config) == 170 * 3

    def test_free_form_items_pay_for_one_call_plus_the_verifier(self) -> None:
        config = CascadeConfig(CHEAP, STRONG, k_samples=3, verifier_cost_nano=1250)
        assert confidence_check_cost(record(1, task="summarization"), config) == 170 + 1250

    def test_escalating_costs_more_than_going_straight_to_strong(self) -> None:
        # The trap a naive cascade hides: you pay for the confidence check AND
        # the strong call. A cascade that always escalates is strictly worse
        # than no cascade at all.
        records = [record(i, task="math_word_problem", conf=0.0) for i in range(20)]
        always_escalate = simulate(records, CascadeConfig(CHEAP, STRONG, threshold=1.0))
        baseline = all_strong_baseline(records, STRONG)
        assert always_escalate.mean_cost_nano > baseline.mean_cost_nano
        assert always_escalate.escalation_rate == 1.0

    def test_never_escalating_costs_only_the_confidence_check(self) -> None:
        records = [record(i, task="math_word_problem", conf=1.0) for i in range(20)]
        out = simulate(records, CascadeConfig(CHEAP, STRONG, k_samples=3, threshold=0.0))
        assert out.mean_cost_nano == 170 * 3
        assert out.escalation_rate == 0.0

    def test_cheap_baseline_pays_no_confidence_check(self) -> None:
        records = [record(i) for i in range(10)]
        assert all_cheap_baseline(records, CHEAP).mean_cost_nano == 170


class TestCascadeBehaviour:
    def test_low_confidence_escalates_and_inherits_strong_quality(self) -> None:
        records = [record(i, conf=0.1, cheap_ok=False) for i in range(30)]
        out = simulate(records, CascadeConfig(CHEAP, STRONG, threshold=0.5))
        assert out.escalation_rate == 1.0
        assert out.quality == 1.0  # served the strong model on every item

    def test_high_confidence_serves_cheap_and_inherits_its_quality(self) -> None:
        records = [record(i, conf=0.99, cheap_ok=False) for i in range(30)]
        out = simulate(records, CascadeConfig(CHEAP, STRONG, threshold=0.5))
        assert out.escalation_rate == 0.0
        assert out.quality == 0.0  # cheap was wrong on every item, and we served it

    def test_threshold_is_monotone_in_escalation(self) -> None:
        records = world(200, "dev", seed=3)
        rates = [
            simulate(records, CascadeConfig(CHEAP, STRONG, threshold=t)).escalation_rate
            for t in (0.0, 0.25, 0.5, 0.75, 1.0)
        ]
        assert rates == sorted(rates)

    def test_a_perfectly_predictive_signal_gets_full_quality_at_a_discount(self) -> None:
        # Ground truth planted: confidence 1.0 exactly when cheap is right.
        records = [record(i, conf=1.0 if i % 2 else 0.0, cheap_ok=bool(i % 2)) for i in range(100)]
        out = simulate(records, CascadeConfig(CHEAP, STRONG, k_samples=1, threshold=0.5))
        assert out.quality == 1.0
        assert out.mean_cost_nano < all_strong_baseline(records, STRONG).mean_cost_nano

    def test_rejects_nonsense_configuration(self) -> None:
        with pytest.raises(ValueError):
            CascadeConfig(CHEAP, STRONG, k_samples=0)
        with pytest.raises(ValueError):
            CascadeConfig(CHEAP, STRONG, threshold=1.5)


class TestSweep:
    def test_produces_a_point_per_threshold(self) -> None:
        result = sweep(
            world(150, "dev", 1), CHEAP, STRONG, "dev", grid=(0.0, 0.5, 1.0), iterations=400
        )
        assert [p.threshold for p in result.points] == [0.0, 0.5, 1.0]

    def test_pareto_frontier_is_monotone_in_cost_and_quality(self) -> None:
        result = sweep(world(200, "dev", 2), CHEAP, STRONG, "dev", iterations=400)
        frontier = result.pareto()
        assert all(
            frontier[i].mean_cost_nano <= frontier[i + 1].mean_cost_nano
            and frontier[i].quality < frontier[i + 1].quality
            for i in range(len(frontier) - 1)
        )

    def test_selects_the_cheapest_eligible_point(self) -> None:
        result = sweep(world(300, "dev", 4), CHEAP, STRONG, "dev", iterations=400)
        chosen = result.operating_point()
        if chosen is not None:
            assert chosen.eligible
            cheaper = [p for p in result.eligible() if p.mean_cost_nano < chosen.mean_cost_nano]
            assert cheaper == []

    def test_selects_nothing_when_no_threshold_holds_quality(self) -> None:
        # A useless confidence signal on a cheap model that is always wrong.
        records = [record(i, conf=1.0, cheap_ok=False) for i in range(120)]
        result = sweep(records, CHEAP, STRONG, "dev", grid=(0.0, 0.25, 0.5), iterations=400)
        assert result.operating_point() is None

    def test_an_underpowered_split_refuses_rather_than_guessing(self) -> None:
        # Six items cannot show non-inferiority. INCONCLUSIVE must not be read
        # as eligible.
        records = [record(i, conf=0.9, cheap_ok=(i % 2 == 0)) for i in range(6)]
        result = sweep(records, CHEAP, STRONG, "dev", grid=(0.0, 0.5), iterations=400)
        assert all(not p.eligible or p.verdict != "INCONCLUSIVE" for p in result.points)

    def test_rejects_an_empty_split(self) -> None:
        with pytest.raises(ValueError, match="empty split"):
            sweep([], CHEAP, STRONG, "dev")


class TestSplitDiscipline:
    def test_selecting_a_split_filters_correctly(self) -> None:
        records = world(20, "dev", 1) + world(20, "test", 2)
        assert len(select_split(records, "dev")) == 20
        assert len(select_split(records, "test")) == 20

    def test_disjoint_splits_pass(self) -> None:
        assert_disjoint(world(10, "dev", 1), world(10, "test", 2))

    def test_a_planted_leak_is_caught(self) -> None:
        # The failure this guards against is invisible in the output: a fitted
        # threshold evaluated on its own training data looks excellent.
        fit = world(10, "dev", 1)
        leaked = [*world(10, "test", 2), fit[0]]
        with pytest.raises(SplitLeakError, match="both the fitting and reporting"):
            assert_disjoint(fit, leaked)

    def test_fit_and_report_use_different_splits(self) -> None:
        assert FIT_SPLIT != REPORT_SPLIT
        # calibration is spoken for by P3's judge labels.
        assert "calibration" not in (FIT_SPLIT, REPORT_SPLIT)


class TestOfflinePolicy:
    def test_assigns_the_cheapest_rung_that_clears_the_floor(self) -> None:
        records = [record(i, task="math_word_problem", cheap_ok=True) for i in range(120)]
        [assignment] = fit_offline_policy(records, LADDER, STRONG, floor=0.9, iterations=400)
        assert assignment.assigned_model == CHEAP

    def test_escalates_when_the_cheap_rung_fails_the_floor(self) -> None:
        records = [
            record(i, task="math_word_problem", cheap_ok=(i % 4 != 0), mid_ok=True)
            for i in range(120)
        ]
        [assignment] = fit_offline_policy(records, LADDER, STRONG, floor=0.9, iterations=400)
        assert assignment.assigned_model in (MID, STRONG)

    def test_uses_the_lower_bound_not_the_point_estimate(self) -> None:
        # 30 items at exactly 90% has a point estimate ON the floor but a lower
        # bound well below it, so it must NOT be demoted.
        records = [record(i, cheap_ok=(i % 10 != 0)) for i in range(30)]
        [assignment] = fit_offline_policy(records, LADDER, STRONG, floor=0.9, iterations=1000)
        assert assignment.assigned_model != CHEAP

    def test_refuses_to_demote_a_route_with_too_little_evidence(self) -> None:
        records = [record(i, cheap_ok=True) for i in range(5)]
        [assignment] = fit_offline_policy(records, LADDER, STRONG, floor=0.5, min_items=25)
        assert assignment.assigned_model == STRONG
        assert "insufficient evidence" in assignment.reason

    def test_falls_back_to_the_safe_default_when_nothing_clears(self) -> None:
        records = [record(i, cheap_ok=False, mid_ok=False) for i in range(60)]
        [assignment] = fit_offline_policy(records, [CHEAP, MID], STRONG, floor=0.9, iterations=400)
        assert assignment.assigned_model == STRONG
        assert "no rung" in assignment.reason

    def test_assigns_each_route_independently(self) -> None:
        records = [record(i, task="math_word_problem", cheap_ok=True) for i in range(60)] + [
            record(100 + i, task="summarization", cheap_ok=False, mid_ok=False) for i in range(60)
        ]
        assignments = {
            a.route_key: a.assigned_model
            for a in fit_offline_policy(records, LADDER, STRONG, floor=0.9, iterations=400)
        }
        assert assignments["math_word_problem"] == CHEAP
        assert assignments["summarization"] == STRONG

    def test_records_every_rung_it_considered(self) -> None:
        records = [record(i, cheap_ok=True) for i in range(60)]
        [assignment] = fit_offline_policy(records, LADDER, STRONG, floor=0.9, iterations=400)
        assert [c["model"] for c in assignment.considered]

    def test_policy_cost_reflects_the_assignments(self) -> None:
        records = [record(i, cheap_ok=True) for i in range(60)]
        assignments = fit_offline_policy(records, LADDER, STRONG, floor=0.9, iterations=400)
        cost, quality = policy_cost(records, assignments)
        assert cost == COSTS[CHEAP]
        assert quality == 1.0


class TestFitAndReport:
    @staticmethod
    def run(n: int = 300, predictive: float = 0.9, **kw: object) -> FitOutput:
        records = world(n, FIT_SPLIT, seed=11, predictive=predictive) + world(
            n, REPORT_SPLIT, seed=12, predictive=predictive
        )
        return fit_and_report(
            records,
            CHEAP,
            STRONG,
            LADDER,
            STRONG,
            created_at="2026-09-15T00:00:00+00:00",
            git_sha="abc123",
            corpus_sha256="f" * 64,
            judge_model="claude-opus-5",
            rubric_version="v1",
            reference_model="claude-opus-5",
            floor=0.9,
            iterations=400,
            **kw,  # type: ignore[arg-type]
        )

    def test_records_which_split_fitted_and_which_reported(self) -> None:
        out = self.run()
        roles = {s.split: s.role for s in out.pareto.sweeps}
        assert roles[FIT_SPLIT] == "fit"
        assert roles[REPORT_SPLIT] == "report"

    def test_the_quotable_number_comes_from_the_held_out_split(self) -> None:
        out = self.run()
        if out.pareto.held_out is not None:
            report_n = next(s.n for s in out.pareto.sweeps if s.role == "report")
            assert out.pareto.held_out.n == report_n

    def test_refuses_to_fit_without_a_fit_split(self) -> None:
        with pytest.raises(ValueError, match="no items in the 'dev' split"):
            fit_and_report(
                world(50, REPORT_SPLIT, 1),
                CHEAP,
                STRONG,
                LADDER,
                STRONG,
                created_at="x",
                git_sha="y",
                corpus_sha256="z",
                judge_model="j",
                rubric_version="v1",
                reference_model="r",
                floor=0.9,
                iterations=200,
            )

    def test_refuses_to_report_without_a_held_out_split(self) -> None:
        with pytest.raises(ValueError, match="no items in the 'test' split"):
            fit_and_report(
                world(50, FIT_SPLIT, 1),
                CHEAP,
                STRONG,
                LADDER,
                STRONG,
                created_at="x",
                git_sha="y",
                corpus_sha256="z",
                judge_model="j",
                rubric_version="v1",
                reference_model="r",
                floor=0.9,
                iterations=200,
            )

    def test_explains_itself_when_no_threshold_qualifies(self) -> None:
        out = self.run(n=120, predictive=0.0)
        if out.pareto.chosen_threshold is None:
            assert out.pareto.no_eligible_threshold_reason
            assert "correct outcome, not a failure" in out.pareto.no_eligible_threshold_reason

    def test_artifacts_round_trip(self, tmp_path: Path) -> None:
        out = self.run()
        pareto_path = out.pareto.write(tmp_path / "pareto.json")
        policy_path = out.policy.write(tmp_path / "policy.json")
        assert ParetoArtifact.read(pareto_path) == out.pareto
        assert PolicyArtifact.read(policy_path) == out.policy

    def test_policy_always_carries_a_strong_safe_default(self) -> None:
        out = self.run()
        assert out.policy.safe_default == STRONG

    def test_cascade_is_opt_in_not_on_by_default(self) -> None:
        out = self.run()
        if out.policy.cascade is not None:
            assert out.policy.cascade.enabled is False

    def test_the_artifact_states_that_cost_includes_the_confidence_check(self) -> None:
        out = self.run()
        assert any("confidence check" in n for n in out.pareto.notes)
