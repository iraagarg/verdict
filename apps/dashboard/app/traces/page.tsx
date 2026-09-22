import { EmptyState, Panel, Stat } from "../../components/ui";
import { loadTraces, usd, type Trace } from "../../lib/artifacts";
import { StreamReplay } from "./replay";

export const metadata = { title: "Traces · Verdict" };

function statusTone(t: Trace): string {
  if (t.status >= 500) return "text-red-700 dark:text-red-400";
  if (t.status >= 400) return "text-amber-700 dark:text-amber-400";
  return "text-emerald-700 dark:text-emerald-400";
}

function TraceCard({ t }: { t: Trace }) {
  return (
    <details className="group rounded-lg border border-neutral-200 open:bg-neutral-50 dark:border-neutral-800 dark:open:bg-neutral-900/60">
      <summary className="cursor-pointer list-none rounded-lg px-3 py-2.5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className={`font-mono text-xs font-semibold ${statusTone(t)}`}>{t.status}</span>
          <span className="break-all font-mono text-xs">{t.model_served}</span>
          {t.streamed ? (
            <span className="rounded bg-sky-100 px-1.5 py-0.5 text-[10px] font-medium text-sky-800 dark:bg-sky-950 dark:text-sky-300">
              stream
            </span>
          ) : null}
          {t.error_kind ? (
            <span className="rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-medium text-red-800 dark:bg-red-950 dark:text-red-300">
              {t.error_kind}
            </span>
          ) : null}
          {!t.usage_is_final ? (
            <span
              className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-900 dark:bg-amber-950 dark:text-amber-300"
              title="The provider never confirmed final usage, so this cost is a floor, not a fact."
            >
              usage unconfirmed
            </span>
          ) : null}
          <span className="ml-auto shrink-0 font-mono text-xs tabular-nums text-neutral-500">
            {usd(t.cost_usd)}
          </span>
        </div>
        <p className="mt-1 line-clamp-2 text-pretty text-xs text-neutral-600 dark:text-neutral-400">
          {t.prompt_preview || <em>no prompt recorded</em>}
        </p>
      </summary>

      <div className="space-y-4 border-t border-neutral-200 px-3 py-3 dark:border-neutral-800">
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Request id" value={t.request_id.slice(0, 8)} />
          <Stat label="Route" value={t.route_key ?? "unrouted"} />
          <Stat label="Tokens" value={`${t.input_tokens} → ${t.output_tokens}`} />
          <Stat label="Latency" value={t.latency_ms === null ? "—" : `${t.latency_ms}ms`} />
          <Stat label="TTFT" value={t.ttft_ms === null ? "—" : `${t.ttft_ms}ms`} />
          <Stat label="Finish" value={t.finish_reason ?? "—"} />
          <Stat label="Cache" value={t.cache_hit} />
          <Stat label="Provider" value={t.provider} />
        </dl>

        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">Prompt</h3>
          <pre className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-neutral-100 p-3 text-xs dark:bg-neutral-950">
            {t.prompt_preview || "—"}
          </pre>
        </div>

        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Response
          </h3>
          <div className="mt-1.5">
            {t.response_text ? (
              <StreamReplay
                text={t.response_text}
                ttftMs={t.ttft_ms}
                latencyMs={t.latency_ms}
                outputTokens={t.output_tokens}
              />
            ) : (
              <p className="rounded-lg border border-dashed border-neutral-300 p-3 text-xs text-neutral-500 dark:border-neutral-700">
                No response body recorded. This happens on errors, on aborted streams, and when the
                gateway dropped the trace under load — all of which are deliberate behaviours, not
                missing data.
              </p>
            )}
          </div>
        </div>
      </div>
    </details>
  );
}

export default function TracesPage() {
  const artifact = loadTraces();

  if (artifact === null) {
    return (
      <div className="space-y-5">
        <h1 className="text-2xl font-semibold tracking-tight">Trace explorer</h1>
        <Panel title="No traces">
          <EmptyState
            what="There is no trace snapshot to explore."
            why="The dashboard reads a committed export rather than querying the database, so it builds statically and cannot break during a demo."
            command="./tools/export-traces.sh 60"
          />
        </Panel>
      </div>
    );
  }

  const withBodies = artifact.traces.filter((t) => t.response_text).length;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Trace explorer</h1>
        <p className="mt-2 text-pretty text-sm text-neutral-600 dark:text-neutral-400">
          {artifact.note} Exported {artifact.exported_at.slice(0, 10)}. Stratified sampling matters
          here: most-recent-first returned 200 rows from a single model.
        </p>
      </div>

      <Panel title="Sample">
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Shown" value={String(artifact.sampled)} />
          <Stat label="In database" value={String(artifact.total_traces_in_db)} />
          <Stat label="Replayable" value={String(withBodies)} hint="have a response body" />
          <Stat
            label="Errors"
            value={String(artifact.traces.filter((t) => t.status >= 400).length)}
          />
        </dl>
      </Panel>

      <Panel title="Requests" subtitle="Select a request to inspect it and replay its stream.">
        <ul className="space-y-2">
          {artifact.traces.slice(0, 60).map((t) => (
            <li key={t.id}>
              <TraceCard t={t} />
            </li>
          ))}
        </ul>
        {artifact.traces.length > 60 ? (
          <p className="mt-3 text-xs text-neutral-500">
            Showing 60 of {artifact.traces.length} sampled.
          </p>
        ) : null}
      </Panel>
    </div>
  );
}
