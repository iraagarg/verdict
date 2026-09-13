/**
 * Provider registry: one adapter and one circuit breaker per provider.
 *
 * The breaker is keyed by provider rather than by model because the failure
 * being guarded against is a provider outage. A breaker per model would need
 * N independent failures to notice one dead API.
 */
import type { ModelConfig, ModelEntry } from "@verdict/shared";
import { getModel } from "@verdict/shared";
import { AnthropicAdapter } from "./anthropic.js";
import { OpenAICompatibleAdapter } from "./openai.js";
import { CircuitBreaker } from "../resilience/breaker.js";
import type { ProviderAdapter, ProviderName } from "./types.js";
import type { Env } from "../env.js";

/** Groq speaks the OpenAI Chat Completions protocol at this path. */
const GROQ_BASE_URL = "https://api.groq.com/openai/v1";

export interface ResolvedModel {
  modelId: string;
  entry: ModelEntry;
  adapter: ProviderAdapter;
  breaker: CircuitBreaker;
}

export class ProviderRegistry {
  readonly #adapters = new Map<ProviderName, ProviderAdapter>();
  readonly #breakers = new Map<ProviderName, CircuitBreaker>();
  readonly #config: ModelConfig;

  constructor(
    config: ModelConfig,
    env: Env,
    overrides?: Partial<Record<ProviderName, ProviderAdapter>>,
  ) {
    this.#config = config;

    const register = (name: ProviderName, build: () => ProviderAdapter): void => {
      const adapter = overrides?.[name] ?? build();
      this.#adapters.set(name, adapter);
      this.#breakers.set(
        name,
        new CircuitBreaker(name, {
          failureThreshold: env.BREAKER_FAILURE_THRESHOLD,
          cooldownMs: env.BREAKER_COOLDOWN_MS,
        }),
      );
    };

    if (overrides?.anthropic ?? env.ANTHROPIC_API_KEY !== undefined) {
      register(
        "anthropic",
        () => new AnthropicAdapter(env.ANTHROPIC_API_KEY ?? "", env.ANTHROPIC_BASE_URL),
      );
    }
    if (overrides?.openai ?? env.OPENAI_API_KEY !== undefined) {
      register(
        "openai",
        () => new OpenAICompatibleAdapter("openai", env.OPENAI_API_KEY ?? "", env.OPENAI_BASE_URL),
      );
    }
    if (overrides?.groq ?? env.GROQ_API_KEY !== undefined) {
      register(
        "groq",
        () =>
          new OpenAICompatibleAdapter(
            "groq",
            env.GROQ_API_KEY ?? "",
            env.GROQ_BASE_URL ?? GROQ_BASE_URL,
          ),
      );
    }
  }

  get config(): ModelConfig {
    return this.#config;
  }

  /** True when we hold credentials for the provider that serves this model. */
  isServable(modelId: string): boolean {
    const entry = this.#config.models[modelId];
    return entry !== undefined && this.#adapters.has(entry.provider);
  }

  breakerFor(provider: ProviderName): CircuitBreaker | undefined {
    return this.#breakers.get(provider);
  }

  breakers(): ReadonlyMap<ProviderName, CircuitBreaker> {
    return this.#breakers;
  }

  resolve(modelId: string): ResolvedModel {
    const entry = getModel(this.#config, modelId);
    const adapter = this.#adapters.get(entry.provider);
    const breaker = this.#breakers.get(entry.provider);
    if (!adapter || !breaker) {
      throw new Error(
        `model "${modelId}" needs provider "${entry.provider}", but no API key for it was configured`,
      );
    }
    return { modelId, entry, adapter, breaker };
  }
}
