"""Phase 27 scalability load-test harness.

Reproduces the Phase 24/25 methodology exactly (real httpx+asyncio against
the real running dev backend, real Postgres, no mocking) and extends the
concurrency matrix with c=2 as this phase's brief requires. Sessions for
the 127 synthetic loadtest.*@example.com accounts (seeded in Phase 24,
still present in the real dev DB) are pre-provisioned directly via
session_service.create_session() -- this bypasses only the login
endpoint's IP rate limiter (a single test machine's one source IP would
otherwise trip it after a handful of requests, a limitation of
single-machine load testing, not a backend bug); every *measured* request
in every scenario below goes through the real endpoint, real session
lookup, and real DB queries.

Usage: python3 scripts/phase27_loadtest.py <concurrency> [scenario,...]
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
import uuid as uuidlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import psycopg  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services import session_service  # noqa: E402

BASE_URL = "http://localhost:8000"
DB_URL = "postgresql://lifelink:lifelink@localhost:5432/lifelink"


def get_loadtest_users() -> dict[str, list[dict]]:
    conn = psycopg.connect(DB_URL)
    cur = conn.cursor()
    cur.execute(
        "select id, email, role from users where email like 'loadtest.%' order by email"
    )
    rows = cur.fetchall()
    conn.close()
    by_role: dict[str, list[dict]] = {}
    for uid, email, role in rows:
        by_role.setdefault(role, []).append({"id": uid, "email": email})
    return by_role


def provision_sessions(users: list[dict]) -> list[str]:
    sids = []
    for u in users:
        sid = session_service.create_session(u["id"], "127.0.0.1", "phase27-loadtest")
        sids.append(sid)
    return sids


def cookie_header(sid: str) -> dict[str, str]:
    return {"Cookie": f"{settings.session_cookie_name}={sid}"}


async def run_requests(
    client: httpx.AsyncClient, sids: list[str], concurrency: int, request_fn
):
    """Fires `concurrency` requests concurrently (one per sid, cycling),
    all released at once via an asyncio.Event -- the async analogue of the
    Barrier-based concurrency tests elsewhere in this codebase."""
    start_gate = asyncio.Event()
    results = []

    async def one(sid: str):
        await start_gate.wait()
        t0 = time.monotonic()
        try:
            resp = await request_fn(client, sid)
            dt = time.monotonic() - t0
            results.append((resp.status_code, dt, None))
        except Exception as e:  # noqa: BLE001
            dt = time.monotonic() - t0
            results.append((0, dt, str(e)))

    tasks = [asyncio.create_task(one(sids[i % len(sids)])) for i in range(concurrency)]
    await asyncio.sleep(0.05)
    start_gate.set()
    await asyncio.gather(*tasks)
    return results


def summarize(name: str, results: list[tuple[int, float, str | None]]) -> dict:
    durations = [r[1] for r in results]
    successes = [r for r in results if 200 <= r[0] < 300]
    timeouts = [r for r in results if r[0] == 0]
    errors_5xx = [r for r in results if r[0] >= 500]
    errors_4xx = [r for r in results if 400 <= r[0] < 500]
    summary = {
        "scenario": name,
        "n": len(results),
        "success": len(successes),
        "timeouts": len(timeouts),
        "5xx": len(errors_5xx),
        "4xx": len(errors_4xx),
        "avg_ms": round(statistics.mean(durations) * 1000, 1) if durations else None,
        "p50_ms": round(statistics.median(durations) * 1000, 1) if durations else None,
        "p95_ms": round(
            statistics.quantiles(durations, n=20)[18] * 1000, 1
        )
        if len(durations) >= 5
        else (round(max(durations) * 1000, 1) if durations else None),
        "max_ms": round(max(durations) * 1000, 1) if durations else None,
    }
    print(json.dumps(summary))
    return summary


SCENARIOS = {}


def scenario(name):
    def deco(fn):
        SCENARIOS[name] = fn
        return fn

    return deco


@scenario("A_patient_browsing")
async def scenario_a(client, sid):
    return await client.get(f"{BASE_URL}/patients/me", headers=cookie_header(sid))


@scenario("B_blood_request_create")
async def scenario_b(client, sid):
    key = str(uuidlib.uuid4())
    return await client.post(
        f"{BASE_URL}/blood-requests",
        json={
            "blood_type": "O_NEG",
            "units_needed": 1,
            "urgency": "ROUTINE",
            "idempotency_key": key,
        },
        headers=cookie_header(sid),
    )


@scenario("C_donor_dashboard")
async def scenario_c(client, sid):
    return await client.get(f"{BASE_URL}/donors/me", headers=cookie_header(sid))


@scenario("D_hospital_inventory")
async def scenario_d(client, sid):
    return await client.get(f"{BASE_URL}/hospitals/me/inventory", headers=cookie_header(sid))


@scenario("E_admin_search")
async def scenario_e(client, sid):
    return await client.get(f"{BASE_URL}/admin/users?limit=20", headers=cookie_header(sid))


@scenario("F_emergency_create")
async def scenario_f(client, sid):
    key = str(uuidlib.uuid4())
    return await client.post(
        f"{BASE_URL}/emergencies",
        json={
            "blood_type": "O_NEG",
            "latitude": 12.9716,
            "longitude": 77.5946,
            "idempotency_key": key,
        },
        headers=cookie_header(sid),
    )


ROLE_FOR_SCENARIO = {
    "A_patient_browsing": "PATIENT",
    "B_blood_request_create": "PATIENT",
    "C_donor_dashboard": "DONOR",
    "D_hospital_inventory": "HOSPITAL",
    "E_admin_search": "ADMIN",
    "F_emergency_create": "PATIENT",
}


async def run_mixed(client, sids_by_role, concurrency_per_scenario, client_timeout):
    """Scenario H -- the one Phase 24/25 explicitly left unmeasured: fires
    ALL SIX scenarios' concurrent bursts at once (not one scenario fully
    drained before the next starts), so reads and writes really do contend
    for the same thread pool / DB pool simultaneously, the way a real
    mixed-traffic moment would. Returns one combined result list per
    scenario name."""
    start_gate = asyncio.Event()
    per_scenario_results: dict[str, list] = {name: [] for name in SCENARIOS}

    async def one(name, fn, sid):
        await start_gate.wait()
        t0 = time.monotonic()
        try:
            resp = await fn(client, sid)
            dt = time.monotonic() - t0
            per_scenario_results[name].append((resp.status_code, dt, None))
        except Exception as e:  # noqa: BLE001
            dt = time.monotonic() - t0
            per_scenario_results[name].append((0, dt, str(e)))

    tasks = []
    for name, fn in SCENARIOS.items():
        role = ROLE_FOR_SCENARIO[name]
        sids = sids_by_role.get(role, [])
        if not sids:
            continue
        for i in range(concurrency_per_scenario):
            tasks.append(asyncio.create_task(one(name, fn, sids[i % len(sids)])))

    await asyncio.sleep(0.05)
    start_gate.set()
    await asyncio.gather(*tasks)
    return per_scenario_results


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "single"

    if mode == "mixed":
        # python3 phase27_loadtest.py mixed <concurrency_per_scenario> [timeout_s]
        concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        client_timeout = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0
        users_by_role = get_loadtest_users()
        sids_by_role = {role: provision_sessions(users) for role, users in users_by_role.items()}
        limits = httpx.Limits(max_connections=400, max_keepalive_connections=400)
        async with httpx.AsyncClient(timeout=client_timeout, limits=limits) as client:
            per_scenario = await run_mixed(client, sids_by_role, concurrency, client_timeout)
            all_summaries = []
            for name, results in per_scenario.items():
                if results:
                    all_summaries.append(
                        summarize(f"{name}@mixed_c={concurrency}(x6 scenarios)", results)
                    )
        print("---DONE---")
        return all_summaries

    # single-scenario mode (Phase 24/25 methodology, unchanged)
    concurrency = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    only = sys.argv[2].split(",") if len(sys.argv) > 2 else list(SCENARIOS.keys())
    client_timeout = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0

    users_by_role = get_loadtest_users()
    sids_by_role = {role: provision_sessions(users) for role, users in users_by_role.items()}

    limits = httpx.Limits(max_connections=200, max_keepalive_connections=200)
    async with httpx.AsyncClient(timeout=client_timeout, limits=limits) as client:
        all_summaries = []
        for name in only:
            fn = SCENARIOS[name]
            role = ROLE_FOR_SCENARIO[name]
            sids = sids_by_role.get(role, [])
            if not sids:
                print(f"SKIP {name}: no seeded {role} accounts")
                continue
            results = await run_requests(client, sids, concurrency, fn)
            all_summaries.append(summarize(f"{name}@c={concurrency}", results))
    print("---DONE---")
    return all_summaries


if __name__ == "__main__":
    asyncio.run(main())
