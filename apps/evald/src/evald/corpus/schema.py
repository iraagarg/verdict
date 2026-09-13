"""The frozen corpus item.

Once written, an item is never edited: a correction creates a new slug. That is
what lets a run artifact from three weeks ago still mean something, and it is
why `corpus_sha256` is recorded on every run.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TaskType = Literal[
    "math_word_problem",  # gradable
    "multiple_choice",  # gradable
    "summarization",  # free-form
    "long_form_qa",  # free-form
    "support_reply",  # free-form
]

Split = Literal["calibration", "dev", "test"]

#: Task types scored by an exact, deterministic verifier — no judge required.
GRADABLE_TASKS: frozenset[str] = frozenset({"math_word_problem", "multiple_choice"})


class CorpusItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str = Field(min_length=1)
    task_type: TaskType
    split: Split
    verifiable: bool
    #: The exact request sent to every model. Frozen.
    messages: list[dict[str, str]]
    #: Only for verifiable items.
    ground_truth: str | None = None
    #: Name of the deterministic checker in evald.corpus.verifiers.
    verifier: str | None = None
    #: Provenance. Every item must say where it came from.
    source_dataset: str
    source_id: str
    source_license: str

    def model_post_init(self, _context: object) -> None:
        if self.verifiable and (self.ground_truth is None or self.verifier is None):
            raise ValueError(f"{self.slug}: verifiable items need ground_truth and verifier")
        if not self.verifiable and (self.ground_truth is not None or self.verifier is not None):
            raise ValueError(f"{self.slug}: non-verifiable items must not carry ground truth")

    def prompt_text(self) -> str:
        return "\n\n".join(m["content"] for m in self.messages)


def corpus_sha256(items: list[CorpusItem]) -> str:
    """Stable content hash over the whole corpus, order-independent.

    Recorded on every run artifact so a result can never be silently attributed
    to a corpus it was not produced from.
    """
    digests = sorted(
        hashlib.sha256(
            json.dumps(item.model_dump(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for item in items
    )
    return hashlib.sha256("".join(digests).encode()).hexdigest()
