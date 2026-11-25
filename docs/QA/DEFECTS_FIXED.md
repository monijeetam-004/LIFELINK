# Defects found and fixed in this QA round

**How each fix was made:**

1. Find the root cause.
2. Make the smallest safe fix.
3. Write a regression test, and **show that it fails on the code before the fix** (the "before" column).
4. Run that test on its own, then the relevant suite, then the full regression after all changes.

No existing assertion was weakened and no test was skipped.

**Baseline:** tag `qa-baseline-2026-10-20` (commit `0422954`).

| ID | Severity | Area | Found by |
|---|---|---|---|
| D1 | **Medium** (data integrity) | Donor commitments | New concurrency test (audit brief, race 2) |
| D2 | **Medium** (security: clickjacking) | Frontend image | Live security probe (`evidence/security/security_probe_live.txt`) |
| D3 | Low (availability) | API container image | Live failure injection (`evidence/failure-injection/run1_before_fixes.txt`) |
| D4 | Low (operability) | API start-up | bandit B110 (`evidence/security/bandit_app.txt`) |
| D5 | Low (UX / noise) | Frontend `/mathematical-model` | Route crawl (`evidence/frontend-routes/crawl-results.json`) |
| D6 | Low (test portability) | Backend test suite | Baseline run (`evidence/baseline/backend_baseline.log`) |

## D1. One donor could commit to two open requests at once

* **Symptom.** Two `ACCEPTED` responses from the same donor arriving at the same moment, on two different open blood requests (or two emergencies), **both** returned 200. The donor ended up committed in two places, a false sense of supply for one of them.
* **Root cause.** `donor_commitment.find_conflicting_commitment` was a check-then-insert with no serialization. Each transaction ran the "does this donor already hold a commitment?" query before either had written its row, under READ COMMITTED. The Phase 31 tests covered only sequential calls.
* **Fix.** `backend/app/services/donor_commitment.py` now locks the donor's `donor_profiles` row (`SELECT … FOR UPDATE`) before the check.
  * The second request waits for the first to commit, then sees its commitment and is refused with 409.
  * Only that donor's row is locked, so other donors are not serialized.
  * The lock order is always request/emergency row → donor row, so no lock cycle exists.
* **Tests.** Both use real threads and a barrier, and the first repeats 5 times:
  * `tests/test_qa_round_races.py::test_one_donor_accepting_two_requests_at_once_commits_to_exactly_one`, which also asserts that exactly one ACCEPTED row exists in the database;
  * `…::test_one_donor_accepting_two_emergencies_at_once_commits_to_exactly_one`.
* **Before:** both tests fail with `[200, 200] != [200, 409]`. **After:** both pass, and the Phase 31 suite (`test_phase31_domain_fixes.py`, 20 tests) still passes.

## D2. The shipped frontend could be framed (clickjacking)

* **Symptom.** The frontend image's nginx served the SPA with **no** `X-Frame-Options`, `frame-ancestors`, `X-Content-Type-Options` or `Referrer-Policy`.
* **Root cause.** `vite-csp-plugin.ts` delivers the CSP in a `<meta>` tag, where browsers ignore `frame-ancestors`. The plugin documents that framing protection must come from "whatever static host serves dist/". That host is the repository's own `frontend/nginx.conf`, which never set it.
* **Fix.** `frontend/nginx.conf` now sends four headers: `X-Frame-Options: DENY`, `Content-Security-Policy: frame-ancestors 'none'`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: strict-origin-when-cross-origin`.
  * They are set at server level and repeated in `location /assets/`, because nginx does not inherit `add_header` into a location that sets its own.
* **Tests.**
  * `tests/test_qa_round_races.py::test_frontend_nginx_sends_framing_and_sniffing_protection_everywhere`. **Before:** fails. **After:** passes.
  * Verified on the **real container** in the Docker clean start (`evidence/clean-start/docker/docker_clean_start_run.log`, step 16b): both `/` and a hashed asset return the headers.
  * The containerised browser specs still pass, with 0 console errors.

## D3. The API image did not bound graceful shutdown

* **Symptom.** With an open SSE stream, a SIGTERM'd API kept "waiting for connections to close" for **30.1 s**. That is longer than Docker's default 10 s stop timeout, so the runtime would SIGKILL the process and skip the lifespan shutdown.
* **Root cause.** `scripts/run_production.sh` passes `--timeout-graceful-shutdown 5` (an earlier QA fix), but the image's default `CMD` in `backend/Dockerfile`, which the Dockerfile itself calls "exactly the production path", did not.
* **Fix.** `--timeout-graceful-shutdown 5` was added to the `CMD`.
* **Tests.**
  * `test_every_production_api_entrypoint_bounds_graceful_shutdown`. **Before:** fails. **After:** passes.
  * Live re-run through the production entrypoint: the API exits in **5.22 s** with the stream open (`evidence/failure-injection/run2_after_fixes.txt`).

## D4. A storage failure at start-up was silently swallowed

* **Symptom.** If the object store was down or misconfigured when the API started, `ensure_bucket()` raised and the error was discarded (`except Exception: pass`), leaving no log line.
* **Fix.** `backend/app/main.py` now logs a warning on `lifelink.storage` with the exception **type only**, no detail that could carry a secret. Start-up still continues as before: uploads return a clean 503 until the store is back.
* **Test.** `test_storage_failure_at_startup_is_logged_not_swallowed`. **Before:** fails. **After:** passes.

## D5. A public page called an authenticated endpoint

* **Symptom.** `/mathematical-model` is public: it shows "Sign in to run this page" to anonymous visitors. It still requested `GET /pathways/config`, which returns 401, so every anonymous visit logged a console error and a failed API call. The crawl recorded this at all three widths.
* **Root cause.** The page's `useQuery` ran whatever the sign-in state, while `LivePageShell` only *renders* the content for allowed users. `SimulationMode` already gates its queries with `enabled: !!user`.
* **Fix.** `frontend/src/pages/MathematicalModel.tsx` uses `enabled: !!user`.
* **Tests.** Two vitest tests in `src/components/live/Live.test.tsx` ("MathematicalModel (QA round)"):
  * no call is made when anonymous;
  * the call is made once when signed in.
* **Before:** the first test fails, because the call was made once. **After:** 7/7 tests in the file pass.

## D6. A migration test depended on `python3` being the venv interpreter

* **Symptom.** In the baseline run (`.venv/bin/python -m pytest`, without an activated venv), `test_phase25_qa_hardening.py::test_alembic_head_schema_matches_orm_metadata` failed with "No module named alembic.__main__". The bare `python3` resolved to the system Python.
* **Context.** This is the same failure recorded as an "environment problem" in the gap-elimination baseline (FINAL_GAP_MATRIX G0). It is a portability defect in the test, not in the migrations.
* **Fix.** The test runs the subprocess with `sys.executable`. The assertion is unchanged.
* **Test.** The test itself, run with a minimal `PATH=/usr/bin:/bin`. **Before:** 1 failed (baseline log). **After:** passes.

## Coverage added without a defect

Each test below targets a scenario from the audit brief that had no concurrent or sequential test yet. They passed the first time they were run.

| Test | What it proves |
|---|---|
| `test_two_deadline_monitor_passes_at_once_warn_and_escalate_exactly_once` | Race 5: three monitor passes at once produce exactly one warning, one exceeded flag, one escalation, one event of each kind and one notification of each kind. |
| `test_event_published_twice_reaches_a_live_stream_once` | Race 7: a realtime event re-published to Redis twice reaches a live SSE stream once, and the stream stays live afterwards. |
| `test_successive_route_failures_promote_backups_then_replan_and_reservations_follow` | Brief E: four successive route failures give three backup promotions and then a re-plan. After each step there is exactly one ACTIVE plan, and the reserved units moved to the new source, none left at the failed one. A final failure gives an IMPOSSIBLE plan with every reservation released. |

## Harness issues (in the QA tooling, not the product)

* The first failure-injection run called `POST /auth/verify-email/resend` without its required `email` body, which returned 422. The harness was corrected and the check passed on re-run.
* The QA evidence spec needed the stack's `DATABASE_URL`, `REDIS_URL` and `SECRET_KEY` in the Playwright environment, for its token helper. That is documented in REPRODUCE_TESTS.md.
