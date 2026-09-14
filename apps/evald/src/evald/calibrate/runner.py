"""Ties the pieces together: judge the labelled pairs, compare, report."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from evald.calibrate.labeler import HumanLabel
from evald.calibrate.report import (
    CalibrationReport,
    Disagreement,
    TaskAgreement,
    diagnose,
    git_sha,
    label_distribution,
    now_iso,
)
from evald.calibrate.sample import LabelPair
from evald.calibrate.stats import agreement, interpret
from evald.corpus.schema import CorpusItem
from evald.corpus.verifiers import verify
from evald.judge.judge import Judgment, PairwiseJudge
from evald.judge.store import GenerationStore, MissingGenerationError


@dataclass(slots=True)
class GroundTruthCheck:
    """Judge accuracy where an exact verifier already knows the answer.

    The test: on gradable items where exactly one of the two responses is
    correct, does the judge pick the correct one? This is the only part of the
    calibration that does not depend on one person's opinion.
    """

    n: int = 0
    correct: int = 0

    @property
    def accuracy(self) -> float | None:
        return round(self.correct / self.n, 4) if self.n else None


def judge_pairs(
    pairs: list[LabelPair],
    items: dict[str, CorpusItem],
    store: GenerationStore,
    judge: PairwiseJudge,
    seed: int,
) -> dict[str, Judgment]:
    out: dict[str, Judgment] = {}
    for pair in pairs:
        item = items[pair.slug]
        try:
            candidate = store.get(item, pair.candidate_model)
            reference = store.get(item, pair.reference_model)
        except MissingGenerationError:
            continue
        out[pair.pair_id] = judge.compare(
            item,
            candidate_text=candidate.text,
            reference_text=reference.text,
            candidate_model=pair.candidate_model,
            reference_model=pair.reference_model,
            seed=seed,
        )
    return out


def check_ground_truth(
    items: list[CorpusItem],
    candidate_models: list[str],
    reference_model: str,
    store: GenerationStore,
    judge: PairwiseJudge,
    seed: int,
    limit: int = 150,
) -> GroundTruthCheck:
    """Score the judge against exact verifiers on the gradable slice.

    Only items where exactly ONE side is correct are usable: if both are right
    or both are wrong, there is no objectively better response and a tie is a
    defensible verdict, so the item cannot discriminate.
    """
    check = GroundTruthCheck()
    for item in items:
        if check.n >= limit:
            break
        if not item.verifiable or item.verifier is None or item.ground_truth is None:
            continue
        for model in candidate_models:
            if check.n >= limit:
                break
            try:
                candidate = store.get(item, model)
                reference = store.get(item, reference_model)
            except MissingGenerationError:
                continue

            cand_ok = verify(item.verifier, candidate.text, item.ground_truth)
            ref_ok = verify(item.verifier, reference.text, item.ground_truth)
            if cand_ok == ref_ok:
                continue  # no objectively better response; not a usable test

            judgment = judge.compare(
                item,
                candidate_text=candidate.text,
                reference_text=reference.text,
                candidate_model=model,
                reference_model=reference_model,
                seed=seed,
            )
            if judgment.verdict is None:
                continue

            expected = "win" if cand_ok else "loss"
            check.n += 1
            if judgment.verdict == expected:
                check.correct += 1
    return check


def build_calibration_report(
    labels: dict[str, HumanLabel],
    judgments: dict[str, Judgment],
    pairs: list[LabelPair],
    judge_model: str,
    reference_model: str,
    rubric_version: str,
    corpus_sha: str,
    labeler: str,
    seed: int,
    ground_truth: GroundTruthCheck | None = None,
    ground_truth_caveat: str = "",
    bootstrap_iterations: int = 10_000,
) -> CalibrationReport:
    by_pair = {p.pair_id: p for p in pairs}

    human_labels: list[str] = []
    judge_labels: list[str] = []
    tasks: list[str] = []
    disagreements: list[Disagreement] = []
    unparseable = 0
    missing = 0

    for pair_id, label in sorted(labels.items()):
        judgment = judgments.get(pair_id)
        if judgment is None:
            missing += 1
            continue
        if judgment.unparseable or judgment.verdict is None:
            unparseable += 1
            continue

        human_labels.append(label.verdict)
        judge_labels.append(judgment.verdict)
        tasks.append(label.task_type)

        if label.verdict != judgment.verdict:
            first = judgment.positions[0] if judgment.positions else None
            disagreements.append(
                Disagreement(
                    pair_id=pair_id,
                    slug=label.slug,
                    task_type=label.task_type,
                    candidate_model=label.candidate_model,
                    human=label.verdict,
                    judge=judgment.verdict,
                    judge_confidence=judgment.confidence,
                    judge_reasoning=(first.reply.reasoning if first and first.reply else ""),
                    human_elapsed_ms=label.elapsed_ms,
                    position_flip=judgment.position_flip,
                )
            )

    result = agreement(human_labels, judge_labels, iterations=bootstrap_iterations, seed=seed)

    by_task: list[TaskAgreement] = []
    for task in sorted(set(tasks)):
        idx = [i for i, t in enumerate(tasks) if t == task]
        if len(idx) < 5:
            continue  # too few to report an interval that means anything
        sub = agreement(
            [human_labels[i] for i in idx],
            [judge_labels[i] for i in idx],
            iterations=max(1000, bootstrap_iterations // 5),
            seed=seed,
        )
        by_task.append(
            TaskAgreement(
                task_type=task,
                n=sub.n,
                raw_agreement=sub.raw_agreement,
                kappa=sub.kappa,
                kappa_ci_low=sub.kappa_ci_low,
                kappa_ci_high=sub.kappa_ci_high,
            )
        )

    judged = [j for j in judgments.values() if j.verdict is not None]
    flip_rate = round(sum(1 for j in judged if j.position_flip) / len(judged), 4) if judged else 0.0

    shown = Counter(by_pair[pid].candidate_shown_as for pid in labels if pid in by_pair)
    elapsed = [lbl.elapsed_ms for lbl in labels.values() if lbl.elapsed_ms > 0]

    human_dist = label_distribution(human_labels)
    judge_dist = label_distribution(judge_labels)

    return CalibrationReport(
        created_at=now_iso(),
        git_sha=git_sha(),
        seed=seed,
        judge_model=judge_model,
        reference_model=reference_model,
        rubric_version=rubric_version,
        labeler=labeler,
        corpus_sha256=corpus_sha,
        n_labelled=len(labels),
        n_compared=result.n,
        n_unparseable=unparseable,
        n_skipped_missing_judgment=missing,
        raw_agreement=result.raw_agreement,
        kappa=result.kappa,
        kappa_ci_low=result.kappa_ci_low,
        kappa_ci_high=result.kappa_ci_high,
        kappa_interpretation=interpret(result.kappa),
        clears_gate=result.clears_gate,
        confusion=result.confusion,
        per_class_recall=result.per_class_recall,
        by_task=by_task,
        judge_position_flip_rate=flip_rate,
        human_position_balance={"A": shown.get("A", 0), "B": shown.get("B", 0)},
        human_label_distribution=human_dist,
        judge_label_distribution=judge_dist,
        median_human_elapsed_ms=int(median(elapsed)) if elapsed else 0,
        judge_vs_ground_truth_accuracy=ground_truth.accuracy if ground_truth else None,
        judge_vs_ground_truth_n=ground_truth.n if ground_truth else 0,
        ground_truth_caveat=ground_truth_caveat,
        disagreements=sorted(disagreements, key=lambda d: d.pair_id),
        diagnosis=diagnose(result, disagreements, flip_rate, human_dist, judge_dist),
    )


def write_report(report: CalibrationReport, artifacts: Path) -> Path:
    return report.write(artifacts / "calibration_report.json")
