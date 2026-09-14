"""Agreement statistics.

Cohen's kappa rather than raw agreement, because raw agreement is inflated by
chance and by class imbalance. If 70% of pairs are genuinely ties, a judge that
answers "tie" every time scores 70% raw agreement and is worthless; kappa scores
it at 0.

Everything is seeded. The bootstrap CI matters as much as the point estimate:
the P3 gate is kappa >= 0.6, and a point estimate of 0.62 with an interval from
0.45 to 0.78 has not cleared anything.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

#: The verdict classes, in a fixed order so confusion matrices are comparable.
CLASSES: tuple[str, ...] = ("win", "tie", "loss")


@dataclass(slots=True)
class AgreementResult:
    n: int
    raw_agreement: float
    kappa: float
    kappa_ci_low: float
    kappa_ci_high: float
    #: rows = human label, cols = judge verdict.
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    #: Per-class agreement, to show WHERE the disagreement lives.
    per_class_recall: dict[str, float] = field(default_factory=dict)

    @property
    def clears_gate(self) -> bool:
        """The gate is on the LOWER bound, not the point estimate.

        A kappa whose interval straddles 0.6 has not demonstrated 0.6.
        """
        return self.kappa_ci_low >= 0.6


def confusion_matrix(human: list[str], judge: list[str]) -> dict[str, dict[str, int]]:
    matrix = {h: dict.fromkeys(CLASSES, 0) for h in CLASSES}
    for h, j in zip(human, judge, strict=True):
        matrix[h][j] += 1
    return matrix


def raw_agreement(human: list[str], judge: list[str]) -> float:
    if not human:
        return 0.0
    return sum(1 for h, j in zip(human, judge, strict=True) if h == j) / len(human)


def cohens_kappa(human: list[str], judge: list[str]) -> float:
    """Cohen's kappa for two raters over `CLASSES`.

    Returns 0.0 when expected agreement is 1.0 (both raters used exactly one
    class), because kappa is undefined there and 0 is the honest reading: the
    agreement carries no information.
    """
    n = len(human)
    if n == 0:
        return 0.0

    observed = raw_agreement(human, judge)

    human_counts = {c: human.count(c) for c in CLASSES}
    judge_counts = {c: judge.count(c) for c in CLASSES}
    expected = sum((human_counts[c] / n) * (judge_counts[c] / n) for c in CLASSES)

    if abs(1.0 - expected) < 1e-12:
        return 0.0
    return (observed - expected) / (1.0 - expected)


def bootstrap_kappa_ci(
    human: list[str],
    judge: list[str],
    iterations: int = 10_000,
    alpha: float = 0.05,
    seed: int = 20260915,
) -> tuple[float, float]:
    """Percentile bootstrap CI on kappa, resampling PAIRS (DECISIONS.md D-011)."""
    n = len(human)
    if n < 2:
        return (0.0, 0.0)

    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(iterations):
        idx = [rng.randrange(n) for _ in range(n)]
        draws.append(cohens_kappa([human[i] for i in idx], [judge[i] for i in idx]))

    draws.sort()
    lo = draws[int((alpha / 2) * iterations)]
    hi = draws[min(iterations - 1, int((1 - alpha / 2) * iterations))]
    return (round(lo, 4), round(hi, 4))


def agreement(
    human: list[str],
    judge: list[str],
    iterations: int = 10_000,
    seed: int = 20260915,
) -> AgreementResult:
    if len(human) != len(judge):
        raise ValueError(f"mismatched lengths: {len(human)} human vs {len(judge)} judge")
    for label in (*human, *judge):
        if label not in CLASSES:
            raise ValueError(f"unknown class {label!r}; expected one of {CLASSES}")

    matrix = confusion_matrix(human, judge)
    lo, hi = bootstrap_kappa_ci(human, judge, iterations=iterations, seed=seed)

    recall: dict[str, float] = {}
    for c in CLASSES:
        total = sum(matrix[c].values())
        recall[c] = round(matrix[c][c] / total, 4) if total else 0.0

    return AgreementResult(
        n=len(human),
        raw_agreement=round(raw_agreement(human, judge), 4),
        kappa=round(cohens_kappa(human, judge), 4),
        kappa_ci_low=lo,
        kappa_ci_high=hi,
        confusion=matrix,
        per_class_recall=recall,
    )


def interpret(kappa: float) -> str:
    """Landis & Koch's conventional bands, for readers who want a word not a number."""
    if kappa < 0.0:
        return "worse than chance"
    if kappa < 0.20:
        return "slight"
    if kappa < 0.40:
        return "fair"
    if kappa < 0.60:
        return "moderate"
    if kappa < 0.80:
        return "substantial"
    return "almost perfect"
