"""Gateway client.

Replay goes through the P1 gateway rather than straight to providers. That
dogfoods the gateway on every benchmark run, and means traces, cost accounting,
retry, the circuit breaker and parameter normalisation are all the tested code
paths rather than a second implementation that could disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from evald.cost import Usage


class GatewayError(RuntimeError):
    def __init__(self, status: int, body: str, retryable: bool) -> None:
        super().__init__(f"gateway returned {status}: {body[:300]}")
        self.status = status
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class Generation:
    text: str
    usage: Usage
    finish_reason: str
    model_served: str
    provider: str
    cost_usd: float
    usage_is_final: bool
    request_id: str
    latency_ms: int


class GatewayClient:
    def __init__(self, base_url: str, timeout_s: float = 180.0) -> None:
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GatewayClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def health(self) -> bool:
        try:
            return self._client.get("/health").status_code == 200
        except httpx.HTTPError:
            return False

    def complete(
        self,
        model: str,
        messages: list[dict[str, str]],
        params: dict[str, Any],
    ) -> Generation:
        """One non-streaming completion. Replay has no use for streaming."""
        payload: dict[str, Any] = {"model": model, "messages": messages, **params, "stream": False}

        try:
            res = self._client.post("/v1/chat/completions", json=payload)
        except httpx.HTTPError as err:
            raise GatewayError(0, str(err), retryable=True) from err

        if res.status_code != 200:
            # 429 and 5xx are worth another attempt; a 400 will fail identically.
            retryable = res.status_code == 429 or res.status_code >= 500
            raise GatewayError(res.status_code, res.text, retryable=retryable)

        body = res.json()
        choice = body["choices"][0]
        usage = body.get("usage") or {}

        return Generation(
            text=choice["message"]["content"],
            usage=Usage(
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
            ),
            finish_reason=str(choice.get("finish_reason") or "stop"),
            model_served=res.headers.get("x-verdict-model-served", model),
            provider=res.headers.get("x-verdict-provider", "unknown"),
            cost_usd=float(res.headers.get("x-verdict-cost-usd", "0") or 0),
            # The gateway tells us whether the provider confirmed its counts.
            # An unconfirmed row is a floor, not a fact, and P4 must exclude it.
            usage_is_final=res.headers.get("x-verdict-usage-final", "false") == "true",
            request_id=res.headers.get("x-verdict-request-id", ""),
            latency_ms=int(res.elapsed.total_seconds() * 1000),
        )
