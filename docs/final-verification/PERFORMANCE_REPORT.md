# Performance report — pathway engine

Reproduce:

```bash
cd backend
createdb lifelink_bench
python -m scripts.benchmark_pathways \
  --database-url postgresql+psycopg://lifelink:lifelink@localhost:5432/lifelink_bench \
  --requests 50 --seed 7 --out ../docs/final-verification
```

Environment: 2 vCPU x86_64 sandbox, PostgreSQL 16, Python 3.11.17,
SQLAlchemy 2.0.52, relational path engine (Neo4j not configured).
Data: SYNTHETIC, seed 7 (raw output: `benchmark_pathways.json`, including
the full EXPLAIN (ANALYZE, BUFFERS) plans).

| Network | Facilities | Routes | Units | Plan p50 | Plan p95 | Plan max | Candidate SQL | Usable-at-arrival SQL |
|---|---|---|---|---|---|---|---|---|
| small | 20 | 40 | 210 | 5.46 ms | 17.86 ms | 21.96 ms | 0.240 ms | 0.210 ms |
| medium | 100 | 200 | 1,059 | 22.15 ms | 47.09 ms | 122.23 ms | 0.407 ms | 0.320 ms |
| large | 400 | 800 | 4,429 | 80.08 ms | 217.66 ms | 282.66 ms | 3.389 ms | 1.344 ms |

Outcome mix over the 50 random requests (urgency, component, quantity and
deadline drawn at random): small 38 VIABLE / 5 DEADLINE_RISK / 4 PARTIAL /
3 IMPOSSIBLE; medium 39 / 2 / 2 / 7; large 37 / 3 / 1 / 9.

Reading: the SQL is a small fraction of planning time; the cost is the
hop-bounded path search in Python (O(H·E) per request). At 400 facilities
p95 stays near 0.2 s. With Neo4j configured the search moves to the graph
(not measured here — BLOCKED). In the large plan the candidate query uses
a bitmap index scan on `blood_units.state` and index nested loops into
`hospital_profiles`/`users` (6,116 shared-buffer hits, 3.4 ms). Row
estimates (1 vs 1,000 actual) show the freshly-loaded table had not been
ANALYZEd; the plan was still index-driven.
