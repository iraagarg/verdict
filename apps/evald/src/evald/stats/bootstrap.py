"""Paired percentile bootstrap.

WHY PAIRED: every item is scored by BOTH arms, so the two samples are not
independent. An item that is simply hard drags both arms down together, and an
easy one lifts both. Resampling the arms separately would treat that shared
item difficulty as noise in the comparison, when it is the largest single source
of variance and it cancels exactly. We resample ITEMS, carrying each item's pair
of outcomes together, so item difficulty cancels inside every resample the same
way it cancels in the real data.

WHY BOOTSTRAP RATHER THAN A t-TEST: see DECISIONS.md D-034 for the full
argument. In short, the statistic here is a difference of bounded proportions
over a discrete three-valued outcome; the t-test's assumptions about the shape
of that distribution do not hold, and at the sample sizes and effect sizes we
care about the difference is not academic.

WHY PERCENTILE RATHER THAN BCa: D-011. At n around 150 the acceleration
correction moves the interval marginally, and percentile is something that can
be defended line by line under questioning.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """A confidence interval on the paired difference `mean(b) - mean(a)`."""

    n: int
    #: Point estimate of the difference. Positive means arm B scored higher.
    effect: float
    ci_low: float
    ci_high: float
    alpha: float
    iterations: int
    seed: int
    #: Mean of each arm, for readers who want the levels not just the delta.
    mean_a: float
    mean_b: float

    @property
    def ci_width(self) -> float:
        return self.ci_high - self.ci_low

    @property
    def excludes_zero(self) -> bool:
        return self.ci_low > 0.0 or self.ci_high < 0.0


class PairedDataError(ValueError):
    """The two arms are not a valid paired sample."""


def _validate(a: list[float], b: list[float]) -> None:
    if len(a) != len(b):
        raise PairedDataError(
            f"paired data must be the same length: got {len(a)} and {len(b)}. "
            f"Every item must be scored by BOTH arms, or the pairing is broken."
        )
    if not a:
        raise PairedDataError("cannot bootstrap an empty sample")


def paired_bootstrap(
    a: list[float],
    b: list[float],
    iterations: int = 10_000,
    alpha: float = 0.05,
    seed: int = 20260915,
) -> BootstrapResult:
    """Percentile CI on mean(b) - mean(a), resampling items with replacement.

    `a` and `b` are per-item scores from the two arms, aligned by index.
    """
    _validate(a, b)
    n = len(a)

    # Resample INDICES, not values. This is the entire point of "paired": index
    # i contributes (a[i], b[i]) together or not at all.
    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(iterations):
        idx = [rng.randrange(n) for _ in range(n)]
        deltas.append(sum(b[i] - a[i] for i in idx) / n)

    deltas.sort()
    lo_i = int((alpha / 2) * iterations)
    hi_i = min(iterations - 1, int((1 - alpha / 2) * iterations))

    mean_a = sum(a) / n
    mean_b = sum(b) / n
    return BootstrapResult(
        n=n,
        effect=round(mean_b - mean_a, 6),
        ci_low=round(deltas[lo_i], 6),
        ci_high=round(deltas[hi_i], 6),
        alpha=alpha,
        iterations=iterations,
        seed=seed,
        mean_a=round(mean_a, 6),
        mean_b=round(mean_b, 6),
    )


def bootstrap_rate_ci(
    successes: list[bool],
    iterations: int = 10_000,
    alpha: float = 0.05,
    seed: int = 20260915,
) -> tuple[float, float, float]:
    """CI on a single rate. Returns (rate, ci_low, ci_high).

    This is what D-004's routing rule consumes: a route is demoted only when the
    LOWER bound clears the floor, never the point estimate.
    """
    if not successes:
        raise PairedDataError("cannot bootstrap an empty sample")

    n = len(successes)
    values = [1.0 if s else 0.0 for s in successes]
    rng = random.Random(seed)

    rates: list[float] = []
    for _ in range(iterations):
        rates.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)

    rates.sort()
    lo = rates[int((alpha / 2) * iterations)]
    hi = rates[min(iterations - 1, int((1 - alpha / 2) * iterations))]
    return (round(sum(values) / n, 6), round(lo, 6), round(hi, 6))
