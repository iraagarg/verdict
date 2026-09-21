"""Exact confidence bounds for a proportion, for the rare-event case.

WHY THIS EXISTS. The percentile bootstrap cannot produce a non-zero upper bound
when it never observes the event: resample 0 successes out of 150 any number of
times and every resample still has 0, so the interval is [0, 0]. That is not a
confidence interval, it is an artefact of the method — 0 out of 150 is entirely
consistent with a true rate near 2%.

That mattered the moment the semantic-cache threshold started being chosen on
the false-hit rate's UPPER bound (D-046). The bootstrap would have reported
"provably 0% false hits" for a threshold that had merely not been tested hard
enough, and the cache would have been loosened on the strength of it.

Clopper-Pearson inverts the binomial test directly, so it is correct at the
boundary. It is conservative by construction — the interval is guaranteed to
have at LEAST the nominal coverage — which is the right kind of wrong for a
safety bound.
"""

from __future__ import annotations

import math


def binomial_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p). Exact integer coefficients."""
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0 if k < n else 1.0
    return sum(math.comb(n, i) * p**i * (1.0 - p) ** (n - i) for i in range(k + 1))


def clopper_pearson_upper(successes: int, trials: int, alpha: float = 0.05) -> float:
    """Upper bound of the two-sided Clopper-Pearson interval.

    Solves P(X <= k | n, p) = alpha/2 for p by bisection. The CDF is monotonically
    decreasing in p, so bisection converges and needs no special-casing.

    With k = 0 this reduces to the familiar 1 - (alpha/2)^(1/n), which is the
    "rule of three" (~3/n at 95%) that the bootstrap was missing.
    """
    if trials <= 0:
        raise ValueError("trials must be positive")
    if not 0 <= successes <= trials:
        raise ValueError(f"successes must be in [0, {trials}], got {successes}")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")

    if successes == trials:
        return 1.0

    target = alpha / 2.0
    lo, hi = 0.0, 1.0
    for _ in range(200):  # ~1e-60 precision; cheap and avoids a tolerance argument
        mid = (lo + hi) / 2.0
        if binomial_cdf(successes, trials, mid) > target:
            lo = mid
        else:
            hi = mid
    return round(hi, 8)


def clopper_pearson_lower(successes: int, trials: int, alpha: float = 0.05) -> float:
    """Lower bound of the two-sided Clopper-Pearson interval."""
    if successes == 0:
        return 0.0
    # By symmetry: the lower bound for k of n is 1 - upper bound for (n-k) of n.
    return round(1.0 - clopper_pearson_upper(trials - successes, trials, alpha), 8)


def clopper_pearson(successes: int, trials: int, alpha: float = 0.05) -> tuple[float, float, float]:
    """(rate, lower, upper). The interval a safety decision should be made on."""
    rate = successes / trials if trials else 0.0
    return (
        round(rate, 8),
        clopper_pearson_lower(successes, trials, alpha),
        clopper_pearson_upper(successes, trials, alpha),
    )


def min_trials_for_upper_bound(target: float, alpha: float = 0.05) -> int:
    """Fewest trials that could ever prove a rate below `target`, seeing ZERO events.

    A safety bound has a floor set by sample size, not by results. With zero
    events in n trials the Clopper-Pearson upper bound is 1 - (alpha/2)^(1/n),
    so no number of clean observations below this n can demonstrate the target —
    the honest report is "not enough evidence", not "zero percent".

    At 95% this is close to the rule of three: ~3/target.
    """
    if not 0.0 < target < 1.0:
        raise ValueError("target must be in (0, 1)")
    n = 1
    while clopper_pearson_upper(0, n, alpha) > target:
        n += 1
        if n > 1_000_000:  # pathological target; stop rather than spin
            break
    return n
