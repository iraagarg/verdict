/**
 * Everything the routes need, constructed once at boot.
 *
 * Bundled into one object so tests can substitute a mock provider or an
 * in-memory trace sink without touching route code.
 */
import type { Pool } from "pg";
import { loadModelConfig, type ModelConfig } from "@verdict/shared";
import { ProviderRegistry } from "./providers/registry.js";
import { TraceQueue } from "./trace/queue.js";
import { createTraceSink, type TraceRow } from "./trace/repository.js";
import { createPool } from "./db/pool.js";
import type { ProviderAdapter, ProviderName } from "./providers/types.js";
import type { Env } from "./env.js";

export interface GatewayServices {
  env: Env;
  config: ModelConfig;
  registry: ProviderRegistry;
  traces: TraceQueue<TraceRow>;
  pool: Pool | undefined;
  close: () => Promise<void>;
}

export interface BuildServicesOptions {
  env: Env;
  /** Substitute adapters in tests. */
  adapters?: Partial<Record<ProviderName, ProviderAdapter>>;
  /** Substitute the trace sink in tests; defaults to Postgres. */
  traceSink?: (rows: TraceRow[]) => Promise<void>;
}

export function buildServices(opts: BuildServicesOptions): GatewayServices {
  const { env } = opts;
  const config = loadModelConfig(env.MODELS_CONFIG_PATH);
  const registry = new ProviderRegistry(config, env, opts.adapters);

  const pool = opts.traceSink === undefined ? createPool(env) : undefined;
  const sink = opts.traceSink ?? createTraceSink(pool!);

  const traces = new TraceQueue<TraceRow>({
    capacity: env.TRACE_QUEUE_CAPACITY,
    batchSize: env.TRACE_BATCH_SIZE,
    flushIntervalMs: env.TRACE_FLUSH_INTERVAL_MS,
    sink,
  });
  traces.start();

  return {
    env,
    config,
    registry,
    traces,
    pool,
    close: async () => {
      await traces.stop();
      await pool?.end();
    },
  };
}
