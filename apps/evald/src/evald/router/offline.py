"""Offline per-route assignment — D-004's policy, the one carrying the guarantee.

For each route, walk the ladder cheapest-first and take the first rung whose
quality confidence interval's LOWER BOUND clears the floor on held-out data.
Runtime routing is then a lookup: no judge, no verifier, nothing added to the
latency path.

The decision uses `ci.low >= floor` rather than the point estimate on purpose.
Small samples produce wide intervals and therefore REFUSE to demote, so the
method errs toward spending money — the correct direction for a safety mechanism
(D-004).

ROUTE KEY: task type, not an embedding cluster. D-004 envisaged clustering
prompt embeddings, but the embedding model is still unchosen (config/models.yaml
has it as null), and inventing one now would bake an arbitrary choice into the
policy. Task type is a real, explainable routing key that needs no embeddings.
Swapping in embedding clusters later changes how `route_key` is derived and
nothing else (D-039).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evald.router.records import ItemRecord
from evald.stats.bootstrap import bootstrap_rate_ci


@dataclass(frozen=True, slots=True)
class RouteAssignment:
    route_key: str
    assigned_model: str
    n_items: int
    #: Observed quality of the assigned model on this route.
    quality: float
    ci_low: float
    ci_high: float
    floor: float
    #: Why this rung and not a cheaper one.
    reason: str
    #: Every rung considered, cheapest first, with its lower bound.
    considered: list[dict[str, float | str]] = field(default_factory=list)


#: Routes with fewer than this many held-out items are never demoted. A cheap
#: rung must EARN its assignment with evidence, and a handful of items is not
#: evidence.
DEFAULT_MIN_ITEMS = 25


def fit_offline_policy(
    records: list[ItemRecord],
    ladder: list[str],
    safe_default: str,
    floor: float,
    min_items: int = DEFAULT_MIN_ITEMS,
    alpha: float = 0.05,
    iterations: int = 4000,
    seed: int = 20260915,
) -> list[RouteAssignment]:
    """Assign a model per route. `ladder` must be ordered cheapest-first."""
    by_route: dict[str, list[ItemRecord]] = {}
    for record in records:
        by_route.setdefault(record.task_type, []).append(record)

    assignments: list[RouteAssignment] = []
    for route_key in sorted(by_route):
        items = by_route[route_key]
        considered: list[dict[str, float | str]] = []

        if len(items) < min_items:
            assignments.append(
                RouteAssignment(
                    route_key=route_key,
                    assigned_model=safe_default,
                    n_items=len(items),
                    quality=0.0,
                    ci_low=0.0,
                    ci_high=0.0,
                    floor=floor,
                    reason=f"insufficient evidence: {len(items)} items < {min_items} minimum",
                    considered=considered,
                )
            )
            continue

        chosen: RouteAssignment | None = None
        for model in ladder:
            outcomes = [r.success[model] for r in items if model in r.success]
            if not outcomes:
                continue
            rate, lo, hi = bootstrap_rate_ci(
                outcomes, iterations=iterations, alpha=alpha, seed=seed
            )
            considered.append({"model": model, "quality": rate, "ci_low": lo})

            if chosen is None and lo >= floor:
                chosen = RouteAssignment(
                    route_key=route_key,
                    assigned_model=model,
                    n_items=len(items),
                    quality=rate,
                    ci_low=lo,
                    ci_high=hi,
                    floor=floor,
                    reason=f"cheapest rung whose CI lower bound {lo:.4f} >= floor {floor:.4f}",
                    considered=considered,
                )

        if chosen is None:
            assignments.append(
                RouteAssignment(
                    route_key=route_key,
                    assigned_model=safe_default,
                    n_items=len(items),
                    quality=0.0,
                    ci_low=0.0,
                    ci_high=0.0,
                    floor=floor,
                    reason="no rung's lower bound cleared the floor",
                    considered=considered,
                )
            )
        else:
            assignments.append(
                RouteAssignment(
                    route_key=chosen.route_key,
                    assigned_model=chosen.assigned_model,
                    n_items=chosen.n_items,
                    quality=chosen.quality,
                    ci_low=chosen.ci_low,
                    ci_high=chosen.ci_high,
                    floor=chosen.floor,
                    reason=chosen.reason,
                    considered=considered,
                )
            )

    return assignments


def policy_cost(
    records: list[ItemRecord], assignments: list[RouteAssignment]
) -> tuple[float, float]:
    """(mean cost in nano-USD, quality) of serving `records` under these assignments."""
    by_route = {a.route_key: a.assigned_model for a in assignments}
    costs: list[int] = []
    successes: list[bool] = []
    for record in records:
        model = by_route.get(record.task_type)
        if model is None or model not in record.success:
            continue
        costs.append(record.cost_nano[model])
        successes.append(record.success[model])
    if not costs:
        return (0.0, 0.0)
    return (sum(costs) / len(costs), sum(successes) / len(successes))
