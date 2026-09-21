"use client";

/**
 * Streaming replay.
 *
 * RECONSTRUCTED, not recorded. We stored time-to-first-token and total latency
 * per trace, but not per-token arrival times (DECISIONS.md D-053), so this
 * waits the real TTFT and then emits at the real average rate:
 *
 *     rate = output_tokens / (latency_ms - ttft_ms)
 *
 * Both numbers are measured and per-trace, so the overall shape is honest, but
 * within-stream jitter is smoothed away. The UI says so, because a replay that
 * looks like a recording and is not would be the dashboard telling a small lie
 * about its own precision.
 */
import { useCallback, useEffect, useRef, useState } from "react";

const SPEEDS = [1, 2, 4] as const;

export function StreamReplay({
  text,
  ttftMs,
  latencyMs,
  outputTokens,
}: {
  text: string;
  ttftMs: number | null;
  latencyMs: number | null;
  outputTokens: number;
}) {
  const [shown, setShown] = useState(text.length);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(1);
  const timers = useRef<number[]>([]);

  const clear = useCallback(() => {
    for (const t of timers.current) window.clearTimeout(t);
    timers.current = [];
  }, []);

  useEffect(() => clear, [clear]);

  // Roughly four characters per token; only the total duration matters here,
  // and it is pinned to the measured latency either way.
  const ttft = ttftMs ?? 0;
  const streamMs = Math.max(0, (latencyMs ?? 0) - ttft);
  const chars = Math.max(1, text.length);
  const perChar = outputTokens > 0 ? streamMs / chars : 0;

  const play = useCallback(() => {
    clear();
    setPlaying(true);
    setShown(0);

    const step = Math.max(1, Math.round(chars / 240)); // ≤240 repaints, however long the text
    const first = window.setTimeout(() => setShown(Math.min(step, chars)), ttft / speed);
    timers.current.push(first);

    for (let i = step; i <= chars; i += step) {
      const at = (ttft + i * perChar) / speed;
      const id = window.setTimeout(() => {
        setShown(Math.min(i, chars));
        if (i + step > chars) setPlaying(false);
      }, at);
      timers.current.push(id);
    }
  }, [chars, clear, perChar, speed, ttft]);

  const stop = useCallback(() => {
    clear();
    setPlaying(false);
    setShown(chars);
  }, [chars, clear]);

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={playing ? stop : play}
          className="rounded-md bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 dark:bg-neutral-100 dark:text-neutral-900"
        >
          {playing ? "Stop" : "Replay stream"}
        </button>

        <div className="flex items-center gap-1" role="group" aria-label="Replay speed">
          {SPEEDS.map((s) => (
            <button
              key={s}
              type="button"
              aria-pressed={speed === s}
              onClick={() => setSpeed(s)}
              className={`rounded-md px-2 py-1 text-xs tabular-nums focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 ${
                speed === s
                  ? "bg-neutral-200 font-semibold dark:bg-neutral-700"
                  : "text-neutral-600 hover:bg-neutral-100 dark:text-neutral-400 dark:hover:bg-neutral-800"
              }`}
            >
              {s}×
            </button>
          ))}
        </div>

        <p className="text-xs text-neutral-500">
          Reconstructed from a measured TTFT of {ttftMs ?? "—"}ms and {latencyMs ?? "—"}ms total —
          not a per-token recording.
        </p>
      </div>

      <pre
        aria-live="polite"
        aria-atomic="false"
        className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-neutral-100 p-3 text-xs leading-relaxed text-neutral-800 dark:bg-neutral-950 dark:text-neutral-200"
      >
        {text.slice(0, shown)}
        {playing ? <span className="animate-pulse text-sky-600 dark:text-sky-400">▋</span> : null}
      </pre>
    </div>
  );
}
