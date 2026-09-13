"""Hard spend cap.

A runaway replay run is the single most expensive mistake available in this
project, so the cap is enforced in two places:

  * `reserve()` before a call, using a conservative worst-case estimate, so we
    never start a request that could take us over.
  * `record()` after it, with the provider's actual reported cost.

`reserve` is intentionally pessimistic: it assumes the model emits its full
max_output_tokens. Being wrong in the cheap direction only means stopping a
little early; being wrong the other way means an overspend the user never
authorised.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


class BudgetExceededError(RuntimeError):
    """Raised when the run would exceed its authorised spend."""

    def __init__(self, spent_usd: float, cap_usd: float) -> None:
        super().__init__(
            f"spend cap reached: ${spent_usd:.4f} committed against a ${cap_usd:.4f} cap; "
            f"aborting run"
        )
        self.spent_usd = spent_usd
        self.cap_usd = cap_usd


@dataclass(slots=True)
class Budget:
    cap_usd: float
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _actual_nano: int = 0
    _reserved_nano: int = 0

    def __post_init__(self) -> None:
        if self.cap_usd <= 0:
            raise ValueError("cap_usd must be positive")

    @property
    def spent_usd(self) -> float:
        return self._actual_nano / 1e9

    @property
    def committed_usd(self) -> float:
        """Actual spend plus outstanding reservations."""
        return (self._actual_nano + self._reserved_nano) / 1e9

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.committed_usd)

    def reserve(self, worst_case_nano: int) -> None:
        """Claim headroom for one call. Raises rather than letting it proceed."""
        with self._lock:
            projected = self._actual_nano + self._reserved_nano + worst_case_nano
            if projected > self.cap_usd * 1e9:
                raise BudgetExceededError(projected / 1e9, self.cap_usd)
            self._reserved_nano += worst_case_nano

    def settle(self, worst_case_nano: int, actual_nano: int) -> None:
        """Release a reservation and record what the call really cost."""
        with self._lock:
            self._reserved_nano = max(0, self._reserved_nano - worst_case_nano)
            self._actual_nano += actual_nano

    def release(self, worst_case_nano: int) -> None:
        """Release a reservation for a call that never happened (cache hit, error)."""
        with self._lock:
            self._reserved_nano = max(0, self._reserved_nano - worst_case_nano)
