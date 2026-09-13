"""Deterministic corpus assembly.

Two properties matter here and both are tested:

  1. **Seeded and reproducible.** The same seed and the same source data produce
     a byte-identical corpus. Sampling uses an explicit `random.Random(seed)`,
     never the global RNG, so nothing elsewhere in the process can perturb it.

  2. **Stratified splits.** Each task type is split calibration/dev/test in the
     same proportions, so a split cannot accidentally over-represent one task
     and make a result look better than it is.

Difficulty filtering is a separate stage (`filter_by_difficulty`) because it
needs measured pilot results, not assumptions. Plain GSM8K and MMLU are heavily
quoted public benchmarks; if every rung scores near ceiling the judge has almost
no losses to be validated against and routing has nothing to learn. The pilot
turns that risk into a measured number.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass

from evald.corpus.schema import CorpusItem, Split
from evald.corpus.sources import RawItem

#: calibration / dev / test. Test is largest because it carries the significance
#: tests, and CI width is what decides whether anything can be demoted at all.
DEFAULT_SPLIT_WEIGHTS: dict[Split, float] = {"calibration": 0.20, "dev": 0.30, "test": 0.50}

_VERIFIERS = {"math_word_problem": "final_number", "multiple_choice": "choice_letter"}


@dataclass(frozen=True, slots=True)
class PilotResult:
    """One (item, model) outcome from the difficulty pilot."""

    slug: str
    model: str
    passed: bool


def assign_splits(
    n: int,
    seed: int,
    weights: dict[Split, float] | None = None,
) -> list[Split]:
    """Split labels for `n` items, shuffled with `seed`.

    Sizes are computed by flooring then distributing the remainder, so the totals
    always sum to n exactly rather than drifting with rounding.
    """
    w = weights or DEFAULT_SPLIT_WEIGHTS
    if abs(sum(w.values()) - 1.0) > 1e-9:
        raise ValueError(f"split weights must sum to 1.0, got {sum(w.values())}")

    order: list[Split] = ["calibration", "dev", "test"]
    counts = {s: int(n * w[s]) for s in order}
    for i in range(n - sum(counts.values())):
        counts[order[i % len(order)]] += 1

    labels: list[Split] = []
    for split in order:
        labels.extend([split] * counts[split])
    random.Random(seed).shuffle(labels)
    return labels


def build_corpus(
    raw_by_task: dict[str, list[RawItem]],
    targets: dict[str, int],
    seed: int,
    weights: dict[Split, float] | None = None,
) -> list[CorpusItem]:
    """Sample `targets[task]` items per task type and assign stratified splits."""
    items: list[CorpusItem] = []

    for task in sorted(targets):
        want = targets[task]
        pool = raw_by_task.get(task, [])
        if len(pool) < want:
            raise ValueError(
                f"task {task!r}: need {want} items but only {len(pool)} candidates were loaded"
            )

        # Sample from a stable order with a task-derived seed, so adding a new
        # task cannot reshuffle the items chosen for existing ones.
        ordered = sorted(pool, key=lambda r: (r.source_dataset, r.source_id))
        chosen = random.Random(f"{seed}:{task}").sample(ordered, want)
        splits = assign_splits(want, seed=hash_seed(seed, task), weights=weights)

        for i, (raw, split) in enumerate(zip(chosen, splits, strict=True)):
            verifiable = raw.ground_truth is not None
            items.append(
                CorpusItem(
                    slug=f"{task}-{i:04d}",
                    task_type=raw.task_type,  # type: ignore[arg-type]
                    split=split,
                    verifiable=verifiable,
                    messages=raw.messages,
                    ground_truth=raw.ground_truth,
                    verifier=_VERIFIERS.get(task) if verifiable else None,
                    source_dataset=raw.source_dataset,
                    source_id=raw.source_id,
                    source_license=raw.source_license,
                )
            )

    _reject_duplicate_prompts(items)
    return sorted(items, key=lambda it: it.slug)


def _reject_duplicate_prompts(items: list[CorpusItem]) -> None:
    """Two items with identical prompts are a corpus bug, not a coincidence.

    The replay cache is content-addressed, so duplicates would silently share a
    single generation: they would look like two independent observations while
    actually being one. That would understate variance and narrow every
    confidence interval built on them.
    """
    import hashlib

    seen: dict[str, str] = {}
    for item in items:
        digest = hashlib.sha256(item.prompt_text().encode()).hexdigest()
        if digest in seen:
            raise ValueError(
                f"duplicate prompt: {item.slug!r} has the same text as {seen[digest]!r}. "
                f"Identical prompts would share one cached generation and be counted twice."
            )
        seen[digest] = item.slug


def hash_seed(seed: int, label: str) -> int:
    """Stable per-label seed. Python's hash() is salted per process, so not that."""
    import hashlib

    digest = hashlib.sha256(f"{seed}:{label}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def discriminative_slugs(results: list[PilotResult], min_models: int = 2) -> set[str]:
    """Slugs where the rungs actually disagreed.

    An item every model gets right (or every model gets wrong) contributes
    nothing: it cannot separate a cheap rung from an expensive one, and it gives
    the judge no loss to be validated against. Keeping only items with at least
    one pass AND at least one fail targets the band where quality differences
    are visible.
    """
    by_slug: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        by_slug[r.slug].append(r.passed)

    return {
        slug
        for slug, outcomes in by_slug.items()
        if len(outcomes) >= min_models and any(outcomes) and not all(outcomes)
    }


def filter_by_difficulty(
    items: list[CorpusItem],
    results: list[PilotResult],
    min_models: int = 2,
) -> tuple[list[CorpusItem], dict[str, object]]:
    """Keep discriminative gradable items; free-form items pass through untouched.

    Returns the filtered corpus and a report. The report is committed alongside
    the corpus so the ceiling-effect claim is a measured number, not an
    assertion.
    """
    keep = discriminative_slugs(results, min_models=min_models)
    graded = [it for it in items if it.verifiable]
    piloted = {r.slug for r in results}

    kept: list[CorpusItem] = []
    for item in items:
        if not item.verifiable or item.slug not in piloted:
            kept.append(item)
        elif item.slug in keep:
            kept.append(item)

    by_model: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        by_model[r.model].append(r.passed)

    report: dict[str, object] = {
        "gradable_before": len(graded),
        "gradable_piloted": len(piloted),
        "gradable_discriminative": len(keep),
        "removed_all_pass_or_all_fail": len(piloted) - len(keep),
        "pass_rate_by_model": {
            model: round(sum(v) / len(v), 4) for model, v in sorted(by_model.items())
        },
    }
    return kept, report
