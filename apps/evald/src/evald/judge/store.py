"""Read model outputs back out of the replay cache.

The replay runner is content-addressed, so the generations it produced are
already on disk keyed by (model, messages, params, replicate). Recomputing that
key is enough to fetch any output — no second copy, no export step, and no risk
of the judge scoring text that differs from what was actually replayed.
"""

from __future__ import annotations

from dataclasses import dataclass

from evald.corpus.schema import CorpusItem
from evald.models_config import ModelConfig
from evald.replay.cache import ResponseCache, cache_key
from evald.replay.runner import request_params


class MissingGenerationError(KeyError):
    """No cached output for this (item, model). Replay it before judging."""


@dataclass(frozen=True, slots=True)
class Generation:
    slug: str
    model: str
    text: str
    finish_reason: str


class GenerationStore:
    def __init__(
        self,
        cache: ResponseCache,
        config: ModelConfig,
        max_output_tokens: int,
        replicate_idx: int = 0,
    ) -> None:
        self._cache = cache
        self._config = config
        self._max = max_output_tokens
        self._replicate = replicate_idx

    def get(self, item: CorpusItem, model: str) -> Generation:
        params = request_params(model, self._config, self._max)
        key = cache_key(model, item.messages, params, self._replicate)
        entry = self._cache.get(key)
        if entry is None:
            raise MissingGenerationError(
                f"no cached generation for {item.slug!r} on {model!r}. "
                f"Run `evald replay run` for this model first."
            )
        return Generation(
            slug=item.slug,
            model=model,
            text=str(entry["text"]),
            finish_reason=str(entry.get("finish_reason", "stop")),
        )

    def has(self, item: CorpusItem, model: str) -> bool:
        params = request_params(model, self._config, self._max)
        return cache_key(model, item.messages, params, self._replicate) in self._cache

    def available_models(self, item: CorpusItem, models: list[str]) -> list[str]:
        return [m for m in models if self.has(item, m)]
