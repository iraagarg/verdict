"""Choosing the similarity threshold, and reporting what it costs.

The rule (D-046): take the most permissive threshold whose false-hit rate is
demonstrably under tolerance, judged on the UPPER bound of its confidence
interval rather than the point estimate.

Using the upper bound mirrors D-004's routing rule, which demotes only on a
lower bound. Both err the same way: a small or noisy sample REFUSES to loosen
the cache rather than loosening it on luck. A false hit returns a confidently
wrong answer to a question nobody asked, which is strictly worse than a miss —
so the uncertainty has to cost us hits, not safety.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evald.cache.pairs import LabelledPair
from evald.stats.bootstrap import bootstrap_rate_ci
from evald.stats.proportion import clopper_pearson, min_trials_for_upper_bound

#: Cosine thresholds swept by default. Near-duplicate similarities cluster high,
#: so the grid is dense where the decision actually happens.
DEFAULT_GRID: tuple[float, ...] = tuple(round(0.80 + i * 0.005, 4) for i in range(41))

#: Most a false-hit rate may be, judged on the CI upper bound. A product
#: judgement, not a statistical one.
DEFAULT_MAX_FALSE_HIT_RATE = 0.01

#: A threshold must actually hit something to be worth deploying. A cache with
#: no hits saves nothing and still costs an embedding call and a vector search
#: on every request, so it is strictly worse than no cache — "safe" is not
#: sufficient, it also has to be useful.
MIN_USEFUL_HIT_RATE = 0.05


@dataclass(frozen=True, slots=True)
class ThresholdPoint:
    threshold: float
    #: Duplicates correctly served from cache / all duplicates.
    hit_rate: float
    hit_rate_ci_low: float
    hit_rate_ci_high: float
    n_duplicates: int

    #: Different questions wrongly served from cache / all different pairs.
    #: The number that decides whether this cache is safe.
    false_hit_rate: float
    false_hit_ci_low: float
    false_hit_ci_high: float
    n_different: int

    #: True when the false-hit CI UPPER bound is within tolerance.
    acceptable: bool

    @property
    def miss_rate(self) -> float:
        return round(1.0 - self.hit_rate, 6)


@dataclass(slots=True)
class CalibrationCurve:
    points: list[ThresholdPoint] = field(default_factory=list)
    max_false_hit_rate: float = DEFAULT_MAX_FALSE_HIT_RATE
    min_hit_rate: float = MIN_USEFUL_HIT_RATE
    n_duplicates: int = 0
    n_different: int = 0

    def acceptable(self) -> list[ThresholdPoint]:
        return [p for p in self.points if p.acceptable]

    def chosen(self) -> ThresholdPoint | None:
        """Best hit rate among thresholds that are both PROVABLY SAFE and USEFUL.

        Returns None when no threshold qualifies. That is a real result: the
        honest response is to run no semantic cache at all, and it must not be
        papered over by falling back to "the strictest value we tried" — which
        is invariably a threshold nothing ever hits.
        """
        usable = [p for p in self.acceptable() if p.hit_rate >= self.min_hit_rate]
        if not usable:
            return None
        return max(usable, key=lambda p: (p.hit_rate, -p.threshold))

    def render(self) -> str:
        lines = [
            "SEMANTIC CACHE CALIBRATION",
            f"  tolerance   false-hit Clopper-Pearson upper bound <= {self.max_false_hit_rate:.3f}",
            f"  pairs       {self.n_duplicates} duplicates, {self.n_different} hard negatives",
            "",
            f"  {'thresh':>8}{'hit%':>9}{'false-hit%':>13}{'fh CP hi':>11}  ok",
            f"  {'-' * 52}",
        ]
        for p in self.points:
            if round(p.threshold * 200) % 5:  # thin the printout
                continue
            lines.append(
                f"  {p.threshold:>8.3f}{p.hit_rate * 100:>8.1f}%"
                f"{p.false_hit_rate * 100:>12.2f}%{p.false_hit_ci_high * 100:>10.2f}%"
                f"  {'yes' if p.acceptable else 'no'}"
            )
        chosen = self.chosen()
        lines.append("")
        if chosen is None:
            needed = min_trials_for_upper_bound(self.max_false_hit_rate)
            lines.append(
                "  NO SAFE THRESHOLD. No setting keeps the false-hit rate provably under "
                f"{self.max_false_hit_rate:.1%}."
            )
            if self.n_different < needed:
                lines.append(
                    f"  This is a SAMPLE SIZE limit, not a cache failure: proving a rate below "
                    f"{self.max_false_hit_rate:.1%} needs at least {needed} hard negatives even "
                    f"with ZERO observed false hits, and there are {self.n_different}. "
                    f"Generate more negatives, or raise the tolerance deliberately."
                )
            elif self.acceptable():
                best = max(self.acceptable(), key=lambda p: p.hit_rate)
                lines.append(
                    f"  Thresholds exist that are SAFE but not USEFUL: the best of them hits "
                    f"only {best.hit_rate:.1%}, below the {self.min_hit_rate:.0%} floor. A cache "
                    f"that rarely hits still costs an embedding call and a vector search on "
                    f"every request, so it is worse than no cache."
                )
            else:
                lines.append(
                    "  There is enough evidence, and every threshold fails it. The correct "
                    "action is to run no semantic cache, not to pick the strictest value tried."
                )
        else:
            lines.append(
                f"  CHOSEN {chosen.threshold:.3f}  ->  hit rate {chosen.hit_rate:.1%}, "
                f"false-hit rate {chosen.false_hit_rate:.2%} "
                f"(CI up to {chosen.false_hit_ci_high:.2%})"
            )
        return "\n".join(lines)


def evaluate_threshold(
    duplicates: list[LabelledPair],
    different: list[LabelledPair],
    threshold: float,
    max_false_hit_rate: float,
    alpha: float = 0.05,
    iterations: int = 4000,
    seed: int = 20260921,
) -> ThresholdPoint:
    """One point on the curve. A pair 'hits' when its similarity >= threshold."""
    dup_hits = [(p.similarity or 0.0) >= threshold for p in duplicates]
    diff_hits = [(p.similarity or 0.0) >= threshold for p in different]

    # Hit rate is a mid-range proportion, so the bootstrap is fine and keeps
    # this consistent with how every other rate in the project is bounded.
    hit_rate, hit_lo, hit_hi = (
        bootstrap_rate_ci(dup_hits, iterations=iterations, alpha=alpha, seed=seed)
        if dup_hits
        else (0.0, 0.0, 0.0)
    )

    # The false-hit rate is a RARE-EVENT proportion and the threshold is chosen
    # on its upper bound, so the bootstrap is the wrong tool: with zero observed
    # false hits every resample also has zero, and it reports a bound of exactly
    # 0.00% for a threshold that has merely not been tested hard enough.
    # Clopper-Pearson inverts the binomial test and is correct at the boundary —
    # 0 of 150 becomes 2.43%, not 0% (DECISIONS.md D-047).
    fh_rate, fh_lo, fh_hi = clopper_pearson(sum(diff_hits), len(diff_hits), alpha=alpha)

    return ThresholdPoint(
        threshold=threshold,
        hit_rate=hit_rate,
        hit_rate_ci_low=hit_lo,
        hit_rate_ci_high=hit_hi,
        n_duplicates=len(duplicates),
        false_hit_rate=fh_rate,
        false_hit_ci_low=fh_lo,
        false_hit_ci_high=fh_hi,
        n_different=len(different),
        # The upper bound, not the point estimate: uncertainty must cost hits,
        # never safety.
        acceptable=fh_hi <= max_false_hit_rate,
    )


def calibrate(
    pairs: list[LabelledPair],
    grid: tuple[float, ...] = DEFAULT_GRID,
    max_false_hit_rate: float = DEFAULT_MAX_FALSE_HIT_RATE,
    min_hit_rate: float = MIN_USEFUL_HIT_RATE,
    alpha: float = 0.05,
    iterations: int = 4000,
    seed: int = 20260921,
) -> CalibrationCurve:
    scored = [p for p in pairs if p.similarity is not None]
    duplicates = [p for p in scored if p.label == "duplicate"]
    different = [p for p in scored if p.label == "different"]

    if not duplicates or not different:
        raise ValueError(
            f"calibration needs both kinds of pair; got {len(duplicates)} duplicates "
            f"and {len(different)} hard negatives"
        )

    curve = CalibrationCurve(
        max_false_hit_rate=max_false_hit_rate,
        min_hit_rate=min_hit_rate,
        n_duplicates=len(duplicates),
        n_different=len(different),
    )
    for threshold in grid:
        curve.points.append(
            evaluate_threshold(
                duplicates, different, threshold, max_false_hit_rate, alpha, iterations, seed
            )
        )
    return curve
