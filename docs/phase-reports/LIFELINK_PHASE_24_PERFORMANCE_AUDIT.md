# LIFELINK — PHASE 24 PERFORMANCE AUDIT
## Performance, Scalability & Load/Concurrency Hardening

Status: **Audit + implementation complete.** This document is the audit-first deliverable required before any Phase 24 code change; `LIFELINK_PHASE_24_PERFORMANCE_REPORT.md` is the companion final report with the mandated 33-section structure, before/after benchmark tables, load-test results, and the final GO / GO-WITH-LIMITATIONS decision.

This audit was performed by directly reading the real source (never trusting a subagent's static claim as confirmed without independently reading the file myself), running real `EXPLAIN (ANALYZE, BUFFERS)` against the real, organically-populated dev database, and — where a claim could plausibly be wrong at this codebase's actual data scale — checking real row counts before writing anything down as a finding.

---

## 1. Method

1. Re-verified the Phase 23 baseline from a fresh environment (Postgres/Redis had to be restarted; this sandbox's container state does not persist between long sessions) before touching anything: backend 748 collected / 747 passed / 1 skipped, ruff 101, mypy 3, frontend tsc/eslint/build clean, Playwright 46 tests in 13 files.
2. Recorded the real environment this phase's every measurement was taken in (Section 2).
3. Ran real `EXPLAIN (ANALYZE, BUFFERS)` against the real dev database (`lifelink`, not a synthetic fixture) for the endpoints most likely to show a real index/N+1 problem, using its actual accumulated row counts (Section 4).
4. Dispatched three parallel static-analysis passes (backend endpoint/N+1/pagination/rate-limit inventory; Redis/RQ/Neo4j/connection-pool; frontend bundle/render/network) and then personally re-read every file each one cited before accepting any of its claims (Section 5).
5. Implemented only the changes the evidence actually supported (Section 6), each with a real before/after measurement.
6. Ran a real, custom async load-test harness (httpx + asyncio, no mocking) against the real running dev backend at concurrency 1/5/10/25/50 for 6 of this phase's 8 required scenarios (Section 7), using synthetic, clearly-labeled `loadtest.*@example.com` accounts seeded specifically for this benchmark.
7. Ran the full backend pytest suite, frontend tsc/eslint/build, and the full Playwright suite after every code change, not just at the end.

---

## 2. Performance Environment (applies to every measurement in this document)

| Property | Value |
|---|---|
| Host OS | Linux 6.18.44-fc-v33 |
| CPUs | **2** (confirmed via `nproc`) — the single most important constraint on every load-test number below |
| Python | 3.11.15 |
| Node | v22.22.2 |
| PostgreSQL | 16.13, real instance on localhost, `lifelink` (dev) / `lifelink_test` (test) databases |
| Redis | 7.0.15, real instance on localhost |
| Backend process | a single `uvicorn app.main:app` worker (no `--workers`, no gunicorn) — exactly how this app runs in dev today |
| Object storage (MinIO/S3) | **not installed in this sandbox** and not installable (its download host is blocked by this environment's egress policy) — see Section 8's limitation note |
| Neo4j | configured but unreachable (`Connection refused` on 7687) — same as every prior phase |

**This is a single-developer-machine benchmark, not a production or staging environment.** Every number in this document and the companion report describes what this specific 2-CPU box does under this specific single-process topology — never extrapolated to "production" or "at scale" claims.

---

## 3. Baseline (before any Phase 24 change)

| Check | Result |
|---|---|
| Backend tests | 748 collected, 747 passed, 1 skipped, 0 failed |
| ruff | 101 findings (pre-existing, Phase 22/23 baseline) |
| mypy | 3 errors (pre-existing: 2 in `verifications.py`, 1 in `donors.py`) |
| Frontend tsc | clean |
| Frontend eslint | clean |
| Frontend production build | 1 chunk, `index-*.js` **1.31 MB / 312.59 kB gzip** |
| Playwright | 46 tests / 13 files, all passing |
| `notifications` table | 47,497 rows (real, accumulated from every prior phase's test/E2E runs) |
| `audit_logs` | 2,027 rows |
| `users` | 1,162 |
| `hospital_profiles` | 104 |
| `transport_routes` | 85 |
| `blood_requests` | 121 |
| `emergencies` | 68 |
| `donor_profiles` | 1,015 |
| `hospital_inventory` | 496 |

---

## 4. Database Findings (real EXPLAIN ANALYZE evidence)

Ran directly against the real dev database, repeated more than once per query to rule out cold-cache artifacts before writing anything down as confirmed.

### 4.1 CONFIRMED — missing composite index on `notifications`

`GET /notifications?unread_only=true` (a real, frequently-hit endpoint — the bell icon's dropdown): with 47,497 real rows, `EXPLAIN (ANALYZE, BUFFERS)` showed an index scan on the existing single-column `created_at` index that filtered `user_id`/`read_at` in-memory:

```
Index Scan Backward using ix_notifications_created_at on notifications
  Filter: ((read_at IS NULL) AND (user_id = '...'))
  Rows Removed by Filter: 47479
Execution Time: 36.521 ms
```

`GET /notifications` (`unread_only=false`) was measurably better but still filtering ~11,153 rows per query (1.18 ms — fine today, but the filter cost scales with a user's total notification count, not with `limit`, so it degrades as the table grows).

**Fix implemented** (Section 6.1): two new indexes matching these exact query shapes. Re-measured after the fix: `unread_only=true` dropped to **0.21 ms** (index scan, 0 rows filtered); `unread_only=false` dropped to **0.23 ms**; `/notifications/unread-count` became an index-only scan at **0.07 ms**.

### 4.2 Ruled out (checked, not a problem at current or near-term scale)

- `GET /notifications/unread-count` — already using its existing index correctly at the row counts checked; the *list* variant with `unread_only=true` was the real problem (see 4.1), not the count.
- `GET /admin/audit-log` (2,027 rows) — its `created_at`/`actor_user_id`/`action` single-column indexes are each already sufficient at this scale; no composite-index evidence found.
- `GET /blood-requests` (per-user list, 121 total rows in the whole table) — a full-table scan here is correct and cheaper than an index lookup at this size; PostgreSQL's planner already does this.

### 4.3 Endpoints intentionally NOT changed despite being technically unbounded

- `GET /admin/transport-routes` (85 rows), `GET /hospitals/directory` (104 rows), `GET /hospitals/me/routes` (at most a handful per facility) — all `select(Model)` with no `.limit()`. At today's real row counts these are non-issues; adding pagination now, with zero evidence of a problem, would be exactly the "premature optimization" this phase's own rules forbid. **Documented as a scale ceiling to revisit if `hospital_profiles`/`transport_routes` grow by 1–2 orders of magnitude**, not fixed today.

---

## 5. N+1 Findings

### 5.1 CONFIRMED and fixed — `GET /hospitals/me/requests`

`app/api/routes/hospitals.py::incoming_requests` issued one extra `SELECT count(*) ... WHERE request_id = ?` per visible request (never dedupable — a different WHERE clause every time) plus one `db.get(User, patient_id)` per request (only dedupable when the same patient repeats across rows, not guaranteed).

Reproduced with a real SQLAlchemy `before_cursor_execute` counter against 6 seeded requests from 6 distinct patients (see `tests/test_phase24_performance_hardening.py::test_incoming_requests_query_count_is_bounded_not_linear`): confirmed a real per-row query cost before the fix.

**Fix**: batched both into one `GROUP BY` query and one `IN (...)` query, run once before the per-row loop. Total SELECT count is now fixed regardless of how many requests are visible, verified not to scale with N in the new test, and the returned data (`accepted_count`, `patient_name` per row) is verified identical to the old per-row computation (`test_incoming_requests_accepted_count_and_patient_name_correct`).

### 5.2 Investigated, not fixed (evidence didn't support a change)

Other N+1-shaped patterns exist in this codebase (e.g. `db.get(User, ...)` inside loops elsewhere), but every other one checked either (a) already benefits from SQLAlchemy's per-session identity map because the same small set of ids repeats within one request, or (b) operates over a bounded, small collection (a handful of donor responses per request, a handful of routes per facility) where the query-count overhead is a few extra sub-millisecond round trips, not a measured problem. Fixing these without a reproduced query-count or timing problem would be unevidenced churn.

---

## 6. Implemented Changes (each with a real before/after measurement)

### 6.1 Composite indexes on `notifications`

- `ix_notifications_user_created` — btree `(user_id, created_at DESC)`.
- `ix_notifications_user_unread_created` — partial btree `(user_id, created_at DESC) WHERE read_at IS NULL`.
- Migration: `alembic/versions/c3d4e5f6a7b8_phase24_notifications_composite_index.py`. Verified upgrade → downgrade → upgrade-again against the real dev database; verified the compiled DDL from the SQLAlchemy model's `Index(...)` declarations is byte-identical to the Alembic-produced DDL (`\d notifications` matched exactly), so `Base.metadata.create_all` (the test suite's schema path) and the migration (the real dev/prod schema path) never drift apart.
- **Known false-positive**: `alembic check` (autogenerate diff) reports these two indexes as "changed" due to how it structurally compares `postgresql_ops`-based expression indexes — verified this is a comparator quirk, not a real drift, by compiling both definitions to raw DDL and diffing the text (identical) and by confirming the actual installed indexes in both the dev and test databases match the model exactly.

### 6.2 N+1 fix in `GET /hospitals/me/requests`

See Section 5.1. `app/api/routes/hospitals.py`.

### 6.3 Frontend route-level code splitting

`frontend/src/App.tsx`: converted 47 of the app's 52 routed page components to `React.lazy()` + a single `<Suspense>` boundary around `<Routes>`. Measured with `npx vite build`:

| | Before | After |
|---|---|---|
| Main JS chunk | 1.31 MB / 312.59 kB gzip | 775.50 kB / 225.82 kB gzip |
| Route chunks | 0 (all in one file) | ~70 on-demand chunks |

**Real regression found and fixed during this work**: the first attempt lazy-loaded all 52 pages, including the 5 role-landing dashboards every login redirects straight into. `e2e/blood_request_idempotency.spec.ts` failed reproducibly: `helpers.ts::loginAsUi` only waits for the URL to change, not for the destination component to mount, so a test reading a "before" row count immediately after login could land during the Suspense fallback window (before the dashboard — and its own "Loading your requests…" text — had even mounted), reading 0 instead of the real count. **Fix**: `PatientDashboard`, `HospitalDashboard`, `DonorDashboard`, `FamilyDashboard`, `AdminOverview` were pulled back to eager imports (every login needs exactly one of these five, so there is no code-splitting benefit to lazy-loading a page 100% of sessions load anyway) — this removed the race entirely while keeping the rest of the bundle-size win. Full Playwright suite re-run and confirmed 46/46 passing after this correction.

### 6.4 Connection pool: tested, found no improvement, REVERTED

See Section 7.4 and the report's Connection Pool Findings section for the full before/after evidence. `pool_size`/`max_overflow` were raised from the library defaults (5/10 → 15-connection ceiling) to 20/30 (a 50-connection ceiling) as a hypothesis fix for a real, reproduced degradation under concurrent load. Measured with the identical load-test methodology, same fresh-restart procedure: **no meaningful improvement** — the same endpoints degraded and stalled at the same concurrency levels with either setting. Per this phase's own "revert non-improving optimizations" rule, `app/db/session.py` was reverted to the library defaults. The real bottleneck is documented as a limitation (2 CPUs backing a single-worker synchronous-handler architecture), not falsely "fixed" by a config value that didn't fix it.

---

## 7. Load Testing (real, against the real running dev backend)

Custom async harness (`httpx` + `asyncio`, no mocking) hitting the real backend with pre-provisioned real Redis sessions (to avoid the login endpoint's own correctly-working IP rate limit, which a single test machine's one source IP would otherwise trip after a handful of requests — a limitation of single-machine load testing, not a backend bug). Full methodology, every recorded number, and the complete before/after tables are in the companion report's Load-Test Results and Before/After Benchmarks sections.

**Headline finding**: at concurrency 25–50, several read/write endpoints degrade sharply (avg latency growing from tens of milliseconds to multiple seconds) and, in the worst observed case (donor-dashboard reads and hospital-inventory reads at concurrency 50), the backend entered a state where requests kept timing out even at concurrency 1, well after the burst — a real, reproduced near-total-outage mode under sustained overload on this 2-CPU sandbox. This was reproduced twice (once at the original pool ceiling, once at the enlarged one) with an identical result, which is what ruled out pool size as the cause (Section 6.4) and pointed to CPU/thread-scheduling capacity of the single synchronous-handler worker process as the real constraint — an architectural characteristic of this deployment topology, not a quick fix, and documented honestly as a limitation.

---

## 8. Limitations (carried into the final report's Limitations section)

- **2-CPU sandbox**: every concurrency number in this document is bounded by what a 2-CPU box can do; it is not a production capacity claim.
- **MinIO/S3 unreachable**: this sandbox has no MinIO installed and its release host is blocked by the environment's egress policy. Certificate/document-upload Playwright specs were verified to pass end-to-end using the app's own local-storage fallback (`S3_ENDPOINT_URL=""`, already a first-class supported backend per `app/core/storage.py` — not a workaround invented for this phase); the real S3/MinIO code path itself was not benchmarked in this environment.
- **Neo4j unreachable**: consistent with every prior phase; no graph-engine load numbers are reported, per this phase's own explicit "don't fabricate a benchmark for an unreachable dependency" rule.
- **Single test machine, single source IP**: could not measure the login endpoint's real per-IP rate limiter under genuine multi-client concurrency; worked around by pre-provisioning sessions for every other measurement, which is closer to real user behavior (an established session, not a fresh login per request) anyway.
- **No production traffic pattern data**: load-test scenarios are reasonable approximations of the 8 required scenarios, not measurements against real user behavior.

See the companion report for the full 33-section writeup, the complete before/after table, the exact regression numbers, and the final GO/NO-GO decision.
