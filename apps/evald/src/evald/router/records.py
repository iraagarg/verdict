"""The per-item evidence every routing decision is fitted and evaluated on.

One record per corpus item, carrying what each model did on it. Assembled from
judged replay runs; the simulators below never call a model, so a threshold
sweep over 40 operating points costs nothing and is exactly reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evald.corpus.schema import GRADABLE_TASKS


@dataclass(frozen=True, slots=True)
class ItemRecord:
    slug: str
    task_type: str
    split: str

    #: Per model: did its output win-or-tie against the frozen reference?
    #: This is D-003's metric and D-004's floor, so the same number flows
    #: unchanged from the judge through P4's verdict into the routing decision.
    success: dict[str, bool] = field(default_factory=dict)

    #: Per model: cost of ONE call on this item, in integer nano-USD.
    cost_nano: dict[str, int] = field(default_factory=dict)

    #: Per model: fraction of K self-consistency samples that agreed, in [0, 1].
    #: Only meaningful for gradable tasks, where agreement is exact-match on the
    #: extracted answer rather than a guess about whether two texts mean the same.
    agreement: dict[str, float] = field(default_factory=dict)

    #: Per model: a cheap verifier's adequacy score for free-form outputs, [0, 1].
    verifier_confidence: dict[str, float] = field(default_factory=dict)

    @property
    def gradable(self) -> bool:
        return self.task_type in GRADABLE_TASKS

    def confidence(self, model: str) -> float:
        """The escalation signal for this item under the hybrid scheme (D-037).

        Gradable items use self-consistency, because agreement between K samples
        is an EXACT comparison of extracted answers — no second model, no
        semantic guesswork. Free-form items use a cheap verifier, because
        deciding whether two paragraphs agree needs semantics, and the embedding
        model that would provide it is still unchosen.
        """
        source = self.agreement if self.gradable else self.verifier_confidence
        try:
            return source[model]
        except KeyError as err:
            kind = "agreement" if self.gradable else "verifier_confidence"
            raise KeyError(
                f"{self.slug}: no {kind} recorded for {model!r}. "
                f"The cascade cannot be fitted without a confidence signal for every item."
            ) from err


class SplitLeakError(RuntimeError):
    """Raised when fitting would touch data reserved for reporting."""


def select_split(records: list[ItemRecord], split: str) -> list[ItemRecord]:
    return [r for r in records if r.split == split]


def assert_disjoint(fit: list[ItemRecord], report: list[ItemRecord]) -> None:
    """Hard guard: a threshold may never be fitted on the data it is reported on.

    This is the single easiest way to produce an impressive and meaningless
    result, and it is invisible in the output — a leaked fit looks exactly like
    a good one. So it is checked rather than trusted.
    """
    overlap = {r.slug for r in fit} & {r.slug for r in report}
    if overlap:
        sample = sorted(overlap)[:5]
        raise SplitLeakError(
            f"{len(overlap)} item(s) appear in both the fitting and reporting sets "
            f"(e.g. {sample}). A threshold fitted on the data it is evaluated on "
            f"reports its own training performance."
        )
