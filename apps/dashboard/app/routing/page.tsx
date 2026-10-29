import { EmptyState, Panel, Stat } from "../../components/ui";
import { loadPolicy, pct, type PolicyRoute } from "../../lib/artifacts";

export const metadata = { title: "Routing · Verdict" };

/**
 * The page the dashboard was missing.
 *
 * Every other page showed a measurement. None showed the DECISION those
 * measurements exist to justify, which left a visitor able to read the whole
 * site without ever seeing that traffic is routed differently because of them
 * (D-079). The thesis is "route to the cheapest model that provably clears a
 * quality floor"; this is where that sentence becomes three rows and a number.
 */

/** Rendered to scale: the floor as a line, the CI as a bar, so clearing it is visible. */
function FloorBar({ route }: { route: PolicyRoute }) {
  // Fixed window rather than data-driven bounds: a window that rescales per
  // route makes two routes look comparable when they are not.
  const LO = 0.5;
  const span = 1 - LO;
  const x = (v: number) => `${Math.min(100, Math.max(0, ((v - LO) / span) * 100))}%`;
  const clears = route.ci_low >= route.floor;

  return (
    <div className="mt-3">
      <div className="relative h-8 rounded border border-neutral-300 bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900">
        {/* Everything below the floor is the region a cheap rung may not enter. */}
        <div
          className="absolute inset-y-0 left-0 rounded-l bg-red-500/10 dark:bg-red-500/15"
          style={{ width: x(route.floor) }}
          aria-hidden
        />
        <div
          className="absolute inset-y-0 w-px bg-red-500"
          style={{ left: x(route.floor) }}
          aria-hidden
        />
        <div
          className={`absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full ${
            clears ? "bg-emerald-500" : "bg-amber-500"
          }`}
          style={{ left: x(route.ci_low), width: `calc(${x(route.ci_high)} - ${x(route.ci_low)})` }}
          aria-hidden
        />
        <div
          className={`absolute top-1/2 h-3 w-0.5 -translate-y-1/2 ${
            clears ? "bg-emerald-600" : "bg-amber-600"
          }`}
          style={{ left: x(route.quality) }}
          aria-hidden
        />
      </div>
      <div className="mt-1.5 flex justify-between text-[11px] text-neutral-500 dark:text-neutral-400">
        <span>{pct(LO, 0)}</span>
        <span className="text-red-600 dark:text-red-400">floor {pct(route.floor, 1)}</span>
        <span>100%</span>
      </div>
      <p className="mt-2 text-xs text-neutral-600 dark:text-neutral-400">
        The bar is the 95% interval for <strong>{route.assigned_model}</strong> on this route. What
        matters is its <strong>left</strong> edge: {pct(route.ci_low, 1)}. Routing cheap is
        justified only when the <em>worst case the data allows</em> is still above the floor — not
        when the average happens to look fine.
      </p>
    </div>
  );
}

function RouteCard({ route, cheapest }: { route: PolicyRoute; cheapest: string | null }) {
  const clears = route.ci_low >= route.floor;
  const isCheap = cheapest !== null && route.assigned_model === cheapest;

  return (
    <Panel title={route.route_key} subtitle={`${route.n_items} paired items`}>
      <p className="text-sm">
        <span
          className={`rounded px-2 py-0.5 font-mono text-xs ${
            isCheap
              ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
              : "bg-neutral-200 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300"
          }`}
        >
          → {route.assigned_model}
        </span>
      </p>
      <p className="mt-3 text-sm text-neutral-700 dark:text-neutral-300">{route.reason}</p>
      <FloorBar route={route} />
      <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Measured" value={pct(route.quality, 1)} />
        <Stat
          label="CI lower"
          value={pct(route.ci_low, 1)}
          hint={clears ? "clears" : "below floor"}
        />
        <Stat label="Floor" value={pct(route.floor, 1)} />
        <Stat label="Items" value={String(route.n_items)} />
      </dl>
    </Panel>
  );
}

export default function RoutingPage() {
  const policy = loadPolicy();

  if (policy === null) {
    return (
      <div className="space-y-5">
        <h1 className="text-2xl font-semibold tracking-tight">Routing policy</h1>
        <Panel title="No policy fitted">
          <EmptyState
            what="There is no routing policy to show."
            why="A policy is fitted from judged or verified replay records: every route needs enough paired items to put a confidence interval around the cheap rung's quality. Until that runs, the gateway serves safe_default for everything."
            command={
              "cd apps/evald && .venv/bin/python -m evald.cli records --models A,B\n" +
              ".venv/bin/python -m evald.cli fit --cheap-model A --strong-model B"
            }
          />
        </Panel>
      </div>
    );
  }

  // A "discounted" route is one whose CI lower bound cleared the floor — that
  // is the condition the policy actually applied. Derived from the policy, not
  // from a price table this page does not read: the dashboard must never assert
  // a price it cannot cite.
  const discounted = policy.routes.filter((r) => r.ci_low >= r.floor).length;
  const cheapest = policy.routes.find((r) => r.ci_low >= r.floor)?.assigned_model ?? null;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Routing policy</h1>
        <p className="mt-2 text-pretty text-sm text-neutral-600 dark:text-neutral-400">
          The decision every other page on this site exists to justify. Fitted{" "}
          {policy.created_at.slice(0, 10)} · git {policy.git_sha.slice(0, 7)} · the gateway loads
          this file and reports which route it took on every response.
        </p>
      </div>

      <Panel title="What the gateway does with it">
        <pre className="overflow-x-auto rounded bg-neutral-100 p-3 text-xs leading-relaxed dark:bg-neutral-900">
          {`POST /v1/chat/completions   {"model": "verdict-auto"}\n\n` +
            policy.routes
              .map(
                (r) =>
                  `  x-verdict-route: ${r.route_key.padEnd(18)} → ${r.assigned_model.padEnd(18)}` +
                  ` CI low ${pct(r.ci_low, 1)} ${r.ci_low >= r.floor ? "≥" : "<"} floor ${pct(r.floor, 1)}`,
              )
              .join("\n") +
            `\n  (no route header)${" ".repeat(20)}→ ${policy.safe_default.padEnd(18)} unrecognised route gets no discount`}
        </pre>
        <p className="mt-3 text-sm text-neutral-700 dark:text-neutral-300">
          Cheap where it is proven safe. Expensive where it is not.{" "}
          <strong>Safest when it does not know</strong> — an unrecognised route is not a cheap
          route, because the guarantee was fitted on traffic this one may not resemble.
        </p>
        <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Routes" value={String(policy.routes.length)} />
          <Stat label="Routed cheap" value={`${discounted} of ${policy.routes.length}`} />
          <Stat label="Margin" value={`±${pct(policy.margin, 1)}`} />
          <Stat label="Fallback" value={policy.safe_default} hint="when in doubt" />
        </dl>
      </Panel>

      {policy.routes.map((r) => (
        <RouteCard key={r.route_key} route={r} cheapest={cheapest} />
      ))}

      <Panel title="Why there is no cascade">
        <p className="text-sm text-neutral-700 dark:text-neutral-300">
          An earlier version escalated: ask the cheap model, measure its self-consistency, and retry
          on the expensive one when it disagreed with itself. That was fitted against a rung{" "}
          <strong>33× cheaper and 18 quality points behind</strong>, where escalating a quarter of
          requests costs eight times the cheap price — the saving is gone before it starts, and the
          quality gap forces frequent escalation. No threshold on that curve can win.
        </p>
        <p className="mt-3 text-sm text-neutral-700 dark:text-neutral-300">
          At a <strong>2× gap with a rung already proven non-inferior</strong> there is nothing to
          escalate. Escalation costs the cheap call <em>plus</em> the strong one, so it only pays
          when the gap is large and escalation rare. Here it is neither. <code>cascade: null</code>{" "}
          is a finding, not an omission.
        </p>
      </Panel>
    </div>
  );
}
