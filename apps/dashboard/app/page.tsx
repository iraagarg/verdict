import Link from "next/link";
import { Panel, Stat, VerdictBadge } from "../components/ui";
import { loadCache, loadPilot, loadTraces, loadVerdicts, pct, usd } from "../lib/artifacts";

export default function Home() {
  const verdicts = loadVerdicts();
  const pilot = loadPilot();
  const cache = loadCache();
  const traces = loadTraces();

  const spend = traces?.traces.reduce((sum, t) => sum + t.cost_usd, 0) ?? 0;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Verdict</h1>
        <p className="mt-2 max-w-2xl text-pretty text-sm leading-relaxed text-neutral-600 dark:text-neutral-400">
          An OpenAI-compatible gateway that routes traffic to the cheapest model which provably
          clears a statistical quality floor. This dashboard shows only measurements that exist.
        </p>
      </div>

      <Panel
        id="results"
        title="Measured results"
        subtitle="Each of these is read from a committed artifact, with the sample size that produced it."
      >
        {verdicts.length === 0 ? (
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            No verdicts recorded yet.
          </p>
        ) : (
          <ul className="space-y-3">
            {verdicts.map((v) => (
              <li
                key={`${v.baseline}-${v.candidate}`}
                className="rounded-lg border border-neutral-200 p-3 dark:border-neutral-800"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <VerdictBadge outcome={v.outcome} />
                  <code className="break-all font-mono text-xs text-neutral-600 dark:text-neutral-400">
                    {v.baseline} → {v.candidate}
                  </code>
                </div>
                <dl className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <Stat label="Effect" value={`${(v.effect * 100).toFixed(2)}pp`} />
                  <Stat
                    label="95% CI"
                    value={`${(v.ci_low * 100).toFixed(1)} … ${(v.ci_high * 100).toFixed(1)}`}
                  />
                  <Stat label="n" value={String(v.n)} />
                  <Stat label="McNemar p" value={v.mcnemar_p.toFixed(4)} />
                </dl>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-xs text-neutral-500">
          <Link href="/compare" className="underline underline-offset-2">
            See the full comparison →
          </Link>
        </p>
      </Panel>

      <div className="grid gap-5 sm:grid-cols-2">
        <Panel title="Model pass rates" subtitle="Gradable slice, exact-match verifiers.">
          {pilot === null ? (
            <p className="text-sm text-neutral-600 dark:text-neutral-400">No pilot recorded.</p>
          ) : (
            <dl className="space-y-2">
              {Object.entries(pilot.pass_rate_by_model)
                .sort(([, a], [, b]) => b - a)
                .map(([model, rate]) => (
                  <div key={model} className="flex items-baseline justify-between gap-3">
                    <dt className="break-all font-mono text-xs text-neutral-700 dark:text-neutral-300">
                      {model}
                    </dt>
                    <dd className="font-mono text-sm tabular-nums">{pct(rate)}</dd>
                  </div>
                ))}
              <p className="pt-2 text-xs text-neutral-500">
                n = {pilot.gradable_piloted} items per model.
              </p>
            </dl>
          )}
        </Panel>

        <Panel title="Semantic cache" subtitle="Calibrated, and the answer was not to deploy it.">
          {cache === null ? (
            <p className="text-sm text-neutral-600 dark:text-neutral-400">Not calibrated.</p>
          ) : (
            <dl className="space-y-2">
              <Stat
                label="Chosen threshold"
                value={
                  cache.chosen_threshold === null ? "none — unsafe" : String(cache.chosen_threshold)
                }
                hint={
                  cache.chosen_threshold === null
                    ? `No setting keeps false hits provably under ${pct(cache.max_false_hit_rate, 0)}.`
                    : undefined
                }
              />
              <Stat
                label="Pairs"
                value={`${cache.n_duplicates} dup / ${cache.n_different} hard neg`}
              />
              <p className="pt-1 text-xs text-neutral-500">
                <Link href="/pareto#cache" className="underline underline-offset-2">
                  What a useful cache would cost →
                </Link>
              </p>
            </dl>
          )}
        </Panel>
      </div>

      <Panel title="Traffic" subtitle={traces?.note}>
        {traces === null ? (
          <p className="text-sm text-neutral-600 dark:text-neutral-400">No traces exported.</p>
        ) : (
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Sampled" value={String(traces.sampled)} />
            <Stat label="In database" value={String(traces.total_traces_in_db)} />
            <Stat label="Sampled spend" value={usd(spend)} />
            <Stat
              label="Models"
              value={String(new Set(traces.traces.map((t) => t.model_served)).size)}
            />
          </dl>
        )}
      </Panel>
    </div>
  );
}
