#!/usr/bin/env python3
"""Check every headline figure in README.md against the artifact that produced it.

The README says "a number that is not in a committed artifact does not exist".
That was an assertion. This makes it a test.

Each CHECK below names a string that must appear in the README, and the artifact
value it must equal. A stale README now fails CI the same way a broken test does,
which matters because the README is the one file that gets edited by hand and
read by strangers -- exactly the conditions under which a number drifts from the
measurement it came from and nobody notices.

Deliberately NOT a regex sweep over every number in the file. A checker that
scrapes all digits has to be taught about version numbers, years, port numbers
and prose, and the exceptions list becomes the place errors hide. An explicit
table is longer and says precisely what is claimed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
ARTIFACTS = REPO / "artifacts"


def load(name: str) -> dict[str, Any]:
    with (ARTIFACTS / name).open() as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def dig(d: Any, path: str) -> Any:
    for part in path.split("."):
        d = d[part]
    return d


#: (artifact, json path, how to render it, why this figure is in the README)
Check = tuple[str, str, Callable[[Any], str], str]

CHECKS: list[Check] = [
    # --- the pilot: pass rates -------------------------------------------
    (
        "difficulty-pilot.json",
        "pass_rate_by_model.openai/gpt-oss-20b",
        lambda v: f"{v * 100:.1f}%",
        "cheap rung pass rate",
    ),
    (
        "difficulty-pilot.json",
        "pass_rate_by_model.claude-haiku-4-5",
        lambda v: f"**{v * 100:.1f}%**",
        "haiku pass rate",
    ),
    # --- the headline verdict --------------------------------------------
    (
        "verdict-claude-haiku-4-5-vs-claude-sonnet-5.json",
        "outcome",
        str,
        "the verdict that justifies the whole project",
    ),
    (
        "verdict-claude-haiku-4-5-vs-claude-sonnet-5.json",
        "n",
        lambda v: f"{v} items",
        "sample size behind INCONCLUSIVE",
    ),
    (
        "verdict-openai_gpt-oss-20b-vs-claude-haiku-4-5.json",
        "outcome",
        str,
        "proof the system commits when there IS a difference",
    ),
    # --- the cache, measured and rejected --------------------------------
    (
        "cache-calibration.json",
        "n_duplicates",
        lambda v: f"{v} paraphrase",
        "positives in the cache calibration",
    ),
    (
        "cache-calibration.json",
        "n_different",
        lambda v: f"{v} hard negatives",
        "negatives in the cache calibration",
    ),
    (
        "cache-calibration.json",
        "min_negatives_required",
        lambda v: f"**{v} hard negatives",
        "the sample-size floor on a rare-event bound",
    ),
    # --- load test --------------------------------------------------------
    (
        "loadtest.json",
        "sustained_requests_per_minute",
        lambda v: f"**Sustained {v:,} req/min**",
        "throughput",
    ),
    (
        "loadtest.json",
        "through_gateway.requests_total",
        lambda v: f"{v:,} requests",
        "how many requests that throughput is averaged over",
    ),
    (
        "loadtest.json",
        "overhead_by_percentile_difference_ms.p99",
        lambda v: f"**+{v:g} ms**",
        "p99 proxy overhead",
    ),
    (
        "loadtest.json",
        "client_bytes_ratio",
        lambda v: f"{v:g}× the bytes",
        "the residual asymmetry, disclosed rather than hidden",
    ),
    (
        "loadtest.json",
        "end_to_end_real_provider.latency_ms.p50",
        lambda v: f"**p50 {v:g} ms",
        "real-provider latency, for scale",
    ),
]


def main() -> int:
    text = README.read_text()
    failures: list[str] = []
    checked = 0

    for artifact, path, render, why in CHECKS:
        try:
            value = dig(load(artifact), path)
        except (KeyError, FileNotFoundError) as err:
            failures.append(f"  {artifact}:{path} -- cannot read ({err})")
            continue

        expected = render(value)
        checked += 1
        if expected not in text:
            failures.append(
                f"  MISSING {expected!r}\n"
                f"          from {artifact}:{path}\n"
                f"          ({why})"
            )

    if failures:
        sys.stderr.write(
            f"README.md disagrees with artifacts/ in {len(failures)} place(s):\n\n"
            + "\n".join(failures)
            + "\n\nEither the README is stale, or a measurement was re-run and the "
            "README was not updated.\nFix the README -- never the artifact.\n"
        )
        return 1

    print(f"README.md: {checked} figures match artifacts/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
