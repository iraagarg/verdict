/**
 * Shared primitives.
 *
 * `EmptyState` is the most-used component here and that is deliberate: most of
 * this project's expensive measurements have not been run, so "no data" is the
 * honest and common case. Each one says what is missing and which command would
 * produce it, rather than showing a blank panel or a zeroed chart — a chart of
 * zeros reads as "the answer is zero", which is a different and false claim.
 */
import type { ReactNode } from "react";

export function Panel({
  title,
  subtitle,
  children,
  id,
}: {
  title: string;
  subtitle?: string | undefined;
  children: ReactNode;
  id?: string | undefined;
}) {
  return (
    <section
      id={id}
      aria-labelledby={id ? `${id}-heading` : undefined}
      className="rounded-xl border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900 sm:p-5"
    >
      <h2
        id={id ? `${id}-heading` : undefined}
        className="text-base font-semibold tracking-tight text-neutral-900 dark:text-neutral-100"
      >
        {title}
      </h2>
      {subtitle ? (
        <p className="mt-1 text-pretty text-sm text-neutral-600 dark:text-neutral-400">
          {subtitle}
        </p>
      ) : null}
      <div className="mt-4">{children}</div>
    </section>
  );
}

export function EmptyState({
  what,
  why,
  command,
}: {
  what: string;
  why: string;
  command?: string | undefined;
}) {
  return (
    <div className="rounded-lg border border-dashed border-neutral-300 p-5 text-sm dark:border-neutral-700">
      <p className="font-medium text-neutral-900 dark:text-neutral-100">{what}</p>
      <p className="mt-1 text-pretty text-neutral-600 dark:text-neutral-400">{why}</p>
      {command ? (
        <pre className="mt-3 overflow-x-auto rounded-md bg-neutral-100 p-3 text-xs text-neutral-800 dark:bg-neutral-950 dark:text-neutral-300">
          <code>{command}</code>
        </pre>
      ) : null}
    </div>
  );
}

const VERDICT_STYLES: Record<string, string> = {
  IMPROVEMENT:
    "bg-emerald-100 text-emerald-900 ring-emerald-600/30 dark:bg-emerald-950 dark:text-emerald-200",
  REGRESSION: "bg-red-100 text-red-900 ring-red-600/30 dark:bg-red-950 dark:text-red-200",
  EQUIVALENT: "bg-sky-100 text-sky-900 ring-sky-600/30 dark:bg-sky-950 dark:text-sky-200",
  INCONCLUSIVE:
    "bg-amber-100 text-amber-900 ring-amber-600/30 dark:bg-amber-950 dark:text-amber-200",
};

export function VerdictBadge({ outcome }: { outcome: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-1 text-xs font-semibold uppercase tracking-wide ring-1 ring-inset ${
        VERDICT_STYLES[outcome] ??
        "bg-neutral-100 text-neutral-900 dark:bg-neutral-800 dark:text-neutral-200"
      }`}
    >
      {outcome}
    </span>
  );
}

export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string | undefined;
}) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-500">
        {label}
      </dt>
      <dd className="mt-0.5 font-mono text-sm text-neutral-900 dark:text-neutral-100">{value}</dd>
      {hint ? <p className="mt-0.5 text-xs text-neutral-500">{hint}</p> : null}
    </div>
  );
}

/**
 * A confidence interval drawn to scale against a margin.
 *
 * The whole point of the four-verdict design is WHERE the interval sits
 * relative to the margin, and that is far easier to see than to read. The
 * shaded band is the margin; the bar is the interval.
 */
export function IntervalBar({
  low,
  high,
  margin,
  effect,
}: {
  low: number;
  high: number;
  margin: number;
  effect: number;
}) {
  const span = Math.max(Math.abs(low), Math.abs(high), margin * 1.6) * 1.15;
  const toPct = (v: number) => ((v + span) / (2 * span)) * 100;

  return (
    <div
      className="relative h-11 w-full select-none"
      role="img"
      aria-label={`Effect ${(effect * 100).toFixed(2)} percent, 95% confidence interval from ${(low * 100).toFixed(2)} to ${(high * 100).toFixed(2)} percent, against a margin of plus or minus ${(margin * 100).toFixed(1)} percent.`}
    >
      {/* the practical-significance margin */}
      <div
        className="absolute inset-y-3 rounded bg-neutral-200/80 dark:bg-neutral-700/60"
        style={{ left: `${toPct(-margin)}%`, width: `${toPct(margin) - toPct(-margin)}%` }}
      />
      {/* zero */}
      <div
        className="absolute inset-y-1 w-px bg-neutral-400 dark:bg-neutral-500"
        style={{ left: `${toPct(0)}%` }}
      />
      {/* the interval */}
      <div
        className="absolute inset-y-[18px] rounded-full bg-neutral-900 dark:bg-neutral-100"
        style={{ left: `${toPct(low)}%`, width: `${Math.max(1.5, toPct(high) - toPct(low))}%` }}
      />
      {/* the point estimate */}
      <div
        className="absolute inset-y-3 w-0.5 bg-sky-600 dark:bg-sky-400"
        style={{ left: `${toPct(effect)}%` }}
      />
    </div>
  );
}
