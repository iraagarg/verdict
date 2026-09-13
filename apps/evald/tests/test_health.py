"""Integration tests for the evald HTTP surface."""

from __future__ import annotations

from fastapi.testclient import TestClient

from evald.main import build_app
from evald.settings import Settings

settings = Settings(
    database_url="postgres://verdict:verdict@localhost:5432/verdict",
    redis_url="redis://localhost:6379",
    anthropic_api_key="sk-ant-test-abc123",
    cost_cap_usd_per_day=5.0,
)
client = TestClient(build_app(settings))


def test_health_does_not_require_a_datastore() -> None:
    # Postgres is not running here. Liveness must still pass.
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert res.json()["service"] == "evald"


def test_ready_reports_dependencies() -> None:
    res = client.get("/ready")
    assert res.status_code == 200
    assert res.json() == {"status": "ready", "dependencies": {}}


def test_unknown_route_is_404() -> None:
    assert client.get("/v1/nope").status_code == 404
