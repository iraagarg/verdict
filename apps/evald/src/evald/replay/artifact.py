"""The versioned run artifact.

Non-negotiable #1: if a number is not in a committed artifact file, it does not
exist. This module defines the only shape those files take.

Every artifact records enough to reconstruct what produced it — git SHA, seed,
corpus hash, the exact model ladder and its prices, and the projected-vs-actual
cost comparison — because a number whose provenance cannot be stated is not
usable in an interview.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

ARTIFACT_VERSION: Final = 1


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unknown"


class ModelSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    rung: str
    calls_attempted: int
    calls_succeeded: int
    calls_failed: int
    cache_hits: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    unconfirmed_usage_calls: int
    #: Exact-match accuracy on the gradable slice. None when nothing gradable ran.
    verifier_pass_rate: float | None = None
    verifier_n: int = 0
    median_latency_ms: int | None = None


class RunArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_version: Literal[1] = 1
    run_id: str
    created_at: str
    git_sha: str
    seed: int

    corpus_sha256: str
    corpus_size: int
    corpus_by_split: dict[str, int]
    corpus_by_task: dict[str, int]

    models: list[str]
    #: Snapshot of the prices used, so a later price change cannot silently
    #: rewrite what this run cost.
    pricing_snapshot: dict[str, dict[str, float | None]]

    replicates: dict[str, int] = Field(default_factory=dict)
    status: Literal["complete", "aborted_budget", "aborted_error"] = "complete"
    abort_reason: str | None = None

    summaries: list[ModelSummary] = Field(default_factory=list)
    cost: dict[str, float] = Field(default_factory=dict)
    cache: dict[str, float] = Field(default_factory=dict)
    token_estimate_method: str = ""
    errors_by_kind: dict[str, int] = Field(default_factory=dict)

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.model_dump(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return p

    @staticmethod
    def read(path: str | Path) -> RunArtifact:
        return RunArtifact.model_validate_json(Path(path).read_text(encoding="utf-8"))


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def pricing_snapshot(models: list[str], config: Any) -> dict[str, dict[str, float | None]]:
    return {
        m: {
            "input": config[m].pricing.input,
            "output": config[m].pricing.output,
            "cache_read": config[m].pricing.cache_read,
            "cache_write": config[m].pricing.cache_write,
        }
        for m in models
    }
