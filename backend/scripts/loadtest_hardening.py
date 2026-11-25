"""Production hardening -- closed-loop load test against a RUNNING server.

Not a production capacity claim: it measures this build on whatever machine
runs it, and the load generator shares that machine. Never point it at
production (it creates sessions directly in Redis for seeded accounts).

    # server under test (production runner, N workers) on the volume DB
    WEB_CONCURRENCY=2 LOG_LEVEL=warning DATABASE_URL=.../lifelink_perf scripts/run_production.sh
    # generator
    DATABASE_URL=.../lifelink_perf python -m scripts.loadtest_hardening \
        --concurrency 1,8,32,64 --seconds 20 --sse 200 --server-pid <uvicorn pid>

For each concurrency level C: C workers loop for `--seconds`, each picking
a weighted-random request from MIX as a random seeded account of the right
role (real endpoint, real session lookup, real SQL). Reports throughput,
p50/p95/p99, error counts, and the server's CPU% and RSS (summed over the
server process tree, from /proc). With `--sse N`, N authenticated event
streams stay open during the whole run, and the number of Redis pub/sub
connections the server holds is reported.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import subprocess
import time

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.db.session import engine
from app.services import session_service

D = "lifelink-demo.example.com"

# (weight, role, method, path) -- read-heavy, like real dashboard traffic.
MIX = [
    (3, "DONOR", "GET", "/donors/me/requests"),
    (3, "DONOR", "GET", "/notifications/unread-count"),
    (2, "DONOR", "GET", "/notifications"),
    (1, "DONOR", "GET", "/emergencies/active"),
    (2, "PATIENT", "GET", "/blood-requests"),
    (1, "PATIENT", "GET", "/notifications/unread-count"),
    (2, "HOSPITAL", "GET", "/hospitals/me/requests"),
    (1, "HOSPITAL", "GET", "/hospitals/me/inventory/components"),
    (1, "HOSPITAL", "GET", "/map/data"),
    (1, "ADMIN", "GET", "/admin/blood-requests"),
    (1, "ADMIN", "GET", "/admin/analytics/overview"),
    (1, "DONOR", "POST", "/notifications/read-all"),
]


def _sessions(per_role: int) -> dict[str, list[str]]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "select id, role from users where is_active and is_email_verified "
            "and role in ('DONOR','PATIENT','HOSPITAL','ADMIN') order by email"
        )).all()
        hosp_with_profile = {r[0] for r in conn.execute(text(
            "select user_id from hospital_profiles"))}
    by_role: dict[str, list] = {}
    for uid, role in rows:
        if role == "HOSPITAL" and uid not in hosp_with_profile:
            continue
        by_role.setdefault(role, []).append(uid)
    out = {}
    for role, ids in by_role.items():
        random.Random(1).shuffle(ids)
        out[role] = [session_service.create_session(u, "127.0.0.1", "loadtest-hardening")
                     for u in ids[:per_role]]
    return out


def _proc_tree(pid: int) -> list[int]:
    out = subprocess.run(["ps", "-o", "pid=", "--ppid", str(pid)], capture_output=True, text=True)
    kids = [int(x) for x in out.stdout.split()]
    pids = [pid]
    for k in kids:
        pids += _proc_tree(k)
    return pids


def _cpu_ticks(pids: list[int]) -> int:
    total = 0
    for p in pids:
        try:
            with open(f"/proc/{p}/stat") as f:
                parts = f.read().rsplit(")", 1)[1].split()
            total += int(parts[11]) + int(parts[12])
        except OSError:
            pass
    return total


def _rss_mb(pids: list[int]) -> float:
    total = 0
    for p in pids:
        try:
            with open(f"/proc/{p}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        total += int(line.split()[1])
        except OSError:
            pass
    return total / 1024


def _redis_pubsub_clients() -> int:
    from app.core.redis import redis_client

    return sum(1 for c in redis_client.client_list() if int(c.get("sub", 0) or 0) > 0
               or int(c.get("psub", 0) or 0) > 0)


async def _sse(base: str, sid: str, stop: asyncio.Event, ready: list[int]) -> None:
    cookies = {settings.session_cookie_name: sid}
    async with httpx.AsyncClient(base_url=base, cookies=cookies, timeout=None) as c:
        async with c.stream("GET", "/events/stream") as r:
            async for line in r.aiter_lines():
                if line.startswith("event: ready"):
                    ready[0] += 1
                if stop.is_set():
                    break


async def _level(base: str, sessions: dict, conc: int, seconds: float, origin: str):
    weighted = [m for m in MIX for _ in range(m[0])]
    lat: list[float] = []
    codes: dict[int, int] = {}
    per_path: dict[str, list[float]] = {}
    deadline = time.monotonic() + seconds
    limits = httpx.Limits(max_connections=conc + 4, max_keepalive_connections=conc + 4)
    async with httpx.AsyncClient(base_url=base, timeout=30, limits=limits,
                                 headers={"Origin": origin}) as client:
        async def worker(seed: int) -> None:
            rng = random.Random(seed)
            while time.monotonic() < deadline:
                _, role, method, path = rng.choice(weighted)
                sid = rng.choice(sessions[role])
                t = time.perf_counter()
                try:
                    r = await client.request(method, path,
                                             cookies={settings.session_cookie_name: sid})
                    code = r.status_code
                except httpx.HTTPError:
                    code = 0
                ms = (time.perf_counter() - t) * 1000
                lat.append(ms)
                per_path.setdefault(f"{method} {path}", []).append(ms)
                codes[code] = codes.get(code, 0) + 1
        await asyncio.gather(*(worker(i) for i in range(conc)))
    return lat, codes, per_path


async def main_async(a) -> None:
    sessions = _sessions(a.users_per_role)
    print(f"sessions: { {k: len(v) for k, v in sessions.items()} }")
    pids = _proc_tree(a.server_pid) if a.server_pid else []
    hz = os.sysconf("SC_CLK_TCK")
    stop = asyncio.Event()
    sse_tasks = []
    if a.sse:
        ready = [0]
        pool = sessions["DONOR"] + sessions["PATIENT"]
        sse_tasks = [asyncio.create_task(_sse(a.base, pool[i % len(pool)], stop, ready))
                     for i in range(a.sse)]
        t = time.monotonic()
        while ready[0] < a.sse and time.monotonic() - t < 60:
            await asyncio.sleep(0.2)
        print(f"SSE streams open: {ready[0]}/{a.sse} in {time.monotonic() - t:.1f}s; "
              f"server Redis pub/sub connections: {_redis_pubsub_clients()}; "
              f"server RSS {_rss_mb(pids):.0f} MB")
    print("| C | requests | throughput (req/s) | p50 ms | p95 ms | p99 ms | non-2xx | "
          "server CPU % (of 1 core) | server RSS MB |")
    print("|---|---|---|---|---|---|---|---|---|")
    paths: list = []
    for conc in [int(x) for x in a.concurrency.split(",")]:
        c0, t0 = _cpu_ticks(pids), time.monotonic()
        lat, codes, per_path = await _level(a.base, sessions, conc, a.seconds, a.origin)
        paths.append((conc, per_path))
        el = time.monotonic() - t0
        cpu = (_cpu_ticks(pids) - c0) / hz / el * 100 if pids else float("nan")
        q = statistics.quantiles(lat, n=100)
        bad = sum(v for k, v in codes.items() if not 200 <= k < 300)
        print(f"| {conc} | {len(lat)} | {len(lat) / el:.1f} | {q[49]:.1f} | {q[94]:.1f} | "
              f"{q[98]:.1f} | {bad} {dict(sorted(codes.items())) if bad else ''} | "
              f"{cpu:.0f} | {_rss_mb(pids):.0f} |")
    if a.per_path:
        for conc, per_path in paths:
            print(f"\nper endpoint at C={conc}: path | n | p50 | p95 | max")
            for path, v in sorted(per_path.items(), key=lambda kv: -statistics.median(kv[1])):
                v = sorted(v)
                print(f"  {path} | {len(v)} | {v[len(v) // 2]:.1f} | "
                      f"{v[max(0, int(len(v) * .95) - 1)]:.1f} | {v[-1]:.1f}")
    if sse_tasks:
        print(f"after run: server Redis pub/sub connections: {_redis_pubsub_clients()}")
        stop.set()
        for t in sse_tasks:
            t.cancel()
        await asyncio.gather(*sse_tasks, return_exceptions=True)


def main() -> None:
    if settings.is_production:
        raise SystemExit("never against production")
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--origin", default=settings.cors_origin_list[0])
    p.add_argument("--concurrency", default="1,8,32")
    p.add_argument("--seconds", type=float, default=20)
    p.add_argument("--users-per-role", type=int, default=40)
    p.add_argument("--sse", type=int, default=0)
    p.add_argument("--server-pid", type=int, default=0)
    p.add_argument("--per-path", action="store_true")
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
