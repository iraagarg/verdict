"""Cost arithmetic in integer nano-USD.

Mirrors apps/gateway/src/cost/meter.ts. Everything is integer: a replay run
accumulates cost across thousands of calls, and floating-point dollars would
drift into the number that every downstream claim depends on.

$1.00 per million tokens = 1e-6 USD/token = 1000 nano-USD/token. The config
schema guarantees at most three decimals, so the conversion is exact.
"""

from __future__ import annotations

from dataclasses import dataclass

from evald.models_config import ModelEntry


class CostError(ValueError):
    """Raised when a cost cannot be computed honestly."""


def nano_usd_per_token(usd_per_mtok: float) -> int:
    return round(usd_per_mtok * 1000)


def nano_to_usd(nano: int) -> float:
    """Convert to USD at the 8 decimals the traces schema stores."""
    return round(nano / 10) / 1e8


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 0:
                raise CostError(f"{name} must be a non-negative integer, got {value!r}")

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


def cost_nano(model_id: str, model: ModelEntry, usage: Usage) -> int:
    """Exact cost in nano-USD.

    A null cache price means "not verified", never zero. If a provider billed us
    for cached tokens and we have no verified rate, refusing is the only honest
    option: silently pricing them at zero would under-report real spend.
    """
    total = usage.input_tokens * nano_usd_per_token(model.pricing.input)
    total += usage.output_tokens * nano_usd_per_token(model.pricing.output)

    for tokens, price, field in (
        (usage.cache_read_tokens, model.pricing.cache_read, "cache_read"),
        (usage.cache_write_tokens, model.pricing.cache_write, "cache_write"),
    ):
        if tokens == 0:
            continue
        if price is None:
            raise CostError(
                f"{model_id} reported {tokens} {field} tokens but config/models.yaml has no "
                f"verified {field} price. Refusing to record an under-counted cost."
            )
        total += tokens * nano_usd_per_token(price)

    return total


def cost_usd(model_id: str, model: ModelEntry, usage: Usage) -> float:
    return nano_to_usd(cost_nano(model_id, model, usage))
