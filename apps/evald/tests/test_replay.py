"""Runner guarantees: determinism, cache, spend cap, resumability, concurrency."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from evald.corpus.schema import CorpusItem
from evald.cost import Usage
from evald.models_config import load_model_config
from evald.replay.budget import Budget, BudgetExceededError
from evald.replay.cache import ResponseCache, cache_key
from evald.replay.client import GatewayError, Generation
from evald.replay.plan import estimate_input_tokens, project_run
from evald.replay.runner import build_tasks, request_params, run_replay, summarise

CFG = load_model_config("../../config/models.yaml")


def make_item(slug: str = "math_word_problem-0000", verifiable: bool = True) -> CorpusItem:
    # The prompt must vary with the slug. The cache is keyed on CONTENT, so two
    # items with identical prompts legitimately share one generation — which is
    # correct behaviour, and why build_corpus rejects duplicate prompts.
    return CorpusItem(
        slug=slug,
        task_type="math_word_problem",
        split="test",
        verifiable=verifiable,
        messages=[{"role": "user", "content": f"What is 2+2? (item {slug})"}],
        ground_truth="4" if verifiable else None,
        verifier="final_number" if verifiable else None,
        source_dataset="openai/gsm8k",
        source_id="test-0",
        source_license="MIT",
    )


def fake_complete(text: str = "#### 4", in_tok: int = 10, out_tok: int = 5) -> Any:
    calls: list[tuple[str, dict[str, Any]]] = []

    def complete(model: str, messages: list[dict[str, str]], params: dict[str, Any]) -> Generation:
        calls.append((model, params))
        return Generation(
            text=text,
            usage=Usage(input_tokens=in_tok, output_tokens=out_tok),
            finish_reason="stop",
            model_served=model,
            provider="anthropic",
            cost_usd=0.0,
            usage_is_final=True,
            request_id="req-1",
            latency_ms=12,
        )

    complete.calls = calls  # type: ignore[attr-defined]
    return complete


class TestCacheKey:
    def test_is_stable_across_dict_ordering(self) -> None:
        a = cache_key("m", [{"role": "user", "content": "x"}], {"a": 1, "b": 2}, 0)
        b = cache_key("m", [{"role": "user", "content": "x"}], {"b": 2, "a": 1}, 0)
        assert a == b

    def test_separates_replicates(self) -> None:
        # Temperature cannot be pinned on Claude (D-009), so replicate 0 and 1
        # are genuinely different samples and must not collide.
        k0 = cache_key("m", [{"role": "user", "content": "x"}], {}, 0)
        k1 = cache_key("m", [{"role": "user", "content": "x"}], {}, 1)
        assert k0 != k1

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"model": "other"},
            {"messages": [{"role": "user", "content": "different"}]},
            {"params": {"temperature": 1.0}},
        ],
    )
    def test_changes_when_anything_that_affects_output_changes(
        self, kwargs: dict[str, Any]
    ) -> None:
        def key(**over: Any) -> str:
            merged: dict[str, Any] = {
                "model": "m",
                "messages": [{"role": "user", "content": "x"}],
                "params": {"temperature": 0.0},
                "replicate_idx": 0,
                **over,
            }
            return cache_key(
                str(merged["model"]),
                merged["messages"],
                merged["params"],
                int(merged["replicate_idx"]),
            )

        assert key() != key(**kwargs)


class TestResponseCache:
    def test_round_trips(self, tmp_path: Path) -> None:
        cache = ResponseCache(tmp_path)
        assert cache.get("abc123") is None
        cache.put("abc123", {"text": "hello"})
        assert cache.get("abc123") == {"text": "hello"}
        assert cache.stats.hits == 1
        assert cache.stats.misses == 1

    def test_survives_a_truncated_file_from_a_hard_kill(self, tmp_path: Path) -> None:
        cache = ResponseCache(tmp_path)
        cache.put("deadbeef", {"text": "ok"})
        cache.path_for("deadbeef").write_text('{"text": "trunc')
        assert cache.get("deadbeef") is None  # treated as a miss, not a crash

    def test_writes_are_atomic(self, tmp_path: Path) -> None:
        cache = ResponseCache(tmp_path)
        cache.put("k" * 64, {"text": "v"})
        leftovers = list(tmp_path.rglob("*.tmp"))
        assert leftovers == []


class TestBudget:
    def test_allows_spending_under_the_cap(self) -> None:
        b = Budget(cap_usd=1.0)
        b.reserve(500_000_000)  # $0.50
        b.settle(500_000_000, 400_000_000)
        assert b.spent_usd == pytest.approx(0.4)

    def test_refuses_a_call_that_would_exceed_the_cap(self) -> None:
        b = Budget(cap_usd=1.0)
        b.reserve(900_000_000)
        with pytest.raises(BudgetExceededError):
            b.reserve(200_000_000)

    def test_counts_outstanding_reservations_not_just_actual_spend(self) -> None:
        # Without this, N concurrent workers could each pass the check and
        # collectively blow through the cap.
        b = Budget(cap_usd=1.0)
        for _ in range(10):
            b.reserve(100_000_000)
        with pytest.raises(BudgetExceededError):
            b.reserve(1)

    def test_releasing_an_unused_reservation_restores_headroom(self) -> None:
        b = Budget(cap_usd=1.0)
        b.reserve(900_000_000)
        b.release(900_000_000)
        b.reserve(900_000_000)  # must not raise

    def test_is_thread_safe(self) -> None:
        b = Budget(cap_usd=1_000.0)

        def work() -> None:
            for _ in range(200):
                b.reserve(1_000)
                b.settle(1_000, 1_000)

        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert b.spent_usd == pytest.approx(8 * 200 * 1_000 / 1e9)

    def test_rejects_a_nonsense_cap(self) -> None:
        with pytest.raises(ValueError):
            Budget(cap_usd=0)


class TestRequestParams:
    def test_pins_temperature_where_the_model_accepts_it(self) -> None:
        assert request_params("openai/gpt-oss-20b", CFG, 1024)["temperature"] == 0.0
        assert request_params("gpt-5-nano", CFG, 1024)["temperature"] == 0.0

    def test_omits_temperature_where_it_returns_http_400(self) -> None:
        # D-009: Claude Opus 5 and Sonnet 5 reject sampling params outright.
        assert "temperature" not in request_params("claude-opus-5", CFG, 1024)
        assert "temperature" not in request_params("claude-sonnet-5", CFG, 1024)

    def test_caps_max_tokens_at_the_model_limit(self) -> None:
        params = request_params("openai/gpt-oss-20b", CFG, 10_000_000)
        assert params["max_tokens"] == CFG["openai/gpt-oss-20b"].max_output_tokens


class TestBuildTasks:
    def test_is_deterministic_and_ordered(self) -> None:
        items = [make_item("b-1"), make_item("a-1")]
        first = [t.uid for t in build_tasks(items, ["m1", "m2"])]
        second = [t.uid for t in build_tasks(items, ["m1", "m2"])]
        assert first == second
        assert first[0].startswith("a-1")

    def test_expands_replicates_only_where_requested(self) -> None:
        items = [make_item("a-1"), make_item("b-1")]
        tasks = build_tasks(items, ["m1"], replicates={"a-1": 3})
        assert sum(1 for t in tasks if t.item.slug == "a-1") == 3
        assert sum(1 for t in tasks if t.item.slug == "b-1") == 1


class TestRunReplay:
    def test_runs_every_task_and_scores_the_gradable_ones(self, tmp_path: Path) -> None:
        tasks = build_tasks([make_item()], ["claude-haiku-4-5"])
        stats = run_replay(
            tasks, CFG, ResponseCache(tmp_path), fake_complete(), Budget(10.0), concurrency=2
        )
        assert len(stats.outcomes) == 1
        assert stats.outcomes[0].verifier_pass is True

    def test_marks_a_wrong_answer_as_failing_the_verifier(self, tmp_path: Path) -> None:
        tasks = build_tasks([make_item()], ["claude-haiku-4-5"])
        stats = run_replay(
            tasks, CFG, ResponseCache(tmp_path), fake_complete("#### 5"), Budget(10.0)
        )
        assert stats.outcomes[0].verifier_pass is False

    def test_a_second_run_is_free_and_makes_no_calls(self, tmp_path: Path) -> None:
        cache = ResponseCache(tmp_path)
        tasks = build_tasks([make_item()], ["claude-haiku-4-5"])
        complete = fake_complete()

        run_replay(tasks, CFG, cache, complete, Budget(10.0))
        assert len(complete.calls) == 1

        stats = run_replay(tasks, CFG, cache, complete, Budget(10.0))
        assert len(complete.calls) == 1  # no new paid calls
        assert stats.outcomes[0].from_cache is True
        assert stats.outcomes[0].cost_usd == 0.0

    def test_resumes_after_a_crash_without_redoing_finished_work(self, tmp_path: Path) -> None:
        # Resumption needs no journal: the cache key IS the identity of the work.
        cache = ResponseCache(tmp_path)
        items = [make_item(f"math_word_problem-{i:04d}") for i in range(4)]
        complete = fake_complete()

        run_replay(build_tasks(items[:2], ["claude-haiku-4-5"]), CFG, cache, complete, Budget(10.0))
        assert len(complete.calls) == 2

        stats = run_replay(
            build_tasks(items, ["claude-haiku-4-5"]), CFG, cache, complete, Budget(10.0)
        )
        assert len(complete.calls) == 4  # only the two new items were paid for
        assert sum(1 for o in stats.outcomes if o.from_cache) == 2

    def test_aborts_hard_when_the_spend_cap_is_reached(self, tmp_path: Path) -> None:
        items = [make_item(f"math_word_problem-{i:04d}") for i in range(50)]
        tasks = build_tasks(items, ["claude-opus-5"])
        complete = fake_complete()
        # A cap far below what 50 opus calls would reserve.
        stats = run_replay(
            tasks, CFG, ResponseCache(tmp_path), complete, Budget(0.0001), concurrency=1
        )
        assert stats.aborted is not None
        assert "spend cap reached" in stats.aborted
        assert len(complete.calls) < 50

    def test_never_exceeds_the_cap_even_under_concurrency(self, tmp_path: Path) -> None:
        items = [make_item(f"math_word_problem-{i:04d}") for i in range(200)]
        tasks = build_tasks(items, ["claude-opus-5"])
        budget = Budget(0.01)
        run_replay(tasks, CFG, ResponseCache(tmp_path), fake_complete(), budget, concurrency=16)
        assert budget.spent_usd <= 0.01

    def test_one_failing_item_does_not_kill_the_run(self, tmp_path: Path) -> None:
        calls = {"n": 0}

        def flaky(model: str, messages: list[dict[str, str]], params: dict[str, Any]) -> Generation:
            calls["n"] += 1
            if calls["n"] == 1:
                raise GatewayError(400, "bad request", retryable=False)
            gen: Generation = fake_complete()(model, messages, params)
            return gen

        items = [make_item(f"math_word_problem-{i:04d}") for i in range(3)]
        stats = run_replay(
            build_tasks(items, ["claude-haiku-4-5"]),
            CFG,
            ResponseCache(tmp_path),
            flaky,
            Budget(10.0),
            concurrency=1,
        )
        assert len(stats.outcomes) == 3
        assert sum(1 for o in stats.outcomes if o.error_kind) == 1
        assert stats.errors_by_kind == {"gateway_400": 1}

    def test_retries_a_retryable_failure(self, tmp_path: Path) -> None:
        calls = {"n": 0}

        def flaky(model: str, messages: list[dict[str, str]], params: dict[str, Any]) -> Generation:
            calls["n"] += 1
            if calls["n"] < 3:
                raise GatewayError(503, "upstream down", retryable=True)
            gen: Generation = fake_complete()(model, messages, params)
            return gen

        stats = run_replay(
            build_tasks([make_item()], ["claude-haiku-4-5"]),
            CFG,
            ResponseCache(tmp_path),
            flaky,
            Budget(10.0),
            max_attempts=3,
        )
        assert stats.outcomes[0].error_kind is None
        assert calls["n"] == 3

    def test_does_not_retry_a_non_retryable_failure(self, tmp_path: Path) -> None:
        calls = {"n": 0}

        def always_400(
            model: str, messages: list[dict[str, str]], params: dict[str, Any]
        ) -> Generation:
            calls["n"] += 1
            raise GatewayError(400, "bad", retryable=False)

        run_replay(
            build_tasks([make_item()], ["claude-haiku-4-5"]),
            CFG,
            ResponseCache(tmp_path),
            always_400,
            Budget(10.0),
            max_attempts=5,
        )
        assert calls["n"] == 1

    def test_does_not_cache_unconfirmed_usage(self, tmp_path: Path) -> None:
        # Caching a truncated result would freeze a wrong token count forever.
        def truncated(
            model: str, messages: list[dict[str, str]], params: dict[str, Any]
        ) -> Generation:
            return Generation(
                text="partial",
                usage=Usage(input_tokens=5, output_tokens=2),
                finish_reason="length",
                model_served=model,
                provider="anthropic",
                cost_usd=0.0,
                usage_is_final=False,
                request_id="r",
                latency_ms=5,
            )

        cache = ResponseCache(tmp_path)
        run_replay(
            build_tasks([make_item()], ["claude-haiku-4-5"]), CFG, cache, truncated, Budget(10.0)
        )
        assert cache.stats.writes == 0

    def test_summarise_reports_per_model_aggregates(self, tmp_path: Path) -> None:
        items = [make_item(f"math_word_problem-{i:04d}") for i in range(4)]
        stats = run_replay(
            build_tasks(items, ["claude-haiku-4-5", "claude-opus-5"]),
            CFG,
            ResponseCache(tmp_path),
            fake_complete(),
            Budget(10.0),
        )
        summaries = {s["model"]: s for s in summarise(stats, CFG)}
        assert summaries["claude-haiku-4-5"]["calls_succeeded"] == 4
        assert summaries["claude-haiku-4-5"]["verifier_pass_rate"] == 1.0
        assert summaries["claude-opus-5"]["rung"] == "strong"


class TestProjection:
    def test_estimates_scale_with_the_number_of_models(self) -> None:
        items = [make_item(f"math_word_problem-{i:04d}") for i in range(10)]
        one = project_run(items, ["claude-haiku-4-5"], CFG)
        two = project_run(items, ["claude-haiku-4-5", "claude-opus-5"], CFG)
        assert two.total_calls == 2 * one.total_calls
        assert two.total_cost_usd > one.total_cost_usd

    def test_prices_replicates_only_where_requested(self) -> None:
        items = [make_item("a-1"), make_item("b-1")]
        base = project_run(items, ["claude-haiku-4-5"], CFG)
        with_reps = project_run(items, ["claude-haiku-4-5"], CFG, replicates={"a-1": 3})
        assert with_reps.total_calls == base.total_calls + 2

    def test_renders_a_cap_verdict(self) -> None:
        items = [make_item()]
        text = project_run(items, ["claude-opus-5"], CFG).render(cap_usd=0.0)
        assert "EXCEEDS CAP" in text
        assert "estimate, not a measured number" in text

    def test_input_estimate_grows_with_prompt_length(self) -> None:
        short = make_item()
        long_item = short.model_copy(update={"messages": [{"role": "user", "content": "x" * 4000}]})
        assert estimate_input_tokens(long_item) > estimate_input_tokens(short)
