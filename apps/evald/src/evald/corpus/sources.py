"""Dataset loaders.

Each source records its licence on every item it produces. A corpus whose
provenance cannot be stated is not usable in a portfolio, and "where did this
data come from" is a question an interviewer is entitled to ask.

Task prompts are frozen here. Changing a prompt changes what is being measured,
so it invalidates the corpus hash and therefore every artifact derived from it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

MATH_INSTRUCTION = (
    "Solve the problem. Show your reasoning, then give the final numeric answer "
    "on its own last line in the form: #### <answer>"
)

GSM8K_INSTRUCTION = (
    "Solve the problem. Show your reasoning, then give the final numeric answer "
    "on its own last line in the form: #### <answer>"
)

MMLU_PRO_INSTRUCTION = (
    "Answer the multiple-choice question. Reason briefly, then give the letter of "
    "the correct option on its own last line in the form: #### <letter>"
)

SUMMARIZATION_INSTRUCTION = (
    "Write a concise summary of the article below in 2-4 sentences. "
    "Cover only what the article states."
)

LONG_FORM_QA_INSTRUCTION = (
    "Answer the question thoroughly and accurately for a general audience. Two to four paragraphs."
)

SUPPORT_REPLY_INSTRUCTION = (
    "You are a customer support agent. Write a reply to the customer message below. "
    "Be warm, specific and actionable. Do not invent order numbers, policies or dates."
)


@dataclass(frozen=True, slots=True)
class RawItem:
    """One candidate before splitting and difficulty filtering."""

    source_dataset: str
    source_id: str
    source_license: str
    task_type: str
    messages: list[dict[str, str]]
    ground_truth: str | None


#: Dataset licences, recorded per item. Verified from each dataset's HF card.
LICENSES = {
    "openai/gsm8k": "MIT",
    "nlile/hendrycks-MATH-benchmark": "MIT",
    "TIGER-Lab/MMLU-Pro": "MIT",
    "abisee/cnn_dailymail": "Apache-2.0",
    "databricks/databricks-dolly-15k": "CC-BY-SA-3.0",
    "bitext/Bitext-customer-support-llm-chatbot-training-dataset": "CDLA-Sharing-1.0",
}

_GSM8K_ANSWER = re.compile(r"####\s*(-?[\d,]+(?:\.\d+)?)")


def _dedupe(items: list[RawItem]) -> list[RawItem]:
    """Drop items whose prompt text is already present.

    Real datasets repeat themselves — the Bitext support set in particular has
    many identical customer messages. Duplicates must not reach the corpus: the
    replay cache is content-addressed, so two identical prompts would share one
    generation while being counted as two independent observations, understating
    variance and narrowing every confidence interval built on them.
    """
    seen: set[str] = set()
    out: list[RawItem] = []
    for item in items:
        text = "\n\n".join(m["content"] for m in item.messages)
        if text in seen:
            continue
        seen.add(text)
        out.append(item)
    return out


def _load(name: str, config: str | None, split: str, limit: int) -> Iterator[dict[str, object]]:
    from datasets import load_dataset  # type: ignore[import-untyped]  # lazy: heavy, build-only

    args = [name] + ([config] if config else [])
    ds = load_dataset(*args, split=split, streaming=True)
    for _, row in zip(range(limit), ds, strict=False):
        yield dict(row)


def gsm8k(limit: int) -> list[RawItem]:
    out: list[RawItem] = []
    for i, row in enumerate(_load("openai/gsm8k", "main", "test", limit)):
        answer = str(row["answer"])
        match = _GSM8K_ANSWER.search(answer)
        if match is None:
            continue  # no extractable ground truth; skip rather than guess
        out.append(
            RawItem(
                source_dataset="openai/gsm8k",
                source_id=f"test-{i}",
                source_license=LICENSES["openai/gsm8k"],
                task_type="math_word_problem",
                messages=[
                    {"role": "system", "content": GSM8K_INSTRUCTION},
                    {"role": "user", "content": str(row["question"])},
                ],
                ground_truth=match.group(1).replace(",", ""),
            )
        )
    return _dedupe(out)


#: Hendrycks MATH answers are LaTeX. Only problems whose answer is a plain
#: number are usable, because the existing `final_number` verifier compares
#: numbers and a LaTeX-aware comparator is a research project of its own.
_PLAIN_NUMBER = re.compile(r"^-?\d[\d,]*(?:\.\d+)?$")

#: Levels 3-5 only. The pilot measured gpt-oss-120b at 98% on GSM8K — a ceiling
#: that made half the gradable slice worthless — so difficulty is now a source
#: filter, not something discovered afterwards. DECISIONS.md D-030.
MATH_MIN_LEVEL = 3


def hendrycks_math(limit: int) -> list[RawItem]:
    """Competition maths, restricted to hard levels with machine-checkable answers."""
    out: list[RawItem] = []
    # Both splits: the level>=3 AND plain-numeric-answer filter is strict enough
    # that one split does not yield 600 items. There is no train/test leakage
    # concern here because nothing is being trained — the split is just how the
    # upstream dataset is packaged.
    rows = [
        *_load("nlile/hendrycks-MATH-benchmark", None, "test", 6000),
        *_load("nlile/hendrycks-MATH-benchmark", None, "train", 9000),
    ]
    for row in rows:
        try:
            level = int(str(row["level"]))
        except (TypeError, ValueError):
            continue
        if level < MATH_MIN_LEVEL:
            continue

        answer = str(row["answer"]).strip().replace("\\!", "").replace("\\,", "")
        if not _PLAIN_NUMBER.match(answer):
            continue  # LaTeX answer we cannot verify exactly; skip rather than guess

        out.append(
            RawItem(
                source_dataset="nlile/hendrycks-MATH-benchmark",
                source_id=str(row["unique_id"]),
                source_license=LICENSES["nlile/hendrycks-MATH-benchmark"],
                task_type="math_word_problem",
                messages=[
                    {"role": "system", "content": MATH_INSTRUCTION},
                    {"role": "user", "content": str(row["problem"])},
                ],
                ground_truth=answer.replace(",", ""),
            )
        )
        if len(out) >= limit:
            break
    return _dedupe(out)[:limit]


def mmlu_pro(limit: int) -> list[RawItem]:
    """MMLU-Pro rather than MMLU: 10 options instead of 4, built to resist ceiling effects."""
    out: list[RawItem] = []
    for row in _load("TIGER-Lab/MMLU-Pro", None, "test", limit):
        options = list(row["options"])  # type: ignore[call-overload]
        rendered = "\n".join(f"{chr(65 + i)}. {opt}" for i, opt in enumerate(options))
        out.append(
            RawItem(
                source_dataset="TIGER-Lab/MMLU-Pro",
                source_id=str(row["question_id"]),
                source_license=LICENSES["TIGER-Lab/MMLU-Pro"],
                task_type="multiple_choice",
                messages=[
                    {"role": "system", "content": MMLU_PRO_INSTRUCTION},
                    {"role": "user", "content": f"{row['question']}\n\n{rendered}"},
                ],
                ground_truth=str(row["answer"]).strip().upper(),
            )
        )
    return _dedupe(out)


def cnn_summarization(limit: int) -> list[RawItem]:
    out: list[RawItem] = []
    for row in _load("abisee/cnn_dailymail", "3.0.0", "test", limit):
        article = str(row["article"])
        # Keep articles in a band where cost is predictable and the task is real.
        if not 1500 <= len(article) <= 6000:
            continue
        out.append(
            RawItem(
                source_dataset="abisee/cnn_dailymail",
                source_id=str(row["id"]),
                source_license=LICENSES["abisee/cnn_dailymail"],
                task_type="summarization",
                messages=[
                    {"role": "system", "content": SUMMARIZATION_INSTRUCTION},
                    {"role": "user", "content": article},
                ],
                ground_truth=None,
            )
        )
    return _dedupe(out)


def dolly_long_form_qa(limit: int) -> list[RawItem]:
    out: list[RawItem] = []
    wanted = {"open_qa", "general_qa"}
    for i, row in enumerate(
        _load("databricks/databricks-dolly-15k", None, "train", max(limit * 20, 4000))
    ):
        if str(row["category"]) not in wanted or str(row.get("context", "")):
            continue
        question = str(row["instruction"]).strip()
        if len(question) < 25:
            continue  # too terse to need a long-form answer
        out.append(
            RawItem(
                source_dataset="databricks/databricks-dolly-15k",
                source_id=f"train-{i}",
                source_license=LICENSES["databricks/databricks-dolly-15k"],
                task_type="long_form_qa",
                messages=[
                    {"role": "system", "content": LONG_FORM_QA_INSTRUCTION},
                    {"role": "user", "content": question},
                ],
                ground_truth=None,
            )
        )
        out = _dedupe(out)
        if len(out) >= limit:
            break
    return _dedupe(out)[:limit]


def bitext_support_reply(limit: int) -> list[RawItem]:
    key = "bitext/Bitext-customer-support-llm-chatbot-training-dataset"
    out: list[RawItem] = []
    for i, row in enumerate(_load(key, None, "train", max(limit * 60, 8000))):
        message = str(row["instruction"]).strip()
        if len(message) < 20:
            continue
        out.append(
            RawItem(
                source_dataset=key,
                source_id=f"train-{i}",
                source_license=LICENSES[key],
                task_type="support_reply",
                messages=[
                    {"role": "system", "content": SUPPORT_REPLY_INSTRUCTION},
                    {"role": "user", "content": message},
                ],
                ground_truth=None,
            )
        )
        out = _dedupe(out)
        if len(out) >= limit:
            break
    return _dedupe(out)[:limit]
