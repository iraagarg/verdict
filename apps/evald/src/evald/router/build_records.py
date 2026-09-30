"""Build routing records from generations already in the replay cache.

The router has never had a policy to route with, so every request falls back to
`safe_default` and the gateway reports `router_off`. That is correct behaviour
and it demonstrates nothing: a cost-optimal router that has never been given a
measurement is indistinguishable from no router at all.

Meanwhile 559 generations sit in `.cache/replay`, already paid for. The cache key
is deterministic — `sha256(model, messages, params, replicate_idx)` — so every
one can be looked up again without a single API call. This turns that spend into
the input `evald fit` needs (D-074).

## What `success` means here, and what it does not

`ItemRecord.success` is documented as the judge's win-or-tie against the frozen
reference (D-003). This builds it from **exact-match verifier correctness**
instead, because no reference-model generations exist and the judge has never
been calibrated against a human.

That substitution is not free and the artifact says so:

  * It only covers the **gradable** slice — maths and multiple choice. Free-form
    items have no verifier, so they get no record and no route.
  * It is a *different* quality measure: ground truth rather than a judge's
    preference. Arguably stronger, since it cannot be wrong about who won — but
    it is not the measure D-003 specified, and a policy fitted on it must not be
    described as if it were.

A policy fitted from these records is therefore about maths and multiple choice,
and claims nothing about the traffic the corpus's free-form half represents.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from evald.corpus.schema import CorpusItem
from evald.corpus.verifiers import verify
from evald.cost import Usage, cost_nano
from evald.models_config import ModelConfig
from evald.replay.cache import ResponseCache, cache_key
from evald.replay.runner import request_params
from evald.router.records import ItemRecord


def build_records(
    items: list[CorpusItem],
    models: list[str],
    config: ModelConfig,
    cache: ResponseCache,
    max_output_tokens: int = 1024,
) -> tuple[list[ItemRecord], dict[str, int]]:
    """Return (records, stats). Reads only the cache; never calls a model."""
    records: list[ItemRecord] = []
    stats = {"items": 0, "skipped_not_gradable": 0, "skipped_incomplete": 0, "lookups_missed": 0}

    for item in items:
        if item.ground_truth is None or item.verifier is None:
            stats["skipped_not_gradable"] += 1
            continue

        success: dict[str, bool] = {}
        cost: dict[str, int] = {}

        for model in models:
            params = request_params(model, config, max_output_tokens)
            key = cache_key(model, item.messages, params, 0)
            hit = cache.get(key)
            if hit is None:
                stats["lookups_missed"] += 1
                continue
            success[model] = verify(item.verifier, hit["text"], item.ground_truth)
            cost[model] = cost_nano(
                model,
                config.models[model],
                Usage(
                    input_tokens=hit["input_tokens"],
                    output_tokens=hit["output_tokens"],
                ),
            )

        # A record covering only some of the ladder would let the sweep compare
        # a model against a rung it has no data for, which reads as a free
        # quality win. Drop the item instead.
        if len(success) != len(models):
            stats["skipped_incomplete"] += 1
            continue

        records.append(
            ItemRecord(
                slug=item.slug,
                task_type=item.task_type,
                split=item.split,
                success=success,
                cost_nano=cost,
            )
        )
        stats["items"] += 1

    return records, stats


def write_records(records: list[ItemRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(asdict(r), sort_keys=True) + "\n")
