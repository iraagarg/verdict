import { EmptyState, IntervalBar, Panel, Stat, VerdictBadge } from "../../components/ui";
import { loadVerdicts, pct, signedPct, type VerdictArtifact } from "../../lib/artifacts";

export const metadata = { title: "Compare · Verdict" };

/**
 * The plain-English reading of a verdict.
 *
 * The four outcomes exist because "no significant difference" hides two
 * opposite findings, and that distinction is worth nothing if the page shows a
 * badge and leaves the reader to infer what it means.
 */
function explain(v: VerdictArtifact): string {
  switch (v.outcome) {
    case "REGRESSION":
      return `The entire interval is worse than −${pct(v.margin, 1)}, so ${v.candidate} is worse by an amount that matters.`;
    case "IMPROVEMENT":
      return `The entire interval is better than +${pct(v.margin, 1)}, so ${v.candidate} is better by an amount that matters.`;
    case "EQUIVALENT":
      return `The entire interval fits inside ±${pct(v.margin, 1)}. This is evidence the two are close enough — not merely a failure to find a difference.`;
    default:
      return `The interval straddles a decision boundary, so this run cannot tell a real difference from noise. It needs more items, not a different conclusion. This is NOT "no difference".`;
  }
}

function VerdictCard({ v }: { v: VerdictArtifact }) {
  return (
    <Panel
      title={`${v.baseline} → ${v.candidate}`}
      subtitle={`Recorded ${v.created_at.slice(0, 10)} · git ${v.git_sha.slice(0, 7)}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <VerdictBadge outcome={v.outcome} />
        {v.statistically_significant ? (
          <span className="rounded bg-neutral-100 px-2 py-0.5 text-xs text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
            statistically significant
          </span>
        ) : null}
        {!v.can_demonstrate_equivalence ? (
          <span className="rounded bg-neutral-100 px-2 py-0.5 text-xs text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
            too wide to show equivalence
          </span>
        ) : null}
      </div>

      <p className="mt-3 text-pretty text-sm leading-relaxed text-neutral-700 dark:text-neutral-300">
        {explain(v)}
      </p>

      <div className="mt-4">
        <IntervalBar low={v.ci_low} high={v.ci_high} margin={v.margin} effect={v.effect} />
        <div className="mt-1 flex justify-between font-mono text-[11px] text-neutral-500">
          <span>{signedPct(v.ci_low)}</span>
          <span className="text-neutral-400">shaded band = ±{pct(v.margin, 1)} margin</span>
          <span>{signedPct(v.ci_high)}</span>
        </div>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Effect" value={`${(v.effect * 100).toFixed(2)}pp`} />
        <Stat label="Rates" value={`${pct(v.baseline_rate)} → ${pct(v.candidate_rate)}`} />
        <Stat label="n" value={String(v.n)} hint={`${v.n_discordant} discordant`} />
        <Stat
          label="Resolution"
          value={`±${(v.minimum_detectable_effect * 100).toFixed(1)}pp`}
          hint="smallest detectable"
        />
        <Stat label="McNemar p" value={v.mcnemar_p.toFixed(6)} hint={v.mcnemar_method} />
        <Stat label="Margin" value={`±${pct(v.margin, 1)}`} />
        <Stat label="Concordant" value={String(v.n_concordant)} />
        <Stat label="Discordant" value={String(v.n_discordant)} />
      </dl>

      {v.notes.length > 0 ? (
        <ul className="mt-4 space-y-1 border-t border-neutral-200 pt-3 text-xs text-neutral-500 dark:border-neutral-800">
          {v.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}

export default function ComparePage() {
  const verdicts = loadVerdicts();

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Run comparison</h1>
        <p className="mt-2 max-w-2xl text-pretty text-sm leading-relaxed text-neutral-600 dark:text-neutral-400">
          Four verdicts, not three. <strong>Equivalent</strong> means the difference was measured
          precisely and is small. <strong>Inconclusive</strong> means it was not measured precisely
          enough to say. Reporting both as &ldquo;no significant difference&rdquo; is how an
          underpowered run gets mistaken for a pass.
        </p>
      </div>

      {verdicts.length === 0 ? (
        <Panel title="No comparisons yet">
          <EmptyState
            what="No verdict artifacts found."
            why="A verdict compares two models on the gradable slice using cached generations. It needs both models replayed over the same items."
            command="cd apps/evald && .venv/bin/python -m evald.cli verdict --model-a A --model-b B"
          />
        </Panel>
      ) : (
        verdicts.map((v) => <VerdictCard key={`${v.baseline}-${v.candidate}`} v={v} />)
      )}
    </div>
  );
}
