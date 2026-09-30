/**
 * `GET /v1/models` — what this deployment can actually serve.
 *
 * Part of the OpenAI surface, and missing until a client needed it. The example
 * app hard-coded three models in a dropdown; two of them could not work, because
 * the deployed gateway carries only a Groq key. Every client would have had to
 * guess the same way, and guess wrong in a different environment.
 *
 * So the list is filtered by `isServable` — a model appears only when this
 * process holds credentials for the provider behind it. The same config
 * deployed with different keys advertises a different list, which is the point:
 * a client asks rather than assumes (D-073).
 *
 * `verdict-auto` is listed only when `safe_default` is itself servable.
 * Advertising a routing alias that resolves to a model this deployment cannot
 * call would be advertising a guaranteed failure.
 */
import type { GatewayApp } from "../http.js";
import type { GatewayServices } from "../services.js";
import { AUTO_MODEL } from "../router/select.js";

interface ModelRow {
  id: string;
  object: "model";
  created: number;
  owned_by: string;
  /** Verdict extensions. Unknown fields are ignored by OpenAI clients. */
  verdict?: {
    rung: string;
    input_usd_per_mtok: number;
    output_usd_per_mtok: number;
  };
}

export function registerModelRoutes(app: GatewayApp, services: GatewayServices): void {
  app.get("/v1/models", async () => {
    const cfg = services.registry.config;

    const data: ModelRow[] = Object.entries(cfg.models)
      .filter(([id]) => services.registry.isServable(id))
      .map(([id, entry]) => ({
        id,
        object: "model" as const,
        // OpenAI sends a unix timestamp here. Nothing in this project has a
        // meaningful creation date for a model, and inventing one would be a
        // fabricated number in a project that refuses those, so it reports when
        // the pricing was last verified — a date that is real and checkable.
        created: Math.floor(new Date(entry.pricing.verified_at).getTime() / 1000),
        owned_by: entry.provider,
        verdict: {
          rung: entry.rung,
          input_usd_per_mtok: entry.pricing.input,
          output_usd_per_mtok: entry.pricing.output,
        },
      }))
      .sort((a, b) => (a.verdict?.input_usd_per_mtok ?? 0) - (b.verdict?.input_usd_per_mtok ?? 0));

    if (services.registry.isServable(cfg.safe_default)) {
      data.push({
        id: AUTO_MODEL,
        object: "model",
        created: 0,
        owned_by: "verdict",
      });
    }

    return { object: "list", data };
  });
}
