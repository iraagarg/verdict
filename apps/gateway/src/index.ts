/**
 * Gateway entrypoint.
 *
 * The only responsibility here is: validate the environment, and exit non-zero
 * BEFORE binding the port if it is invalid. Everything else is in app.ts so it
 * can be tested without starting a server.
 */
import { buildApp } from "./app.js";
import { buildServices } from "./services.js";
import { EnvValidationError, loadEnv } from "./env.js";

async function main(): Promise<void> {
  let env;
  try {
    env = loadEnv();
  } catch (err) {
    if (err instanceof EnvValidationError) {
      // No logger yet — the logger needs a validated LOG_LEVEL.
      console.error(`[gateway] ${err.message}`);
      process.exit(1);
    }
    throw err;
  }

  const services = buildServices({ env });
  const app = buildApp({ env, services });

  const shutdown = async (signal: string): Promise<void> => {
    app.log.info({ signal }, "shutting down");
    try {
      await app.close();
      // Drain buffered traces before exiting; the queue is write-behind, so a
      // hard exit here would discard whatever had not been flushed yet.
      await services.close();
      process.exit(0);
    } catch (err) {
      app.log.error({ err }, "error during shutdown");
      process.exit(1);
    }
  };

  process.on("SIGTERM", () => void shutdown("SIGTERM"));
  process.on("SIGINT", () => void shutdown("SIGINT"));

  try {
    await app.listen({ port: env.GATEWAY_PORT, host: env.GATEWAY_HOST });
  } catch (err) {
    app.log.error({ err }, "failed to bind port");
    process.exit(1);
  }
}

void main();
