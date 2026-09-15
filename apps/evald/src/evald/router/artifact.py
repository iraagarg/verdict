"""The committed routing artifacts: the Pareto curve and the fitted policy.

Two files, deliberately separate:

  * `pareto.json` is the MEASUREMENT — every threshold, on both splits, with the
    fit/report separation recorded explicitly so a reader can check it rather
    than take it on trust.
  * `policy.json` is the DECISION — what the gateway actually loads and serves.

Keeping them apart means the gateway never reads a file full of numbers it does
not need, and a policy can be inspected without wading through a sweep.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

POLICY_VERSION = 1


class SweepPointModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold: float
    n: int
    quality: float
    mean_cost_nano: float
    escalation_rate: float
    cost_ratio: float
    verdict: str
    effect: float
    ci_low: float
    ci_high: float
    mcnemar_p: float
    eligible: bool


class SplitSweep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    split: str
    #: "fit" or "report". A reader must be able to tell at a glance which numbers
    #: chose the operating point and which merely describe it.
    role: Literal["fit", "report"]
    n: int
    baseline_quality: float
    baseline_mean_cost_nano: float
    points: list[SweepPointModel] = Field(default_factory=list)


class ParetoArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_version: Literal[1] = 1
    created_at: str
    git_sha: str
    seed: int

    corpus_sha256: str
    judge_model: str
    rubric_version: str
    reference_model: str

    cheap_model: str
    strong_model: str
    k_samples: int
    verifier_cost_nano: int
    margin: float
    alpha: float

    fit_split: str
    report_split: str
    sweeps: list[SplitSweep] = Field(default_factory=list)

    #: The threshold chosen on the FIT split.
    chosen_threshold: float | None = None
    #: How that threshold performed on the HELD-OUT split. This is the only
    #: number that may be quoted as the cascade's result.
    held_out: SweepPointModel | None = None
    #: Present when the fit selected nothing, which is a real outcome.
    no_eligible_threshold_reason: str | None = None

    offline_policy: list[dict[str, object]] = Field(default_factory=list)
    offline_held_out: dict[str, float] = Field(default_factory=dict)

    notes: list[str] = Field(default_factory=list)

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.model_dump(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return p

    @staticmethod
    def read(path: str | Path) -> ParetoArtifact:
        return ParetoArtifact.model_validate_json(Path(path).read_text(encoding="utf-8"))


class RouteEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_key: str
    assigned_model: str
    n_items: int
    quality: float
    ci_low: float
    ci_high: float
    floor: float
    reason: str


class CascadeEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    cheap_model: str
    strong_model: str
    threshold: float
    k_samples: int
    #: Held-out quality and cost ratio of this configuration.
    held_out_quality: float
    held_out_cost_ratio: float
    held_out_n: int


class PolicyArtifact(BaseModel):
    """What the gateway loads. Kept small and boring on purpose."""

    model_config = ConfigDict(extra="forbid")

    policy_version: Literal[1] = 1
    created_at: str
    git_sha: str
    corpus_sha256: str

    #: Served when no route matches, when evidence is insufficient, or when
    #: anything at all is ambiguous. Always a strong rung (DESIGN.md §8.2).
    safe_default: str
    floor: float
    margin: float

    routes: list[RouteEntry] = Field(default_factory=list)
    cascade: CascadeEntry | None = None

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.model_dump(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return p

    @staticmethod
    def read(path: str | Path) -> PolicyArtifact:
        return PolicyArtifact.model_validate_json(Path(path).read_text(encoding="utf-8"))
