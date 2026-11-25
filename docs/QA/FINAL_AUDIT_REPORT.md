# LifeLink: final audit report (QA round 2026-10-20)

**Final status: PASS WITH DOCUMENTED LIMITATIONS.**

**What passed.** Every mandatory check that could run in this environment passed after the fixes in §12. No critical defect is open.

**What is outstanding.** Each item is listed in §13:

* the integrations that need third-party access: Neo4j, Gmail, MSG91, the live geocoder and live map tiles;
* the official container base images;
* clinical review.

This is a research prototype. Nothing here is clinical validation, hospital readiness or patient-safety certification.

## 1. Repository and build identity

| Item | Value |
|---|---|
| Baseline | Tag `qa-baseline-2026-10-20` = commit `0422954` (clean tree) |
| Final | Tag `qa-final-2026-10-20`: the commit that adds this report. `git log -1 qa-final-2026-10-20` |
| Backend build | Python package from `backend/requirements-dev.lock` (hashed); Docker image `lifelink-api` built from `backend/Dockerfile` in the Docker clean start |
| Frontend build | `npm run build` (Vite) → `dist/`; Docker image `lifelink-frontend` (nginx) from `frontend/Dockerfile` |
| Database schema | Alembic head `e1f2a3b4c5d6`; 31 revisions in one linear chain |

## 2. Environment and versions

* **Host:** Linux 6.18 sandbox VM with 2 vCPUs and 8 GB RAM.
* **Backend stack:** Python 3.11.17, FastAPI 0.141.1, SQLAlchemy 2.0.52, pytest 9.1.1.
* **Services:**
  * PostgreSQL 16.15, Redis 7.0.15, ClamAV 1.5.4 (project signature database);
  * MinIO RELEASE.2025-04-22T22-12-26Z and Mailpit v1.21.8, as official release binaries;
  * Docker Engine 29.8.2 with Compose v5.5.1, on locally built base images.
* **Frontend stack:** Node 22.22.0 (CI pins Node 20), React 19.2.8, Vitest 5.0.1, Playwright 1.56.0 with its pinned Chromium.
* **Egress:** outbound traffic goes through an allow-listing proxy. Container registries, Neo4j downloads, Gmail SMTP, MSG91, Nominatim, OpenStreetMap tiles and the ClamAV mirror are unreachable.

## 3. Exact commands executed

Every command, with paths and environment variables, is in **REPRODUCE_TESTS.md**. The output of each run is in `docs/QA/evidence/`:

* `baseline/` and `final/`: the backend, vitest and Playwright runs, static checks and invariants;
* `workflows/` and `frontend-routes/`: browser evidence;
* `failure-injection/`, `security/` and `performance/`;
* `clean-start/native/` and `clean-start/docker/`;
* `inventory/`.

## 4. Tests discovered, passed, failed, skipped and blocked

| Suite | Baseline (tag) | Final (after all fixes) | Evidence |
|---|---|---|---|
| Backend (pytest; real PostgreSQL, Redis, clamd; infra tests against real MinIO and Mailpit) | 1,398 collected: **1,396 passed, 1 failed** (D6), 1 skipped | 1,406 collected: **1,405 passed, 0 failed**, 1 skipped (35 min 09 s) | `evidence/baseline/backend_baseline.log`, `evidence/final/backend_final.log`, JUnit XML |
| Frontend unit (Vitest, 22 files) | **170 / 170** | **172 / 172** (2 new) | `evidence/baseline/vitest_baseline.log`, `evidence/final/vitest_final.log` |
| Browser E2E (Playwright, CI-identical harness) | **106 passed, 2 skipped, 0 failed** (recorded run on identical application code) | **107 passed, 2 skipped, 0 failed** (1 new spec; 8.4 min) | `evidence/final/playwright_ci_harness.log`, `playwright_junit.xml` |
| Invariants after E2E | — | **18 / 18 invariant checks OK**; audit chain verified (340 entries, 0 unsealed) | `evidence/final/invariants_after_e2e.txt` |
| QA evidence workflows (full stack, real worker and Mailpit) | — | **10 / 10** (9 + the new failover spec) | `evidence/workflows/*.log` |
| Static checks | ruff, tsc, eslint, build, alembic check: all clean | All clean | `evidence/baseline/static_*.txt`, `evidence/final/static_final.txt` |

**Skipped and blocked:**

* **Backend (1 skipped):** `test_real_neo4j_paths_equal_relational_paths`. Needs a Neo4j server: BLOCKED_BY_ENVIRONMENT.
* **Playwright (2 skipped):** `clean_start.spec.ts` and `container_frontend.spec.ts` need a freshly built or containerised stack. Both **passed** in the clean starts of this round (§10).

**New tests in this round:**

* 8 backend tests in `tests/test_qa_round_races.py`;
* 2 vitest tests in `Live.test.tsx`;
* 1 Playwright spec, `e2e/qa_failover_ui.spec.ts`;
* QA tooling in `docs/QA/tools/`: inventory, crawl parameters, failure injection, security probe, query counts and stack scripts.

## 5. API operations and frontend routes

| Inventory | Count | Covered | Detail |
|---|---|---|---|
| API operations (from the live FastAPI app) | **160** | **160 exercised**: 154 with success and 4xx responses observed; 5 public or session-probe operations with success only; 1 (`POST /admin/graph/resync`) BLOCKED (needs Neo4j; its 401/403/503 behaviour is verified) | FEATURE_TEST_MATRIX.md §3 |
| Frontend routes (`App.tsx`) | **63** | **63**: 62 crawled at 3 widths (186 visits) plus the catch-all route by e2e; 44 also opened by URL in a workflow spec; **75/75** guarded-route authorisation checks denied | FEATURE_TEST_MATRIX.md §4 |
| Database tables | 39 | Schema equals models (`alembic check`, `test_alembic_head_schema_matches_orm_metadata`) | PROJECT_INVENTORY.md |

## 6. End-to-end workflows verified

Phase 4, areas A–H, are mapped item by item to tests in FEATURE_TEST_MATRIX.md §1. Every area is **IMPLEMENTED_AND_VERIFIED**. Area E (planning) runs on the relational engine, because Neo4j is blocked.

**Browser workflows with screenshot evidence** (SCREENSHOT_INDEX.md: 51 workflow screenshots, all PASS):

* registration with a real e-mail link through the worker and Mailpit;
* a donation verified by a hospital, then the certificate;
* document upload with malware scanning;
* inventory by component;
* partial and then full fulfilment, with live updates;
* the shipment lifecycle with reconciliation;
* admin oversight with audit-chain verification;
* realtime loss and recovery;
* access control;
* **new:** route failover driven from the admin UI.

**Complete emergency-to-delivery workflow:** SOS → plan → reserve → route failure → failover → shipment with cold chain → receipt → resolve → audit chain. It passed in both clean starts (§10).

## 7. Security findings and retest outcomes

Details are in **SECURITY_FINDINGS.md**.

* **Live black-box probe** against the production entrypoint: **27/27 pass**.
* **Fixed and retested:**
  * D1: donor double-booking race (Medium);
  * D2: no anti-framing or nosniff headers on the shipped frontend (Medium), verified on the real container;
  * D3: unbounded graceful shutdown in the image (Low);
  * D4: start-up storage failure swallowed (Low);
  * D5: a public page calling an authenticated endpoint (Low).
* **Dependency audits:**
  * pip-audit: 0 vulnerabilities;
  * npm, shipped bundle: 0;
  * npm, full tree: 7 dev-tooling advisories, gate passing; 1 moderate advisory is new and recorded.
* **bandit:** 0 high; 1 medium and 4 low, triaged and accepted with rationale.

**Secret scan** (`evidence/security/secret_scan.txt`) found:

* generated MinIO credentials in 0 files;
* credential or key patterns (AWS keys, private keys, GitHub/Slack/Google tokens, JWTs): 0;
* session cookies with a value in the evidence: 0;
* verification tokens in the evidence: 0;
* tracked `.env` files: 0;
* filled secret settings: 1 match, a placeholder (`MSG91_AUTH_KEY=…`).

`.env.example` holds placeholders and public development defaults only. All screenshots use synthetic accounts and show no token, cookie or password.

## 8. Concurrency and failure injection

**Concurrency:** all 7 races of the audit brief are covered with real threads and barriers (FEATURE_TEST_MATRIX.md §2).

* Race 2 exposed **defect D1**, which is now fixed.
* Races 5 and 7 and the multi-failure sequence received new tests. They passed the first time.

**Live failure injection** (`evidence/failure-injection/`) used the production entrypoint and real services.

* Run 1: **25/27**. One failure was a harness bug. The other was **real defect D3**: shutdown took 30.1 s with an open SSE stream.
* Run 2, after the fixes: **27/27**. It covered:
  * **Redis and PostgreSQL outages:** honest 503s, recovery and pool re-establishment;
  * **MinIO outage:** 503 with nothing half-written, then a byte-identical download;
  * **clamd outage:** SCAN_FAILED quarantine, not downloadable, not approvable, released by a re-scan;
  * **worker outage:** the job waits and is delivered once;
  * **SMTP outage:** the job lands in the failed registry with no false success, and a resend works;
  * **graceful shutdown:** 5.22 s with an open SSE stream.

## 9. External integrations: real services versus mocks

Details, and the exact procedure for each blocked item, are in **INTEGRATION_STATUS.md**.

| Real service (verified) | Blocked (stand-in or unit-level only, never counted as real) |
|---|---|
| PostgreSQL, Redis, RQ worker, deadline monitor, Mailpit (SMTP), MinIO (S3), clamd, Docker Compose stack | Neo4j, Gmail, MSG91, live geocoder, live map tiles, official base images, official ClamAV signatures |

## 10. Clean-start reproducibility

| Path | Result | Evidence |
|---|---|---|
| **Docker:** `down -v` → `.env` from `.env.example` → `up --build` → checks inside the API container → browser against the nginx container → security-header check | **11/12 checks PASS, 1 BLOCKED (Neo4j)**. All services healthy (worker and deadline monitor included). `clean_start.spec.ts` and `container_frontend.spec.ts` passed. The new nginx headers were present on `/` and on assets | `evidence/clean-start/docker/` |
| **Native:** fresh source copy → venv from the hashed lock → new `initdb` cluster → fresh Redis and clamd → `npm ci` + build → migrations → seed → checks → browser | **10 PASS, 2 BLOCKED.** Neo4j is unavailable. The S3 check ran against this harness's S3 stand-in by design; real MinIO is covered by the Docker run and §9. The browser check passed; the API log had 0 errors | `evidence/clean-start/native/` |

Both runs ended with the same synthetic seed: 1,066 users, 61 facilities and 85 routes. The full emergency-to-delivery workflow passed in both. The reproducibility claim is limited to locally built base images (§13).

## 11. Performance

Details are in **PERFORMANCE_RESULTS.md**.

* **Planner benchmark:** reproducible; feasibility counts were identical to the previous run with the same seed. p95 was 14.7 / 26.2 / 178.2 ms at 20 / 100 / 400 facilities.
* **Live load test** (2 workers, 100 open SSE streams): 0 errors. Throughput was 65–72 req/s on 2 shared vCPUs; p95 was 41 ms with 1 client. The 100 streams used 2 Redis connections.
* **N+1 check:** the hot endpoints run a constant or bounded number of statements.
* **Limits:** these are not production capacity figures.

## 12. Defects fixed and regression tests added

Details are in **DEFECTS_FIXED.md**. Each fix has a regression test that **fails on the code before the fix**.

| ID | Severity | Defect | Changed files | Regression test |
|---|---|---|---|---|
| D1 | Medium | Concurrent accepts could commit one donor to two open requests | `backend/app/services/donor_commitment.py` | `test_one_donor_accepting_two_{requests,emergencies}_at_once_…` |
| D2 | Medium | Shipped frontend frameable; no nosniff | `frontend/nginx.conf` | `test_frontend_nginx_sends_framing_and_sniffing_protection_everywhere` + live container check |
| D3 | Low | Image CMD without bounded graceful shutdown | `backend/Dockerfile` | `test_every_production_api_entrypoint_bounds_graceful_shutdown` + live 5.22 s |
| D4 | Low | Start-up storage failure swallowed | `backend/app/main.py` | `test_storage_failure_at_startup_is_logged_not_swallowed` |
| D5 | Low | Public page called an authenticated endpoint | `frontend/src/pages/MathematicalModel.tsx` | vitest "MathematicalModel (QA round)" + re-crawl |
| D6 | Low | Test depended on PATH for its interpreter | `backend/tests/test_phase25_qa_hardening.py` | The test itself under a minimal PATH |

**Other changes:**

* new QA tooling and tests (§4);
* `docs/qa` renamed to `docs/QA`, because the lower-case name would collide on case-insensitive file systems; the earlier round's files are kept in `docs/QA/earlier_round/`;
* `.gitignore` now versions QA screenshot evidence;
* the Docker clean-start harness gained a security-header step.

No business rule was changed, no test was deleted or weakened, and no assertion was relaxed.

## 13. Remaining defects, risks, blockers and clinical limitations

* **Open defects:** none known.
* **Blockers (environment):** Neo4j, Gmail, MSG91, the live geocoder, live map tiles, the official container base images, the official ClamAV signatures, production-scale hardware, a manual screen-reader pass and an independent penetration test. Each has a procedure in INTEGRATION_STATUS.md or KNOWN_LIMITATIONS.md.
* **Risks and product gaps:**
  * **P1–P2:** a fixed-time route model and no sensor integration.
  * **P3:** bounded per-row expiry sweeps on two inventory endpoints.
  * **P4:** dev-only npm advisories, which need the Tailwind 4 migration.
  * **P6:** bundle size.
* **Clinical limitations:** see CLINICAL_ASSUMPTIONS_AND_LIMITATIONS.md.
  * **The 56-day donation interval has no cited source and may not match the Indian guidance the seed data imply**: it needs review by a qualified professional, and it was not changed by this round.
  * There is no crossmatch, antibody screen, irradiation, CMV or TTI handling.
  * Compatibility and shelf-life sources are US and Australian.
  * The prototype must not be used for real transfusion decisions.

## 14. Evidence

| What | Where |
|---|---|
| Index of all screenshots | `docs/QA/SCREENSHOT_INDEX.md` |
| Raw evidence | `docs/QA/evidence/` (`baseline`, `final`, `workflows`, `frontend-routes`, `emergency-planning`, `accessibility`, `failure-injection`, `security`, `performance`, `clean-start`, `inventory`) |
| QA tools | `docs/QA/tools/` |
| Earlier rounds, unchanged | `docs/final-verification/`, `docs/QA/earlier_round/` |

## 15. Final status

**PASS WITH DOCUMENTED LIMITATIONS.**

* Every mandatory check in the verified scope passed: backend, frontend, browser, invariants, static checks, security probe, failure injection, both clean starts, accessibility automation and the performance smoke test.
* All six defects found were fixed and retested.
* The outstanding external validations and the clinical review are listed in §13. None of them is claimed as done.
