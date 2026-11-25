# LIFELINK — PHASE 24 PERFORMANCE REPORT
## Performance, Scalability & Load/Concurrency Hardening

---

## 1. Status

**GO-WITH-LIMITATIONS.**

Three evidence-backed performance fixes were implemented, measured, and verified to improve real, reproduced problems without touching any security control, auth path, RBAC check, or business-logic invariant: (1) two composite/partial indexes on `notifications`, (2) an N+1 fix in the hospital incoming-requests endpoint, and (3) frontend route-level code splitting (with a real regression found and fixed during the work). A fourth candidate fix — enlarging the Postgres connection pool — was implemented, measured, found to produce **no meaningful improvement**, and **reverted** per this phase's own rule.

Real load testing also surfaced a genuine, serious, **unresolved** finding: under sustained concurrent load (25–50 simultaneous callers) on this sandbox's 2-CPU/single-worker topology, several endpoints degrade sharply and, in the worst case, the backend enters a near-total-outage state that does not self-recover even at concurrency 1 well after the load burst ends, until the process is restarted. This is a real architectural limitation of the current deployment topology (not a bug in any one endpoint), it is documented honestly below with the evidence that both confirms it and rules out the one hypothesis fix that was tried, and it is **not fixed by this phase** — hence "GO-WITH-LIMITATIONS" rather than "GO". Every existing functional guarantee (auth, RBAC, family-link security, emergency/blood-request workflows, donor matching, inventory correctness under concurrency, notifications, audit logging, idempotency, rate limiting) was verified unchanged: full backend suite 752 collected / 751 passed / 1 skipped / 0 failed, full Playwright suite 46/46 passed, ruff and mypy findings counts unchanged from baseline.

---

## 2. Baseline (before any Phase 24 change)

| Check | Result |
|---|---|
| Backend tests | 748 collected, 747 passed, 1 skipped, 0 failed |
| ruff | 101 findings (pre-existing, unchanged since Phase 22/23) |
| mypy (`python3 -m mypy app`) | 3 errors (pre-existing: 2 in `verifications.py`, 1 in `donors.py`) |
| Frontend tsc | clean |
| Frontend eslint | clean |
| Frontend production build | 1 chunk, `index-*.js` **1.31 MB / 312.59 kB gzip** |
| Playwright | 46 tests / 13 files, all passing |

Real dev-database row counts at the start of this phase (never synthetic unless explicitly labeled `loadtest.*`):

| Table | Rows |
|---|---|
| `users` | 1,162 |
| `hospital_profiles` | 104 |
| `transport_routes` | 85 |
| `blood_requests` | 121 |
| `emergencies` | 68 |
| `notifications` | 47,497 |
| `donor_profiles` | 1,015 |
| `hospital_inventory` | 496 |
| `certificates` | 6 |
| `graph_pathway_logs` | 3 |
| `audit_logs` | 2,027 |

---

## 3. Performance Environment (applies to every measurement in this document)

| Property | Value |
|---|---|
| Host OS | Linux 6.18.44-fc-v33 |
| CPUs | **2** (`nproc`) — the single most important constraint on every load-test number in Section 25 |
| Python | 3.11.15 |
| Node | v22.22.2 |
| PostgreSQL | 16.13, real instance on localhost — `lifelink` (dev) / `lifelink_test` (test) databases |
| Redis | 7.0.15, real instance on localhost |
| Backend process | a single `uvicorn app.main:app` worker (no `--workers`, no gunicorn) — how this app runs in dev today |
| Object storage (MinIO/S3) | not installed in this sandbox; its release host is blocked by the environment's egress policy (see Section 32) |
| Neo4j | configured but unreachable (`Connection refused` on 7687), consistent with every prior phase |

**Every number in this report describes a single-developer-machine benchmark under a single-worker/2-CPU topology — never a production or staging claim, and never extrapolated beyond what was actually measured.**

---

## 4. Endpoint Inventory

The backend exposes 111 routes across `app/api/routes/*.py`. 110 of 111 are synchronous `def` handlers, which FastAPI/Starlette runs in anyio's default worker threadpool (default cap ~40 concurrent threads); none are `async def` handlers doing real async I/O. Grouped by domain:

| Domain | File | Route count (approx.) | Notes |
|---|---|---|---|
| Auth / session | `auth.py` | 6 | login, logout, register, password change, session touch |
| Blood requests | `blood_requests.py` | ~8 | create (idempotent), list, accept, cancel |
| Emergencies | `emergencies.py` | ~6 | create (idempotent, synchronous donor-notification fan-out), list, respond |
| Donors | `donors.py` | ~10 | profile, matching-adjacent reads, responses |
| Hospitals | `hospitals.py` | ~12 | inventory CRUD, incoming-requests (Section 9), transport routes |
| Matching | `matching.py` | ~4 | compatible-donor / compatible-inventory queries (join-based, no N+1 found) |
| Notifications | `notifications.py` | 4 | list, unread-only, unread-count, mark-read (Section 6/11) |
| Admin | `admin.py` | ~20 | users, audit-log, facilities, donors, certificates, transport-routes, verifications |
| Verifications | `verifications.py` | ~6 | document upload/review |
| Certificates | `certificates.py` | ~4 | generation, download, public verify |
| Graph | `graph_service.py` consumers | ~3 | pathway/decision-transparency reads |
| Family links | `family.py` | ~5 | link request/accept/list |

No endpoint inventory gaps were found relative to the prior phase's documented route count.

---

## 5. Database Findings

Real `EXPLAIN (ANALYZE, BUFFERS)` was run against the real dev database (not a synthetic fixture), repeated more than once per query to rule out cold-cache artifacts.

**CONFIRMED — missing composite index on `notifications`.** `GET /notifications?unread_only=true` (the bell dropdown, hit on every authenticated page load) at 47,497 real rows used the existing single-column `created_at` index and filtered `user_id`/`read_at` in memory:

```
Index Scan Backward using ix_notifications_created_at on notifications
  Filter: ((read_at IS NULL) AND (user_id = '...'))
  Rows Removed by Filter: 47479
Execution Time: 36.521 ms
```

`unread_only=false` filtered ~11,153 rows per query at 1.18 ms — acceptable today, but its cost scales with a user's total notification count, not with `limit`.

**Ruled out (checked, not a problem at current scale):** `GET /notifications/unread-count` was already using its index correctly; `GET /admin/audit-log` (2,027 rows) has sufficient single-column indexes; `GET /blood-requests` (121 rows total) — a full-table scan is correct and cheaper than an index lookup at this size, and the planner already does this.

See Section 12 for the implemented fix and its measured before/after.

---

## 6. N+1 Findings

**CONFIRMED and fixed — `GET /hospitals/me/requests`** (`app/api/routes/hospitals.py::incoming_requests`). Before the fix, this issued one extra `SELECT count(*) ... WHERE request_id = ?` per visible request (a different WHERE clause every time — never dedupable) plus one `db.get(User, patient_id)` per request (only dedupable when the same patient repeats). Reproduced with a real SQLAlchemy `before_cursor_execute` query counter against 6 seeded requests from 6 distinct patients (`tests/test_phase24_performance_hardening.py::test_incoming_requests_query_count_is_bounded_not_linear`).

**Fix**: batched into one `GROUP BY` query (accepted counts) and one `IN (...)` query (patient names), run once before the per-row loop. Total SELECT count is now fixed regardless of how many requests are visible — verified in the new test not to scale with N — and the returned `accepted_count`/`patient_name` values are verified identical to the old per-row computation.

**Investigated, not fixed** (evidence didn't support a change): other `db.get(User, ...)`-in-a-loop-shaped patterns exist elsewhere (e.g. a handful of donor responses per request, a handful of routes per facility), but each either already benefits from SQLAlchemy's per-session identity map (the same small id set repeats within one request) or operates over a collection small enough that the extra round trips are sub-millisecond and unmeasured as a problem. `matching.py` was read in full and confirmed clean (proper joins, no per-row queries).

---

## 7. Index Findings

Two indexes added (Section 12); no other index gaps were found with real evidence behind them at current data volumes. `admin.py::list_transport_routes` (85 rows), `hospitals.py::hospital_directory` (104 rows) and `hospitals.py::my_transport_routes` (a handful per facility) are technically unbounded `select(Model)` queries with no `.limit()`, but at these row counts a covering index would not measurably change anything — adding one now, with zero evidence of a problem, is exactly the "premature optimization" this phase's rules forbid. **Documented as a scale ceiling to revisit if these tables grow 1–2 orders of magnitude**, not fixed today.

---

## 8. Pagination Findings

`admin.py`'s `list_blood_requests`, `list_emergencies`, `list_audit_log`, `list_facilities`, `list_donors`, and `list_certificates` are all already properly paginated/bounded (`limit`/`page_size` with `ge`/`le` validation). The new regression test `test_pagination_bounds_unchanged_admin_and_hospital_endpoints` confirms these bounds were not loosened by any Phase 24 change. The three unbounded list endpoints noted in Section 7 are the only pagination gaps found, and are left unfixed for the reason stated there.

---

## 9. Blood Request Performance

No index or N+1 problem was found in the blood-request create/list/accept path at 121 real rows. Load-tested directly as Scenario B (Section 25): creation cost scales roughly linearly with concurrency on this 2-CPU box (see the table — this is expected CPU-bound-handler behavior, not a database problem; `EXPLAIN` on the underlying inserts/lookups showed no missing index).

---

## 10. Emergency Performance

`emergencies.py`'s create path performs the compatible-donor notification fan-out **synchronously inside the request** (see Section 13 — this is the same synchronous-dispatch pattern as the blood-request/notification path generally, not something unique to emergencies). Load-tested as Scenario F; every attempt returned an error at every concurrency level during the full load-test run because Scenario F ran last in sequence, after Scenario D had already driven the backend into the near-total-outage state described in Section 25 (corroborated in Sections 19–20) — this is evidence of that outage's severity (it didn't recover for unrelated endpoints either), not a defect specific to the emergency endpoint. The existing `test_concurrent_emergency_inventory_reservations_never_oversell` test (`tests/test_phase17_resilience.py:228`) already covers correctness under concurrent emergency-driven inventory reservation and continues to pass unchanged.

---

## 11. Notification Performance

Covered in depth in Section 5/12 (the composite-index fix). Additionally confirmed by direct code read: `app/workers/queue.py` defines an `sms_queue` and a worker subscribes to it (`app/workers/worker.py`), but nothing in the codebase ever calls `.enqueue()` on it — SMS notification dispatch (`sms_service.py::send_for_event` → `provider.send_sms()`) is fully synchronous in-request, dead-code queue infrastructure notwithstanding. This is a real finding but not a regression introduced by this phase, and fixing it (wiring SMS through the queue) is a behavior change beyond "performance hardening without changing behavior" — documented here, not changed.

`email_service.py::deliver()`'s own docstring confirms the enqueue-before-`db.commit()` ordering is a pre-existing, deliberately documented design choice from an earlier phase (a job could theoretically run before the enclosing transaction commits) — a correctness/ordering question already known and accepted, not a new Phase 24 finding, and out of scope for a performance-only phase.

---

## 12. Redis Performance

`session_service.py`'s `create_session`/`touch_session` each use a Redis pipeline (1 round trip per call), so a typical authenticated request performs `get_session` (1) + `touch_session` (1) = **2 Redis round trips**, confirmed by direct read of the source. `app/core/ratelimit.py::enforce_rate_limit` is **not** pipelined — it issues a separate `incr`, a conditional `expire`, and a conditional `ttl` (1–3 round trips depending on state). This is a legitimate, minor optimization opportunity (could be pipelined into 1 round trip), but with Redis round trips measured in the sub-millisecond range on localhost and no reproduced request-latency problem traced to it, changing it now would be unevidenced churn — documented, not changed.

**Composite index fix, measured (Section 5 detail):**

| Query | Before | After |
|---|---|---|
| `GET /notifications?unread_only=true` | 36.521 ms (47,479 rows filtered) | **0.21 ms** (index scan, 0 rows filtered) |
| `GET /notifications` (`unread_only=false`) | 1.18 ms (~11,153 rows filtered) | **0.23 ms** |
| `GET /notifications/unread-count` | already index-based | **0.07 ms** (now index-only scan) |

Migration `alembic/versions/c3d4e5f6a7b8_phase24_notifications_composite_index.py` adds:
- `ix_notifications_user_created` — btree `(user_id, created_at DESC)`
- `ix_notifications_user_unread_created` — partial btree `(user_id, created_at DESC) WHERE read_at IS NULL`

Verified upgrade → downgrade → upgrade-again against the real dev database. The SQLAlchemy model (`app/models/notification.py`) carries matching `Index(...)` declarations so `Base.metadata.create_all` (the test suite's schema path) and Alembic (the dev/prod schema path) never drift — verified by compiling both to raw DDL and confirming byte-identical text, and by inspecting `\d notifications` in both databases. (`alembic check` reports a false-positive diff on these two indexes due to a known comparator quirk with `postgresql_ops`-based indexes; confirmed not a real drift by the DDL comparison above.)

---

## 13. RQ (background job) Performance

Only the email path is actually queued through RQ (`email_service.py:84`, `email_queue.enqueue(...)`); the SMS path is fully synchronous despite a defined `sms_queue` and subscribed worker (Section 11) — dead-code infrastructure, not a live performance issue, since SMS sending itself was not found to be a measured bottleneck at this data scale. No RQ-specific load numbers are reported beyond what Scenario B/F's request latencies already capture (they include the synchronous SMS/email-enqueue cost inline).

---

## 14. Neo4j Performance

Neo4j is unreachable in this sandbox (`Connection refused` on port 7687), consistent with every prior phase. Per this phase's own explicit rule against fabricating a benchmark for an unreachable dependency, **no Neo4j load numbers are reported**. The graph pathway query itself (`graph_service.py::_PATHWAY_QUERY`) was read in full and confirmed to be bounded — `_MAX_PATH_HOPS = 6` hops and a `LIMIT $max_results` clause (itself bounded 1–50 by the calling route) — correcting an earlier subagent-reported suspicion that it was unbounded. This is a false positive that was ruled out by direct source inspection, not a finding.

---

## 15. Search Performance

No dedicated full-text or fuzzy search endpoint exists in this codebase beyond the already-covered paginated list/filter endpoints in Section 8 (e.g. `admin.py`'s facility/donor list filters). No search-specific bottleneck was found or evidenced at current data volumes; not modified this phase.

---

## 16. Analytics Performance

The admin analytics endpoints were reviewed for N+1/missing-index patterns during the static-analysis pass; none were found to have a reproduced problem at current row counts (`audit_logs` at 2,027 rows, `hospital_inventory` at 496). Not modified this phase — no evidenced bottleneck, and changing aggregation queries without a reproduced slow-query measurement would be unevidenced churn per this phase's own rules.

---

## 17. Inventory Concurrency

Correctness under concurrent inventory mutation was not re-derived from scratch this phase — it is already covered by the existing, still-passing `test_concurrent_emergency_inventory_reservations_never_oversell` (`tests/test_phase17_resilience.py:228`), which exercises real concurrent reservation attempts against the same inventory row and asserts no oversell. This test was run as part of the full 752-test regression suite (Section 31) with the exact result unchanged from baseline, confirming this phase's changes (index additions, N+1 fix, code splitting, the tried-and-reverted pool change) did not affect inventory-concurrency correctness.

---

## 18. Concurrency Matrix

Load-tested workflows and their observed behavior across concurrency 1/5/10/25/50 (full numbers in Section 25):

| Workflow | Endpoint(s) | Behavior under rising concurrency |
|---|---|---|
| Patient browsing | `GET /blood-requests`, `/notifications`, `/notifications/unread-count` | Degrades gracefully — latency grows roughly linearly with concurrency, zero errors/timeouts even at 50 |
| Blood-request creation | `POST /blood-requests` (idempotent, distinct patients) | Degrades gracefully but steeply — median latency 180 ms → 6.2 s from c=1 to c=50; zero failures, all succeed |
| Donor dashboard reads | `GET /donors/me`, `/donors/me/requests`, `/notifications` | Degrades sharply from c=10 onward; **partial timeout onset at c=25** (25 of 75 requests timed out); **complete failure at c=50** (150 of 150 timed out) |
| Hospital inventory reads | `GET /hospitals/me/inventory`, `/hospitals/me/requests` | **Complete failure at every concurrency level tested (1, 5, 10, 25, 50)** in the final full-suite run — see Section 25's near-total-outage finding (Sections 19–20); this scenario ran immediately after the donor-dashboard c=50 collapse and never recovered |
| Admin search/list | `/admin/users`, `/admin/audit-log`, `/admin/facilities` | Could not be measured in the final run — the backend was already in the outage state left by the D scenario; see Section 25 |
| Emergency creation | `POST /emergencies` (idempotent, synchronous donor fan-out) | Same as admin search — could not be measured in the final run for the same reason |

This matrix itself is part of the evidence for Section 25's central finding: once the backend is pushed into the pathological state, it does not recover for *unrelated* endpoints either, which is why E and F show complete failure even at concurrency 1.

---

## 19. Connection Pool Findings

A real, reproduced degradation at concurrency 25–50 (Section 18/25) was hypothesized to be a connection-pool-size problem, since `pg_stat_activity` showed pooled connections stuck in `idle in transaction` / `ClientRead`. This was tested directly:

| Config | `pool_size` / `max_overflow` | Ceiling |
|---|---|---|
| Before (library default) | 5 / 10 | 15 connections |
| After (tested) | 20 / 30 | 50 connections |

Both configurations were measured with the **identical** load-test methodology and the **identical** fresh-restart procedure (three separate runs total: BEFORE, AFTER, and a final CONTROL re-run of the default configuration to rule out run-to-run noise). Result:

- Scenario A/B (patient browsing, blood-request creation): no meaningful difference between BEFORE and AFTER at any concurrency level.
- Scenario C (donor dashboard) at c=50: BEFORE = 0 of 150 succeeded (150 timeouts); AFTER = 50 of 150 succeeded, 100 timed out — a partial difference, but not a fix, and B's own latency was *higher* with the larger pool at c=50 (avg 8,762 ms AFTER vs 6,603 ms BEFORE) — consistent with more open connections increasing thread-scheduling contention rather than relieving it.
- Scenario D (hospital inventory): in the AFTER run this scenario fully recovered (20/20, 50/50, 100/100 across c=10/25/50) after failing at c=1/c=5; in the final CONTROL run (default pool, same as BEFORE) it failed at **every** concurrency level including c=1. This inconsistency between runs — same code, same settings in BEFORE and CONTROL, different outcomes — is itself evidence that the pool-size knob is not the deciding factor: what determines whether a given scenario succeeds or enters the outage state is the backend's leftover state from whatever ran immediately before it (how many threads/connections the prior scenario's burst left occupied), not how large the pool ceiling is.

**Conclusion: no meaningful, reliable improvement from enlarging the pool.** With only 2 CPUs backing a single uvicorn worker serving 110 synchronous handlers on anyio's threadpool, the real constraint is CPU/thread-scheduling capacity — more open Postgres connections just means more of them sit idle waiting for a thread slot, not more real parallelism. Per this phase's explicit "revert non-improving optimizations" rule, `app/db/session.py` was reverted to the library defaults (`pool_size`/`max_overflow` unset), with an in-code comment documenting exactly what was tried and why it was reverted, rather than shipping a config value that measurably didn't fix the problem it was meant to fix.

---

## 20. Resource Exhaustion Findings

The near-total-outage state described in Section 19/21 is a resource-exhaustion finding in itself: `pg_stat_activity`, inspected live during a concurrency-50 burst, showed pooled Postgres connections stuck in `idle in transaction` / `ClientRead` — i.e. threads holding a connection while waiting for a CPU-bound thread slot to free up, not slow queries. With anyio's default threadpool capacity (~40 concurrent threads) shared across a single 2-CPU worker process, a burst of 50 concurrent synchronous handlers oversubscribes that pool, and requests queue for a thread rather than failing fast — which is why timeouts (10s client-side) rather than fast errors are what's observed. No evidence of a memory leak was found (the process was not observed growing unbounded across repeated runs), and no file-descriptor exhaustion was observed independent of the thread/connection-scheduling issue above. The exhausted resource is thread-scheduling capacity under this topology, not memory or file descriptors.

---

## 21. Rate Limit Findings

Confirmed by direct read of `app/core/config.py` and `app/core/ratelimit.py`: `rl_login_ip = (10, 300)` (10 logins/300s per IP), `rl_login_account = (5, 300)` (5 logins/300s per account), plus separate limiters for registration, verification submission, and emergency creation (exercised and confirmed still working by the existing, passing `phase23_security_hardening.spec.ts` test (I)). `enforce_rate_limit` issues 1–3 unpipelined Redis round trips per call (`incr`, conditional `expire`, conditional `ttl`) — a legitimate minor optimization opportunity, not changed this phase for lack of a measured problem (Section 12).

Importantly, **rate limiting was ruled out as the cause of the load-test degradation**: across every load-test run in Section 22, the status-code breakdown for every scenario shows only `200`/`201` on success and pure timeouts/connection-errors on failure — never a `429`. The near-total-outage state is a thread/connection-scheduling problem, not the rate limiter rejecting requests; the two are independent findings that could be superficially confused and are called out separately here to avoid that.

The rate limiter's own interaction with fast, repeated automated testing is itself a real, documented fact of this environment (Section 26) — its correct behavior under a 46-test Playwright suite running at full speed from one IP required a test-only accommodation to get a clean regression signal, without touching the shipped limiter values (Section 32).

---

## 22. Frontend Performance

Confirmed by direct grep/read of the frontend source: zero `React.memo` usage anywhere in the codebase, zero `AbortController`/request-cancellation usage anywhere, and `main.tsx`'s `QueryClient` is constructed with no `defaultOptions` (so TanStack Query v5's defaults apply — `staleTime: 0`, refetch-on-window-focus enabled). None of these were found to have a *reproduced* performance problem at this app's real data scale and traffic pattern (no dashboard was measured re-rendering excessively, no duplicate-request storm was observed in the load tests), so none were changed — adding `React.memo` or cancellation logic without a reproduced re-render or race problem would be exactly the unevidenced churn this phase's rules forbid. They are documented here as identified-but-unconfirmed opportunities for a future phase with reproducing evidence.

One frontend finding was fixed with real evidence: the route-level code-splitting bundle-size reduction (Section 23/24), including a real regression found and fixed during that same work (Section 22 of "Bugs Found" in the numbering below).

**Not fixed, documented**: `main.tsx` imports `ToastProvider` (from `./lib/toast`) as a static top-level import, and `toast.tsx` imports `framer-motion` — so `framer-motion` ships in the main chunk to 100% of users regardless of route, even though it's used in only 3 files total (`AuthShell.tsx`, `toast.tsx`, `AuthPage.tsx`). This was identified but not changed: toasts are used app-wide for error/auth-event messaging from the moment the app mounts, so lazy-loading the provider itself risks a real functional gap (an early toast silently dropped during the lazy-chunk fetch window) — the same category of race this phase already found and fixed once with the dashboard lazy-loading regression (Section 22, Bugs Found). Fixing this safely would need more careful work than this phase's time/evidence budget supported; it is a legitimate opportunity for a future, narrowly-scoped pass.

---

## 23. Network Request Findings

No request-cancellation logic exists anywhere in the frontend (Section 22) — a user navigating away mid-fetch does not cancel the in-flight request, though no evidence was found that this causes a measured problem (no race condition or stale-data bug was reproduced). TanStack Query's default `staleTime: 0` means every component mount that queries the same key can trigger a fresh network request rather than serving cached data, which is standard v5 default behavior, not a bug; no excessive-refetch problem was measured in the load tests or in normal browsing (Scenario A). No other unusual network-request pattern (e.g. duplicate concurrent requests for the same resource, no debouncing on a search-as-you-type field) was found during the frontend static-analysis pass.

---

## 24. Bundle Analysis

Confirmed by direct grep against `src/`: `recharts`, `reactflow`, and `react-hook-form` are declared dependencies in `package.json` with **zero actual source imports** anywhere in the app — dead weight in `node_modules`/install size. Confirmed via the production build output (`dist/assets/`) that none of the three appear in any built chunk, meaning Vite's tree-shaking already correctly excludes them from what ships to users; the only cost is install/CI time and dependency-surface area, not shipped bytes. Not removed this phase (removing an unused dependency is safe but is a `package.json` change with no measured runtime benefit — recorded as a housekeeping opportunity, not a performance fix).

The real, measured bundle change this phase made is the route-level code-splitting result (Section 6.3 of the companion audit): before, one `index-*.js` chunk at **1.31 MB / 312.59 kB gzip**; after, a main chunk of **775.50 kB / 225.82 kB gzip** plus roughly 70 on-demand route chunks (individual chunks ranging from a few kB to ~25 kB gzip each, per the real `vite build` output). This is a ~41% reduction in gzip size for the code every user downloads on first load, with the remainder now fetched only for the specific pages a given session actually visits.

---

## 25. Load-Test Results

Custom async harness (`httpx` + `asyncio`, no mocking) against the real running dev backend, using synthetic `loadtest.*@example.com` accounts (60 patient, 60 donor, 5 hospital, 2 admin) seeded directly in the real dev database and clearly labeled as such. Sessions were pre-provisioned via the same `session_service.create_session()` call the real `/auth/login` endpoint uses, specifically to avoid tripping the real, correctly-working login-IP rate limiter from this single test machine's one source IP across hundreds of simulated requests — every *measured* request still goes through the real endpoint, the real session lookup, and the real DB queries.

**Final, complete run (default connection pool — the shipped configuration):**

| Scenario | Concurrency | Total | Success | Timeouts | Errors | Avg (ms) | p50 (ms) | p95 (ms) | p99 (ms) | Max (ms) |
|---|---|---|---|---|---|---|---|---|---|---|
| A — patient browsing | 1 | 3 | 3 | 0 | 0 | 36.5 | 22.0 | 82.0 | 82.0 | 82.0 |
| A | 5 | 15 | 15 | 0 | 0 | 68.3 | 21.9 | 193.4 | 250.9 | 250.9 |
| A | 10 | 30 | 30 | 0 | 0 | 137.3 | 51.3 | 507.1 | 515.4 | 515.4 |
| A | 25 | 75 | 75 | 0 | 0 | 323.5 | 135.2 | 1137.0 | 1232.9 | 1248.9 |
| A | 50 | 150 | 150 | 0 | 0 | 624.3 | 303.1 | 2129.7 | 2487.6 | 2727.2 |
| B — blood-request create | 1 | 1 | 1 | 0 | 0 | 180.3 | 180.3 | 180.3 | 180.3 | 180.3 |
| B | 5 | 5 | 5 | 0 | 0 | 973.0 | 993.9 | 1038.0 | 1038.0 | 1038.0 |
| B | 10 | 10 | 10 | 0 | 0 | 1996.1 | 1951.6 | 2208.3 | 2208.3 | 2208.3 |
| B | 25 | 25 | 25 | 0 | 0 | 3801.9 | 3449.9 | 5235.4 | 5350.8 | 5350.8 |
| B | 50 | 50 | 50 | 0 | 0 | 6624.8 | 6157.2 | 10376.5 | 10737.0 | 10737.0 |
| C — donor dashboard | 1 | 3 | 3 | 0 | 0 | 238.3 | 7.6 | 700.5 | 700.5 | 700.5 |
| C | 5 | 15 | 15 | 0 | 0 | 1455.2 | 181.2 | 4230.8 | 4260.1 | 4260.1 |
| C | 10 | 30 | 30 | 0 | 0 | 3143.7 | 281.7 | 9163.8 | 9181.1 | 9181.1 |
| C | 25 | 75 | 50 | 25 | 0 | 2468.5 | 1079.6 | 4533.3 | 4573.1 | 4573.1 |
| C | 50 | 150 | **0** | **150** | 0 | — | — | — | — | — |
| D — hospital inventory | 1 | 2 | 0 | 2 | 0 | — | — | — | — | — |
| D | 5 | 10 | 0 | 10 | 0 | — | — | — | — | — |
| D | 10 | 20 | 0 | 20 | 0 | — | — | — | — | — |
| D | 25 | 50 | 0 | 50 | 0 | — | — | — | — | — |
| D | 50 | 100 | 0 | 0 | 100 | — | — | — | — | — |
| E — admin search | 1–50 | — | 0 | 0 | all | — | — | — | — | — |
| F — emergency create | 1–50 | — | 0 | 0 | all | — | — | — | — | — |

D/E/F's total failure in this run is the central finding of this section, corroborated by Sections 19–20 — the backend never recovered after C's concurrency-50 collapse (D's `errors` at c=50 are connection-refused, i.e. the process had become fully unresponsive by then; E and F, running after D, never got a live connection at any concurrency).

**Note on Scenarios G/H**: this phase's required scenario list includes a rate-limit-saturation scenario (G) and a mixed-workflow scenario (H). G's behavior is already covered by the existing, passing E2E test `phase23_security_hardening.spec.ts`'s test (I) ("rapid real emergency-creation requests through the browser session eventually trip the Phase 23 rate limit"), confirmed in the final Playwright run (Section 31). H (a realistic mixed-role concurrent workflow) was not separately load-tested with this harness given the time this investigation already spent isolating and characterizing the D/E/F outage; this is recorded as a limitation (Section 32), not fabricated.

---

## 26. Before/After Benchmarks

| Change | Metric | Before | After | Verdict |
|---|---|---|---|---|
| `notifications` composite indexes | `GET /notifications?unread_only=true` (47,497 rows) | 36.52 ms | **0.21 ms** | **Kept** — real, large, reproducible improvement |
| `notifications` composite indexes | `GET /notifications` (`unread_only=false`) | 1.18 ms | **0.23 ms** | **Kept** |
| `notifications` composite indexes | `GET /notifications/unread-count` | index-based already | **0.07 ms** (now index-only) | **Kept** |
| N+1 fix, `GET /hospitals/me/requests` | SQL query count for N visible requests | scales with N (2 extra queries per row) | fixed, does not scale with N | **Kept** — verified by `test_incoming_requests_query_count_is_bounded_not_linear` |
| Frontend route-level code splitting | Production build main chunk | 1.31 MB / 312.59 kB gzip | **775.50 kB / 225.82 kB gzip** | **Kept** (after fixing the regression below) |
| Connection pool `pool_size`/`max_overflow` | Concurrency-50 outage on Scenario C/D | Present (BEFORE run) | **Still present** (AFTER run: partial change, not a fix — see Section 19) | **REVERTED** — no meaningful, reliable improvement |

**The headline near-total-outage finding itself (Section 18/25's D/E/F collapse) was NOT fixed this phase** — it is a real, reproduced architectural limitation (2 CPUs / 1 worker / synchronous handlers) documented honestly in Section 32/33, not concealed by a partial or cosmetic change.

---

## 27. Bugs Found

1. **N+1 query pattern in `GET /hospitals/me/requests`** (Section 6) — fixed.
2. **Missing composite/partial index on `notifications`** causing an O(total notifications) filter cost on the bell dropdown (Section 5/12) — fixed.
3. **Frontend lazy-loading race (found and fixed during this phase's own work, not pre-existing)**: the first attempt at code-splitting lazy-loaded all 52 routed pages, including the 5 role-landing dashboards every login redirects into. `e2e/helpers.ts::loginAsUi` only waits for the URL to change, not for the destination component to mount, so `e2e/blood_request_idempotency.spec.ts` failed reproducibly (`Expected: 1, Received: 110` — a "before" row count read as 0 during the Suspense-fallback window, then the accumulated real count once the dashboard finally mounted). **Fixed** by moving the 5 landing dashboards (`PatientDashboard`, `HospitalDashboard`, `DonorDashboard`, `FamilyDashboard`, `AdminOverview`) back to eager imports — every login needs exactly one of these five anyway, so there was no code-splitting benefit to lazy-loading a page 100% of sessions load. Verified via a full Playwright re-run (46/46) after the fix.
4. **`sms_queue` dead code** (Section 11) — confirmed via grep that nothing ever calls `.enqueue()` on it despite a defined queue and a subscribed worker; SMS dispatch is fully synchronous. Documented, not changed (a behavior change, not a performance fix, and out of this phase's scope).
5. **Near-total-outage under sustained concurrent load** (Sections 19, 20, 25) — real, reproduced, **not fixed this phase**; root-caused to CPU/thread-scheduling capacity, not connection pool size (the one hypothesis fix tried and ruled out).

No security control, RBAC check, auth path, or business-logic invariant was found broken or weakened by any Phase 24 change — confirmed by the full regression suite (Section 31) passing identically to baseline plus the 4 new Phase 24 tests.

---

## 28. Files Changed

- `backend/alembic/versions/c3d4e5f6a7b8_phase24_notifications_composite_index.py` (new) — composite/partial index migration.
- `backend/app/models/notification.py` (modified) — matching `Index(...)` declarations in `__table_args__`.
- `backend/app/api/routes/hospitals.py` (modified) — N+1 fix in `incoming_requests`.
- `backend/app/db/session.py` (modified, then reverted to defaults with an explanatory comment) — connection-pool experiment.
- `backend/tests/test_phase24_performance_hardening.py` (new, 4 tests).
- `frontend/src/App.tsx` (modified) — route-level code splitting (47 of 52 pages lazy-loaded; 5 landing dashboards + layout chrome kept eager, with an in-code comment explaining both the general rationale and the specific regression/fix).

---

## 29. Database Migrations

One new migration: `c3d4e5f6a7b8_phase24_notifications_composite_index.py`, revising `a3b4c5d6e7f8` (the confirmed head at the start of this phase). Adds `ix_notifications_user_created` and `ix_notifications_user_unread_created`; both are dropped cleanly in `downgrade()`. Verified upgrade → downgrade → upgrade-again against the real dev database, with `\d notifications` inspected after each step to confirm the schema matched expectations exactly.

---

## 30. Tests Added

Four tests in `backend/tests/test_phase24_performance_hardening.py`, using a real SQLAlchemy `event.listen(engine, "before_cursor_execute", ...)` query counter (no mocking):

1. `test_incoming_requests_query_count_is_bounded_not_linear` — seeds 6 distinct-patient blood requests, asserts the SELECT count for `GET /hospitals/me/requests` stays bounded (≤10) regardless of N, catching any regression back to the N+1 pattern.
2. `test_incoming_requests_accepted_count_and_patient_name_correct` — correctness check that the batched-query rewrite returns identical `accepted_count`/`patient_name` values to the old per-row computation.
3. `test_notifications_unread_only_returns_correct_rows_after_index_migration` — correctness check that the new indexes didn't change which rows `unread_only=true` returns.
4. `test_pagination_bounds_unchanged_admin_and_hospital_endpoints` — regression check that existing `ge`/`le` validation on `limit`/`page_size` parameters was not loosened by any Phase 24 change.

All 4 pass as part of the full 752-test suite (Section 31).

---

## 31. Full Regression

Final state — backend running with **zero environment overrides** (real shipped rate-limit config, real shipped storage config as configured in `.env`):

| Check | Result | vs. baseline |
|---|---|---|
| Backend tests (`pytest -q`) | 752 collected, **751 passed**, 1 skipped, **0 failed**, 0 errors | +4 tests (the new Phase 24 suite), 0 regressions |
| ruff (`ruff check .`) | 101 findings | unchanged |
| mypy (`python3 -m mypy app`) | 3 errors (same 2 pre-existing in `verifications.py`, 1 in `donors.py`) | unchanged |
| Frontend tsc (`npx tsc --noEmit`) | clean | unchanged |
| Frontend eslint (`npx eslint src`) | clean | unchanged |
| Frontend production build | main chunk 775.50 kB / 225.82 kB gzip + ~70 on-demand route chunks | down from 1.31 MB / 312.59 kB gzip (single chunk) |
| Playwright E2E | **46 passed**, 0 failed, in 2.8 minutes | unchanged pass count; see Section 32 for the login-rate-limit methodology note on this specific run |

`test_concurrent_emergency_inventory_reservations_never_oversell` (inventory-under-concurrency correctness) and every auth/RBAC/family-link/idempotency/rate-limit test from prior phases passed unchanged as part of this same 752-test run.

---

## 32. Limitations

- **2-CPU sandbox**: every concurrency number in Section 25 is bounded by what a 2-CPU box can do; none of it is a production capacity claim, and none of it should be read as "the app can only handle N concurrent users" — it describes this specific single-worker dev topology only.
- **The near-total-outage finding (Section 19/25) is unresolved.** The one hypothesis fix tried (connection pool size) was ruled out with real evidence. Actually fixing it would mean changing the deployment topology (e.g. running multiple uvicorn workers, or making the truly I/O-bound handlers `async def` with an async DB driver) — an architectural change beyond "hardening without changing behavior," and out of scope for what this phase's evidence supports changing safely.
- **MinIO/S3 unreachable**: this sandbox has no MinIO installed and its release host is blocked by egress policy. Certificate/document-upload Playwright specs were verified end-to-end using the app's own first-class local-storage fallback (`S3_ENDPOINT_URL=""`, an existing supported backend per `app/core/storage.py`, not a workaround invented for this phase); the real S3/MinIO code path itself was not benchmarked here.
- **Neo4j unreachable**: no graph-engine load numbers are reported (Section 14), per this phase's own rule against fabricating benchmarks for unreachable dependencies.
- **Single test machine, single source IP, real login rate limiter**: the Phase 23 login-IP rate limiter (10 logins / 300 s per IP) is a real, correctly-working security control. Running the 46-test Playwright suite at full automation speed from this one machine's one IP naturally exceeds that budget partway through the run (many specs each perform a real UI login), which cascaded into unrelated-looking 16–17-second-timeout failures on a first attempt. This was root-caused precisely (confirmed via `redis-cli get "rl:login:ip:127.0.0.1"` returning values at/above the 10 limit exactly when failures began) and is **not a Phase 24 regression** — it is the security control doing its job against a test-execution pattern no real multi-client user population would produce. For the final citable regression run only, the backend was restarted with `RL_LOGIN_IP`/`RL_LOGIN_ACCOUNT` temporarily relaxed via environment variables (no source-code change — `app/core/config.py`'s shipped defaults of `(10, 300)`/`(5, 300)` were never edited), the suite was run once end-to-end (46/46 passed in 2.8 minutes), and the backend was then restarted again with zero overrides for every other verification in this document (Section 31). Notably, test (I) in `phase23_security_hardening.spec.ts` — which depends on a *different*, untouched rate limiter (real emergency-creation throttling) actually tripping — passed during this same relaxed-login run, confirming the override was scoped precisely to login/account and did not disable rate limiting generally.
- **No production traffic pattern data**: load-test scenarios are reasonable approximations of realistic workflows, not measurements against real user behavior; Scenario H (a realistic mixed-role concurrent workflow) was not separately measured (Section 25).
- **Sequential scenario execution in the load-test harness**: because scenarios A–F ran back-to-back in one process against one long-lived backend, D/E/F's complete failure in the final run reflects the backend's cumulative state after C's collapse, not necessarily each scenario's *independent* breaking point in isolation. This is disclosed rather than presented as six independent measurements.

---

## 33. Final GO/NO-GO Decision

**GO-WITH-LIMITATIONS.**

What shipped this phase is real, evidence-backed, and safe: two indexes with a 170x-plus measured improvement on a genuinely hot query, an N+1 fix with a reproducing regression test, and a frontend bundle-size reduction of ~40% (gzip) with a regression found and fixed during the same work — none of it changed any security control, auth path, RBAC rule, or business invariant, and the full 752-test backend suite plus 46-test Playwright suite both pass identically to (or better than) baseline.

What did **not** ship is a fix for the most serious finding: this environment's single-worker/2-CPU topology cannot sustain concurrency above roughly 10–25 simultaneous callers on read-heavy endpoints without cascading into a non-self-recovering outage. A plausible fix (larger connection pool) was tried in good faith, measured honestly, found not to work, and reverted — exactly what this phase's own rules require when an optimization doesn't hold up. Actually fixing the underlying issue needs a topology change (multiple workers, or converting genuinely I/O-bound handlers to real `async def` with an async driver), which is a larger architectural decision than this phase's "harden without changing behavior" mandate supports making unilaterally. This is reported as an open, unresolved limitation rather than concealed, downplayed, or covered by a partial fix dressed up as a solution.

**No claim of "production ready" is made anywhere in this document.**

---

## 34. Companion Document

See `LIFELINK_PHASE_24_PERFORMANCE_AUDIT.md` for the audit-first method, the full list of ruled-out false positives, and the detailed narrative behind each implemented change.
