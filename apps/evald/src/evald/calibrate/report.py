"""calibration_report.json — the artifact that decides whether the judge is usable.

The gate is kappa >= 0.6 on the LOWER bound of the confidence interval. When it
fails, this module does not soften the number: it produces the disagreement
cases and a diagnosis, because "the judge scored 0.48 and here is exactly where
it disagrees with me" is a result, whereas a quietly rounded-up figure is not.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from evald.calibrate.stats import CLASSES, AgreementResult

REPORT_VERSION = 1
KAPPA_GATE = 0.6


class Disagreement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair_id: str
    slug: str
    task_type: str
    candidate_model: str
    human: str
    judge: str
    judge_confidence: float | None = None
    judge_reasoning: str = ""
    human_elapsed_ms: int = 0
    position_flip: bool = False


class TaskAgreement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: str
    n: int
    raw_agreement: float
    kappa: float
    kappa_ci_low: float
    kappa_ci_high: float


class CalibrationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_version: Literal[1] = 1
    created_at: str
    git_sha: str
    seed: int

    judge_model: str
    reference_model: str
    rubric_version: str
    labeler: str

    corpus_sha256: str
    n_labelled: int
    n_compared: int
    n_unparseable: int
    n_skipped_missing_judgment: int

    # ── headline ────────────────────────────────────────────────────────────
    raw_agreement: float
    kappa: float
    kappa_ci_low: float
    kappa_ci_high: float
    kappa_interpretation: str
    gate: float = KAPPA_GATE
    clears_gate: bool

    confusion: dict[str, dict[str, int]]
    per_class_recall: dict[str, float]
    by_task: list[TaskAgreement] = Field(default_factory=list)

    # ── bias diagnostics ────────────────────────────────────────────────────
    judge_position_flip_rate: float
    human_position_balance: dict[str, int] = Field(default_factory=dict)
    human_label_distribution: dict[str, int] = Field(default_factory=dict)
    judge_label_distribution: dict[str, int] = Field(default_factory=dict)
    median_human_elapsed_ms: int = 0

    # ── ground truth, where it exists ───────────────────────────────────────
    #: The judge's accuracy against an exact verifier on the gradable slice.
    #: None when the gradable slice was not judged.
    judge_vs_ground_truth_accuracy: float | None = None
    judge_vs_ground_truth_n: int = 0
    ground_truth_caveat: str = ""

    disagreements: list[Disagreement] = Field(default_factory=list)
    diagnosis: list[str] = Field(default_factory=list)

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.model_dump(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return p

    @staticmethod
    def read(path: str | Path) -> CalibrationReport:
        return CalibrationReport.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def summary(self) -> str:
        verdict = "PASS" if self.clears_gate else "FAIL"
        lines = [
            "CALIBRATION REPORT",
            f"  judge            {self.judge_model}  (rubric {self.rubric_version})",
            f"  labelled         {self.n_labelled}, compared {self.n_compared}",
            "",
            f"  Cohen's kappa    {self.kappa:.4f}  "
            f"[{self.kappa_ci_low:.4f}, {self.kappa_ci_high:.4f}]"
            f"  ({self.kappa_interpretation})",
            f"  raw agreement    {self.raw_agreement:.4f}",
            f"  gate (CI low >= {self.gate})   {verdict}",
            "",
            f"  judge position flip rate   {self.judge_position_flip_rate:.4f}",
        ]
        if self.judge_vs_ground_truth_accuracy is not None:
            lines.append(
                f"  judge vs ground truth      {self.judge_vs_ground_truth_accuracy:.4f} "
                f"(n={self.judge_vs_ground_truth_n})"
            )
        if self.diagnosis:
            lines += ["", "  DIAGNOSIS:"]
            lines += [f"    - {d}" for d in self.diagnosis]
        return "\n".join(lines)


def diagnose(
    result: AgreementResult,
    disagreements: list[Disagreement],
    flip_rate: float,
    human_dist: dict[str, int],
    judge_dist: dict[str, int],
) -> list[str]:
    """Name the specific failure modes present, rather than reporting one number.

    Each finding maps to a concrete rubric change, because "kappa is low" is not
    actionable and "the judge calls 3x more ties than you do, so tighten the tie
    definition" is.
    """
    findings: list[str] = []
    n = max(1, result.n)

    if result.clears_gate:
        findings.append(
            f"Judge clears the gate: kappa CI lower bound "
            f"{result.kappa_ci_low:.3f} >= {KAPPA_GATE}."
        )
    else:
        findings.append(
            f"Judge does NOT clear the gate: kappa CI is "
            f"[{result.kappa_ci_low:.3f}, {result.kappa_ci_high:.3f}], "
            f"lower bound below {KAPPA_GATE}. Revise the rubric, bump RUBRIC_VERSION, "
            f"and re-label a fresh sample — reusing these labels would overfit the "
            f"rubric to them."
        )

    # Tie calibration is the most common and most fixable disagreement.
    human_ties = human_dist.get("tie", 0)
    judge_ties = judge_dist.get("tie", 0)
    if judge_ties > 2 * max(1, human_ties):
        findings.append(
            f"Judge is tie-happy: {judge_ties} ties vs your {human_ties}. It is declining to "
            f"call differences you can see. Tighten the tie clause to require near-identical "
            f"quality on the top-ranked criterion."
        )
    elif human_ties > 2 * max(1, judge_ties):
        findings.append(
            f"Judge is over-deciding: {judge_ties} ties vs your {human_ties}. It is "
            f"inventing differences. Broaden the tie clause and state explicitly that "
            f"small differences do not count."
        )

    if flip_rate > 0.2:
        findings.append(
            f"Position flip rate is {flip_rate:.1%}. The judge's answer depends on ORDER for "
            f"one comparison in five, which means the rubric is not discriminating and it is "
            f"falling back on presentation order."
        )

    # Directional bias: systematically favouring one side is a rubric problem.
    toward_win = sum(1 for d in disagreements if d.judge == "win" and d.human != "win")
    toward_loss = sum(1 for d in disagreements if d.judge == "loss" and d.human != "loss")
    if toward_win > 2 * max(1, toward_loss):
        findings.append(
            f"Judge favours the candidate in {toward_win} disagreements vs {toward_loss} the "
            f"other way. It is being too generous to the cheaper model — the dangerous "
            f"direction, since it would push toward wrongly demoting a route."
        )
    elif toward_loss > 2 * max(1, toward_win):
        findings.append(
            f"Judge favours the reference in {toward_loss} disagreements vs {toward_win} the "
            f"other way. Possible self-preference (the reference is an Anthropic model and so "
            f"is the judge). This biases toward keeping the expensive model, which is the safe "
            f"direction, but it still corrupts the absolute quality floor."
        )

    worst = sorted(result.per_class_recall.items(), key=lambda kv: kv[1])
    if worst and worst[0][1] < 0.5:
        findings.append(
            f"Weakest class is {worst[0][0]!r} at {worst[0][1]:.1%} recall. Most disagreement is "
            f"concentrated there; look at those cases first."
        )

    by_task = Counter(d.task_type for d in disagreements)
    if by_task:
        task, count = by_task.most_common(1)[0]
        if count > len(disagreements) * 0.5:
            findings.append(
                f"{count} of {len(disagreements)} disagreements are on {task!r}. That task's "
                f"criteria block is the one to revise, not the shared skeleton."
            )

    fast = sum(1 for d in disagreements if 0 < d.human_elapsed_ms < 5_000)
    if fast > n * 0.15:
        findings.append(
            f"{fast} disagreements were labelled in under 5 seconds. Some may be your own "
            f"error rather than the judge's — worth re-checking those before blaming the rubric."
        )

    return findings


def label_distribution(labels: list[str]) -> dict[str, int]:
    counts = Counter(labels)
    return {c: counts.get(c, 0) for c in CLASSES}


def git_sha() -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=5
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


def build_report(**kwargs: Any) -> CalibrationReport:
    return CalibrationReport.model_validate(kwargs)
