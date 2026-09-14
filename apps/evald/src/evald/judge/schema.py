"""The judge's structured verdict, and how a raw reply becomes one.

Constrained decoding is not available uniformly across Anthropic, OpenAI and
Groq, and the judge must behave IDENTICALLY across judge models or the
calibration does not transfer. So the contract is prompt-driven JSON validated
strictly here, with one retry, and `unparseable` recorded on a second failure
(DESIGN.md failure mode #11) rather than a guessed verdict.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: As the judge sees it: which of the two positions won.
PositionVerdict = Literal["A", "B", "tie"]
#: As the analysis needs it: how the candidate fared against the reference.
Verdict = Literal["win", "tie", "loss"]

#: Models wrap JSON in fences despite instructions not to. Tolerate that rather
#: than discard a perfectly good verdict over punctuation.
_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class JudgeReply(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reasoning: str = ""
    verdict: PositionVerdict
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class UnparseableVerdictError(ValueError):
    """The judge returned something that is not a verdict."""


def parse_reply(raw: str) -> JudgeReply:
    """Extract a verdict from the judge's raw text. Strict about meaning, lenient about wrapping."""
    if not raw or not raw.strip():
        raise UnparseableVerdictError("judge returned an empty response")

    candidates = []
    fenced = _FENCE.search(raw)
    if fenced:
        candidates.append(fenced.group(1))
    obj = _OBJECT.search(raw)
    if obj:
        candidates.append(obj.group(0))
    candidates.append(raw.strip())

    for text in candidates:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        try:
            return JudgeReply.model_validate(data)
        except Exception:
            continue

    raise UnparseableVerdictError(f"no valid verdict object in judge output: {raw[:200]!r}")


def to_candidate_verdict(
    position_verdict: PositionVerdict, candidate_was: Literal["A", "B"]
) -> Verdict:
    """Translate a position verdict into a candidate-relative one.

    The judge says "A won". Whether that means the candidate won depends on
    which slot the candidate occupied — which is randomised for debiasing. Doing
    this translation in one tested place is the difference between measuring
    quality and measuring position bias.
    """
    if position_verdict == "tie":
        return "tie"
    return "win" if position_verdict == candidate_was else "loss"


def combine_positions(first: Verdict, second: Verdict) -> Verdict:
    """Collapse the two position-swapped verdicts into one.

    Agreement stands. Disagreement means the judge's answer depended on the
    ORDER rather than the content, so the honest reading is that it could not
    tell them apart (DECISIONS.md D-003).
    """
    return first if first == second else "tie"
