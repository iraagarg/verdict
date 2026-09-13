"""Tests for environment validation.

These mirror apps/gateway/src/env.test.ts. Where the two services validate the
same variable, they must agree -- a DATABASE_URL the gateway accepts and evald
rejects would mean `docker compose up` half-works, which is worse than failing.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from evald.settings import Settings, redact

BASE = {
    "database_url": "postgres://verdict:verdict@localhost:5432/verdict",
    "redis_url": "redis://localhost:6379",
    "anthropic_api_key": "sk-ant-test-abc123",
    "cost_cap_usd_per_day": 5.0,
}


def make(**overrides: object) -> Settings:
    return Settings(**{**BASE, **overrides})  # type: ignore[arg-type]


def test_accepts_minimal_valid_environment() -> None:
    s = make()
    assert s.node_env == "development"
    assert s.log_level == "info"
    assert s.evald_port == 8000


def test_coerces_numeric_strings() -> None:
    s = make(evald_port="9001", cost_cap_usd_per_day="12.5")
    assert s.evald_port == 9001
    assert s.cost_cap_usd_per_day == 12.5


def test_requires_at_least_one_provider_key() -> None:
    values = {k: v for k, v in BASE.items() if k != "anthropic_api_key"}
    with pytest.raises(ValidationError, match="at least one of"):
        Settings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("key", ["openai_api_key", "groq_api_key"])
def test_any_single_provider_key_suffices(key: str) -> None:
    values = {k: v for k, v in BASE.items() if k != "anthropic_api_key"}
    Settings(**{**values, key: "a-real-looking-key"})  # type: ignore[arg-type]


@pytest.mark.parametrize("fake", ["your-key-here", "changeme", "<paste-key>", "TODO"])
def test_rejects_placeholder_keys(fake: str) -> None:
    with pytest.raises(ValidationError, match="placeholder"):
        make(anthropic_api_key=fake)


def test_blank_key_is_treated_as_unset_not_as_present() -> None:
    values = {k: v for k, v in BASE.items() if k != "anthropic_api_key"}
    with pytest.raises(ValidationError, match="at least one of"):
        Settings(**{**values, "anthropic_api_key": "   "})  # type: ignore[arg-type]


def test_rejects_non_postgres_database_url() -> None:
    with pytest.raises(ValidationError, match="postgres"):
        make(database_url="mysql://localhost:3306/verdict")


def test_rejects_non_redis_url() -> None:
    with pytest.raises(ValidationError, match="redis"):
        make(redis_url="http://localhost:6379")


def test_accepts_managed_provider_urls() -> None:
    make(
        database_url="postgresql://user:pw@ep-x.neon.tech/verdict?sslmode=require",
        redis_url="rediss://default:token@some-host.upstash.io:6379",
    )


@pytest.mark.parametrize("port", [0, 70000, -1])
def test_rejects_out_of_range_port(port: int) -> None:
    with pytest.raises(ValidationError):
        make(evald_port=port)


@pytest.mark.parametrize("cap", [0, -1])
def test_rejects_non_positive_cost_cap(cap: float) -> None:
    with pytest.raises(ValidationError):
        make(cost_cap_usd_per_day=cap)


def test_requires_a_cost_cap() -> None:
    values = {k: v for k, v in BASE.items() if k != "cost_cap_usd_per_day"}
    with pytest.raises(ValidationError, match="cost_cap_usd_per_day"):
        Settings(**values)  # type: ignore[arg-type]


def test_rejects_invalid_log_level() -> None:
    with pytest.raises(ValidationError):
        make(log_level="verbose")


def test_redact_strips_secret_values() -> None:
    secret = "sk-ant-super-secret-9f3a2b"
    message = f"failed while calling provider with {secret}"
    cleaned = redact(message, {"anthropic_api_key": secret})
    assert secret not in cleaned
    assert "[redacted]" in cleaned


def test_redact_leaves_non_secret_fields_alone() -> None:
    message = "evald_port was 8000"
    assert redact(message, {"evald_port": "8000"}) == message
