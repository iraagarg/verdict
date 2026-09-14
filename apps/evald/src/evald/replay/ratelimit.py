"""Client-side rate limiting.

The first real pilot run discovered Groq's limits the expensive way: it fired as
fast as it could, collected 429s, and (before the gateway fix) tripped the
circuit breaker into refusing 339 queued requests.

Retrying a 429 is correct but wasteful — every one is a round trip that buys
nothing. Knowing the provider's published limit and staying under it is strictly
better. This is a token bucket: it refills continuously at `rate` per minute up
to `burst`, and `acquire()` blocks until a token is free.

A bucket, not a fixed sleep, because bursts are fine as long as the average
holds — which is exactly how published RPM limits work.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class RateLimiter:
    """Token bucket. Thread-safe; shared across the runner's worker pool."""

    #: Requests per minute. The provider's published limit.
    rpm: int
    #: How many requests may go out back-to-back. Defaults to a tenth of rpm.
    burst: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _tokens: float = field(default=0.0, repr=False)
    _last: float = field(default=0.0, repr=False)
    #: Injected in tests so they do not sleep.
    _now: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.rpm <= 0:
            raise ValueError("rpm must be positive")
        if self.burst <= 0:
            self.burst = max(1, self.rpm // 10)
        self._tokens = float(self.burst)
        self._last = time.monotonic()

    @property
    def _per_second(self) -> float:
        return self.rpm / 60.0

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(float(self.burst), self._tokens + elapsed * self._per_second)

    def try_acquire(self, now: float | None = None) -> float:
        """Take a token if one is free. Returns 0.0, or the seconds to wait."""
        with self._lock:
            self._refill(time.monotonic() if now is None else now)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return 0.0
            return (1.0 - self._tokens) / self._per_second

    def acquire(self) -> None:
        """Block until a token is available."""
        while True:
            wait = self.try_acquire()
            if wait <= 0.0:
                return
            time.sleep(wait)


class NullRateLimiter:
    """No pacing. The default, for providers with no published limit worth honouring."""

    def acquire(self) -> None:
        return

    def try_acquire(self, now: float | None = None) -> float:
        return 0.0
