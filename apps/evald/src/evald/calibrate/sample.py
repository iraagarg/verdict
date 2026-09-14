"""Choosing which pairs a human labels.

Stratified and seeded across (task type x candidate model), for one reason: a
kappa computed on a sample dominated by one task or one model is a number about
that corner, not about the judge.

Deliberately NOT chosen by where the judge is uncertain. Sampling the judge's
hard cases would make kappa a measure of its worst performance, not its
performance — and would make the number incomparable to anything.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from evald.corpus.schema import CorpusItem


@dataclass(frozen=True, slots=True)
class LabelPair:
    """One comparison awaiting a human verdict."""

    pair_id: str
    slug: str
    task_type: str
    candidate_model: str
    reference_model: str
    #: Which slot the candidate occupies when shown. Randomised per pair so the
    #: labeller's own position bias becomes measurable rather than baked in.
    candidate_shown_as: str  # "A" or "B"

    def texts(self, candidate_text: str, reference_text: str) -> tuple[str, str]:
        """(shown as A, shown as B)."""
        if self.candidate_shown_as == "A":
            return candidate_text, reference_text
        return reference_text, candidate_text

    def to_verdict(self, chosen: str) -> str:
        """Translate the human's A/B/tie choice into a candidate-relative verdict."""
        if chosen == "tie":
            return "tie"
        return "win" if chosen == self.candidate_shown_as else "loss"


def build_pairs(
    items: list[CorpusItem],
    candidate_models: list[str],
    reference_model: str,
    n: int,
    seed: int,
    free_form_only: bool = True,
) -> list[LabelPair]:
    """A stratified, seeded sample of `n` comparisons."""
    pool = [i for i in items if (not free_form_only or not i.verifiable)]
    if not pool or not candidate_models:
        return []

    rng = random.Random(f"{seed}:pairs")
    by_task: dict[str, list[CorpusItem]] = {}
    for item in pool:
        by_task.setdefault(item.task_type, []).append(item)

    # Balance across task x model cells, then take the first n of a seeded shuffle.
    cells = [(task, model) for task in sorted(by_task) for model in sorted(candidate_models)]
    per_cell = max(1, n // len(cells))

    pairs: list[LabelPair] = []
    for task, model in cells:
        candidates = sorted(by_task[task], key=lambda i: i.slug)
        take = min(per_cell, len(candidates))
        chosen = random.Random(f"{seed}:{task}:{model}").sample(candidates, take)
        for item in chosen:
            shown_as = (
                "A" if random.Random(f"{seed}:{item.slug}:{model}:pos").random() < 0.5 else "B"
            )
            pairs.append(
                LabelPair(
                    pair_id=f"{item.slug}|{model}",
                    slug=item.slug,
                    task_type=item.task_type,
                    candidate_model=model,
                    reference_model=reference_model,
                    candidate_shown_as=shown_as,
                )
            )

    rng.shuffle(pairs)
    return pairs[:n]
