# Performance results

**Machine.** One sandbox VM with 2 vCPUs and 8 GB RAM, running PostgreSQL 16, Redis 7 and Python 3.11.17.

**The load generator shares this machine**, so every figure below is a *lower bound on a small machine*, **not a production capacity claim**. Every result is reproducible with the command given.

## 1. Planner scaling (reproducible benchmark)

**Command:**

```
python -m scripts.benchmark_pathways --database-url …/lifelink_bench \
  --sizes small medium large --requests 50 --seed 7 --out docs/QA/evidence/performance
```

**Evidence:** `evidence/performance/benchmark_pathways.json` (with `EXPLAIN (ANALYZE, BUFFERS)`) and `benchmark_run.log` (25 s wall time).

| Network | Facilities | Routes | Units | p50 (ms) | p95 (ms) | max (ms) | Feasibility of the 50 plans |
|---|---|---|---|---|---|---|---|
| small | 20 | 40 | 210 | 6.5 | 14.7 | 27.1 | 38 VIABLE, 5 DEADLINE_RISK, 4 PARTIAL, 3 IMPOSSIBLE |
| medium | 100 | 200 | 1,059 | 15.9 | 26.2 | 81.8 | 39 VIABLE, 2 DEADLINE_RISK, 2 PARTIAL, 7 IMPOSSIBLE |
| large | 400 | 800 | 4,429 | 67.3 | 178.2 | 185.4 | 37 VIABLE, 3 DEADLINE_RISK, 1 PARTIAL, 9 IMPOSSIBLE |

**Reproducibility check.**

* With the same seed, the 2026-10-13 run gave **identical feasibility counts** for every size. The plans are deterministic.
* Latency was within the run-to-run noise of a shared 2-vCPU machine. The 2026-10-13 large-network p95 was 217.7 ms; this run's was 178.2 ms.

**Where the time goes.** The two SQL statements the planner issues are index scans taking a few milliseconds (`EXPLAIN` in the JSON). The rest is the hop-bounded path search in Python, O(H·|E|) per request.

## 2. HTTP load test on the live API (production entrypoint)

**Setup:**

* the server is `scripts/run_production.sh` with `WEB_CONCURRENCY=2`, on the demo-seeded QA database;
* 100 authenticated SSE streams were held open for the whole run;
* the mix is read-heavy: 11 endpoints across 4 roles.

**Command:**

```
python -m scripts.loadtest_hardening --base http://127.0.0.1:8600 --concurrency 1,8,32 --seconds 15 --sse 100 --per-path --server-pid <pid>
```

**Evidence:** `evidence/performance/loadtest_live.txt`.

| Concurrency | Requests | Throughput (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | Non-2xx | Server CPU (% of 1 core) | Server RSS (MB) |
|---|---|---|---|---|---|---|---|---|
| 1 | 970 | 64.7 | 11.9 | 40.9 | 58.7 | 0 | 74 | 348 |
| 8 | 1,093 | 72.5 | 93.5 | 226.5 | 365.5 | 0 | 91 | 357 |
| 32 | 1,042 | 67.6 | 415.8 | 898.1 | 1,082.1 | 0 | 93 | 368 |

**Results:**

* **0 errors at every level.** Throughput saturates at about 65–72 req/s, where the server is CPU-bound on a CPU it shares with the generator. Latency then grows with queueing, as expected.
* **The SSE fan-out stays cheap:** 100 open streams used **2** Redis pub/sub connections, one per server process.

## 3. Query counts on the busiest endpoints (N+1 check)

Measured with an SQLAlchemy `before_cursor_execute` counter on a warm request, against the demo-seeded QA database. Tool: `docs/QA/tools/query_counts.py`; output: `evidence/performance/query_counts.txt`. Timings are single in-process requests.

| Endpoint | SQL statements | Time (ms) | Assessment |
|---|---|---|---|
| `GET /admin/blood-requests` | 2 | 15 | constant |
| `GET /map/data` | 3 | 23 | constant (payload 32 kB) |
| `GET /donors/me/requests` | 6 | 19 | constant |
| `GET /hospitals/me/requests` | 6 | 15 | constant |
| `GET /admin/analytics/overview` | 8 | 14 | constant |
| `GET /hospitals/me/inventory/components` | 28 | 55 | **bounded, not N+1 over units:** the lazy expiry sweep touches each (group, component) inventory row the facility holds, at most 8 × 4 = 32. Observation P3 in KNOWN_LIMITATIONS.md: it could be batched into one statement |
| `GET /hospitals/me/units` | 28 | 57 | same sweep, then one query (payload 74 kB) |

**Regression guards that already exist:**

* `tests/test_phase24_performance_hardening.py`, `test_phase27_notification_scaling.py` and `test_phase_k_performance.py` bound query counts so that they do not grow linearly;
* `test_hardening_performance.py`;
* all of them pass in the final regression.

Earlier profiling on volume databases (10,001 and 81,312 requests; N+1 removals such as the donor feed going from 2,043 to 6 statements; index additions; incremental audit-chain verification) is in `docs/final-verification/PERFORMANCE_REPORT.md` and is not repeated here.

## 4. Thresholds

These are set from the observed baseline on this machine, **not** from production requirements, which have not been specified.

| Metric | Observed | Threshold proposed for regression detection |
|---|---|---|
| Planner p95, 400 facilities | 178–218 ms | ≤ 400 ms on a 2-vCPU machine |
| API p95, single client, read mix | 40.9 ms | ≤ 100 ms |
| Errors under load (C ≤ 32) | 0 | 0 |
| Redis pub/sub connections per server process with N open streams | 1 | 1 |
| Graceful shutdown with an open SSE stream | 5.22 s | < 10 s (Docker's default stop timeout) |

## 5. Not measured

* Production hardware.
* Multi-node deployment.
* Network latency between services, which are all on one host here.
* The Neo4j engine (unavailable).
* Frontend rendering performance beyond the crawl's load checks. Bundle size triggers Vite's 500 kB warning (KNOWN_LIMITATIONS P6).
