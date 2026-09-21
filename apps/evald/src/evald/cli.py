"""evald command line.

    evald corpus build        assemble the frozen corpus from public datasets
    evald replay plan         print the projected cost and STOP
    evald replay run          execute, writing a versioned run artifact
    evald pilot               measure difficulty so the filter is evidence-based

`replay run` refuses to spend anything until a projection has been shown and a
cap supplied. Non-negotiable #1 applies here: a run either produces a committed
artifact or it produced nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from evald.calibrate.labeler import load_labels, run_session, set_requests
from evald.calibrate.runner import (
    build_calibration_report,
    check_ground_truth,
    judge_pairs,
    write_report,
)
from evald.calibrate.sample import build_pairs
from evald.corpus import sources
from evald.corpus.assemble import PilotResult, build_corpus, filter_by_difficulty
from evald.corpus.schema import CorpusItem, corpus_sha256
from evald.corpus.verifiers import verify
from evald.judge.judge import PairwiseJudge
from evald.judge.rubric import RUBRIC_VERSION
from evald.judge.store import GenerationStore, MissingGenerationError
from evald.models_config import ModelConfig, load_model_config
from evald.replay.artifact import ModelSummary, RunArtifact, git_sha, now_iso, pricing_snapshot
from evald.replay.budget import Budget
from evald.replay.cache import ResponseCache, cache_key
from evald.replay.client import GatewayClient
from evald.replay.plan import TOKEN_ESTIMATE_METHOD, compare_projection, project_run
from evald.replay.ratelimit import NullRateLimiter, RateLimiter
from evald.replay.runner import Progress, build_tasks, request_params, run_replay, summarise
from evald.router.fit import FIT_SPLIT, REPORT_SPLIT, fit_and_report
from evald.stats.verdict import compare_runs

DEFAULT_CORPUS = Path("../../corpus/items.jsonl")
DEFAULT_CACHE = Path("../../.cache/replay")
DEFAULT_ARTIFACTS = Path("../../artifacts")
DEFAULT_LABELS = Path("../../calibration/labels.jsonl")

#: 1,200 gradable + 300 free-form. Only the free-form slice costs judge money in
#: P3, so the volume lives where it is cheap (DECISIONS.md D-021).
DEFAULT_TARGETS = {
    "math_word_problem": 600,
    "multiple_choice": 600,
    "summarization": 100,
    "long_form_qa": 100,
    "support_reply": 100,
}


def load_corpus(path: str | Path) -> list[CorpusItem]:
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"no corpus at {path}. Run `evald corpus build` first.")
    return [
        CorpusItem.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def write_corpus(items: list[CorpusItem], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(i.model_dump(), sort_keys=True) + "\n" for i in items),
        encoding="utf-8",
    )


def cmd_corpus_build(args: argparse.Namespace) -> int:
    print("loading source datasets (this downloads from HuggingFace)...", file=sys.stderr)
    raw = {
        "math_word_problem": sources.hendrycks_math(args.targets["math_word_problem"]),
        "multiple_choice": sources.mmlu_pro(args.targets["multiple_choice"] * 2),
        "summarization": sources.cnn_summarization(args.targets["summarization"] * 6),
        "long_form_qa": sources.dolly_long_form_qa(args.targets["long_form_qa"]),
        "support_reply": sources.bitext_support_reply(args.targets["support_reply"]),
    }
    for task, pool in raw.items():
        print(f"  {task:<20} {len(pool):>5} candidates", file=sys.stderr)

    items = build_corpus(raw, args.targets, seed=args.seed)
    write_corpus(items, args.out)

    by_task = Counter(i.task_type for i in items)
    by_split = Counter(i.split for i in items)
    print(f"\nwrote {len(items)} items to {args.out}")
    print(f"  sha256   {corpus_sha256(items)}")
    print(f"  by task  {dict(sorted(by_task.items()))}")
    print(f"  by split {dict(sorted(by_split.items()))}")
    print(f"  gradable {sum(1 for i in items if i.verifiable)}")
    print("\nNext: `evald pilot` to measure difficulty before filtering.")
    return 0


def _models(args: argparse.Namespace, config_models: list[str]) -> list[str]:
    return args.models.split(",") if args.models else config_models


def cmd_replay_plan(args: argparse.Namespace) -> int:
    config = load_model_config(args.config)
    items = load_corpus(args.corpus)
    models = _models(args, config.by_cost())
    cache = ResponseCache(args.cache)

    reps = _replicate_map(items, args.replicate_subset, args.replicates)
    cached = sum(
        1
        for item in items
        for m in models
        for k in range(reps.get(item.slug, 1))
        if cache_key(m, item.messages, request_params(m, config, args.max_tokens), k) in cache
    )

    projection = project_run(items, models, config, replicates=reps, already_cached=cached)
    print(projection.render(cap_usd=args.cap))
    print(f"\ncorpus: {len(items)} items, sha256 {corpus_sha256(items)[:16]}...")
    print("\nNothing has been spent. Run `evald replay run --cap <usd>` to execute.")
    return 0


def _print_progress(p: Progress) -> None:
    print(p.render(), file=sys.stderr, flush=True)


def _replicate_map(items: list[CorpusItem], subset: int, k: int) -> dict[str, int]:
    """K replicates on the first `subset` slugs, 1 elsewhere.

    D-009 made sampling variance something we measure rather than eliminate,
    because temperature cannot be pinned on the Anthropic rungs. Measuring it on
    a subset costs ~15% extra instead of 200% (DECISIONS.md D-022).
    """
    if subset <= 0 or k <= 1:
        return {}
    chosen = sorted(i.slug for i in items)[:subset]
    return dict.fromkeys(chosen, k)


def cmd_replay_run(args: argparse.Namespace) -> int:
    config = load_model_config(args.config)
    items = load_corpus(args.corpus)
    models = _models(args, config.by_cost())
    cache = ResponseCache(args.cache)
    reps = _replicate_map(items, args.replicate_subset, args.replicates)

    projection = project_run(items, models, config, replicates=reps)
    print(projection.render(cap_usd=args.cap))

    if projection.total_cost_usd > args.cap and not args.yes:
        print(
            f"\nRefusing to start: projection ${projection.total_cost_usd:.4f} exceeds "
            f"cap ${args.cap:.2f}. Raise --cap, cut --models, or pass --yes to run until "
            f"the cap aborts it.",
            file=sys.stderr,
        )
        return 2

    if not args.yes:
        reply = input("\nProceed? [y/N] ").strip().lower()
        if reply != "y":
            print("aborted; nothing spent.")
            return 1

    with GatewayClient(args.gateway) as client:
        if not client.health():
            raise SystemExit(f"gateway at {args.gateway} is not healthy; start it with `make up`.")

        budget = Budget(cap_usd=args.cap)
        tasks = build_tasks(items, models, replicates=reps)
        stats = run_replay(
            tasks,
            config,
            cache,
            client.complete,
            budget,
            max_output_tokens=args.max_tokens,
            concurrency=args.concurrency,
            rate_limiter=RateLimiter(rpm=args.rpm) if args.rpm > 0 else NullRateLimiter(),
            on_progress=_print_progress,
        )

    summaries = summarise(stats, config)
    actual = round(sum(s["cost_usd"] for s in summaries), 8)

    artifact = RunArtifact(
        run_id=args.run_id or str(uuid.uuid4()),
        created_at=now_iso(),
        git_sha=git_sha(),
        seed=args.seed,
        corpus_sha256=corpus_sha256(items),
        corpus_size=len(items),
        corpus_by_split=dict(sorted(Counter(i.split for i in items).items())),
        corpus_by_task=dict(sorted(Counter(i.task_type for i in items).items())),
        models=models,
        pricing_snapshot=pricing_snapshot(models, config),
        replicates=reps,
        status="aborted_budget" if stats.aborted else "complete",
        abort_reason=stats.aborted,
        summaries=[ModelSummary.model_validate(s) for s in summaries],
        cost=compare_projection(projection.total_cost_usd, actual),
        cache={
            "hits": float(cache.stats.hits),
            "misses": float(cache.stats.misses),
            "hit_rate": round(cache.stats.hit_rate, 4),
        },
        token_estimate_method=TOKEN_ESTIMATE_METHOD,
        errors_by_kind=stats.errors_by_kind,
    )

    out = artifact.write(Path(args.artifacts) / f"replay-{artifact.run_id[:8]}.json")
    print(f"\nartifact: {out}")
    print(f"status:   {artifact.status}")
    print(f"spent:    ${actual:.4f} (projected ${projection.total_cost_usd:.4f})")
    if stats.aborted:
        print(f"ABORTED:  {stats.aborted}", file=sys.stderr)
        return 3
    return 0


def _pilot_sample(items: list[CorpusItem], n: int, seed: int) -> list[CorpusItem]:
    """A seeded, STRATIFIED sample of the gradable slice.

    Taking the first n verifiable items looks reasonable and is wrong: slugs
    sort alphabetically, so `math_word_problem` comes before `multiple_choice`
    and a 100-item pilot tests only GSM8K. The MMLU-Pro half — the half chosen
    specifically to resist ceiling effects — would never be measured, and the
    pass rates would be silently attributed to the whole gradable slice.
    """
    import random

    gradable = [i for i in items if i.verifiable]
    by_task: dict[str, list[CorpusItem]] = {}
    for item in gradable:
        by_task.setdefault(item.task_type, []).append(item)

    if not by_task:
        return []

    per_task = max(1, n // len(by_task))
    chosen: list[CorpusItem] = []
    for task in sorted(by_task):
        pool = sorted(by_task[task], key=lambda i: i.slug)
        take = min(per_task, len(pool))
        chosen.extend(random.Random(f"{seed}:pilot:{task}").sample(pool, take))

    return sorted(chosen, key=lambda i: i.slug)[:n]


def cmd_pilot(args: argparse.Namespace) -> int:
    """Measure whether the gradable slice actually discriminates between rungs."""
    config = load_model_config(args.config)
    items = _pilot_sample(load_corpus(args.corpus), args.n, args.seed)
    models = _models(args, ["openai/gpt-oss-20b", "claude-haiku-4-5", "claude-opus-5"])

    projection = project_run(items, models, config)
    print(projection.render(cap_usd=args.cap))
    if not args.yes and input("\nProceed? [y/N] ").strip().lower() != "y":
        return 1

    with GatewayClient(args.gateway) as client:
        stats = run_replay(
            build_tasks(items, models),
            config,
            ResponseCache(args.cache),
            client.complete,
            Budget(cap_usd=args.cap),
            max_output_tokens=args.max_tokens,
            concurrency=args.concurrency,
            rate_limiter=RateLimiter(rpm=args.rpm) if args.rpm > 0 else NullRateLimiter(),
            on_progress=_print_progress,
        )

    results = [
        PilotResult(
            slug=o.task.item.slug,
            model=o.task.model,
            passed=bool(o.verifier_pass),
            task_type=o.task.item.task_type,
        )
        for o in stats.outcomes
        if o.error_kind is None and o.verifier_pass is not None
    ]
    kept, report = filter_by_difficulty(load_corpus(args.corpus), results, mode=args.mode)

    Path(args.artifacts).mkdir(parents=True, exist_ok=True)
    report_path = Path(args.artifacts) / "difficulty-pilot.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    print(f"\npilot report: {report_path}")
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.apply:
        write_corpus(kept, args.corpus)
        print(f"\nfiltered corpus written: {len(kept)} items (was {len(load_corpus(args.corpus))})")
    else:
        print("\nRe-run with --apply to filter the corpus to the discriminative band.")
    return 0


def _judge_complete(
    client: GatewayClient,
) -> Callable[[str, list[dict[str, str]], dict[str, Any]], str]:
    """Adapt the gateway client to the judge's simpler text-in/text-out shape."""

    def complete(model: str, messages: list[dict[str, str]], params: dict[str, Any]) -> str:
        return client.complete(model, messages, params).text

    return complete


def _load_pairs(
    args: argparse.Namespace, items: list[CorpusItem], config: ModelConfig
) -> list[Any]:
    candidates = [m for m in _models(args, config.by_cost()) if m != config.reference_model]
    return build_pairs(
        items,
        candidate_models=candidates,
        reference_model=config.reference_model,
        n=args.pairs,
        seed=args.seed,
    )


def cmd_label(args: argparse.Namespace) -> int:
    """Interactive labelling. Blind, resumable, saves after every decision."""
    config = load_model_config(args.config)
    items = load_corpus(args.corpus)
    by_slug = {i.slug: i for i in items}
    pairs = _load_pairs(args, items, config)

    store = GenerationStore(ResponseCache(args.cache), config, args.max_tokens)

    texts: dict[str, tuple[str, str]] = {}
    requests: dict[str, str] = {}
    usable = []
    for pair in pairs:
        item = by_slug[pair.slug]
        try:
            candidate = store.get(item, pair.candidate_model)
            reference = store.get(item, pair.reference_model)
        except MissingGenerationError:
            continue
        texts[pair.pair_id] = (candidate.text, reference.text)
        requests[pair.pair_id] = item.prompt_text()
        usable.append(pair)

    if not usable:
        raise SystemExit(
            "No cached generations to label. Run `evald replay run` for the reference model "
            f"({config.reference_model}) and at least one candidate first."
        )
    if len(usable) < len(pairs):
        print(
            f"note: {len(pairs) - len(usable)} of {len(pairs)} pairs have no cached generation "
            f"and were skipped. Replay more models to label the full sample.",
            file=sys.stderr,
        )

    set_requests(requests)
    run_session(usable, texts, Path(args.labels))
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Judge the labelled pairs, compare against the human labels, write the report."""
    config = load_model_config(args.config)
    items = load_corpus(args.corpus)
    by_slug = {i.slug: i for i in items}
    pairs = _load_pairs(args, items, config)

    labels = load_labels(Path(args.labels))
    if not labels:
        raise SystemExit(f"no labels at {args.labels}. Run `evald label` first.")

    labelled_ids = set(labels)
    to_judge = [p for p in pairs if p.pair_id in labelled_ids]

    store = GenerationStore(ResponseCache(args.cache), config, args.max_tokens)

    projection_note = (
        f"Judging {len(to_judge)} labelled pairs x 2 positions = {len(to_judge) * 2} calls "
        f"on {args.judge_model}."
    )
    print(projection_note)
    if not args.yes and input("Proceed? [y/N] ").strip().lower() != "y":
        return 1

    with GatewayClient(args.gateway) as client:
        if not client.health():
            raise SystemExit(f"gateway at {args.gateway} is not healthy; start it with `make up`.")

        judge = PairwiseJudge(
            args.judge_model, _judge_complete(client), max_tokens=args.judge_max_tokens
        )
        judgments = judge_pairs(to_judge, by_slug, store, judge, seed=args.seed)

        ground_truth = None
        caveat = ""
        if not args.skip_ground_truth:
            candidates = [m for m in _models(args, config.by_cost()) if m != config.reference_model]
            ground_truth = check_ground_truth(
                items,
                candidates,
                config.reference_model,
                store,
                judge,
                args.seed,
                limit=args.gt_limit,
            )
            caveat = (
                "Measured only on gradable items where exactly one side is correct. The maths "
                "slice is verified not-too-easy but not yet verified not-too-hard (DECISIONS.md "
                "D-030), so this accuracy may be computed on an unrepresentative difficulty band."
            )

    report = build_calibration_report(
        labels=labels,
        judgments=judgments,
        pairs=pairs,
        judge_model=args.judge_model,
        reference_model=config.reference_model,
        rubric_version=RUBRIC_VERSION,
        corpus_sha=corpus_sha256(items),
        labeler=args.labeler,
        seed=args.seed,
        ground_truth=ground_truth,
        ground_truth_caveat=caveat,
    )

    out = write_report(report, Path(args.artifacts))
    print()
    print(report.summary())
    print(f"\nreport: {out}")
    if not report.clears_gate:
        print(
            "\nThe judge did NOT clear the gate. The number stands as measured; see "
            "`diagnosis` and `disagreements` in the report, revise the rubric, bump "
            "RUBRIC_VERSION, and re-label a FRESH sample.",
            file=sys.stderr,
        )
        return 4
    return 0


def cmd_fit(args: argparse.Namespace) -> int:
    """Fit the routing policy on TRAIN and report it on HELD-OUT.

    Consumes judged replay records; calls no models, so the threshold sweep is
    free and exactly reproducible.
    """
    config = load_model_config(args.config)
    records_path = Path(args.records)
    if not records_path.is_file():
        raise SystemExit(
            f"no routing records at {records_path}.\n"
            f"These are built from judged replay runs: every corpus item needs, for each "
            f"model, whether it won-or-tied the reference and what it cost. "
            f"Run `evald replay run` and `evald judge` first."
        )

    from evald.router.records import ItemRecord

    records = [
        ItemRecord(**json.loads(line))
        for line in records_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    ladder = [m for m in config.by_cost()]
    out = fit_and_report(
        records,
        cheap_model=args.cheap_model or ladder[0],
        strong_model=config.safe_default,
        ladder=ladder,
        safe_default=config.safe_default,
        created_at=now_iso(),
        git_sha=git_sha(),
        corpus_sha256=corpus_sha256(load_corpus(args.corpus)),
        judge_model=args.judge_model,
        rubric_version=RUBRIC_VERSION,
        reference_model=config.reference_model,
        floor=args.floor,
        margin=args.margin,
        k_samples=args.k_samples,
        verifier_cost_nano=args.verifier_cost_nano,
        seed=args.seed,
    )

    artifacts = Path(args.artifacts)
    pareto_path = out.pareto.write(artifacts / "pareto.json")
    policy_path = out.policy.write(artifacts / "policy.json")

    print(f"fit on {FIT_SPLIT!r}, reported on {REPORT_SPLIT!r}")
    print(f"  pareto: {pareto_path}")
    print(f"  policy: {policy_path}")
    if out.pareto.chosen_threshold is None:
        print(f"\n  {out.pareto.no_eligible_threshold_reason}", file=sys.stderr)
        return 5

    held = out.pareto.held_out
    assert held is not None
    print(f"\n  chosen threshold  {out.pareto.chosen_threshold}  (on {FIT_SPLIT})")
    print(
        f"  HELD-OUT quality  {held.quality:.4f}   cost {held.cost_ratio * 100:.1f}% of all-strong"
    )
    print(f"  HELD-OUT verdict  {held.verdict}  (n={held.n})")
    print("\n  Only the HELD-OUT numbers may be quoted.")
    return 0


def cmd_verdict(args: argparse.Namespace) -> int:
    """Compare two models on the gradable slice, from cached generations.

    Uses VERIFIER correctness (exact match against ground truth), not the
    judge's win-or-tie. That is a different metric from D-003's and the output
    says so — but it needs no judge, no labels and no spend, so it is the first
    statistically honest comparison available.
    """
    config = load_model_config(args.config)
    items = load_corpus(args.corpus)
    store = GenerationStore(ResponseCache(args.cache), config, args.max_tokens)

    paired_a: list[bool] = []
    paired_b: list[bool] = []
    slugs: list[str] = []
    missing = 0

    for item in items:
        if not item.verifiable or item.verifier is None or item.ground_truth is None:
            continue
        try:
            gen_a = store.get(item, args.model_a)
            gen_b = store.get(item, args.model_b)
        except MissingGenerationError:
            missing += 1
            continue
        paired_a.append(verify(item.verifier, gen_a.text, item.ground_truth))
        paired_b.append(verify(item.verifier, gen_b.text, item.ground_truth))
        slugs.append(item.slug)

    if not paired_a:
        raise SystemExit(
            f"no items have cached generations for BOTH {args.model_a} and {args.model_b}. "
            f"Run the pilot or a replay covering both models first."
        )

    verdict = compare_runs(
        paired_a,
        paired_b,
        margin=args.margin,
        label_baseline=args.model_a,
        label_candidate=args.model_b,
    )
    print(verdict.summary())
    print("\n  metric        verifier correctness (exact match), NOT judge win-or-tie")
    print(f"  items paired  {len(paired_a)} of {sum(1 for i in items if i.verifiable)} gradable")
    if missing:
        print(f"  skipped       {missing} items lacking a cached generation for one side")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="evald")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--config", default="../../config/models.yaml")
        sp.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
        sp.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
        sp.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
        sp.add_argument("--gateway", default="http://localhost:8080")
        sp.add_argument(
            "--models", default="", help="comma-separated; defaults to the whole ladder"
        )
        sp.add_argument("--max-tokens", type=int, default=1024)
        sp.add_argument("--concurrency", type=int, default=8)
        sp.add_argument("--cap", type=float, required=True, help="hard USD spend cap")
        sp.add_argument("--yes", action="store_true")
        sp.add_argument("--seed", type=int, default=20260914)
        sp.add_argument("--replicates", type=int, default=3)
        sp.add_argument("--replicate-subset", type=int, default=200)
        sp.add_argument(
            "--rpm",
            type=int,
            default=0,
            help=(
                "cap outgoing requests per minute to the provider's published limit "
                "(Groq's free plan is 30). 0 disables pacing."
            ),
        )

    corpus = sub.add_parser("corpus").add_subparsers(dest="sub", required=True)
    cb = corpus.add_parser("build")
    cb.add_argument("--out", type=Path, default=DEFAULT_CORPUS)
    cb.add_argument("--seed", type=int, default=20260914)
    cb.set_defaults(func=cmd_corpus_build, targets=DEFAULT_TARGETS)

    replay = sub.add_parser("replay").add_subparsers(dest="sub", required=True)
    rp = replay.add_parser("plan")
    common(rp)
    rp.set_defaults(func=cmd_replay_plan)

    rr = replay.add_parser("run")
    common(rr)
    rr.add_argument("--run-id", default="")
    rr.set_defaults(func=cmd_replay_run)

    pilot = sub.add_parser("pilot")
    common(pilot)
    pilot.add_argument("-n", type=int, default=200)
    pilot.add_argument("--apply", action="store_true")
    pilot.add_argument(
        "--mode",
        choices=["discriminative", "drop_easy"],
        default="discriminative",
        help=(
            "discriminative: keep items with at least one pass AND one fail "
            "(needs a cheap/mid/strong pilot). drop_easy: keep everything except "
            "items every pilot model got right (correct when the pilot was "
            "cheap models only)."
        ),
    )
    pilot.set_defaults(func=cmd_pilot)

    label = sub.add_parser("label", help="hand-label sampled pairs (blind, resumable)")
    label.add_argument("--config", default="../../config/models.yaml")
    label.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    label.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    label.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    label.add_argument("--models", default="")
    label.add_argument("--pairs", type=int, default=200)
    label.add_argument("--max-tokens", type=int, default=1024)
    label.add_argument("--seed", type=int, default=20260915)
    label.set_defaults(func=cmd_label)

    cal = sub.add_parser("calibrate", help="judge the labelled pairs and report kappa")
    cal.add_argument("--config", default="../../config/models.yaml")
    cal.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    cal.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    cal.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    cal.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    cal.add_argument("--gateway", default="http://localhost:8080")
    cal.add_argument("--models", default="")
    cal.add_argument("--pairs", type=int, default=200)
    cal.add_argument("--max-tokens", type=int, default=1024)
    cal.add_argument("--judge-model", default="claude-opus-5")
    cal.add_argument("--judge-max-tokens", type=int, default=700)
    cal.add_argument("--labeler", default="iraa")
    cal.add_argument("--gt-limit", type=int, default=150)
    cal.add_argument("--skip-ground-truth", action="store_true")
    cal.add_argument("--seed", type=int, default=20260915)
    cal.add_argument("--yes", action="store_true")
    cal.set_defaults(func=cmd_calibrate)

    fit = sub.add_parser("fit", help="fit the routing policy and write pareto.json + policy.json")
    fit.add_argument("--config", default="../../config/models.yaml")
    fit.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    fit.add_argument("--records", type=Path, default=Path("../../artifacts/routing-records.jsonl"))
    fit.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    fit.add_argument("--cheap-model", default="")
    fit.add_argument("--judge-model", default="claude-opus-5")
    fit.add_argument("--floor", type=float, default=0.9)
    fit.add_argument("--margin", type=float, default=0.03)
    fit.add_argument("--k-samples", type=int, default=3)
    fit.add_argument("--verifier-cost-nano", type=int, default=0)
    fit.add_argument("--seed", type=int, default=20260915)
    fit.set_defaults(func=cmd_fit)

    vd = sub.add_parser("verdict", help="compare two models on the gradable slice (free, cached)")
    vd.add_argument("--config", default="../../config/models.yaml")
    vd.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    vd.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    vd.add_argument("--model-a", required=True, help="baseline")
    vd.add_argument("--model-b", required=True, help="candidate")
    vd.add_argument("--max-tokens", type=int, default=2048)
    vd.add_argument("--margin", type=float, default=0.03)
    vd.set_defaults(func=cmd_verdict)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
