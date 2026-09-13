/**
 * Incremental token and cost accounting.
 *
 * Two properties this exists to guarantee:
 *
 *  1. **Exact arithmetic.** Prices are converted to integer nano-USD per token
 *     and all accumulation is integer. A long stream updates the meter on every
 *     usage event; floating-point accumulation would drift, and the drift would
 *     land in a column that feeds every cost number in the project.
 *
 *  2. **No fabricated numbers.** The meter only ever records usage a provider
 *     reported. It never estimates from character counts or a local tokenizer.
 *     `isFinal` says whether the provider has confirmed the totals; a trace
 *     written with `isFinal: false` is explicitly marked as unconfirmed rather
 *     than silently presented as truth. DECISIONS.md D-017.
 *
 * Usage values are ABSOLUTE, not deltas. Anthropic reports input tokens once at
 * `message_start` and a cumulative output count on every `message_delta`;
 * OpenAI and Groq report totals once in a final usage chunk. Treating both as
 * absolute observations makes one code path correct for all three.
 */
import { nanoUsdPerToken, nanoUsdToUsd, type ModelEntry } from "@verdict/shared";

export interface TokenUsage {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
}

export interface CostSnapshot extends TokenUsage {
  /** Total cost in integer nano-USD. Exact. */
  costNanoUsd: number;
  /** `costNanoUsd` expressed in USD, rounded to the 8 decimals the schema stores. */
  costUsd: number;
  /** True once the provider has reported its final usage for this request. */
  isFinal: boolean;
}

export class CostAccountingError extends Error {
  override readonly name = "CostAccountingError";
}

const ZERO: TokenUsage = {
  inputTokens: 0,
  outputTokens: 0,
  cacheReadTokens: 0,
  cacheWriteTokens: 0,
};

export class CostMeter {
  readonly #modelId: string;
  readonly #inputNano: number;
  readonly #outputNano: number;
  readonly #cacheReadNano: number | null;
  readonly #cacheWriteNano: number | null;

  #usage: TokenUsage = { ...ZERO };
  #final = false;

  constructor(modelId: string, model: ModelEntry) {
    this.#modelId = modelId;
    this.#inputNano = nanoUsdPerToken(model.pricing.input);
    this.#outputNano = nanoUsdPerToken(model.pricing.output);
    this.#cacheReadNano =
      model.pricing.cache_read === null ? null : nanoUsdPerToken(model.pricing.cache_read);
    this.#cacheWriteNano =
      model.pricing.cache_write === null ? null : nanoUsdPerToken(model.pricing.cache_write);
  }

  /**
   * Record an absolute usage observation from the provider. Only the fields
   * present are updated, so a `message_delta` carrying just `output_tokens`
   * leaves the input count alone.
   */
  observe(usage: Partial<TokenUsage>): void {
    for (const [key, value] of Object.entries(usage) as [keyof TokenUsage, number | undefined][]) {
      if (value === undefined) continue;
      if (!Number.isInteger(value) || value < 0) {
        throw new CostAccountingError(
          `provider reported a non-integer or negative ${key}: ${String(value)}`,
        );
      }
      this.#usage[key] = value;
    }
  }

  /** Mark the provider's totals as final. Idempotent. */
  markFinal(): void {
    this.#final = true;
  }

  get isFinal(): boolean {
    return this.#final;
  }

  snapshot(): CostSnapshot {
    const nano =
      this.#usage.inputTokens * this.#inputNano +
      this.#usage.outputTokens * this.#outputNano +
      this.#usage.cacheReadTokens * this.#priceFor("cache_read", this.#cacheReadNano) +
      this.#usage.cacheWriteTokens * this.#priceFor("cache_write", this.#cacheWriteNano);

    return {
      ...this.#usage,
      costNanoUsd: nano,
      costUsd: nanoUsdToUsd(nano),
      isFinal: this.#final,
    };
  }

  /**
   * A null price means "not yet verified" — never zero. If the provider
   * actually billed us for cached tokens and we have no verified rate, the
   * honest response is to fail rather than to under-report the cost.
   */
  #priceFor(field: "cache_read" | "cache_write", nano: number | null): number {
    const tokens =
      field === "cache_read" ? this.#usage.cacheReadTokens : this.#usage.cacheWriteTokens;
    if (tokens === 0) return 0;
    if (nano === null) {
      throw new CostAccountingError(
        `${this.#modelId} reported ${tokens} ${field} tokens but config/models.yaml has no verified ` +
          `${field} price. Refusing to record an under-counted cost; verify the rate and set it.`,
      );
    }
    return nano;
  }
}
