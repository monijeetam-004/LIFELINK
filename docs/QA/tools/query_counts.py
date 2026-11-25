"""Count SQL statements and time per request for the busiest read endpoints.

    DATABASE_URL=…/lifelink_qa REDIS_URL=… SECRET_KEY=… python query_counts.py

Runs in-process (FastAPI TestClient) against a demo-seeded database; each
endpoint is called once to warm up, then measured once. Read-only.
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient
from sqlalchemy import event

from app.db.session import engine
from app.main import app

ORIGIN = {"Origin": "http://localhost:5173"}
PW = "LifeLink-Demo-2026!"  # documented, non-secret demo seed password
count = {"n": 0}
event.listen(engine, "before_cursor_execute", lambda *a, **k: count.__setitem__("n", count["n"] + 1))


def login(role: str) -> TestClient:
    c = TestClient(app, headers=ORIGIN)
    r = c.post("/auth/login", json={"email": f"demo.{role}@lifelink-demo.example.com", "password": PW})
    r.raise_for_status()
    return c


h, a, d = login("hospital"), login("admin"), login("donor")
for client, path in [
    (a, "/admin/blood-requests"), (h, "/map/data"), (d, "/donors/me/requests"),
    (h, "/hospitals/me/requests"), (a, "/admin/analytics/overview"),
    (h, "/hospitals/me/inventory/components"), (h, "/hospitals/me/units"),
]:
    client.get(path)
    count["n"] = 0
    t = time.perf_counter()
    r = client.get(path)
    ms = (time.perf_counter() - t) * 1000
    print(f"{path}: status {r.status_code}, SQL statements {count['n']}, {ms:.1f} ms, body {len(r.content)} B")
