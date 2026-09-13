export default function Home() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-2xl flex-col justify-center gap-6 px-5 py-16">
      <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Verdict</h1>
      <p className="text-pretty text-base leading-relaxed text-neutral-400 sm:text-lg">
        An OpenAI-compatible gateway that routes live traffic to the cheapest model which still
        clears a statistically proven quality floor.
      </p>
      <p className="rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-sm text-neutral-400">
        Phase 0 — scaffold only. The Pareto curve, trace explorer and regression diffs arrive in
        Phase 7.
      </p>
    </main>
  );
}
