"""The committed verdict artifact."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evald.stats.verdict import Verdict

VERDICT_ARTIFACT_VERSION = 1


class VerdictArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_version: Literal[1] = 1
    created_at: str
    git_sha: str
    seed: int

    corpus_sha256: str
    judge_model: str
    rubric_version: str
    reference_model: str

    baseline: str
    candidate: str

    outcome: Literal["REGRESSION", "IMPROVEMENT", "EQUIVALENT", "INCONCLUSIVE"]
    effect: float
    ci_low: float
    ci_high: float
    alpha: float
    margin: float
    n: int

    statistically_significant: bool
    minimum_detectable_effect: float
    can_demonstrate_equivalence: bool

    mcnemar_p: float
    mcnemar_method: str
    n_discordant: int
    n_concordant: int

    baseline_rate: float
    candidate_rate: float

    #: BH-adjusted q-value, present only when this verdict was one of several
    #: tests in a family (D-012). A bare p-value from a family of tests is
    #: misleading, so its absence is meaningful too.
    q_value: float | None = None
    family_size: int | None = None

    excluded: dict[str, int] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.model_dump(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return p

    @staticmethod
    def read(path: str | Path) -> VerdictArtifact:
        return VerdictArtifact.model_validate_json(Path(path).read_text(encoding="utf-8"))


def to_artifact(
    verdict: Verdict,
    *,
    created_at: str,
    git_sha: str,
    corpus_sha256: str,
    judge_model: str,
    rubric_version: str,
    reference_model: str,
    excluded: dict[str, int] | None = None,
    notes: list[str] | None = None,
    q_value: float | None = None,
    family_size: int | None = None,
) -> VerdictArtifact:
    return VerdictArtifact(
        created_at=created_at,
        git_sha=git_sha,
        seed=20260915,
        corpus_sha256=corpus_sha256,
        judge_model=judge_model,
        rubric_version=rubric_version,
        reference_model=reference_model,
        baseline=verdict.label_baseline,
        candidate=verdict.label_candidate,
        outcome=verdict.outcome,
        effect=verdict.effect,
        ci_low=verdict.ci_low,
        ci_high=verdict.ci_high,
        alpha=verdict.alpha,
        margin=verdict.margin,
        n=verdict.n,
        statistically_significant=verdict.statistically_significant,
        minimum_detectable_effect=verdict.minimum_detectable_effect,
        can_demonstrate_equivalence=verdict.can_demonstrate_equivalence,
        mcnemar_p=verdict.mcnemar_p,
        mcnemar_method=verdict.mcnemar_method,
        n_discordant=verdict.n_discordant,
        n_concordant=verdict.n_concordant,
        baseline_rate=verdict.baseline_rate,
        candidate_rate=verdict.candidate_rate,
        q_value=q_value,
        family_size=family_size,
        excluded=excluded or {},
        notes=notes or [],
    )
