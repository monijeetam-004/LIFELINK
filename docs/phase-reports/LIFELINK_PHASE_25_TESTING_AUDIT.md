# LIFELINK — PHASE 25 TESTING AUDIT
## Complete Testing, Regression & Quality Assurance

Status: audit-first deliverable, written before any test was added and before the two fixes described below were made. `LIFELINK_PHASE_25_TESTING_REPORT.md` is the companion final report with the full 34-part mandated structure.

---

## 1. Method

1. Re-established the real environment from a fresh container: PostgreSQL 16 and Redis were both down at the start of this session and were started explicitly (`service postgresql start`, `service redis-server start`); all data from Phase 24 (1,306+ real users, all migrations at head) had persisted on disk across the container's earlier reclaim.
2. Found and corrected a real methodology error carried over from Phase 24: the backend must be started with its working directory set to `backend/` so pydantic-settings loads `.env` — `.env` already contains `RL_LOGIN_IP=[200,300]`, a real, intentional, Phase-20-documented relaxation for local E2E use. Phase 24's login-rate-limit cascade against Playwright was very likely caused by starting uvicorn from the wrong working directory, silently discarding this `.env` override and falling back to the hard-coded `(10, 300)` default. Starting the server correctly this time meant the 46+3-test Playwright suite ran once, cleanly, with **zero** rate-limit-related interference — a real fix to how this project is run locally, not a new workaround.
3. Dispatched two independent, read-only subagents to build a real, evidence-based test inventory directly from source: one over all 51 backend test files (602+ `def test_` functions), one over all 13 (now 14) frontend Playwright spec files. Neither was asked to trust prior phase reports; both were asked to read actual test bodies and flag anti-patterns (assert-200-only tests, tests that mock away the exact thing they claim to verify).
4. Independently re-verified, by direct grep/read (not by trusting the subagents' word), the single most safety-critical claim in this phase's brief: that no Gmail/email password is ever used for LifeLink authentication. Confirmed `mail_password` (the app's own outbound SMTP relay credential) has zero code path connecting it to login, session, or password reset; confirmed the OTP is stored only as an HMAC (`otp_hash`) and never appears in a response schema.
5. Ran the full backend suite twice more before touching any code (once was contaminated by my own mistake — see Section 3 below — and re-run cleanly) to get a trustworthy baseline, then again twice after adding new tests and one real fix.
6. Fixed one real, reproducible security-relevant concurrency bug (Section 2), added six new backend regression tests and three new frontend E2E tests for confirmed, real gaps (never for hypothetical ones), and documented — rather than fixed — one lower-severity, real idempotency gap in donation logging, per this phase's explicit instruction to prefer honest documentation over unevidenced or oversized changes.
7. Reproduced the Phase 24 performance finding (near-total-outage under sustained concurrent load) fresh, in this new environment, rather than trusting the old report's numbers — confirmed via a live `pg_stat_activity` snapshot showing the identical `idle in transaction` / `ClientRead` signature Phase 24 found.

---

## 2. The one real bug found and fixed

**`app/services/auth_service.py::reset_password`** read the active `PasswordResetOTP` row with a plain `select(...)` and no row lock, then later did `row.attempts += 1; db.commit()` on that same in-Python object. Every other mutate-after-read row in this codebase (`RegistrationCounter`, `BloodRequest`, `Emergency`, `FamilyLink`, hospital inventory, `VerificationRequest`, `TransportRoute`, a `Donation` being certified) uses `.with_for_update()` or `db.get(..., with_for_update=True)` for exactly this reason — this OTP-attempts row was the one place that pattern was missing.

Without the lock, N concurrent wrong-OTP guesses each read the same stale `attempts` value and each independently compute `attempts + 1`; the last `COMMIT` wins (a lost update), so the persisted `attempts` count ends up far below the real number of guesses made. **This meant `password_reset_max_attempts` (5) could be bypassed by guessing in parallel instead of serially** — a real, exploitable lockout-bypass, reproduced with a dedicated test (20 concurrent wrong guesses, then proving the correct OTP is still accepted afterward) before the fix, and proven closed by the same test after adding `.with_for_update()`.

This is a minimal, one-line, fully in-pattern fix (copying an existing, proven convention used seven other places in this codebase), not a rewrite, and does not change the intended behavior of the endpoint — a wrong guess is still a wrong guess, the fifth wrong guess still locks the account for the OTP's remaining lifetime.

---

## 3. A methodology mistake I made and corrected mid-session

While validating the new test file, I accidentally ran two `pytest` processes concurrently against the same shared real `lifelink_test` Postgres database (a second ad-hoc run I started to iterate on one new test, without waiting for a first full-suite run already in flight). This caused real lock contention and, after I impatiently `pg_terminate_backend()`-killed the stuck connection, two unrelated tests failed with `psycopg.errors.AdminShutdown` — an artifact of my own interference, not a code regression. I diagnosed this precisely (via `pg_stat_activity` showing the stuck `idle in transaction` connection I had created), waited for both processes to fully exit, and re-ran the complete suite once, cleanly, from a cold start. All reported numbers in the companion report are from that clean, uncontaminated run.

---

## 4. What the independent test-inventory audit found (full detail in the report)

- **Backend**: 602 existing tests before this phase, generally high quality — IDOR/RBAC tests consistently check DB/audit-log state, not just HTTP status; email/RQ/Redis integration tests use a real local SMTP server (`aiosmtpd`) and a real `rq.SimpleWorker`, not mocks. Real, confirmed gaps: no concurrency test existed for donation creation, OTP verification, or password reset (two of three fixed/covered this phase); zero CORS test coverage despite `CORSMiddleware` being active (fixed); no migration-vs-ORM-model drift test, since the suite's schema is built from `Base.metadata` and never actually runs Alembic (fixed).
- **Frontend**: 46 existing Playwright tests, strong on notifications/IDOR/security/idempotency, weaker on: dedicated error-page routes (`/403`/`/404`/`/500` were wired but never directly navigated to in a test — one gap closed this phase); a true HTTP 500 response body (every existing "offline" test used a network-layer abort, never a real 5xx — one gap closed this phase); and — **not closed this phase, documented as a real, remaining gap** — most non-patient/non-hospital-inventory dashboard error/empty states (donor, family, most admin sub-pages, most hospital sub-pages), and responsive-breakpoint coverage beyond a single 390px check (320/375/768/1024/1280/1440 are all untested).

See the companion report's Bugs Found, Tests Added, and Untested Areas sections for the complete, itemized list with file:line evidence.

---

## 5. Performance reproduction (Section 18's mandate — reproduce, do not rewrite)

Reproduced the Phase 24 finding in this fresh environment using the same custom `httpx`+`asyncio` load-test harness against the same 127 synthetic `loadtest.*@example.com` accounts (still present in the real dev DB). Same 2-CPU, single-worker topology; same qualitative result: read/write endpoints degrade sharply past ~10-25 concurrent callers, and the hospital-inventory scenario failed at every concurrency level tested after an earlier scenario's burst — confirmed via a live `pg_stat_activity` snapshot showing pooled connections stuck `idle in transaction`/`ClientRead`, the identical signature Phase 24 found. No architecture change was attempted, per this phase's explicit instruction. Full numbers are in the companion report's Performance Reproduction section.

---

See `LIFELINK_PHASE_25_TESTING_REPORT.md` for the complete 34-part writeup: exact commands, exact pass/fail counts, the full test matrix, and the final readiness assessment.
