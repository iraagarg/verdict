"""Test fixtures.

`Settings` reads the process environment, so a developer's shell or a CI job
that exports DATABASE_URL would silently satisfy a field a test is trying to
prove is required. Every test therefore runs against a cleared environment and
supplies its inputs explicitly.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

#: Every variable Settings reads. Cleared before each test.
_MANAGED = (
    "NODE_ENV",
    "LOG_LEVEL",
    "EVALD_PORT",
    "EVALD_HOST",
    "DATABASE_URL",
    "REDIS_URL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "COST_CAP_USD_PER_DAY",
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in _MANAGED:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    yield


def test_environment_is_actually_cleared() -> None:
    """Guards the fixture itself -- a broken fixture would make tests pass wrongly."""
    assert not any(os.environ.get(name) for name in _MANAGED)
