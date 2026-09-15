"""Fit on TRAIN, report on HELD-OUT. Nothing else in this phase matters as much.

A threshold fitted on the data it is reported on will look excellent and mean
nothing, and the output gives no hint that it happened. So the separation is
enforced with an assertion rather than a convention, and the artifact records
which split played which role.
"""

from __future__ import annotations

from dataclasses import dataclass

from evald.router.artifact import (
    CascadeEntry,
    ParetoArtifact,
    PolicyArtifact,
    RouteEntry,
    SplitSweep,
    SweepPointModel,
)
from evald.router.cascade import CascadeConfig, all_strong_baseline
from evald.router.offline import fit_offline_policy, policy_cost
from evald.router.records import ItemRecord, assert_disjoint, select_split
from evald.router.sweep import DEFAULT_GRID, SweepResult, evaluate_threshold, sweep
from evald.stats.verdict import DEFAULT_MARGIN

#: dev fits, test reports. `calibration` is spoken for by P3's judge labels, so
#: using it here would mix the instrument's training data into the policy's.
FIT_SPLIT = "dev"
REPORT_SPLIT = "test"


@dataclass(slots=True)
class FitOutput:
    pareto: ParetoArtifact
    policy: PolicyArtifact
    fit_sweep: SweepResult
    report_sweep: SweepResult


def _to_model(point: object) -> SweepPointModel:
    return SweepPointModel.model_validate(point, from_attributes=True)


def _sweep_to_split(result: SweepResult, role: str) -> SplitSweep:
    return SplitSweep(
        split=result.split,
        role=role,  # type: ignore[arg-type]
        n=result.points[0].n if result.points else 0,
        baseline_quality=result.baseline_quality,
        baseline_mean_cost_nano=result.baseline_mean_cost_nano,
        points=[_to_model(p) for p in result.points],
    )


def fit_and_report(
    records: list[ItemRecord],
    cheap_model: str,
    strong_model: str,
    ladder: list[str],
    safe_default: str,
    *,
    created_at: str,
    git_sha: str,
    corpus_sha256: str,
    judge_model: str,
    rubric_version: str,
    reference_model: str,
    floor: float,
    margin: float = DEFAULT_MARGIN,
    alpha: float = 0.05,
    grid: tuple[float, ...] = DEFAULT_GRID,
    k_samples: int = 3,
    verifier_cost_nano: int = 0,
    iterations: int = 4000,
    seed: int = 20260915,
) -> FitOutput:
    fit_records = select_split(records, FIT_SPLIT)
    report_records = select_split(records, REPORT_SPLIT)

    if not fit_records:
        raise ValueError(f"no items in the {FIT_SPLIT!r} split to fit on")
    if not report_records:
        raise ValueError(f"no items in the {REPORT_SPLIT!r} split to report on")

    # The guard that makes the rest of this trustworthy.
    assert_disjoint(fit_records, report_records)

    common = {
        "cheap_model": cheap_model,
        "strong_model": strong_model,
        "grid": grid,
        "k_samples": k_samples,
        "verifier_cost_nano": verifier_cost_nano,
        "margin": margin,
        "alpha": alpha,
        "iterations": iterations,
        "seed": seed,
    }

    fit_sweep = sweep(fit_records, split_name=FIT_SPLIT, **common)  # type: ignore[arg-type]
    report_sweep = sweep(report_records, split_name=REPORT_SPLIT, **common)  # type: ignore[arg-type]

    chosen = fit_sweep.operating_point()

    held_out_point: SweepPointModel | None = None
    no_eligible: str | None = None
    if chosen is None:
        no_eligible = (
            f"No threshold on the {FIT_SPLIT!r} split was shown non-inferior to always using "
            f"{strong_model} at a margin of {margin}. Keeping the strong model is the correct "
            f"outcome, not a failure of the sweep."
        )
    else:
        # Re-evaluate the CHOSEN threshold on held-out data. This is the only
        # cascade number that may be quoted.
        baseline = all_strong_baseline(report_records, strong_model)
        config = CascadeConfig(
            cheap_model=cheap_model,
            strong_model=strong_model,
            k_samples=k_samples,
            verifier_cost_nano=verifier_cost_nano,
            threshold=chosen.threshold,
        )
        point, _ = evaluate_threshold(
            report_records, config, baseline, margin, alpha, iterations, seed
        )
        held_out_point = _to_model(point)

    # Offline policy: fitted on dev, reported on test, same discipline.
    assignments = fit_offline_policy(
        fit_records,
        ladder=ladder,
        safe_default=safe_default,
        floor=floor,
        alpha=alpha,
        iterations=iterations,
        seed=seed,
    )
    offline_cost, offline_quality = policy_cost(report_records, assignments)
    strong_cost = all_strong_baseline(report_records, strong_model).mean_cost_nano

    pareto = ParetoArtifact(
        created_at=created_at,
        git_sha=git_sha,
        seed=seed,
        corpus_sha256=corpus_sha256,
        judge_model=judge_model,
        rubric_version=rubric_version,
        reference_model=reference_model,
        cheap_model=cheap_model,
        strong_model=strong_model,
        k_samples=k_samples,
        verifier_cost_nano=verifier_cost_nano,
        margin=margin,
        alpha=alpha,
        fit_split=FIT_SPLIT,
        report_split=REPORT_SPLIT,
        sweeps=[_sweep_to_split(fit_sweep, "fit"), _sweep_to_split(report_sweep, "report")],
        chosen_threshold=chosen.threshold if chosen else None,
        held_out=held_out_point,
        no_eligible_threshold_reason=no_eligible,
        offline_policy=[
            {
                "route_key": a.route_key,
                "assigned_model": a.assigned_model,
                "n_items": a.n_items,
                "ci_low": a.ci_low,
                "reason": a.reason,
            }
            for a in assignments
        ],
        offline_held_out={
            "quality": round(offline_quality, 6),
            "mean_cost_nano": round(offline_cost, 4),
            "cost_ratio": round(offline_cost / strong_cost, 6) if strong_cost else 0.0,
        },
        notes=[
            "The cascade threshold was chosen on the fit split and evaluated once on the "
            "held-out split. Only `held_out` may be quoted as the cascade's result.",
            "The report-split sweep is included for transparency and played no part in selection.",
            "Cost includes the confidence check, so always-escalating costs MORE than always "
            "using the strong model.",
        ],
    )

    policy = PolicyArtifact(
        created_at=created_at,
        git_sha=git_sha,
        corpus_sha256=corpus_sha256,
        safe_default=safe_default,
        floor=floor,
        margin=margin,
        routes=[
            RouteEntry(
                route_key=a.route_key,
                assigned_model=a.assigned_model,
                n_items=a.n_items,
                quality=a.quality,
                ci_low=a.ci_low,
                ci_high=a.ci_high,
                floor=a.floor,
                reason=a.reason,
            )
            for a in assignments
        ],
        cascade=(
            CascadeEntry(
                enabled=False,  # opt-in; the gateway serves offline by default
                cheap_model=cheap_model,
                strong_model=strong_model,
                threshold=chosen.threshold,
                k_samples=k_samples,
                held_out_quality=held_out_point.quality if held_out_point else 0.0,
                held_out_cost_ratio=held_out_point.cost_ratio if held_out_point else 1.0,
                held_out_n=held_out_point.n if held_out_point else 0,
            )
            if chosen and held_out_point
            else None
        ),
    )

    return FitOutput(pareto=pareto, policy=policy, fit_sweep=fit_sweep, report_sweep=report_sweep)
