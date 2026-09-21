"""Model ladder and pricing, loaded from config/models.yaml.

A Pydantic mirror of packages/shared/src/config/models.ts. The two services MUST
agree: a price the gateway accepts and evald rejects (or worse, reads
differently) would mean the cost on a trace and the cost in an artifact disagree
with no way to tell which is right.

The invariants enforced here are the same three:
  1. Every price carries `verified_at` and `source`, or the load fails.
  2. `None` means "not verified" and is NOT zero.
  3. Every price is an exact multiple of $0.001/MTok, so cost arithmetic can be
     integer nano-USD and never drift.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Rung = Literal["cheap", "mid", "strong"]
Provider = Literal["anthropic", "openai", "groq"]

#: Current Claude model IDs carry no date suffix.
_DATE_SUFFIX = re.compile(r"-\d{8}$")


class ModelConfigError(ValueError):
    """Raised when config/models.yaml is missing, malformed, or unverifiable."""


class Pricing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_read: float | None = Field(default=None, ge=0)
    cache_write: float | None = Field(default=None, ge=0)
    verified_at: str
    source: str

    @field_validator("verified_at")
    @classmethod
    def _iso_date(cls, v: str) -> str:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise ValueError("verified_at must be YYYY-MM-DD")
        return v

    @field_validator("source")
    @classmethod
    def _is_url(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("source must be an https URL to official pricing documentation")
        return v

    @field_validator("input", "output", "cache_read", "cache_write")
    @classmethod
    def _exact_milli(cls, v: float | None) -> float | None:
        if v is None:
            return None
        if abs(v * 1000 - round(v * 1000)) > 1e-9:
            raise ValueError(
                "must be an exact multiple of 0.001 USD/MTok so cost can be computed "
                "in integer nano-USD"
            )
        return v


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supports_sampling_params: bool
    supports_effort: bool
    thinking_mode: Literal["adaptive", "budget_tokens", "none"]
    supports_prefill: bool


class ModelEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Provider
    rung: Rung
    context_window: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    pricing: Pricing
    capabilities: Capabilities


class Embedding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None
    dimensions: int | None
    provider: Provider | None = None
    pricing: Pricing | None = None


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    embedding: Embedding
    safe_default: str
    reference_model: str
    models: dict[str, ModelEntry]

    @model_validator(mode="after")
    def _check_references(self) -> ModelConfig:
        if not self.models:
            raise ValueError("models must not be empty")

        for model_id, entry in self.models.items():
            if entry.provider == "anthropic" and _DATE_SUFFIX.search(model_id):
                raise ValueError(
                    f'"{model_id}" has a date suffix. Current Claude model IDs do not — '
                    f'use "{_DATE_SUFFIX.sub("", model_id)}".'
                )

        for field in ("safe_default", "reference_model"):
            model_id = getattr(self, field)
            if model_id not in self.models:
                raise ValueError(f'{field} "{model_id}" is not defined in models')

        # The router fails toward quality, so the fallback must be a strong rung.
        if self.models[self.safe_default].rung != "strong":
            raise ValueError(
                f'safe_default must be a "strong" rung '
                f"(got {self.models[self.safe_default].rung!r})"
            )

        if (self.embedding.model is None) != (self.embedding.dimensions is None):
            raise ValueError(
                "embedding.model and embedding.dimensions must both be set or both null"
            )

        return self

    def rung_of(self, model_id: str) -> Rung:
        return self[model_id].rung

    def __getitem__(self, model_id: str) -> ModelEntry:
        try:
            return self.models[model_id]
        except KeyError as err:
            known = ", ".join(sorted(self.models))
            raise ModelConfigError(
                f'unknown model "{model_id}"; configured models are: {known}'
            ) from err

    def by_cost(self) -> list[str]:
        """Model ids ordered cheapest-input-first. The order the router walks."""
        return sorted(self.models, key=lambda m: (self.models[m].pricing.input, m))


def load_model_config(path: str | Path) -> ModelConfig:
    """Read, parse and validate the ladder. Raises ModelConfigError on any problem."""
    p = Path(path)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise ModelConfigError(f"cannot read models.yaml at {p}") from err
    except yaml.YAMLError as err:
        raise ModelConfigError(f"models.yaml is not valid YAML: {err}") from err

    try:
        return ModelConfig.model_validate(raw)
    except Exception as err:
        raise ModelConfigError(f"models.yaml failed validation:\n{err}") from err
