"""Embeddings, run locally.

`BAAI/bge-small-en-v1.5` via fastembed: ONNX on CPU, no API key, no network
after the first model download, and free. Chosen in D-049 after D-044's OpenAI
model turned out to need an account we do not have.

Deterministic, which matters more here than it might elsewhere: the similarity
threshold is fitted against these vectors, so if the embeddings moved between
the fit and the deployment the threshold would silently stop meaning what it
was measured to mean.

Cached on disk by content hash, so re-running a calibration costs nothing and
CI never re-embeds.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Protocol

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_DIMENSIONS = 384


class Embedder(Protocol):
    """The seam tests substitute. Real embedding in a unit test would be slow and pointless."""

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...

    @property
    def model_name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...


class LocalEmbedder:
    def __init__(
        self, model_name: str = DEFAULT_MODEL, dimensions: int = DEFAULT_DIMENSIONS
    ) -> None:
        self._model_name = model_name
        self._dimensions = dimensions
        self._model: Any = None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _load(self) -> Any:
        if self._model is None:
            # Imported lazily: fastembed pulls onnxruntime, and nothing that
            # merely reads a calibration artifact should pay for that.
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self._model_name)
        return self._model

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = [[float(x) for x in v] for v in self._load().embed(list(texts))]
        for v in vectors:
            if len(v) != self._dimensions:
                raise ValueError(
                    f"{self._model_name} returned {len(v)} dimensions, but config and the "
                    f"database schema expect {self._dimensions}. These must agree or every "
                    f"stored vector is unusable."
                )
        return vectors


def text_hash(text: str, model_name: str) -> str:
    """Cache key. Includes the model: vectors from different models never mix."""
    return hashlib.sha256(f"{model_name}\x00{text}".encode()).hexdigest()


class EmbeddingCache:
    """Content-addressed vector cache. Same discipline as the replay cache (D-009)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.hits = 0
        self.misses = 0

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> list[float] | None:
        path = self._path(key)
        if not path.is_file():
            self.misses += 1
            return None
        try:
            data: list[float] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.misses += 1  # truncated by a hard kill; treat as absent
            return None
        self.hits += 1
        return data

    def put(self, key: str, vector: list[float]) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(vector, fh)
            os.replace(tmp, path)  # atomic
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def embed_all(
    texts: dict[str, str],
    embedder: Embedder,
    cache: EmbeddingCache | None = None,
    batch_size: int = 64,
    on_progress: Any = None,
) -> dict[str, list[float]]:
    """Embed a {key: text} mapping, reusing anything already cached."""
    out: dict[str, list[float]] = {}
    pending: list[tuple[str, str, str]] = []  # (key, text, cache_key)

    for key, text in texts.items():
        ck = text_hash(text, embedder.model_name)
        cached = cache.get(ck) if cache else None
        if cached is not None:
            out[key] = cached
        else:
            pending.append((key, text, ck))

    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        vectors = embedder.embed_texts([t for _, t, _ in batch])
        for (key, _, ck), vector in zip(batch, vectors, strict=True):
            out[key] = vector
            if cache:
                cache.put(ck, vector)
        if on_progress:
            on_progress(min(start + batch_size, len(pending)), len(pending))

    return out


def batched(items: Iterable[Any], size: int) -> Iterable[list[Any]]:
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
