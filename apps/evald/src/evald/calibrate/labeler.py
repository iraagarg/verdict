"""The interactive labelling harness.

Three properties this must have, all of which affect whether the resulting kappa
means anything:

  * **Blind.** The labeller never learns which model produced which response,
    and never sees the judge's verdict. Knowing either would make the labels a
    measurement of expectation rather than of quality.
  * **Resumable.** Labels are appended to disk after every single decision.
    200 labels is hours of work; losing it to a closed terminal is unacceptable.
  * **Honest about effort.** Time per label is recorded. Suspiciously fast
    labels are a quality signal worth being able to look at afterwards.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from evald.calibrate.sample import LabelPair

VALID_CHOICES = {"a": "A", "b": "B", "t": "tie"}


@dataclass(frozen=True, slots=True)
class HumanLabel:
    pair_id: str
    slug: str
    task_type: str
    candidate_model: str
    #: The candidate-relative verdict: win / tie / loss.
    verdict: str
    #: What the labeller actually pressed, before translation.
    chosen: str
    candidate_shown_as: str
    elapsed_ms: int
    labeled_at: str
    notes: str = ""


def load_labels(path: Path) -> dict[str, HumanLabel]:
    if not path.is_file():
        return {}
    out: dict[str, HumanLabel] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        out[str(data["pair_id"])] = HumanLabel(**data)
    return out


def append_label(path: Path, label: HumanLabel) -> None:
    """Append-only, flushed immediately. A crash loses at most nothing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(label), sort_keys=True) + "\n")
        fh.flush()


def _wrap(text: str, width: int = 96) -> str:
    import textwrap

    return "\n".join(
        textwrap.fill(line, width=width) if line.strip() else "" for line in text.splitlines()
    )


def render_pair(
    index: int,
    total: int,
    request: str,
    text_a: str,
    text_b: str,
    task_type: str,
) -> str:
    bar = "=" * 96
    return (
        f"\n{bar}\n"
        f"  PAIR {index}/{total}    task: {task_type}\n"
        f"{bar}\n\n"
        f"--- THE REQUEST ---\n{_wrap(request[:2400])}\n\n"
        f"--- RESPONSE A ---\n{_wrap(text_a[:2400])}\n\n"
        f"--- RESPONSE B ---\n{_wrap(text_b[:2400])}\n\n"
        f"{bar}\n"
        f"  [a] A is better   [b] B is better   [t] tie / too close to call\n"
        f"  [s] skip          [q] save and quit\n"
        f"{bar}"
    )


def run_session(
    pairs: list[LabelPair],
    texts: dict[str, tuple[str, str]],
    labels_path: Path,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> int:
    """Label until the list is exhausted or the user quits. Returns labels added."""
    done = load_labels(labels_path)
    remaining = [p for p in pairs if p.pair_id not in done]

    say = print_fn
    ask = input_fn

    if not remaining:
        say(f"All {len(pairs)} pairs already labelled.")
        return 0

    say(
        f"\n{len(done)} of {len(pairs)} already done. {len(remaining)} to go.\n"
        f"Judge the RESPONSES, not the style. Ties are a real answer — use them.\n"
        f"Progress saves after every label; [q] quits safely."
    )

    added = 0
    for i, pair in enumerate(remaining, start=1):
        candidate_text, reference_text = texts[pair.pair_id]
        text_a, text_b = pair.texts(candidate_text, reference_text)

        say(
            render_pair(
                len(done) + i, len(pairs), _request_of(pair, texts), text_a, text_b, pair.task_type
            )
        )

        started = time.monotonic()
        while True:
            raw = ask("  your call > ").strip().lower()
            if raw == "q":
                say(f"\nSaved. {added} labelled this session, {len(done) + added} total.")
                return added
            if raw == "s":
                break
            if raw in VALID_CHOICES:
                chosen = VALID_CHOICES[raw]
                append_label(
                    labels_path,
                    HumanLabel(
                        pair_id=pair.pair_id,
                        slug=pair.slug,
                        task_type=pair.task_type,
                        candidate_model=pair.candidate_model,
                        verdict=pair.to_verdict(chosen),
                        chosen=chosen,
                        candidate_shown_as=pair.candidate_shown_as,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        labeled_at=_now(),
                    ),
                )
                added += 1
                break
            say("  please press a, b, t, s or q")

    say(f"\nDone. {added} labelled this session, {len(done) + added} total.")
    return added


def _request_of(pair: LabelPair, texts: dict[str, tuple[str, str]]) -> str:
    return _REQUESTS.get(pair.pair_id, "")


#: Populated by the CLI before a session; keeps `run_session` testable.
_REQUESTS: dict[str, str] = {}


def set_requests(mapping: dict[str, str]) -> None:
    _REQUESTS.clear()
    _REQUESTS.update(mapping)


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")
