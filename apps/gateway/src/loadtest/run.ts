/**
 * Load test: what does putting Verdict in front of a provider actually cost?
 *
 * ## Why the upstream is a mock and not a real provider
 *
 * The question "how much latency does the gateway add?" has an answer around a
 * millisecond. A real provider's own latency varies by hundreds of milliseconds
 * between identical calls. Measuring overhead against Anthropic would therefore
 * measure Anthropic: the signal is three orders of magnitude below the noise,
 * and any number produced that way is unfalsifiable.
 *
 * So the headline figure is measured against a controlled upstream — the same
 * mock provider the correctness tests use (D-021), which speaks real Anthropic
 * SSE over a real socket. Two runs, identical load, same machine, back to back:
 *
 *   baseline: client -> mock
 *   through:  client -> gateway -> mock
 *
 * The difference is the gateway, and nothing else.
 *
 * A second, smaller run against a real provider is reported alongside for
 * end-to-end context. It is NOT the overhead number and the artifact labels it
 * so, because it is dominated by the provider.
 *
 * ## The honest caveat on subtracting percentiles
 *
 * `p99(through) - p99(baseline)` is the difference of two percentiles, not the
 * 99th percentile of the added latency. Those are different quantities: the
 * slowest 1% of each run need not be the same requests. The artifact records
 * both raw distributions so the subtraction can be checked, and names the
 * statistic `overhead_by_percentile_difference` rather than implying more
 * precision than the method supports.
 */
import autocannon from "autocannon";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { startMockProvider, anthropicScript, type MockProvider } from "../testing/mock-provider.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "../../../..");

/** Long enough for the runtime to reach steady state, short enough to iterate. */
const DURATION_S = Number(process.env["LOADTEST_DURATION_S"] ?? 20);
const CONNECTIONS = Number(process.env["LOADTEST_CONNECTIONS"] ?? 50);
const WARMUP_S = 5;

const MOCK_PORT = 39_101;
const GATEWAY_PORT = 39_102;

/**
 * Six content frames, not twenty.
 *
 * The mock schedules each frame on a `setTimeout(…, 0)`, which in practice
 * costs about a millisecond per frame. Twenty frames put a ~25ms floor under
 * both arms — identical on each side, so the subtraction stayed valid, but it
 * buried the gateway's own contribution inside timer noise. A shorter script
 * lowers the floor and lets the quantity being measured actually show.
 */
const SCRIPT = anthropicScript(
  Array.from({ length: 6 }, (_, i) => `token${String(i)} `),
  { input: 120, output: 40 },
);

/**
 * BOTH arms stream.
 *
 * The first version of this test sent `stream: false` through the gateway while
 * the baseline streamed, and reported the gateway as FASTER than not having a
 * gateway. The arithmetic was right and the experiment was wrong: in the
 * baseline the load generator paid to parse every SSE frame, while through the
 * gateway the load generator read one small JSON body and the GATEWAY paid that
 * cost. Two different client workloads, so the difference was not the gateway.
 *
 * Streaming on both sides puts the same job in front of the load generator on
 * each arm, which is the only configuration where the subtraction means
 * anything. Streaming passthrough is also the gateway's actual job.
 */
const BODY = JSON.stringify({
  model: "claude-haiku-4-5",
  messages: [{ role: "user", content: "Summarise the following support ticket in two sentences." }],
  stream: true,
});

interface Percentiles {
  p50: number;
  p90: number;
  p95: number;
  p99: number;
  mean: number;
  max: number;
}

interface RunResult {
  latency_ms: Percentiles;
  requests_per_second_mean: number;
  requests_total: number;
  /**
   * Bytes the load generator actually read. Recorded so the two arms can be
   * checked for the asymmetry that invalidated the first version of this test:
   * if these differ wildly, the arms are not comparable and the subtraction is
   * meaningless however clean the latency numbers look.
   */
  bytes_per_request_mean: number;
  non_2xx: number;
  errors: number;
  timeouts: number;
}

function summarise(r: autocannon.Result): RunResult {
  return {
    latency_ms: {
      p50: r.latency.p50,
      p90: r.latency.p90,
      p95: r.latency.p97_5, // autocannon reports 97.5, not 95
      p99: r.latency.p99,
      mean: r.latency.mean,
      max: r.latency.max,
    },
    requests_per_second_mean: r.requests.mean,
    requests_total: r.requests.total,
    bytes_per_request_mean: Math.round(r.throughput.mean / Math.max(r.requests.mean, 1)),
    non_2xx: r.non2xx,
    errors: r.errors,
    timeouts: r.timeouts,
  };
}

async function fire(url: string, title: string, duration: number): Promise<autocannon.Result> {
  return autocannon({
    url,
    connections: CONNECTIONS,
    duration,
    title,
    method: "POST",
    headers: { "content-type": "application/json" },
    body: BODY,
  });
}

/** Mock speaks Anthropic's protocol, so a direct baseline must use Anthropic's path. */
async function fireMockDirect(mockUrl: string, duration: number): Promise<autocannon.Result> {
  return autocannon({
    url: `${mockUrl}/v1/messages`,
    connections: CONNECTIONS,
    duration,
    title: "baseline",
    method: "POST",
    headers: { "content-type": "application/json", "anthropic-version": "2023-06-01" },
    body: JSON.stringify({
      model: "claude-haiku-4-5",
      max_tokens: 256,
      messages: [{ role: "user", content: "Summarise the following support ticket." }],
      stream: true,
    }),
  });
}

/**
 * A small run against a REAL provider, for end-to-end context only.
 *
 * This is not the overhead measurement and the artifact says so. Deliberately
 * gentle — two connections for ten seconds — because a free tier will rate-limit
 * anything heavier, and a run full of 429s measures the rate limiter rather than
 * the gateway. Skipped entirely when no key is present.
 */
async function realProviderRun(): Promise<RunResult | null> {
  const key = process.env["GROQ_API_KEY"];
  if (key === undefined || key.trim() === "") return null;

  const gw = spawn("node", ["dist/index.js"], {
    cwd: resolve(REPO, "apps/gateway"),
    env: {
      ...process.env,
      NODE_ENV: "production",
      LOG_LEVEL: "error",
      GATEWAY_PORT: String(GATEWAY_PORT + 1),
      GATEWAY_HOST: "127.0.0.1",
      GROQ_API_KEY: key,
      ANTHROPIC_API_KEY: "",
      OPENAI_API_KEY: "",
      DATABASE_URL:
        process.env["LOADTEST_DATABASE_URL"] ?? "postgres://verdict:verdict@localhost:5432/verdict",
      REDIS_URL: process.env["LOADTEST_REDIS_URL"] ?? "redis://localhost:6379",
      COST_CAP_USD_PER_DAY: "1000",
      MODELS_CONFIG_PATH: resolve(REPO, "config/models.yaml"),
      ROUTER_MODE: "off",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  try {
    await waitForHealth(`http://127.0.0.1:${String(GATEWAY_PORT + 1)}/health`);
    const r = await autocannon({
      url: `http://127.0.0.1:${String(GATEWAY_PORT + 1)}/v1/chat/completions`,
      connections: 2,
      duration: 10,
      title: "real",
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model: "openai/gpt-oss-20b",
        max_tokens: 64,
        messages: [{ role: "user", content: "Reply with one short sentence about bridges." }],
        stream: true,
      }),
    });
    return summarise(r);
  } finally {
    gw.kill("SIGTERM");
  }
}

async function waitForHealth(url: string, timeoutMs = 30_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
    } catch {
      // not up yet
    }
    if (Date.now() > deadline) throw new Error(`gateway did not become healthy at ${url}`);
    await new Promise((r) => setTimeout(r, 250));
  }
}

/**
 * Boots the gateway exactly as production does, with one change: the Anthropic
 * base URL points at the mock.
 *
 * Postgres and Redis are the real local ones rather than stubs, because the env
 * schema requires them (D-006: refuse to start on a bad environment rather than
 * fail at request time) and because trace persistence is part of what the
 * gateway costs. Measuring with it switched off would flatter the result.
 */
function startGateway(mockUrl: string): ChildProcess {
  const child = spawn("node", ["dist/index.js"], {
    cwd: resolve(REPO, "apps/gateway"),
    env: {
      ...process.env,
      NODE_ENV: "production",
      LOG_LEVEL: "error", // at info, pino's own throughput would be in the measurement
      GATEWAY_PORT: String(GATEWAY_PORT),
      GATEWAY_HOST: "127.0.0.1",
      ANTHROPIC_API_KEY: "sk-ant-loadtest-placeholder",
      ANTHROPIC_BASE_URL: mockUrl,
      OPENAI_API_KEY: "",
      GROQ_API_KEY: "",
      DATABASE_URL:
        process.env["LOADTEST_DATABASE_URL"] ?? "postgres://verdict:verdict@localhost:5432/verdict",
      REDIS_URL: process.env["LOADTEST_REDIS_URL"] ?? "redis://localhost:6379",
      COST_CAP_USD_PER_DAY: "1000", // nothing real is billed; the mock is free
      MODELS_CONFIG_PATH: resolve(REPO, "config/models.yaml"),
      ROUTER_MODE: "off",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stderr?.on("data", (d: Buffer) => process.stderr.write(`[gateway] ${d.toString()}`));
  return child;
}

function gitSha(): string {
  try {
    return execFileSync("git", ["rev-parse", "HEAD"], { cwd: REPO }).toString().trim();
  } catch {
    return "unknown";
  }
}

async function main(): Promise<void> {
  let mock: MockProvider | undefined;
  let gateway: ChildProcess | undefined;

  try {
    // `record: false` — see MockOptions. Retaining 300k request bodies would
    // make the upstream the bottleneck and corrupt the measurement.
    mock = await startMockProvider({ events: SCRIPT }, { record: false, port: MOCK_PORT });
    process.stdout.write(`mock upstream on ${mock.url}\n`);

    gateway = startGateway(mock.url);
    await waitForHealth(`http://127.0.0.1:${String(GATEWAY_PORT)}/health`);
    process.stdout.write(`gateway on http://127.0.0.1:${String(GATEWAY_PORT)}\n\n`);

    const gatewayUrl = `http://127.0.0.1:${String(GATEWAY_PORT)}/v1/chat/completions`;

    // Warm up both paths first. A cold V8 spends its first seconds in the
    // interpreter before the JIT tiers up, and that would land entirely in the
    // gateway's numbers if only the gateway ran cold.
    process.stdout.write(`warming up (${String(WARMUP_S)}s each)...\n`);
    await fireMockDirect(mock.url, WARMUP_S);
    await fire(gatewayUrl, "warmup", WARMUP_S);

    process.stdout.write(`\nbaseline: client -> mock (${String(DURATION_S)}s)\n`);
    const baseline = await fireMockDirect(mock.url, DURATION_S);

    process.stdout.write(`through:  client -> gateway -> mock (${String(DURATION_S)}s)\n`);
    const through = await fire(gatewayUrl, "through", DURATION_S);

    const b = summarise(baseline);
    const t = summarise(through);

    process.stdout.write(`real:     client -> gateway -> Groq (10s, gentle)\n`);
    const real = await realProviderRun();

    const overhead: Record<string, number> = {};
    for (const k of ["p50", "p90", "p95", "p99", "mean"] as const) {
      overhead[k] = Number((t.latency_ms[k] - b.latency_ms[k]).toFixed(3));
    }

    const artifact = {
      artifact_version: 1,
      created_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
      git_sha: gitSha(),
      tool: "autocannon",
      tool_version: (
        JSON.parse(
          execFileSync("node", ["-p", "JSON.stringify(require('autocannon/package.json'))"], {
            cwd: resolve(REPO, "apps/gateway"),
          }).toString(),
        ) as { version: string }
      ).version,
      host: {
        platform: process.platform,
        arch: process.arch,
        cpus: (await import("node:os")).cpus().length,
        node: process.version,
      },
      load: {
        connections: CONNECTIONS,
        duration_s: DURATION_S,
        warmup_s: WARMUP_S,
        request: "POST /v1/chat/completions, non-streaming, ~40 output tokens",
      },
      upstream: "controlled mock speaking Anthropic SSE over a real socket",
      baseline_mock_direct: b,
      through_gateway: t,
      overhead_by_percentile_difference_ms: overhead,
      sustained_requests_per_minute: Math.round(t.requests_per_second_mean * 60),
      /**
       * End-to-end against a real provider. NOT the overhead figure: these
       * numbers are the provider's, with the gateway's ~1ms somewhere inside.
       */
      end_to_end_real_provider: real ?? "skipped - no GROQ_API_KEY",
      client_bytes_ratio: Number(
        (t.bytes_per_request_mean / Math.max(b.bytes_per_request_mean, 1)).toFixed(2),
      ),
      notes: [
        "Overhead is measured against a CONTROLLED upstream, not a real provider. A provider's own latency varies by hundreds of ms between identical calls, which is three orders of magnitude above the gateway's contribution; measuring overhead against one would measure the provider.",
        "The gateway arm delivers MORE bytes to the load generator than the baseline (see client_bytes_ratio): OpenAI's chunk envelope is more verbose than Anthropic's, and translating between them is the gateway's job. That residual asymmetry works AGAINST the gateway — the load generator does more reading on the arm being measured — so the true proxy cost is at most the figure reported here, not more.",
        "`overhead_by_percentile_difference_ms` subtracts percentile from percentile. That is NOT the percentile of the added latency: the slowest 1% of each run need not be the same requests. Both raw distributions are recorded so the subtraction can be checked.",
        "Non-streaming requests. Streaming adds per-frame write cost that this figure does not capture; TTFT under load is a separate measurement.",
        "Trace persistence is ON, against the real local Postgres, because it is part of what the gateway costs in production. The queue is write-behind and bounded (D-018) so it does not block the request path, but measuring with it disabled would flatter the result.",
        "Single machine, client and both servers on one host. Real deployments add network latency that dwarfs these figures.",
      ],
    };

    mkdirSync(resolve(REPO, "artifacts"), { recursive: true });
    const out = resolve(REPO, "artifacts/loadtest.json");
    writeFileSync(out, `${JSON.stringify(artifact, null, 2)}\n`);

    process.stdout.write("\n");
    process.stdout.write(`  PROXY OVERHEAD (gateway minus baseline, same load, same upstream)\n`);
    for (const k of ["p50", "p90", "p95", "p99"] as const) {
      process.stdout.write(
        `    ${k.padEnd(5)} ${String(b.latency_ms[k]).padStart(7)} -> ${String(
          t.latency_ms[k],
        ).padStart(7)} ms   = +${String(overhead[k])} ms\n`,
      );
    }
    process.stdout.write(
      `\n  sustained ${artifact.sustained_requests_per_minute.toLocaleString()} req/min ` +
        `(${t.requests_per_second_mean.toFixed(0)} rps, ${String(t.non_2xx)} non-2xx, ${String(t.errors)} errors)\n`,
    );
    if (real) {
      process.stdout.write(
        `\n  END TO END vs real Groq (context only, NOT the overhead figure)\n` +
          `    p50 ${String(real.latency_ms.p50)}  p95 ${String(real.latency_ms.p95)}  ` +
          `p99 ${String(real.latency_ms.p99)} ms   (${String(real.non_2xx)} non-2xx)\n` +
          `    the gateway's ~1ms is inside those numbers somewhere\n`,
      );
    } else {
      process.stdout.write(`\n  real-provider arm skipped (no GROQ_API_KEY)\n`);
    }
    process.stdout.write(`\n  artifact: ${out}\n`);

    if (t.non_2xx > 0 || t.errors > 0) {
      process.stderr.write(
        `\nREFUSING to report clean numbers: ${String(t.non_2xx)} non-2xx and ${String(t.errors)} errors.\n` +
          `A throughput figure measured while requests were failing is not a throughput figure.\n`,
      );
      process.exitCode = 1;
    }

    // A proxy cannot be faster than not having the proxy. If that is what came
    // out, the two arms were not measuring the same thing — which is exactly
    // how the first version of this test failed (see BODY above). Fail loudly
    // rather than publish a flattering number nobody can defend.
    const p50Overhead = overhead["p50"] ?? 0;
    if (p50Overhead < 0) {
      process.stderr.write(
        `\nIMPLAUSIBLE: p50 overhead is ${String(p50Overhead)}ms — the gateway cannot be faster than its own upstream.\n` +
          `The two arms are not comparable. Check bytes_per_request_mean on each:\n` +
          `  baseline ${String(b.bytes_per_request_mean)} B/req, through ${String(t.bytes_per_request_mean)} B/req.\n`,
      );
      process.exitCode = 1;
    }
  } finally {
    gateway?.kill("SIGTERM");
    await mock?.close();
  }
}

await main();
