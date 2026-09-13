"""Cost arithmetic must agree exactly with the TypeScript meter."""

from __future__ import annotations

import pytest

from evald.cost import CostError, Usage, cost_nano, cost_usd, nano_to_usd, nano_usd_per_token
from evald.models_config import load_model_config

CFG = load_model_config("../../config/models.yaml")


def test_nano_conversion_is_exact() -> None:
    assert nano_usd_per_token(1.00) == 1000
    assert nano_usd_per_token(0.075) == 75
    assert nano_usd_per_token(25.00) == 25000
    assert nano_usd_per_token(6.25) == 6250
    assert nano_usd_per_token(0.05) == 50


def test_matches_the_typescript_meter_on_a_known_case() -> None:
    # Same fixture asserted in apps/gateway/src/cost/meter.test.ts:
    # sonnet-5, 1200 in / 340 cache read / 256 out = 5_028_000 nano.
    usage = Usage(input_tokens=1200, output_tokens=256, cache_read_tokens=340)
    assert cost_nano("claude-sonnet-5", CFG["claude-sonnet-5"], usage) == 5_028_000


def test_gateway_streamed_trace_reproduces() -> None:
    # The live Docker run recorded 14 in / 11 out on haiku at $0.00006900.
    usage = Usage(input_tokens=14, output_tokens=11)
    assert cost_usd("claude-haiku-4-5", CFG["claude-haiku-4-5"], usage) == pytest.approx(0.000069)


def test_accumulates_exactly_over_many_additions() -> None:
    total = Usage()
    for _ in range(10_000):
        total = total + Usage(output_tokens=1)
    assert total.output_tokens == 10_000
    assert cost_nano("claude-opus-5", CFG["claude-opus-5"], total) == 10_000 * 25_000


def test_refuses_to_undercount_unverified_cache_pricing() -> None:
    # Groq publishes no cached-input rate. Treating None as zero would
    # under-report real spend, which non-negotiable #1 forbids.
    groq = CFG["openai/gpt-oss-20b"]
    assert groq.pricing.cache_read is None
    with pytest.raises(CostError, match="no verified cache_read price"):
        cost_nano("openai/gpt-oss-20b", groq, Usage(input_tokens=10, cache_read_tokens=5))


def test_zero_cache_tokens_never_needs_a_cache_price() -> None:
    groq = CFG["openai/gpt-oss-20b"]
    assert cost_nano("openai/gpt-oss-20b", groq, Usage(input_tokens=100, output_tokens=100)) > 0


@pytest.mark.parametrize("bad", [-1, 1.5, "3"])
def test_rejects_impossible_token_counts(bad: object) -> None:
    with pytest.raises(CostError):
        Usage(output_tokens=bad)  # type: ignore[arg-type]


def test_nano_to_usd_rounds_to_schema_precision() -> None:
    assert nano_to_usd(1_000_000_000) == 1.0
    assert nano_to_usd(0) == 0.0


def test_every_configured_model_prices_a_simple_call() -> None:
    for model_id in CFG.models:
        assert cost_nano(model_id, CFG[model_id], Usage(input_tokens=1000, output_tokens=1000)) > 0


def test_ladder_is_ordered_cheapest_first() -> None:
    order = CFG.by_cost()
    prices = [CFG[m].pricing.input for m in order]
    assert prices == sorted(prices)
    assert order[-1] == "claude-opus-5"
