"""Regression-vs-noise verdicts.

Four outcomes, not three. "No significant difference" conflates two findings
that are opposites:

  * We measured the difference precisely and it is small.  -> EQUIVALENT
  * We measured nothing precisely enough to say.           -> INCONCLUSIVE

The first is evidence of absence and supports demoting a route. The second is
absence of evidence and must not. Reporting both as "no significant difference"
is how underpowered runs get mistaken for passes, and it is the single easiest
way for this project to make a false claim (DECISIONS.md D-035).

The verdict is driven by the confidence interval rather than by the p-value,
because the CI is on the EFFECT SIZE and is therefore directly comparable to a
margin the user chose. A p-value answers "is it exactly zero?", which nobody
cares about: a 0.3-point quality difference on 50,000 items is statistically
significant and practically irrelevant. McNemar's p is reported alongside as
corroboration and is what D-012's false-discovery correction operates on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from evald.stats.bootstrap import BootstrapResult, paired_bootstrap
from evald.stats.mcnemar import McNemarResult, mcnemar

Outcome = Literal["REGRESSION", "IMPROVEMENT", "EQUIVALENT", "INCONCLUSIVE"]

#: Default practical-significance margin, in win-or-tie rate points.
#: A 3-point difference is the smallest we are prepared to call meaningful.
#: This is a product judgement, not a statistical one, and it belongs in config.
DEFAULT_MARGIN = 0.03


@dataclass(frozen=True, slots=True)
class Verdict:
    outcome: Outcome
    #: mean(candidate) - mean(baseline), in rate points. Negative = candidate worse.
    effect: float
    ci_low: float
    ci_high: float
    n: int
    margin: float
    alpha: float

    #: True when the CI excludes zero. Kept separate from `outcome` because a
    #: difference can be real and still too small to matter.
    statistically_significant: bool
    #: The smallest effect this sample could resolve: the CI half-width.
    minimum_detectable_effect: float
    #: False when the CI is wider than the margin. Such a run can still reach
    #: REGRESSION or IMPROVEMENT if the effect is large enough — it simply can
    #: never reach EQUIVALENT, because an interval wider than the margin cannot
    #: fit inside it however the estimate falls.
    can_demonstrate_equivalence: bool

    mcnemar_p: float
    mcnemar_method: str
    n_discordant: int
    n_concordant: int

    baseline_rate: float
    candidate_rate: float
    label_baseline: str = "baseline"
    label_candidate: str = "candidate"

    def summary(self) -> str:
        arrow = "+" if self.effect >= 0 else ""
        lines = [
            f"VERDICT: {self.outcome}",
            f"  {self.label_baseline} -> {self.label_candidate}",
            f"  effect        {arrow}{self.effect:.4f}  "
            f"[{self.ci_low:+.4f}, {self.ci_high:+.4f}]  "
            f"({round((1 - self.alpha) * 100)}% CI)",
            f"  rates         {self.baseline_rate:.4f} -> {self.candidate_rate:.4f}",
            f"  n             {self.n} items, {self.n_discordant} discordant",
            f"  margin        +/-{self.margin:.4f}",
            f"  resolution    {self.minimum_detectable_effect:.4f}"
            + (
                ""
                if self.can_demonstrate_equivalence
                else "   (too wide to ever show EQUIVALENT at this margin)"
            ),
            f"  McNemar p     {self.mcnemar_p:.6f}  ({self.mcnemar_method}, "
            f"{self.n_discordant} discordant)",
            "",
            f"  {_explain(self)}",
        ]
        return "\n".join(lines)


def _explain(v: Verdict) -> str:
    if v.outcome == "REGRESSION":
        return (
            f"The whole interval is worse than -{v.margin:.3f}, so the candidate is worse "
            f"by an amount that matters."
        )
    if v.outcome == "IMPROVEMENT":
        return (
            f"The whole interval is better than +{v.margin:.3f}, so the candidate is better "
            f"by an amount that matters."
        )
    if v.outcome == "EQUIVALENT":
        return (
            f"The whole interval fits inside +/-{v.margin:.3f}. This is evidence the two are "
            f"close enough — not merely a failure to find a difference."
        )
    return (
        f"The interval straddles a decision boundary, so this run cannot tell a real "
        f"difference from noise at a margin of {v.margin:.3f}. It needs more items, not a "
        f"different conclusion. Do NOT read this as 'no difference'."
    )


def classify(
    ci_low: float,
    ci_high: float,
    margin: float,
) -> Outcome:
    """Map a confidence interval onto a verdict, given a practical-significance margin.

    The rule is deliberately conservative: a verdict is only issued when the
    ENTIRE interval sits on one side of the relevant boundary. Anything that
    straddles a boundary is INCONCLUSIVE, because straddling is precisely the
    state of not knowing.
    """
    if margin < 0:
        raise ValueError("margin must be non-negative")
    if ci_low > ci_high:
        raise ValueError(f"malformed interval: [{ci_low}, {ci_high}]")

    if ci_high < -margin:
        return "REGRESSION"
    if ci_low > margin:
        return "IMPROVEMENT"
    if -margin <= ci_low and ci_high <= margin:
        return "EQUIVALENT"
    return "INCONCLUSIVE"


def compare_runs(
    baseline: list[bool],
    candidate: list[bool],
    margin: float = DEFAULT_MARGIN,
    alpha: float = 0.05,
    iterations: int = 10_000,
    seed: int = 20260915,
    label_baseline: str = "baseline",
    label_candidate: str = "candidate",
) -> Verdict:
    """Compare two arms scored on the SAME items, aligned by index.

    Each entry is that item's success under D-003's metric: a win-or-tie against
    the frozen reference counts as success (DECISIONS.md D-036).
    """
    boot: BootstrapResult = paired_bootstrap(
        [1.0 if x else 0.0 for x in baseline],
        [1.0 if x else 0.0 for x in candidate],
        iterations=iterations,
        alpha=alpha,
        seed=seed,
    )
    mcn: McNemarResult = mcnemar(baseline, candidate)

    half_width = round(boot.ci_width / 2.0, 6)
    return Verdict(
        outcome=classify(boot.ci_low, boot.ci_high, margin),
        effect=boot.effect,
        ci_low=boot.ci_low,
        ci_high=boot.ci_high,
        n=boot.n,
        margin=margin,
        alpha=alpha,
        statistically_significant=boot.excludes_zero,
        minimum_detectable_effect=half_width,
        can_demonstrate_equivalence=half_width <= margin,
        mcnemar_p=round(mcn.p_value, 8),
        mcnemar_method=mcn.method,
        n_discordant=mcn.counts.discordant,
        n_concordant=mcn.counts.concordant,
        baseline_rate=boot.mean_a,
        candidate_rate=boot.mean_b,
        label_baseline=label_baseline,
        label_candidate=label_candidate,
    )


def required_pairs(
    effect: float,
    discordant_rate: float,
    alpha: float = 0.05,
    power: float = 0.8,
) -> int:
    """Planning helper: roughly how many ITEMS to detect `effect`.

    Normal approximation on the discordant split. Deliberately labelled a
    planning estimate — it answers "is this run hopeless before I pay for it?",
    not "what is my exact power?".
    """
    from statistics import NormalDist

    if not 0 < discordant_rate <= 1:
        raise ValueError("discordant_rate must be in (0, 1]")
    if effect <= 0:
        raise ValueError("effect must be positive")

    z_alpha = NormalDist().inv_cdf(1 - alpha / 2)
    z_beta = NormalDist().inv_cdf(power)

    # Fraction of discordant pairs that must favour one arm to produce `effect`.
    p = 0.5 + effect / (2 * discordant_rate)
    if p >= 1.0:
        return 0  # the effect is larger than the discordant rate can express

    n_discordant = ((z_alpha + z_beta) ** 2 * 0.25) / ((p - 0.5) ** 2)
    return math.ceil(n_discordant / discordant_rate)
