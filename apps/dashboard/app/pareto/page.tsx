import { EmptyState, Panel } from "../../components/ui";
import { loadCache, loadPareto, pct } from "../../lib/artifacts";

export const metadata = { title: "Pareto · Verdict" };

/** Inline SVG rather than a chart library: two dozen points need no dependency. */
function Curve({
  points,
  chosen,
}: {
  points: Array<{ x: number; y: number; ok: boolean; label: string }>;
  chosen: number | null;
}) {
  const W = 640;
  const H = 260;
  const P = 34;
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const xMin = Math.min(...xs, 0);
  const xMax = Math.max(...xs, 1);
  const yMin = Math.min(...ys, 0);
  const yMax = Math.max(...ys, 1);
  const sx = (v: number) => P + ((v - xMin) / (xMax - xMin || 1)) * (W - 2 * P);
  const sy = (v: number) => H - P - ((v - yMin) / (yMax - yMin || 1)) * (H - 2 * P);

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="h-auto w-full"
      role="img"
      aria-label={`Cost against quality across ${points.length} thresholds. Points that are provably safe are filled.`}
    >
      <line
        x1={P}
        y1={H - P}
        x2={W - P}
        y2={H - P}
        className="stroke-neutral-300 dark:stroke-neutral-700"
      />
      <line
        x1={P}
        y1={P}
        x2={P}
        y2={H - P}
        className="stroke-neutral-300 dark:stroke-neutral-700"
      />
      <polyline
        fill="none"
        strokeWidth={1.5}
        className="stroke-neutral-400 dark:stroke-neutral-600"
        points={points.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ")}
      />
      {points.map((p) => (
        <circle
          key={p.label}
          cx={sx(p.x)}
          cy={sy(p.y)}
          r={p.label === String(chosen) ? 6 : 3.5}
          className={
            p.ok
              ? "fill-emerald-600 dark:fill-emerald-400"
              : "fill-neutral-400 dark:fill-neutral-600"
          }
        >
          <title>{`threshold ${p.label}: quality ${pct(p.y)}, cost ${pct(p.x)} of all-strong`}</title>
        </circle>
      ))}
      <text x={W / 2} y={H - 8} textAnchor="middle" className="fill-neutral-500 text-[11px]">
        cost, as a fraction of always using the strong model
      </text>
      <text
        x={12}
        y={H / 2}
        textAnchor="middle"
        transform={`rotate(-90 12 ${H / 2})`}
        className="fill-neutral-500 text-[11px]"
      >
        quality
      </text>
    </svg>
  );
}

export default function ParetoPage() {
  const pareto = loadPareto();
  const cache = loadCache();

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-semibold tracking-tight">Cost vs quality</h1>

      <Panel
        title="Routing Pareto curve"
        subtitle="Every escalation threshold, fitted on the dev split and reported on held-out data."
      >
        {pareto === null ? (
          <EmptyState
            what="No routing policy has been fitted."
            why="The sweep consumes judged replay records: every corpus item needs, for each model, whether it won-or-tied the reference and what it cost. That requires a full replay and a judging pass, neither of which has been run."
            command={"make bench CAP=30\ncd apps/evald && .venv/bin/python -m evald.cli fit"}
          />
        ) : (
          <Curve
            points={(pareto.sweeps.find((s) => s.role === "report")?.points ?? []).map((p) => ({
              x: p.cost_ratio,
              y: p.quality,
              ok: p.eligible,
              label: String(p.threshold),
            }))}
            chosen={pareto.chosen_threshold}
          />
        )}
      </Panel>

      <Panel
        id="cache"
        title="Semantic cache: hit rate against false-hit rate"
        subtitle="A cache that returns wrong answers quickly is worse than no cache, so neither rate is shown without the other."
      >
        {cache === null ? (
          <EmptyState
            what="The cache has not been calibrated."
            why="Calibration needs the corpus embedded and a set of paraphrase pairs. Embeddings run locally and cost nothing."
            command="cd apps/evald && .venv/bin/python -m evald.cli cache-calibrate"
          />
        ) : (
          <>
            <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
              <table className="w-full min-w-[420px] text-sm">
                <caption className="sr-only">
                  Hit rate and false-hit rate at each similarity threshold.
                </caption>
                <thead>
                  <tr className="border-b border-neutral-200 text-left dark:border-neutral-800">
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Threshold
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Hit rate
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      False hits
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Upper bound
                    </th>
                    <th scope="col" className="py-2 font-medium">
                      Safe?
                    </th>
                  </tr>
                </thead>
                <tbody className="font-mono tabular-nums">
                  {cache.points
                    .filter((p) => Math.round(p.threshold * 200) % 5 === 0)
                    .map((p) => (
                      <tr
                        key={p.threshold}
                        className="border-b border-neutral-100 dark:border-neutral-800/60"
                      >
                        <td className="py-1.5 pr-3">{p.threshold.toFixed(3)}</td>
                        <td className="py-1.5 pr-3">{pct(p.hit_rate)}</td>
                        <td className="py-1.5 pr-3">{pct(p.false_hit_rate, 2)}</td>
                        <td className="py-1.5 pr-3">{pct(p.false_hit_ci_high, 2)}</td>
                        <td className="py-1.5">
                          <span
                            className={
                              p.acceptable
                                ? "text-emerald-700 dark:text-emerald-400"
                                : "text-neutral-500"
                            }
                          >
                            {p.acceptable ? "yes" : "no"}
                          </span>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>

            {cache.chosen_threshold === null ? (
              <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-900 dark:bg-amber-950/40">
                <p className="font-medium">No threshold is both safe and useful.</p>
                <p className="mt-1 text-pretty text-neutral-700 dark:text-neutral-300">
                  At a {pct(cache.max_false_hit_rate, 0)} tolerance, nothing clears the bar while
                  still hitting often enough to be worth the embedding call. The gateway therefore
                  serves no semantic cache. What loosening the bar would buy:
                </p>
                <ul className="mt-2 space-y-1 font-mono text-xs">
                  {cache.price_of_usefulness.map((row) => {
                    const tol = row[0] ?? 0;
                    const hit = row[1] ?? 0;
                    const thresh = row[2] ?? 0;
                    return (
                      <li key={tol}>
                        accept up to {pct(tol, 0)} wrong →{" "}
                        {hit > 0
                          ? `${pct(hit)} hit rate at ${thresh.toFixed(3)}`
                          : "still nothing useful"}
                      </li>
                    );
                  })}
                </ul>
              </div>
            ) : null}

            <p className="mt-3 text-xs text-neutral-500">
              {cache.n_duplicates} paraphrase pairs against {cache.n_different} hard negatives.
              Proving a rate below {pct(cache.max_false_hit_rate, 0)} needs at least{" "}
              {cache.min_negatives_required} negatives even with zero observed false hits.
            </p>
          </>
        )}
      </Panel>
    </div>
  );
}
