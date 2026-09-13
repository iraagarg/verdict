"""Content-addressed on-disk response cache.

This is what makes `make bench` reproducible at $0 and what makes a crashed run
resumable with no bookkeeping: the cache key IS the identity of the work, so a
re-run simply finds the answer already there.

The key covers everything that could change the output — model, the exact
messages, every sampling parameter, and the replicate index. It deliberately
does NOT cover wall-clock time, run id or corpus version, because none of those
change what the model would say.

D-009: `temperature` cannot be pinned on Claude Opus 5 or Sonnet 5 (HTTP 400),
so replicate_idx is part of the key. Replicate 0 and replicate 1 are two
genuinely different samples and must not collide.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def cache_key(
    model: str,
    messages: list[dict[str, str]],
    params: dict[str, Any],
    replicate_idx: int,
) -> str:
    payload = {
        "model": model,
        "messages": messages,
        # sort_keys makes the key independent of dict insertion order, which
        # would otherwise produce cache misses that look like nondeterminism.
        "params": params,
        "replicate_idx": replicate_idx,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class CacheStats:
    hits: int = 0
    misses: int = 0
    writes: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return 0.0 if total == 0 else self.hits / total


class ResponseCache:
    """Sharded directory of JSON files, one per cache key."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.stats = CacheStats()

    def path_for(self, key: str) -> Path:
        # Two-character shard: a flat directory of 50k files is slow to list and
        # unpleasant on some filesystems.
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self.path_for(key)
        if not path.is_file():
            self.stats.misses += 1
            return None
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A truncated file from a hard kill mid-write. Treat as a miss and
            # let it be rewritten rather than failing the whole run.
            self.stats.misses += 1
            return None
        self.stats.hits += 1
        return data

    def put(self, key: str, value: dict[str, Any]) -> None:
        """Atomic write: a crash must never leave a half-written cache entry."""
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(value, fh, ensure_ascii=False)
            os.replace(tmp, path)  # atomic on POSIX
            self.stats.writes += 1
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def __contains__(self, key: str) -> bool:
        return self.path_for(key).is_file()
