# Final test report: gap-elimination round

All runs used the final code, on a 2-vCPU / 8 GB sandbox, with:

* real PostgreSQL 16;
* real Redis 7;
* a real ClamAV `clamd` with the project-local signature database;
* the S3 protocol served by moto_server, a stand-in for MinIO;
* SMTP delivered to MailDev, a stand-in for Mailpit.

Nothing below is estimated. Every count comes from a JUnit file or a log that is stored in `evidence/`.

## 1. Backend (pytest)

| Run | Result | Evidence |
|---|---|---|
| **Final full regression.** `CLAMD_TEST_REQUIRED=1`, `S3_TEST_ENDPOINT` (moto), `SMTP_TEST_*` (MailDev), `QA_ROUTE_LOG` | **1,388 passed, 0 failed, 0 errors, 1 skipped**, out of 1,389 (31 min 19 s) | `evidence/backend_junit_final.xml` |
| Follow-up run of the 3 files changed after the full run (4 success-path tests added for new read endpoints) | **53 passed, 0 failed** | `evidence/backend_junit_followup.xml` |
| Follow-up after security fix S12 (`client_ip` no longer trusts `X-Forwarded-For`): every auth, session, rate-limit, CSRF, authorization and audit-chain test file (10 files) | **148 passed, 0 failed** | `evidence/backend_junit_s12.xml` |
| **Total on the final code** | **1,394 distinct tests passed, 1 skipped, 0 failed**: 1,388 in the full run, plus the 4 success-path tests and the 2 S12 tests added afterwards | — |

**The skipped test:** `test_gap_neo4j.py::test_real_neo4j_paths_equal_relational_paths`. It needs a live Neo4j server, and none can be obtained here (registries and dist.neo4j.org are blocked). It is skipped with that reason; it is not marked as passed.

**This round added 115 backend tests:**

| File | Tests |
|---|---|
| `test_gap_pathway_engine.py` | 36 |
| `test_gap_features_api.py` | 42 |
| `test_gap_neo4j.py` | 6 |
| `test_gap_clamav_real.py` | 7 |
| `test_gap_audit_security.py` | 5 |
| `test_gap_infrastructure.py` | 4 |
| `test_gap_concurrency.py` | 3 |
| `test_gap_rate_limit.py` (2 of them added with fix S12, after the full run) | 4 |
| `test_gap_e2e_scenario.py` (35 steps) | 1 |
| `test_migration_integrity.py` | 7 |

Further guards were added to `test_admin.py` (keyset pagination) and `test_seed_demo_data.py` (reproducibility).

### How the final run got to zero failures

Each failure was fixed at its cause. No assertion was weakened and no test was deleted.

| Run | Result | Causes and fixes |
|---|---|---|
| r1 | 28 failed / 1,242 passed | Tests written for the old semantics (exact-type only; unverified facilities as sources; straight-line fallback) were rewritten to the new behaviour, which is stricter. Product bugs were fixed: a NOT NULL actor on system replans, DISTINCT on JSON columns, and false GRAPH crediting. |
| r2 | 4 failed / 1,375 passed | One **real bug**: once audit-log reads were themselves audited, the offset-paginated log repeated entries across pages. It now uses a keyset cursor, with a new regression test. Three stale expectations were also updated: a race test that allowed only one of the two legal winners, and two timelines that predated the new `receipt_outcomes` event, which is now also asserted. |
| r3 (first attempt) | 358 errors | **Environment, not code.** An ad-hoc pytest session started in parallel dropped the shared test schema. The suite was re-run alone. |
| r3 (second attempt) | — | **Environment.** The sandbox ran out of memory and restarted, killing the run. Heavy jobs were serialised from then on. |
| **r3 (final)** | **1,388 passed, 1 skipped** | — |

## 2. API coverage

**160 / 160 operations exercised, 159 / 160 with a 2xx response.** The remaining one, `POST /admin/graph/resync`, needs Neo4j and correctly returns 503 here. See API_COVERAGE.md.

## 3. Frontend

| Check | Result |
|---|---|
| Vitest | **170 / 170 passed**, 22 files |
| `tsc --noEmit` | 0 errors |
| ESLint | 0 problems |
| Production build | succeeds |
| `npm audit --omit=dev` | 0 vulnerabilities |
| v8 coverage | 64.18% lines (FRONTEND_COVERAGE.md) |

## 4. Browser end-to-end (Playwright, Chromium)

The harness mirrors the CI `e2e` job exactly. On a fresh `lifelink_e2e` database it runs:

1. migrations;
2. the E2E user seed and the demo seed;
3. uvicorn and `vite preview`;
4. `npx playwright test`;
5. `scripts/check_invariants.py`.

The environment is the CI job's: `RQ_SYNC=true`, real clamd, no S3, no Neo4j and no mail server. `RL_LOGIN_IP` is relaxed for this job only, because the whole suite signs in from 127.0.0.1; `.github/workflows/ci.yml` documents why.

| Item | Result |
|---|---|
| **Final full run** | **105 passed, 1 failed, 1 skipped** of 107 (9.4 min) |
| The 1 failure | `phase26_donation_idempotency` "resubmitting after a failed attempt". A **test race**, not a product defect. The spec read the "before" row count while the donor's list was still loading (it read 0 while 3 rows were arriving), then saw 4. The backend log shows exactly **one** `POST /donors/me/donations → 201` for that test. The spec now waits for the list response and its rendered rows before counting. The whole spec was re-run against the same stack: **3 / 3 passed**. |
| The 1 skip | `clean_start.spec.ts`, by design. It runs only inside the clean-start harness, where it **passed** (CLEAN_START_VERIFICATION.md). |
| **Final** | **107 / 107 accounted for:** 106 passing, 1 run only in the clean start (passed there) |
| Invariants and audit chain after the E2E run | **All OK** (invariants #1–#45; audit chain verified, 326 sealed entries) |
| axe log of the gap pages | 33 scans, 0 violations (`screenshots/axe-gap-pages.json`) |

Logs: `evidence/playwright_full_run.log`, `evidence/playwright_junit.xml`.

**Earlier attempts in this round.** These are kept as evidence, and each one changed something:

1. **18 passed, 47 failed, all HTTP 429.** The new gap spec's sign-ins used up the per-IP login budget of the specs after it.
2. **99 passed, 7 failed, all timeouts.** The harness, unlike CI, pointed `RQ_SYNC=true` at a real mail catcher. Every new request then e-mailed about 280 donors inline at about 105 ms each, roughly 30 s per request. The harness now matches CI.
3. **37 passed, 43 failed.** This run used the strict login limit without the CI relaxation (`evidence/playwright_run_strict_login_limit.log`). Investigating it uncovered **security defect S12**: `client_ip()` trusted a client-supplied `X-Forwarded-For`, so any client could bypass every per-IP limit by rotating that header. It is fixed and tested (SECURITY_AUDIT.md). The suite therefore cannot use header tricks to avoid the limit; it relies on the documented test-only relaxation.

## 5. Static analysis and dependencies

| Check | Result |
|---|---|
| `ruff check .` (backend) | All checks passed |
| `pip-audit -r requirements.lock` | No known vulnerabilities |
| `npm audit --omit=dev` | 0 vulnerabilities |
| CSRF / origin sweep over every state-changing route | PASS (`test_phase_h_security.py`) |

## 6. Migrations, clean start, experiments

| Area | Result |
|---|---|
| Migration integrity | PASS. Fresh, existing, stale-stamp recovery and foreign-schema refusal (INFRASTRUCTURE_TEST.md §2). |
| Clean start from an empty machine | **PASS WITH EXTERNAL VALIDATION REQUIRED** (CLEAN_START_VERIFICATION.md) |
| Paper experiments on a freshly seeded database | `e4_route_exact_replanning.json`, `e5_e6_coverage_matching.json`, `benchmark_pathways.json` |
