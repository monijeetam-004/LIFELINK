from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ok", "db": True, "redis": True}


def test_root(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["service"] == "lifelink-api"


# --- Phase 28: liveness/readiness split -----------------------------------


def test_liveness_never_touches_a_dependency(client: TestClient) -> None:
    """/health/live must answer instantly and unconditionally -- it exists
    specifically so a container orchestrator never restarts a healthy
    process just because a downstream dependency is slow/unreachable (that
    is what /health/ready is for)."""
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}


def test_readiness_reports_required_and_optional_dependencies(client: TestClient) -> None:
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["required"] == {"db": True, "redis": True}
    # Optional dependencies are always present and informational, never
    # required for a 200 -- see the endpoint's own docstring.
    assert set(body["optional"].keys()) == {"neo4j", "storage", "email"}
    assert "configured" in body["optional"]["neo4j"]
    assert body["optional"]["storage"]["backend"] in {"s3", "local"}
    assert isinstance(body["optional"]["email"]["authenticated"], bool)


def test_readiness_503s_only_when_a_required_dependency_is_down(
    client: TestClient, monkeypatch
) -> None:
    """An optional dependency being unset/unreachable must never flip
    readiness to failed -- only db/redis (the two hard, no-fallback
    dependencies) can do that. Simulated by monkeypatching the DB check to
    fail, the same technique the equivalent /health test elsewhere in this
    suite would use, without touching any real Neo4j/storage/email state
    (all of which are correctly absent/default in the test environment
    already, and the endpoint still reports 200/ready for them)."""
    import app.api.routes.health as health_module

    monkeypatch.setattr(health_module, "_check_db", lambda: False)
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["required"]["db"] is False
