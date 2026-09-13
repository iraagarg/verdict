"""The replay runner.

Guarantees, each of which has a test:

  * **Free to re-run.** Every result is written to a content-addressed cache, so
    a second run of an unchanged corpus makes zero paid calls.
  * **Resumable after a crash.** Resumption needs no journal: the cache key is
    the identity of the work, so finished work is simply already present.
  * **Bounded concurrency.** A fixed worker pool, so a 9,000-call run cannot
    open 9,000 sockets or trip every provider's rate limit at once.
  * **Hard spend cap.** Checked before each call with a pessimistic estimate and
    settled with the provider's real cost. Hitting it aborts the run and writes
    a partial artifact marked `aborted_budget`.
  * **Never silently partial.** A run that stops early says so in the artifact,
    so a truncated run can never be mistaken for a complete one.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from evald.corpus.schema import CorpusItem
from evald.corpus.verifiers import verify
from evald.cost import Usage, cost_nano, nano_to_usd
from evald.models_config import ModelConfig
from evald.replay.budget import Budget, BudgetExceededError
from evald.replay.cache import ResponseCache, cache_key
from evald.replay.client import GatewayError, Generation


@dataclass(frozen=True, slots=True)
class Task:
    item: CorpusItem
    model: str
    replicate_idx: int

    @property
    def uid(self) -> str:
        return f"{self.item.slug}|{self.model}|{self.replicate_idx}"


@dataclass(slots=True)
class Outcome:
    task: Task
    text: str
    usage: Usage
    cost_usd: float
    finish_reason: str
    usage_is_final: bool
    latency_ms: int
    from_cache: bool
    verifier_pass: bool | None = None
    error_kind: str | None = None


@dataclass(slots=True)
class RunStats:
    outcomes: list[Outcome] = field(default_factory=list)
    errors_by_kind: dict[str, int] = field(default_factory=dict)
    aborted: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, outcome: Outcome) -> None:
        with self._lock:
            self.outcomes.append(outcome)
            if outcome.error_kind:
                self.errors_by_kind[outcome.error_kind] = (
                    self.errors_by_kind.get(outcome.error_kind, 0) + 1
                )


CompleteFn = Callable[[str, list[dict[str, str]], dict[str, Any]], Generation]


def build_tasks(
    items: list[CorpusItem],
    models: list[str],
    replicates: dict[str, int] | None = None,
) -> list[Task]:
    """Every (item, model, replicate) to run, in a stable order.

    Stable order matters for resumability: a partially complete run resumed on a
    different day must process the same work in the same sequence.
    """
    reps = replicates or {}
    tasks: list[Task] = []
    for item in sorted(items, key=lambda it: it.slug):
        for model in models:
            for k in range(reps.get(item.slug, 1)):
                tasks.append(Task(item=item, model=model, replicate_idx=k))
    return tasks


def request_params(model: str, config: ModelConfig, max_output_tokens: int) -> dict[str, Any]:
    """Sampling parameters for one model.

    D-009: temperature and top_p return HTTP 400 on Claude Opus 5 and Sonnet 5,
    so they are only sent to models whose capability table says they are
    accepted. Determinism is therefore ASYMMETRIC across the ladder: the
    OpenAI and Groq rungs are pinned, the Anthropic mid and strong rungs are
    not. That is why replicates exist.
    """
    entry = config[model]
    params: dict[str, Any] = {
        "max_tokens": min(max_output_tokens, entry.max_output_tokens),
    }
    if entry.capabilities.supports_sampling_params:
        params["temperature"] = 0.0
    return params


def worst_case_nano(task: Task, config: ModelConfig, params: dict[str, Any]) -> int:
    """Pessimistic cost for the budget reservation.

    Assumes the model emits its full max_tokens. Being wrong in the cheap
    direction stops the run slightly early; being wrong the other way is an
    overspend nobody authorised.
    """
    entry = config[task.model]
    approx_in = max(1, len(task.item.prompt_text()) // 3)  # deliberately generous
    return cost_nano(
        task.model,
        entry,
        Usage(input_tokens=approx_in, output_tokens=int(params["max_tokens"])),
    )


def run_replay(
    tasks: list[Task],
    config: ModelConfig,
    cache: ResponseCache,
    complete: CompleteFn,
    budget: Budget,
    max_output_tokens: int = 1024,
    concurrency: int = 8,
    max_attempts: int = 3,
) -> RunStats:
    """Execute `tasks`, returning stats. Aborts hard when the budget is reached."""
    stats = RunStats()
    stop = threading.Event()

    def execute(task: Task) -> Outcome | None:
        if stop.is_set():
            return None

        params = request_params(task.model, config, max_output_tokens)
        key = cache_key(task.model, task.item.messages, params, task.replicate_idx)

        cached = cache.get(key)
        if cached is not None:
            usage = Usage(
                input_tokens=int(cached["input_tokens"]),
                output_tokens=int(cached["output_tokens"]),
            )
            return _finish(
                task,
                text=str(cached["text"]),
                usage=usage,
                # A cache hit costs nothing. Recording the original price would
                # make a free re-run look like it spent money again.
                cost_usd=0.0,
                finish_reason=str(cached["finish_reason"]),
                usage_is_final=bool(cached["usage_is_final"]),
                latency_ms=int(cached.get("latency_ms", 0)),
                from_cache=True,
            )

        reservation = worst_case_nano(task, config, params)
        try:
            budget.reserve(reservation)
        except BudgetExceededError as err:
            stop.set()
            stats.aborted = str(err)
            return None

        last_error: Exception | None = None
        for attempt in range(max_attempts):
            if stop.is_set():
                budget.release(reservation)
                return None
            try:
                gen = complete(task.model, task.item.messages, params)
            except GatewayError as err:
                last_error = err
                if not err.retryable or attempt == max_attempts - 1:
                    break
                continue
            except Exception as err:
                last_error = err
                break

            actual_nano = cost_nano(task.model, config[task.model], gen.usage)
            budget.settle(reservation, actual_nano)

            # Only cache confirmed usage. Caching an unconfirmed (truncated)
            # result would freeze a wrong token count in place forever.
            if gen.usage_is_final:
                cache.put(
                    key,
                    {
                        "text": gen.text,
                        "input_tokens": gen.usage.input_tokens,
                        "output_tokens": gen.usage.output_tokens,
                        "finish_reason": gen.finish_reason,
                        "usage_is_final": gen.usage_is_final,
                        "latency_ms": gen.latency_ms,
                        "model_served": gen.model_served,
                        "provider": gen.provider,
                    },
                )

            return _finish(
                task,
                text=gen.text,
                usage=gen.usage,
                cost_usd=nano_to_usd(actual_nano),
                finish_reason=gen.finish_reason,
                usage_is_final=gen.usage_is_final,
                latency_ms=gen.latency_ms,
                from_cache=False,
            )

        budget.release(reservation)
        kind = (
            f"gateway_{last_error.status}"
            if isinstance(last_error, GatewayError)
            else type(last_error).__name__
        )
        return Outcome(
            task=task,
            text="",
            usage=Usage(),
            cost_usd=0.0,
            finish_reason="error",
            usage_is_final=False,
            latency_ms=0,
            from_cache=False,
            error_kind=kind,
        )

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(execute, t): t for t in tasks}
        for future in as_completed(futures):
            outcome = future.result()
            if outcome is not None:
                stats.add(outcome)

    return stats


def _finish(
    task: Task,
    *,
    text: str,
    usage: Usage,
    cost_usd: float,
    finish_reason: str,
    usage_is_final: bool,
    latency_ms: int,
    from_cache: bool,
) -> Outcome:
    verifier_pass: bool | None = None
    if task.item.verifiable and task.item.verifier and task.item.ground_truth:
        verifier_pass = verify(task.item.verifier, text, task.item.ground_truth)

    return Outcome(
        task=task,
        text=text,
        usage=usage,
        cost_usd=cost_usd,
        finish_reason=finish_reason,
        usage_is_final=usage_is_final,
        latency_ms=latency_ms,
        from_cache=from_cache,
        verifier_pass=verifier_pass,
    )


def summarise(stats: RunStats, config: ModelConfig) -> list[dict[str, Any]]:
    """Per-model aggregates for the run artifact."""
    by_model: dict[str, list[Outcome]] = {}
    for outcome in stats.outcomes:
        by_model.setdefault(outcome.task.model, []).append(outcome)

    summaries: list[dict[str, Any]] = []
    for model, outcomes in sorted(by_model.items()):
        ok = [o for o in outcomes if o.error_kind is None]
        graded = [o for o in ok if o.verifier_pass is not None]
        latencies = [o.latency_ms for o in ok if not o.from_cache and o.latency_ms > 0]

        summaries.append(
            {
                "model": model,
                "rung": config[model].rung,
                "calls_attempted": len(outcomes),
                "calls_succeeded": len(ok),
                "calls_failed": len(outcomes) - len(ok),
                "cache_hits": sum(1 for o in outcomes if o.from_cache),
                "input_tokens": sum(o.usage.input_tokens for o in ok),
                "output_tokens": sum(o.usage.output_tokens for o in ok),
                "cost_usd": round(sum(o.cost_usd for o in outcomes), 8),
                "unconfirmed_usage_calls": sum(1 for o in ok if not o.usage_is_final),
                "verifier_pass_rate": (
                    round(sum(1 for o in graded if o.verifier_pass) / len(graded), 4)
                    if graded
                    else None
                ),
                "verifier_n": len(graded),
                "median_latency_ms": int(median(latencies)) if latencies else None,
            }
        )
    return summaries
