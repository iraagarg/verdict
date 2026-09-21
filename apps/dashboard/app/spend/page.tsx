import { EmptyState, Panel, Stat } from "../../components/ui";
import { loadTraces, usd, type Trace } from "../../lib/artifacts";

export const metadata = { title: "Spend · Verdict" };

function byHour(traces: Trace[]) {
  const buckets = new Map<string, Map<string, number>>();
  for (const t of traces) {
    const hour = t.created_at.slice(0, 13);
    const models = buckets.get(hour) ?? new Map<string, number>();
    models.set(t.model_served, (models.get(t.model_served) ?? 0) + t.cost_usd);
    buckets.set(hour, models);
  }
  return [...buckets.entries()].sort(([a], [b]) => a.localeCompare(b));
}

const BAR_COLOURS = [
  "bg-sky-500",
  "bg-emerald-500",
  "bg-amber-500",
  "bg-violet-500",
  "bg-rose-500",
];

export default function SpendPage() {
  const artifact = loadTraces();

  if (artifact === null) {
    return (
      <div className="space-y-5">
        <h1 className="text-2xl font-semibold tracking-tight">Spend over time</h1>
        <Panel title="No traffic exported">
          <EmptyState
            what="There is no trace sample to chart."
            why="The dashboard reads a committed snapshot rather than querying Postgres, so it builds statically and cannot break during a demo. That snapshot has not been exported."
            command="./tools/export-traces.sh 60"
          />
        </Panel>
      </div>
    );
  }

  const models = [...new Set(artifact.traces.map((t) => t.model_served))].sort();
  const hours = byHour(artifact.traces);
  const peak = Math.max(
    ...hours.map(([, m]) => [...m.values()].reduce((a, b) => a + b, 0)),
    Number.EPSILON,
  );
  const totalByModel = new Map<string, number>();
  for (const t of artifact.traces) {
    totalByModel.set(t.model_served, (totalByModel.get(t.model_served) ?? 0) + t.cost_usd);
  }
  const total = [...totalByModel.values()].reduce((a, b) => a + b, 0);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Spend over time</h1>
        <p className="mt-2 text-sm text-neutral-600 dark:text-neutral-400">{artifact.note}</p>
      </div>

      <Panel
        title="Total"
        subtitle={`${artifact.sampled} sampled of ${artifact.total_traces_in_db} traces in the database.`}
      >
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Sampled spend" value={usd(total)} />
          <Stat label="Requests" value={String(artifact.traces.length)} />
          <Stat label="Models" value={String(models.length)} />
          <Stat
            label="Mean cost"
            value={usd(total / Math.max(1, artifact.traces.length))}
            hint="per request"
          />
        </dl>
      </Panel>

      <Panel title="By model" subtitle="Where the money actually went.">
        <ul className="space-y-2">
          {[...totalByModel.entries()]
            .sort(([, a], [, b]) => b - a)
            .map(([model, cost], i) => (
              <li key={model}>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="break-all font-mono text-xs">{model}</span>
                  <span className="shrink-0 font-mono text-sm tabular-nums">{usd(cost)}</span>
                </div>
                <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
                  <div
                    className={`h-full rounded-full ${BAR_COLOURS[i % BAR_COLOURS.length]}`}
                    style={{ width: `${Math.max(1, (cost / (total || 1)) * 100)}%` }}
                  />
                </div>
              </li>
            ))}
        </ul>
      </Panel>

      <Panel title="By hour" subtitle="Stacked by model. Hover or focus a segment for the figure.">
        <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
          <div className="flex min-w-max items-end gap-1" style={{ height: 180 }}>
            {hours.map(([hour, perModel]) => {
              const hourTotal = [...perModel.values()].reduce((a, b) => a + b, 0);
              return (
                <div key={hour} className="flex w-7 flex-col items-center gap-1">
                  <div
                    className="flex w-full flex-col-reverse overflow-hidden rounded-sm"
                    style={{ height: `${(hourTotal / peak) * 140}px` }}
                  >
                    {models.map((m) => {
                      const v = perModel.get(m) ?? 0;
                      if (v === 0) return null;
                      return (
                        <div
                          key={m}
                          tabIndex={0}
                          className={`w-full ${BAR_COLOURS[models.indexOf(m) % BAR_COLOURS.length]} focus-visible:ring-2 focus-visible:ring-sky-600`}
                          style={{ height: `${(v / hourTotal) * 100}%` }}
                          title={`${hour}:00 UTC — ${m}: ${usd(v)}`}
                          aria-label={`${hour} hundred hours UTC, ${m}, ${usd(v)}`}
                        />
                      );
                    })}
                  </div>
                  <span className="text-[10px] tabular-nums text-neutral-500">
                    {hour.slice(-2)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
        <ul className="mt-4 flex flex-wrap gap-x-4 gap-y-1">
          {models.map((m, i) => (
            <li key={m} className="flex items-center gap-1.5 text-xs">
              <span
                className={`h-2.5 w-2.5 shrink-0 rounded-sm ${BAR_COLOURS[i % BAR_COLOURS.length]}`}
              />
              <span className="break-all font-mono">{m}</span>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
