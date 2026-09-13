"""Environment validation for evald.

Non-negotiable #6: the process must refuse to start with a missing or malformed
key, and must never fail later at request time. `Settings` is constructed once
at import of `main`, so an invalid environment raises before uvicorn binds.

Mirrors apps/gateway/src/env.ts. The two schemas are deliberately separate --
evald does not need provider keys for P0 and the gateway does not need a
judge budget -- but the shared variables must agree on their validation rules.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["fatal", "error", "warn", "info", "debug", "trace"]

_PLACEHOLDER = re.compile(r"^(your|changeme|placeholder|xxx|todo|<.*>)", re.IGNORECASE)

#: Variables whose values must never appear in an error message or a log line.
SECRET_FIELDS = frozenset(
    {
        "anthropic_api_key",
        "openai_api_key",
        "groq_api_key",
        "database_url",
        "redis_url",
    }
)


class Settings(BaseSettings):
    """Validated environment. Construction failure is a startup failure."""

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",  # the gateway's variables share the process environment
    )

    node_env: Literal["development", "test", "production"] = "development"
    log_level: LogLevel = "info"

    evald_port: int = Field(default=8000, ge=1, le=65535)
    evald_host: str = Field(default="0.0.0.0", min_length=1)

    database_url: str
    redis_url: str

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    groq_api_key: str | None = None

    cost_cap_usd_per_day: float = Field(gt=0)

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, v: str) -> str:
        if not v.startswith(("postgres://", "postgresql://")):
            raise ValueError("must be a postgres:// or postgresql:// URL")
        return v

    @field_validator("redis_url")
    @classmethod
    def _check_redis_url(cls, v: str) -> str:
        if not v.startswith(("redis://", "rediss://")):
            raise ValueError("must be a redis:// or rediss:// URL")
        return v

    @field_validator("anthropic_api_key", "openai_api_key", "groq_api_key")
    @classmethod
    def _reject_placeholder_keys(cls, v: str | None) -> str | None:
        """A present-but-fake key passes a presence check then fails at request time."""
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            return None
        if _PLACEHOLDER.match(stripped):
            raise ValueError("looks like a placeholder rather than a real key")
        return stripped

    @model_validator(mode="after")
    def _require_a_provider_key(self) -> Settings:
        if not any((self.anthropic_api_key, self.openai_api_key, self.groq_api_key)):
            raise ValueError(
                "at least one of ANTHROPIC_API_KEY, OPENAI_API_KEY, GROQ_API_KEY must be set -- "
                "evald cannot replay or judge without provider credentials"
            )
        return self


def redact(message: str, settings_values: dict[str, object]) -> str:
    """Strip any secret value out of a message before it is logged or raised."""
    for name, value in settings_values.items():
        if name in SECRET_FIELDS and isinstance(value, str) and value:
            message = message.replace(value, "[redacted]")
    return message
