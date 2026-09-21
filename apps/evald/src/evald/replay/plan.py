"""Pre-run cost projection.

Nothing may spend money before this has been printed and accepted. The
projection is an ESTIMATE and says so everywhere it is reported: input tokens
come from a character heuristic, and output tokens from a per-task assumption
until a real run has been measured.

After a run, `compare_projection` puts projected next to actual in the artifact.
That is what turns the estimate into something that gets better instead of
something that is quietly wrong forever.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evald.corpus.schema import CorpusItem
from evald.cost import Usage, cost_nano, nano_to_usd
from evald.models_config import ModelConfig

#: ~4 characters per token. A widely used rough heuristic, NOT a tokenizer.
#: Anthropic's tokenizer is not public and tiktoken is wrong for Claude, so an
#: exact pre-run count is not available for the whole ladder (D-017).
CHARS_PER_TOKEN = 4.0

#: Assumed output length per task type, in tokens.
#:
#: The gradable figures are MEASURED: a 175-call pilot across gpt-oss-20b,
#: Haiku 4.5 and Sonnet 5 on an even maths/multiple-choice split averaged ~650
#: output tokens. The original assumptions (300 and 150) under-projected the
#: pilot's cost by 2.1x, because reasoning models spend heavily on chains of
#: thought even for a multiple-choice answer.
#:
#: The free-form figures are still assumptions and are marked as such — no
#: free-form replay has run yet.
DEFAULT_OUTPUT_TOKENS: dict[str, int] = {
    "math_word_problem": 650,  # measured, 2026-09-21 pilot
    "multiple_choice": 650,  # measured, 2026-09-21 pilot
    "summarization": 220,  # assumed
    "long_form_qa": 420,  # assumed
    "support_reply": 260,  # assumed
}

#: Which task types above rest on measurement rather than assumption.
MEASURED_TASKS: frozenset[str] = frozenset({"math_word_problem", "multiple_choice"})

TOKEN_ESTIMATE_METHOD = (
    f"input: chars/{CHARS_PER_TOKEN:g} heuristic. "
    f"output: measured medians for {sorted(MEASURED_TASKS)}, assumptions elsewhere"
)


def estimate_input_tokens(item: CorpusItem) -> int:
    return max(1, int(len(item.prompt_text()) / CHARS_PER_TOKEN))


@dataclass(slots=True)
class ModelProjection:
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass(slots=True)
class RunProjection:
    models: list[ModelProjection] = field(default_factory=list)
    cached_calls: int = 0
    method: str = TOKEN_ESTIMATE_METHOD

    @property
    def total_calls(self) -> int:
        return sum(m.calls for m in self.models)

    @property
    def total_cost_usd(self) -> float:
        return round(sum(m.cost_usd for m in self.models), 6)

    def render(self, cap_usd: float | None = None) -> str:
        lines = [
            "PROJECTED COST  (estimate, not a measured number)",
            f"  method: {self.method}",
            "",
            f"  {'model':<24}{'calls':>8}{'in tok':>12}{'out tok':>12}{'USD':>12}",
            f"  {'-' * 68}",
        ]
        for m in sorted(self.models, key=lambda x: -x.cost_usd):
            lines.append(
                f"  {m.model:<24}{m.calls:>8}{m.input_tokens:>12,}"
                f"{m.output_tokens:>12,}{m.cost_usd:>12.4f}"
            )
        lines += [
            f"  {'-' * 68}",
            f"  {'TOTAL':<24}{self.total_calls:>8}"
            f"{sum(m.input_tokens for m in self.models):>12,}"
            f"{sum(m.output_tokens for m in self.models):>12,}"
            f"{self.total_cost_usd:>12.4f}",
        ]
        if self.cached_calls:
            lines.append(f"  {self.cached_calls} call(s) already cached and therefore free.")
        if cap_usd is not None:
            verdict = "WITHIN CAP" if self.total_cost_usd <= cap_usd else "EXCEEDS CAP"
            lines.append(f"  spend cap: ${cap_usd:.2f}  ->  {verdict}")
        return "\n".join(lines)


def project_run(
    items: list[CorpusItem],
    models: list[str],
    config: ModelConfig,
    replicates: dict[str, int] | None = None,
    already_cached: int = 0,
    output_tokens: dict[str, int] | None = None,
) -> RunProjection:
    """Estimate the cost of replaying `items` across `models`.

    `replicates` maps a slug to how many samples it needs; absent means 1. That
    is how the K=3-on-a-subset decision is priced without pricing it everywhere.
    """
    reps = replicates or {}
    out_tokens = output_tokens or DEFAULT_OUTPUT_TOKENS
    projection = RunProjection(cached_calls=already_cached)

    for model_id in models:
        entry = config[model_id]
        calls = 0
        total_in = 0
        total_out = 0

        for item in items:
            k = reps.get(item.slug, 1)
            per_call_in = estimate_input_tokens(item)
            per_call_out = min(
                out_tokens.get(item.task_type, 300),
                entry.max_output_tokens,
            )
            calls += k
            total_in += per_call_in * k
            total_out += per_call_out * k

        nano = cost_nano(model_id, entry, Usage(input_tokens=total_in, output_tokens=total_out))
        projection.models.append(
            ModelProjection(
                model=model_id,
                calls=calls,
                input_tokens=total_in,
                output_tokens=total_out,
                cost_usd=nano_to_usd(nano),
            )
        )

    return projection


def compare_projection(projected_usd: float, actual_usd: float) -> dict[str, float]:
    """Projected vs actual, recorded on every artifact so the estimate improves."""
    ratio = 0.0 if projected_usd == 0 else round(actual_usd / projected_usd, 4)
    return {
        "projected_usd": round(projected_usd, 6),
        "actual_usd": round(actual_usd, 6),
        "actual_over_projected": ratio,
    }


def calibrate_from_run(
    observed: list[tuple[str, int]],
) -> dict[str, int]:
    """Median observed output tokens per task type, to replace the assumptions."""
    from statistics import median

    by_task: dict[str, list[int]] = {}
    for task, tokens in observed:
        by_task.setdefault(task, []).append(tokens)
    return {task: int(median(v)) for task, v in sorted(by_task.items())}
