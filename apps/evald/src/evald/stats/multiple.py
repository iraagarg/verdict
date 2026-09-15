"""Benjamini-Hochberg false-discovery-rate control.

D-012. A policy fit runs one test per (route, rung). With eight routes and two
candidate rungs that is sixteen tests, and at alpha = 0.05 roughly one will look
significant by chance alone — meaning a route gets demoted on noise about every
other fit.

Bonferroni was rejected as too conservative at this count: it would control the
chance of ANY false demotion so tightly that nothing would ever be demoted, and
a router that never routes is not a router. BH controls the expected PROPORTION
of false demotions, which matches what actually matters here — a few wrong
demotions among many correct ones is tolerable; half of them being noise is not.
"""

from __future__ import annotations


def benjamini_hochberg(p_values: list[float], alpha: float = 0.05) -> list[float]:
    """Return BH-adjusted q-values, in the same order as the input.

    A hypothesis is rejected when its q-value is <= alpha.
    """
    for p in p_values:
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"p-values must be in [0, 1], got {p}")
    if not p_values:
        return []

    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])

    # Walk from the largest p downward, enforcing monotonicity so a q-value can
    # never exceed one belonging to a larger p.
    q = [0.0] * m
    running_min = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running_min = min(running_min, p_values[i] * m / rank)
        q[i] = min(1.0, running_min)
    return [round(v, 8) for v in q]


def significant(p_values: list[float], alpha: float = 0.05) -> list[bool]:
    return [q <= alpha for q in benjamini_hochberg(p_values, alpha)]
