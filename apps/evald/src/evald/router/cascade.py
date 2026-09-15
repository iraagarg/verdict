"""The cascade policy: cheap first, escalate when confidence is low.

Serve the cheap rung, measure how confident we are in its answer, and escalate
to the strong rung only when that confidence falls below a threshold. The
threshold is the single free parameter, and it is fitted on a TRAIN split and
reported on a HELD-OUT split (D-038).

The cost accounting here is deliberately complete: the confidence check is not
free, and a cascade that ignores the cost of deciding whether to escalate would
report savings it does not deliver. Gradable items pay for K cheap samples;
free-form items pay for one cheap call plus one verifier call. Escalated items
pay for all of that AND the strong call — escalation is strictly more expensive
than going straight to the strong model, which is exactly why the threshold
matters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evald.router.records import ItemRecord


@dataclass(frozen=True, slots=True)
class CascadeConfig:
    cheap_model: str
    strong_model: str
    #: Self-consistency samples for gradable items.
    k_samples: int = 3
    #: Cost of one verifier call on a free-form item, in nano-USD.
    verifier_cost_nano: int = 0
    #: Escalate when confidence is strictly below this.
    threshold: float = 0.5

    def __post_init__(self) -> None:
        if self.k_samples < 1:
            raise ValueError("k_samples must be >= 1")
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError("threshold must be in [0, 1]")


@dataclass(slots=True)
class CascadeOutcome:
    """What the cascade did, item by item, at one threshold."""

    threshold: float
    n: int
    #: Per item, in corpus order: did the SERVED output win-or-tie the reference?
    success: list[bool] = field(default_factory=list)
    #: Per item total cost in nano-USD, including the confidence check.
    cost_nano: list[int] = field(default_factory=list)
    #: Per item: did we escalate?
    escalated: list[bool] = field(default_factory=list)
    slugs: list[str] = field(default_factory=list)

    @property
    def quality(self) -> float:
        return sum(self.success) / self.n if self.n else 0.0

    @property
    def total_cost_nano(self) -> int:
        return sum(self.cost_nano)

    @property
    def mean_cost_nano(self) -> float:
        return self.total_cost_nano / self.n if self.n else 0.0

    @property
    def escalation_rate(self) -> float:
        return sum(self.escalated) / self.n if self.n else 0.0


def confidence_check_cost(record: ItemRecord, config: CascadeConfig) -> int:
    """What it costs to decide whether to escalate, before any escalation."""
    cheap = record.cost_nano[config.cheap_model]
    if record.gradable:
        # K samples of the cheap model; agreement between them IS the signal.
        return cheap * config.k_samples
    # One cheap answer plus one verifier call to score it.
    return cheap + config.verifier_cost_nano


def simulate(records: list[ItemRecord], config: CascadeConfig) -> CascadeOutcome:
    """Run the cascade over `records` at `config.threshold`. Calls no models."""
    outcome = CascadeOutcome(threshold=config.threshold, n=len(records))

    for record in records:
        cost = confidence_check_cost(record, config)
        escalate = record.confidence(config.cheap_model) < config.threshold

        if escalate:
            cost += record.cost_nano[config.strong_model]
            served = config.strong_model
        else:
            served = config.cheap_model

        outcome.slugs.append(record.slug)
        outcome.escalated.append(escalate)
        outcome.cost_nano.append(cost)
        outcome.success.append(record.success[served])

    return outcome


def all_strong_baseline(records: list[ItemRecord], strong_model: str) -> CascadeOutcome:
    """The policy every cascade is measured against: always use the strong rung."""
    outcome = CascadeOutcome(threshold=1.0, n=len(records))
    for record in records:
        outcome.slugs.append(record.slug)
        outcome.escalated.append(True)
        outcome.cost_nano.append(record.cost_nano[strong_model])
        outcome.success.append(record.success[strong_model])
    return outcome


def all_cheap_baseline(records: list[ItemRecord], cheap_model: str) -> CascadeOutcome:
    """The other end of the curve: always use the cheap rung, no confidence check."""
    outcome = CascadeOutcome(threshold=0.0, n=len(records))
    for record in records:
        outcome.slugs.append(record.slug)
        outcome.escalated.append(False)
        outcome.cost_nano.append(record.cost_nano[cheap_model])
        outcome.success.append(record.success[cheap_model])
    return outcome
