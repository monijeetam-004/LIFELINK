"""Shortlist endpoint latency against a RUNNING API on the demo database.

    python shortlist_latency.py --api http://127.0.0.1:8500 --origin http://localhost:5173 \
        --warmup 5 --reps 50 --out shortlist_latency.json

Signs in as the synthetic demo hospital B, files ONE throwaway blood request
(A+, 2 units, note "[DEMO] latency"), then times GET /blood-requests/{id}/shortlist
for each ordering mode: `--warmup` untimed calls, then `--reps` timed calls,
sequentially. The request is cancelled at the end. No donor is contacted.
End-to-end HTTP time on the machine that runs both client and server; it is a
sandbox observation, not a capacity figure.
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import time

import httpx

MODES = ["UNIFIED_PRIORITY", "NEAREST", "TIME_AWARE", "TIME_RESILIENCE", "EVENT_DRIVEN"]


def pct(xs, p):
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, round(p / 100 * (len(xs) - 1))))
    return xs[k]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--origin", default="http://localhost:5173")
    ap.add_argument("--email", default="shortlist.hospital.b@lifelink-demo.example.com")
    ap.add_argument("--password", default="LifeLink-Demo-2026!")  # documented demo account
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--reps", type=int, default=50)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    c = httpx.Client(base_url=a.api, headers={"Origin": a.origin}, timeout=60)
    r = c.post("/auth/login", json={"email": a.email, "password": a.password})
    r.raise_for_status()
    rid = c.post("/blood-requests", json={"blood_type": "A_POS", "units_needed": 2,
                                          "urgency": "URGENT", "note": "[DEMO] latency"}).json()["id"]
    out = {"machine": {"cpus": __import__("os").cpu_count(), "python": platform.python_version()},
           "warmup": a.warmup, "reps": a.reps, "modes": {}}
    try:
        for mode in MODES:
            for _ in range(a.warmup):
                c.get(f"/blood-requests/{rid}/shortlist", params={"strategy": mode}).raise_for_status()
            ms = []
            for _ in range(a.reps):
                t = time.perf_counter()
                resp = c.get(f"/blood-requests/{rid}/shortlist", params={"strategy": mode})
                ms.append((time.perf_counter() - t) * 1000)
                resp.raise_for_status()
            eligible = resp.json()["counts"]["eligible_candidates"]
            out["modes"][mode] = {"eligible": eligible, "p50": round(statistics.median(ms), 1),
                                  "p95": round(pct(ms, 95), 1), "p99": round(pct(ms, 99), 1),
                                  "max": round(max(ms), 1), "failures": 0}
    finally:
        c.post(f"/blood-requests/{rid}/cancel", json={"reason": "latency run finished"})
    with open(a.out, "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
