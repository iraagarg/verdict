"""evald HTTP surface.

P0 scaffold: health and readiness only. The replay runner (P2), judge (P3),
statistics (P4) and policy fitting (P5) land in their own phases.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from fastapi import FastAPI
from pydantic import ValidationError

from evald.settings import Settings

GIT_SHA = os.environ.get("GIT_SHA", "unknown")


def build_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="evald", version="0.0.0", docs_url="/docs")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Liveness. Must not touch a datastore."""
        return {"status": "ok", "service": "evald", "git_sha": GIT_SHA}

    @app.get("/ready")
    async def ready() -> dict[str, Any]:
        """Readiness. Dependency checks arrive with the datastore clients in P2."""
        return {"status": "ready", "dependencies": {}}

    return app


def load_settings_or_exit() -> Settings:
    """Validate the environment, exiting non-zero before the port is bound."""
    try:
        return Settings()  # type: ignore[call-arg]  # values come from the environment
    except ValidationError as err:
        print(f"[evald] environment validation failed; refusing to start:\n{err}", file=sys.stderr)
        raise SystemExit(1) from err


def create_app() -> FastAPI:
    """Entrypoint for `uvicorn evald.main:create_app --factory`.

    Deliberately NOT a module-level `app = build_app(...)`. Constructing the app
    at import time would mean that merely importing this module validates the
    environment and can call `SystemExit` -- which makes the module impossible
    to import from a test, and turns any import-time mistake into a confusing
    stack trace instead of the clear message in `load_settings_or_exit`.
    """
    return build_app(load_settings_or_exit())
