"""The judging rubric.

A shared skeleton plus a per-task criteria block (DECISIONS.md D-031). One
instrument, one `RUBRIC_VERSION`, so one set of human labels calibrates it — but
kappa can still be reported per task, which is where a weakness actually shows.

CHANGING ANY TEXT IN THIS FILE INVALIDATES THE CALIBRATION. `RUBRIC_VERSION`
must be bumped with any edit, and the calibration harness refuses to mix
versions. A rubric silently drifting away from the labels it was validated
against is the single easiest way to end up quoting a kappa that means nothing.
"""

from __future__ import annotations

from typing import Final

RUBRIC_VERSION: Final = "v1"

#: Shared across every task. Defines the verdict scale and the tie rules.
SKELETON: Final = """\
You are an impartial evaluator. You will see a user's request and two candidate \
responses, labelled A and B. Decide which response better serves the user.

Judge only what is in front of you:
- Do not reward length. A shorter response that fully answers is better than a \
longer one that pads.
- Do not reward confident tone. Confidence is not accuracy.
- Do not penalise a response for formatting differences unless they hurt the reader.
- If a response invents facts, that is a serious defect even if it reads well.

Verdict scale:
- "A"   - A is clearly better on the criteria below.
- "B"   - B is clearly better.
- "tie" - they are of equivalent quality, OR the difference is too small to call, \
OR you cannot tell from the information given.

"tie" is a real answer, not a cop-out. Forcing a winner between two equivalent \
responses manufactures noise. Use it whenever the difference would not matter to \
the user.
"""

#: Task-specific criteria, appended to the skeleton.
CRITERIA: Final[dict[str, str]] = {
    "summarization": """\
Criteria for this task (a summary of an article), most important first:
1. Faithfulness - every claim must be supported by the article. An invented \
detail outweighs any stylistic advantage.
2. Coverage - the main points of the article are present, not just the opening.
3. Concision - says what matters without restating the article.""",
    "long_form_qa": """\
Criteria for this task (an explanatory answer for a general audience), most \
important first:
1. Accuracy - claims must be correct. A confident wrong statement is the worst \
possible outcome.
2. Completeness - the actual question is answered, not an adjacent one.
3. Clarity - understandable to a non-expert without being condescending.""",
    "support_reply": """\
Criteria for this task (a customer support reply), most important first:
1. No invented specifics - order numbers, refund windows, policies and dates \
that were not given must not appear. Inventing one is disqualifying.
2. Actionability - the customer knows what happens next, or what to do.
3. Tone - warm and professional; neither robotic nor over-familiar.""",
    "math_word_problem": """\
Criteria for this task (a maths problem), most important first:
1. Correctness of the final answer. Nothing else can compensate for a wrong answer.
2. Validity of the reasoning shown.
3. Clarity of presentation.""",
    "multiple_choice": """\
Criteria for this task (a multiple-choice question), most important first:
1. Correctness of the selected option. Nothing else can compensate.
2. Whether the reasoning supports the selection.
3. Clarity.""",
}

#: Appended last so the required shape is the most recent instruction.
OUTPUT_CONTRACT: Final = """\
Respond with a single JSON object and nothing else. No markdown fences, no prose \
before or after.

{"reasoning": "<one or two sentences naming the decisive difference>", \
"verdict": "A" | "B" | "tie", "confidence": <0.0 to 1.0>}

Set confidence below 0.5 when you are genuinely unsure; a low-confidence verdict \
is more useful than a fabricated certainty."""


class UnknownTaskError(KeyError):
    """Raised when a task type has no criteria block, rather than silently guessing."""


def build_prompt(task_type: str, request: str, response_a: str, response_b: str) -> str:
    """Assemble the full judge prompt for one comparison."""
    try:
        criteria = CRITERIA[task_type]
    except KeyError as err:
        raise UnknownTaskError(
            f"no rubric criteria for task {task_type!r}; known: {sorted(CRITERIA)}"
        ) from err

    return (
        f"{SKELETON}\n\n{criteria}\n\n"
        f"--- USER REQUEST ---\n{request}\n\n"
        f"--- RESPONSE A ---\n{response_a}\n\n"
        f"--- RESPONSE B ---\n{response_b}\n\n"
        f"--- END ---\n\n{OUTPUT_CONTRACT}"
    )
