"""Deterministic verifiers for the gradable slice.

These are the whole reason the gradable slice exists. They give an objective
answer to "was this output correct?", which is what lets P3 measure the LLM
judge against ground truth instead of only against Iraa's opinion.

A verifier must be strict enough that a wrong answer cannot pass, and lenient
enough that formatting differences do not fail a correct one. Every leniency
below is deliberate and tested.
"""

from __future__ import annotations

import re
from collections.abc import Callable

VerifierFn = Callable[[str, str], bool]

#: "#### 42", "The answer is 42.", "\boxed{42}" all need to yield 42.
_FINAL_ANSWER_MARKERS = re.compile(
    r"(?:####|final answer\s*(?:is)?\s*:?|answer\s*(?:is)?\s*:?|\\boxed\{)", re.IGNORECASE
)
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _normalise_number(raw: str) -> str | None:
    """Canonicalise a numeric string: strip separators, currency, trailing zeros."""
    cleaned = raw.replace(",", "").replace("$", "").strip().rstrip(".")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    # 18, 18.0 and 18.00 are the same answer.
    return str(int(value)) if value == int(value) else str(value)


def extract_final_number(text: str) -> str | None:
    """Last number after a final-answer marker; otherwise the last number present.

    Taking the LAST number matters: a chain of thought is full of intermediate
    numbers, and the answer is the one at the end.
    """
    if not text:
        return None

    tail = text
    for match in _FINAL_ANSWER_MARKERS.finditer(text):
        tail = text[match.end() :]

    numbers = _NUMBER.findall(tail)
    if not numbers:
        numbers = _NUMBER.findall(text)
    if not numbers:
        return None
    return _normalise_number(str(numbers[-1]))


def verify_final_number(output: str, ground_truth: str) -> bool:
    """GSM8K-style: compare the final numeric answer."""
    expected = _normalise_number(ground_truth)
    if expected is None:
        return False
    actual = extract_final_number(output)
    return actual is not None and actual == expected


_CHOICE_MARKERS = re.compile(
    r"(?:answer\s*(?:is)?\s*:?|option\s*:?|\\boxed\{|####)\s*\(?([A-J])\)?\b", re.IGNORECASE
)
_BARE_CHOICE = re.compile(r"\b\(?([A-J])\)?\b")


def extract_choice(text: str) -> str | None:
    """Letter answer for a multiple-choice item."""
    if not text:
        return None
    matches = _CHOICE_MARKERS.findall(text)
    if matches:
        return str(matches[-1]).upper()
    # Fall back to a lone letter on the last non-empty line, which is the common
    # shape when a model is told to answer with just a letter.
    for line in reversed([ln.strip() for ln in text.splitlines() if ln.strip()]):
        bare = _BARE_CHOICE.fullmatch(line)
        if bare:
            return str(bare.group(1)).upper()
        trailing = _BARE_CHOICE.findall(line)
        if trailing:
            return str(trailing[-1]).upper()
    return None


def verify_choice(output: str, ground_truth: str) -> bool:
    """MMLU-Pro-style: compare the selected option letter."""
    return extract_choice(output) == ground_truth.strip().upper()


REGISTRY: dict[str, VerifierFn] = {
    "final_number": verify_final_number,
    "choice_letter": verify_choice,
}


def verify(verifier: str, output: str, ground_truth: str) -> bool:
    try:
        fn = REGISTRY[verifier]
    except KeyError as err:
        raise KeyError(f"unknown verifier {verifier!r}; known: {sorted(REGISTRY)}") from err
    return fn(output, ground_truth)
