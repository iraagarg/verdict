"""The committed cache-calibration artifact.

Hit rate and false-hit rate are stored in the SAME record and neither can be
read without the other. A cache that returns wrong answers quickly is worse
than no cache, so a hit rate quoted on its own is a misleading number, and the
schema makes quoting one alone awkward by design.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ThresholdPointModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold: float
    hit_rate: float
    hit_rate_ci_low: float
    hit_rate_ci_high: float
    n_duplicates: int
    false_hit_rate: float
    false_hit_ci_low: float
    false_hit_ci_high: float
    n_different: int
    acceptable: bool


class CacheCalibrationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_version: Literal[1] = 1
    created_at: str
    git_sha: str
    seed: int

    corpus_sha256: str
    embedding_model: str
    embedding_dimensions: int

    max_false_hit_rate: float
    min_hit_rate: float
    alpha: float
    #: Clopper-Pearson, not bootstrap: the false-hit rate is a rare event and
    #: the bootstrap reports a bound of exactly zero when it observes none
    #: (D-047).
    false_hit_bound_method: str = "clopper-pearson"

    n_duplicates: int
    n_different: int
    #: How many hard negatives would be needed to prove the tolerance with zero
    #: observed false hits. Below this, "0%" means "untested", not "safe".
    min_negatives_required: int

    points: list[ThresholdPointModel] = Field(default_factory=list)

    chosen_threshold: float | None = None
    chosen_hit_rate: float | None = None
    chosen_false_hit_rate: float | None = None
    chosen_false_hit_ci_high: float | None = None
    no_threshold_reason: str | None = None

    human_verified_pairs: int = 0
    notes: list[str] = Field(default_factory=list)

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.model_dump(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return p

    @staticmethod
    def read(path: str | Path) -> CacheCalibrationArtifact:
        return CacheCalibrationArtifact.model_validate_json(Path(path).read_text(encoding="utf-8"))
