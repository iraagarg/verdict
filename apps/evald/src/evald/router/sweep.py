"""Threshold sweep, Pareto frontier, and operating-point selection.

The rule, from the P5 brief: minimise cost subject to quality staying within the
confidence interval of the strong-model baseline. That is a non-inferiority
criterion, so it is evaluated with P4's verdict layer rather than reimplemented
— a threshold is ELIGIBLE when the paired comparison against the all-strong
baseline comes back EQUIVALENT or IMPROVEMENT.

Using P4 here matters for more than code reuse. `INCONCLUSIVE` is a distinct
outcome, so a threshold whose interval is simply too wide to judge is refused
rather than quietly accepted as "no significant difference" (D-035). An
underpowered sweep therefore selects nothing instead of selecting the cheapest
point by accident.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evald.router.cascade import (
    CascadeConfig,
    CascadeOutcome,
    all_strong_baseline,
    simulate,
)
from evald.router.records import ItemRecord
from evald.stats.verdict import DEFAULT_MARGIN, Verdict, compare_runs

#: Thresholds swept by default. 0.0 never escalates, 1.0 always does.
DEFAULT_GRID: tuple[float, ...] = tuple(round(i / 20, 3) for i in range(21))


@dataclass(frozen=True, slots=True)
class SweepPoint:
    threshold: float
    n: int
    quality: float
    mean_cost_nano: float
    escalation_rate: float
    #: Cost as a fraction of always using the strong model. 1.0 = no saving.
    cost_ratio: float
    verdict: str
    effect: float
    ci_low: float
    ci_high: float
    mcnemar_p: float
    #: True when the comparison against the all-strong baseline is EQUIVALENT or
    #: IMPROVEMENT — i.e. quality has been SHOWN to hold, not merely not refuted.
    eligible: bool

    @property
    def savings(self) -> float:
        return 1.0 - self.cost_ratio


@dataclass(slots=True)
class SweepResult:
    split: str
    points: list[SweepPoint] = field(default_factory=list)
    baseline_quality: float = 0.0
    baseline_mean_cost_nano: float = 0.0
    margin: float = DEFAULT_MARGIN

    def eligible(self) -> list[SweepPoint]:
        return [p for p in self.points if p.eligible]

    def pareto(self) -> list[SweepPoint]:
        """Points not dominated by another point on both cost and quality."""
        frontier: list[SweepPoint] = []
        for p in sorted(self.points, key=lambda x: (x.mean_cost_nano, -x.quality)):
            if not frontier or p.quality > frontier[-1].quality:
                frontier.append(p)
        return frontier

    def operating_point(self) -> SweepPoint | None:
        """Cheapest threshold whose quality is shown to hold. None if none qualifies.

        Returning None is a real outcome, not an error. If no threshold can be
        shown non-inferior, the honest policy is to keep using the strong model.
        """
        eligible = self.eligible()
        if not eligible:
            return None
        return min(eligible, key=lambda p: (p.mean_cost_nano, -p.quality, p.threshold))


def evaluate_threshold(
    records: list[ItemRecord],
    config: CascadeConfig,
    baseline: CascadeOutcome,
    margin: float,
    alpha: float,
    iterations: int,
    seed: int,
) -> tuple[SweepPoint, Verdict]:
    outcome = simulate(records, config)

    verdict = compare_runs(
        baseline.success,
        outcome.success,
        margin=margin,
        alpha=alpha,
        iterations=iterations,
        seed=seed,
        label_baseline=f"all-{config.strong_model}",
        label_candidate=f"cascade@{config.threshold}",
    )

    baseline_cost = baseline.mean_cost_nano
    point = SweepPoint(
        threshold=config.threshold,
        n=outcome.n,
        quality=round(outcome.quality, 6),
        mean_cost_nano=round(outcome.mean_cost_nano, 4),
        escalation_rate=round(outcome.escalation_rate, 6),
        cost_ratio=round(outcome.mean_cost_nano / baseline_cost, 6) if baseline_cost else 0.0,
        verdict=verdict.outcome,
        effect=verdict.effect,
        ci_low=verdict.ci_low,
        ci_high=verdict.ci_high,
        mcnemar_p=verdict.mcnemar_p,
        eligible=verdict.outcome in ("EQUIVALENT", "IMPROVEMENT"),
    )
    return point, verdict


def sweep(
    records: list[ItemRecord],
    cheap_model: str,
    strong_model: str,
    split_name: str,
    grid: tuple[float, ...] = DEFAULT_GRID,
    k_samples: int = 3,
    verifier_cost_nano: int = 0,
    margin: float = DEFAULT_MARGIN,
    alpha: float = 0.05,
    iterations: int = 4000,
    seed: int = 20260915,
) -> SweepResult:
    """Evaluate every threshold on `records`. Calls no models."""
    if not records:
        raise ValueError("cannot sweep an empty split")

    baseline = all_strong_baseline(records, strong_model)
    result = SweepResult(
        split=split_name,
        baseline_quality=round(baseline.quality, 6),
        baseline_mean_cost_nano=round(baseline.mean_cost_nano, 4),
        margin=margin,
    )

    for threshold in grid:
        config = CascadeConfig(
            cheap_model=cheap_model,
            strong_model=strong_model,
            k_samples=k_samples,
            verifier_cost_nano=verifier_cost_nano,
            threshold=threshold,
        )
        point, _ = evaluate_threshold(records, config, baseline, margin, alpha, iterations, seed)
        result.points.append(point)

    return result
