/**
 * Postgres connection pool.
 *
 * Kept deliberately thin. The gateway's only write is the batched trace insert;
 * if that connection is unavailable the gateway keeps serving and the queue
 * drops (DESIGN.md failure mode #7), so nothing here may throw at import time
 * or block startup on a database handshake.
 */
import pg from "pg";
import type { Env } from "../env.js";

export function createPool(env: Env): pg.Pool {
  const pool = new pg.Pool({
    connectionString: env.DATABASE_URL,
    max: env.PG_POOL_MAX,
    connectionTimeoutMillis: 5_000,
    idleTimeoutMillis: 30_000,
    ...(env.DATABASE_URL.includes("sslmode=require") ? { ssl: { rejectUnauthorized: true } } : {}),
  });

  // An idle client error must not become an unhandled 'error' event that kills
  // the process. Losing the connection is a degradation, not a crash.
  pool.on("error", () => {});

  return pool;
}
