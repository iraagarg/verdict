"""The pairwise judge.

Every comparison is run TWICE with the responses swapped. That doubles the cost
and buys two things: it removes position bias from the verdict, and it turns
that bias into a measured quantity (the flip rate) instead of a silent one.

The judge never sees which model produced which response.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from evald.corpus.schema import CorpusItem
from evald.judge.rubric import RUBRIC_VERSION, build_prompt
from evald.judge.schema import (
    JudgeReply,
    UnparseableVerdictError,
    Verdict,
    combine_positions,
    parse_reply,
    to_candidate_verdict,
)

#: (model, messages, params) -> raw assistant text.
CompleteFn = Callable[[str, list[dict[str, str]], dict[str, Any]], str]


@dataclass(frozen=True, slots=True)
class PositionRun:
    """One of the two orderings of a single comparison."""

    candidate_was: Literal["A", "B"]
    reply: JudgeReply | None
    verdict: Verdict | None
    error: str | None = None


@dataclass(slots=True)
class Judgment:
    slug: str
    task_type: str
    candidate_model: str
    reference_model: str
    judge_model: str
    rubric_version: str
    verdict: Verdict | None
    positions: list[PositionRun] = field(default_factory=list)
    #: True when the two orderings disagreed — the judge's answer depended on order.
    position_flip: bool = False
    unparseable: bool = False
    confidence: float | None = None

    @property
    def usable(self) -> bool:
        return self.verdict is not None


def judge_params(max_tokens: int = 700) -> dict[str, Any]:
    """Request parameters for a judging call.

    Deliberately tight. A verdict plus one or two sentences of reasoning does not
    need a long budget, and on models with adaptive thinking an unbounded budget
    turns a cheap classification into an expensive essay.
    """
    return {"max_tokens": max_tokens}


class PairwiseJudge:
    def __init__(
        self,
        judge_model: str,
        complete: CompleteFn,
        max_tokens: int = 700,
        max_attempts: int = 2,
    ) -> None:
        self.judge_model = judge_model
        self._complete = complete
        self._max_tokens = max_tokens
        # One retry, then record `unparseable` and exclude the item from the
        # statistic rather than guessing a verdict (DESIGN.md failure mode #11).
        self._max_attempts = max_attempts

    def _ask(self, prompt: str) -> JudgeReply:
        last: Exception | None = None
        for _ in range(self._max_attempts):
            try:
                raw = self._complete(
                    self.judge_model,
                    [{"role": "user", "content": prompt}],
                    judge_params(self._max_tokens),
                )
                return parse_reply(raw)
            except UnparseableVerdictError as err:
                last = err
            except Exception as err:
                last = err
        raise UnparseableVerdictError(str(last))

    def compare(
        self,
        item: CorpusItem,
        candidate_text: str,
        reference_text: str,
        candidate_model: str,
        reference_model: str,
        seed: int = 0,
    ) -> Judgment:
        """Judge one candidate against the reference, in both orderings."""
        judgment = Judgment(
            slug=item.slug,
            task_type=item.task_type,
            candidate_model=candidate_model,
            reference_model=reference_model,
            judge_model=self.judge_model,
            rubric_version=RUBRIC_VERSION,
            verdict=None,
        )

        # Which ordering runs first is seeded, so a rerun is identical and any
        # residual ordering effect is at least deterministic.
        first_candidate_slot: Literal["A", "B"] = (
            "A" if random.Random(f"{seed}:{item.slug}:{candidate_model}").random() < 0.5 else "B"
        )
        orderings: list[Literal["A", "B"]] = [
            first_candidate_slot,
            "B" if first_candidate_slot == "A" else "A",
        ]

        verdicts: list[Verdict] = []
        confidences: list[float] = []

        for slot in orderings:
            a_text, b_text = (
                (candidate_text, reference_text)
                if slot == "A"
                else (reference_text, candidate_text)
            )
            prompt = build_prompt(item.task_type, item.prompt_text(), a_text, b_text)
            try:
                reply = self._ask(prompt)
            except UnparseableVerdictError as err:
                judgment.positions.append(PositionRun(slot, None, None, str(err)))
                judgment.unparseable = True
                return judgment

            verdict = to_candidate_verdict(reply.verdict, slot)
            verdicts.append(verdict)
            confidences.append(reply.confidence)
            judgment.positions.append(PositionRun(slot, reply, verdict))

        judgment.position_flip = verdicts[0] != verdicts[1]
        judgment.verdict = combine_positions(verdicts[0], verdicts[1])
        judgment.confidence = round(sum(confidences) / len(confidences), 4)
        return judgment
