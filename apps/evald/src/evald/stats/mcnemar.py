"""McNemar's test for paired binary outcomes.

Implemented from first principles rather than imported, so scipy stays an
INDEPENDENT reference in the test suite. Testing a scipy call against scipy
proves nothing.

The idea in one paragraph: with two arms scored on the same items, every item
falls into one of four cells — both succeeded, both failed, only A succeeded,
only B succeeded. The two concordant cells carry no information about which arm
is better; an item both arms get right tells you the item was easy, not that the
arms are equal. All the evidence lives in the DISCORDANT cells. Under the null
that the arms are equally good, a discordant item is equally likely to fall
either way, so the test is simply: of the discordant items, is the split further
from 50/50 than chance would explain?

That conditioning is exactly why McNemar suits this problem. A plain
two-proportion test would treat the concordant items as evidence, diluting a
real effect with items that could never have shown it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class McNemarCounts:
    """The 2x2 table. `b` and `c` are the discordant cells."""

    #: Both arms failed.
    n00: int
    #: A failed, B succeeded. (Evidence FOR B.)
    b: int
    #: A succeeded, B failed. (Evidence FOR A.)
    c: int
    #: Both arms succeeded.
    n11: int

    @property
    def n(self) -> int:
        return self.n00 + self.b + self.c + self.n11

    @property
    def discordant(self) -> int:
        return self.b + self.c

    @property
    def concordant(self) -> int:
        return self.n00 + self.n11


@dataclass(frozen=True, slots=True)
class McNemarResult:
    counts: McNemarCounts
    #: Two-sided p-value.
    p_value: float
    #: "exact" (binomial) or "chi2" (with continuity correction).
    method: str
    statistic: float | None = None

    @property
    def n_discordant(self) -> int:
        return self.counts.discordant


class McNemarError(ValueError):
    """Invalid paired input."""


def tabulate(a: list[bool], b: list[bool]) -> McNemarCounts:
    if len(a) != len(b):
        raise McNemarError(f"paired data must be the same length: got {len(a)} and {len(b)}")
    n00 = n11 = disc_b = disc_c = 0
    for ai, bi in zip(a, b, strict=True):
        if ai and bi:
            n11 += 1
        elif not ai and not bi:
            n00 += 1
        elif bi and not ai:
            disc_b += 1
        else:
            disc_c += 1
    return McNemarCounts(n00=n00, b=disc_b, c=disc_c, n11=n11)


def exact_p(b: int, c: int) -> float:
    """Two-sided exact binomial p-value for `b` successes in `b + c` trials at p = 0.5.

    Exact rather than the chi-square approximation because the discordant count
    is often small — and the approximation is unreliable precisely there, which
    is where a wrong answer would matter most.

    Computed with integer binomial coefficients, so there is no floating-point
    error beyond the final division.
    """
    n = b + c
    if n == 0:
        # No discordant pairs at all: the arms behaved identically on every item.
        # There is no evidence of a difference, and p = 1 is the honest answer.
        return 1.0

    k = min(b, c)
    # The binomial at p = 0.5 is symmetric, so the two-sided p is twice the tail.
    tail = sum(math.comb(n, i) for i in range(k + 1))
    return float(min(1.0, 2.0 * tail / (2**n)))


def chi2_p(b: int, c: int, continuity: bool = True) -> tuple[float, float]:
    """Chi-square approximation with Yates' continuity correction. Returns (statistic, p)."""
    n = b + c
    if n == 0:
        return (0.0, 1.0)

    diff = abs(b - c)
    numerator = (diff - 1.0) ** 2 if continuity else float(diff**2)
    # With continuity correction, |b-c| < 1 would give a negative statistic.
    statistic = max(0.0, numerator / n)

    # chi-square with 1 df is Z^2, so the survival function is erfc(sqrt(x/2)).
    p = float(math.erfc(math.sqrt(statistic / 2.0)))
    return (statistic, min(1.0, p))


#: Below this many discordant pairs the chi-square approximation is not trustworthy.
EXACT_THRESHOLD = 25


def mcnemar(a: list[bool], b: list[bool], force: str | None = None) -> McNemarResult:
    """Run McNemar's test on two paired binary arms.

    Picks the exact test when the discordant count is small, which is the case
    that actually arises here: on a few hundred items with two similar models,
    most items are concordant and the discordant count is often under 25.
    """
    counts = tabulate(a, b)

    method = force or ("exact" if counts.discordant < EXACT_THRESHOLD else "chi2")
    if method == "exact":
        return McNemarResult(counts=counts, p_value=exact_p(counts.b, counts.c), method="exact")
    if method == "chi2":
        statistic, p = chi2_p(counts.b, counts.c)
        return McNemarResult(counts=counts, p_value=p, method="chi2", statistic=round(statistic, 6))
    raise McNemarError(f"unknown method {method!r}; expected 'exact' or 'chi2'")
