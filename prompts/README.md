# prompts/

Prompt files live here. A pull request touching this directory triggers the
eval workflow in [`.github/workflows/pr-eval.yml`](../.github/workflows/pr-eval.yml),
which runs a sampled, cost-capped evaluation and posts the quality delta,
confidence interval, verdict and projected cost change as a PR comment.

Nothing else triggers it. Evals cost real money, so a documentation change must
not start one — the webhook App applies the same rule via `touchesPrompts()`.

The judge rubric itself is not here: it lives in
`apps/evald/src/evald/judge/rubric.py`, under `RUBRIC_VERSION`, because changing
it invalidates every human label calibrated against it and that coupling should
be visible in the code rather than implied by a directory.
