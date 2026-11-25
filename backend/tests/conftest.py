"""Test bootstrap.

Requires a disposable PostgreSQL database and a Redis instance. Configure via:
  TEST_DATABASE_URL   (default: .../lifelink_test)
  REDIS_URL           (default: redis://localhost:6379/15  -- db 15 is FLUSHED)

Run:  ENVIRONMENT=test pytest
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("RQ_SYNC", "true")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
# Every TestClient request comes from the single address "testclient", so the
# per-IP login limiter (10/300 s by default) would trip inside any test that
# signs in more than ten users. The suite therefore uses the same relaxation
# the project's documented local/E2E .env uses (RL_LOGIN_IP=[200,300]); the
# limiter itself is exercised at a strict value by
# tests/test_gap_rate_limit.py, and the per-ACCOUNT limit stays at default.
os.environ.setdefault("RL_LOGIN_IP", "[200,300]")
# Certificates: force the local object store (no MinIO needed for unit tests).
# These override any values from a local .env file.
os.environ["S3_ENDPOINT_URL"] = ""
os.environ["S3_ACCESS_KEY"] = ""
os.environ["S3_SECRET_KEY"] = ""
os.environ.setdefault(
    "STORAGE_LOCAL_DIR", tempfile.mkdtemp(prefix="lifelink-test-storage-")
)
os.environ.setdefault(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://lifelink:lifelink@localhost:5432/lifelink_test",
)
# The app reads DATABASE_URL; point it at the test database before app import.
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import models  # noqa: E402,F401  (register tables)
from app.core import audit_guard  # noqa: E402
from app.core.redis import redis_client  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services import email_service  # noqa: E402

# QA evidence: with QA_ROUTE_LOG=<path>, record every (method, route
# template, status) the suite actually exercises, so route coverage is
# measured from real requests rather than inferred from source text.
_ROUTE_HITS: dict[tuple[str, str, int], int] = {}
if os.environ.get("QA_ROUTE_LOG"):
    from starlette.requests import Request  # noqa: E402

    @app.middleware("http")
    async def _qa_route_recorder(request: Request, call_next):
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", None) or "<unmatched>"
        key = (request.method, path, response.status_code)
        _ROUTE_HITS[key] = _ROUTE_HITS.get(key, 0) + 1
        return response


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    target = os.environ.get("QA_ROUTE_LOG")
    if target and _ROUTE_HITS:
        import json

        with open(target, "w") as fh:
            json.dump([{"method": m, "path": p, "status": s, "count": c}
                       for (m, p, s), c in sorted(_ROUTE_HITS.items())], fh, indent=0)


@pytest.fixture(scope="session", autouse=True)
def _schema() -> object:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    from app.services import realtime_service

    realtime_service.flush()
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean() -> object:
    # Hardening: real-time publication runs on a background thread; let it
    # finish whatever the previous test committed before truncating, so no
    # event from one test is published into the next.
    from app.services import realtime_service

    realtime_service.flush()
    with engine.begin() as conn:
        # audit_logs is append-only (app.core.audit_guard); the test-suite
        # reset acts as the table owner and lifts the guard for this one
        # transaction only.
        conn.execute(text(audit_guard.DISABLE_SQL))
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE'))
        conn.execute(text(audit_guard.ENABLE_SQL))
    redis_client.flushdb()
    email_service.sent_messages.clear()
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, base_url="http://testserver")


@pytest.fixture
def mailbox() -> list[dict]:
    return email_service.sent_messages


@pytest.fixture
def db_session():
    """A raw SQLAlchemy session for tests that exercise a service function
    directly (bypassing the HTTP layer) -- e.g. tests/test_sms.py. Every
    other test file in this suite talks to the API only via `auth_client`
    TestClients; this fixture is for the handful of cases that need to set
    up state (or inspect a row) a public endpoint doesn't expose."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
