> **Superseded (2026-10-12):** the counts in this report (1,270 tests, 141 operations) describe the build at that date. The gap-elimination round added features, tests and 19 API operations (160 in total). Current results: `docs/final-verification/FINAL_TEST_REPORT.md` and `API_COVERAGE.md`.

# LifeLink — Final QA Report (exhaustive QA + fixes)

**Dates:** 2026-10-06 → 2026-10-07 (UTC) · **Release decision:** see §22.

## 1. Executive summary

Every route and workflow in LifeLink was tested in the running application, not just through the existing test suite. Specifically:

- **Inventory:** all 140 API operations and 62 frontend routes were listed (`docs/LIFELINK_EXHAUSTIVE_QA_MATRIX.md`).
- **Browser workflows:** nine end-to-end workflows ran in a real browser, with 50 screenshots taken.
- **Route crawl:** every frontend route was opened at 375, 768 and 1280 px (183 visits), plus 74 authorization checks.
- **Security:** an object-level (IDOR) sweep and a set of manual attack tests were added.
- **Outages:** PostgreSQL, Redis and object storage were stopped and restarted to test failure handling.

**Defects found and fixed: 18 product defects + 2 test-suite defects**, each with a regression test or re-verification (§7). Three things could not be executed in this environment and are marked **BLOCKED** with evidence:

- live Gmail delivery
- `docker compose up`
- MinIO itself

## 2. Environment

- **Machine:** sandbox VM with 2 vCPU and 8 GB RAM.
- **Backend stack:** PostgreSQL 16.13, Redis 7.0.15, Python 3.11 (development venv), with the hashed lockfile also verified on Python 3.12, and ClamAV 1.5.4 `clamd` using a test-signature (EICAR-only) database.
- **Frontend / browser:** Node 22, Chromium (Playwright).
- **Full local stack, without Docker:** API with `RQ_SYNC=false`, a real RQ worker, and stand-ins for two blocked services:
  - **MailDev 2.2.1** instead of Mailpit (Mailpit's image, binary and Go dependencies could not be downloaded);
  - **moto 5.2.3** S3 server instead of MinIO (the MinIO image and binary are blocked).
- **Not reachable from this environment:** Docker Hub, `dl.min.io`, `smtp.gmail.com:587` and the OpenStreetMap tile servers.

## 3–4. Inventory and matrix

See `docs/LIFELINK_EXHAUSTIVE_QA_MATRIX.md`, covering categories A–N; every item is PASS, FIXED, BLOCKED or OUT OF SCOPE. The screenshot index is `docs/QA/SCREENSHOT_INDEX.md`.

## 5. Test results (this round, exact)

| Suite | Result |
|---|---|
| Backend pytest (full, real clamd, `CLAMD_TEST_REQUIRED=1`) | **1270 passed / 1270, 0 failed, 0 errors, 0 skipped** (28 min, 2026-10-07 07:17 UTC, after every fix; `docs/QA/evidence/backend_junit_final.xml`). The run before it had 1266 passed, 1 failed (test defect #19, fixed). API route coverage: 141/141 operations exercised, 140 with a success response (`docs/QA/evidence/route_coverage.txt`). |
| — new this round | `test_qa_email.py` 18 · `test_qa_route_gaps.py` 2 · `test_qa_attack_paths.py` 22 · `test_qa_concurrency.py` 2 (+ strengthened `test_phase11` TLS assertions, Phase H public list) |
| Locked Python 3.12 env (`requirements-dev.lock --require-hashes`) | 54 passed (email + notifications + attack-path suites) |
| Frontend Vitest | 165 passed / 165 (21 files) |
| TypeScript / ESLint / production build | clean / clean (`--max-warnings 0`) / OK |
| Playwright, full suite on a fresh DB with real clamd and 1,002 seeded donors (incl. the 9 QA workflow tests) | **97 passed / 97, 0 failed, 0 skipped** (7.7 min, 2026-10-07 ~05:01 UTC; a screenshot of every test is in the separate screenshots package). Earlier runs of the same suite: 82/5/10 (interim), then 90/1/6 after fix #18. Every failure was traced to a cause and fixed (#18, #19, and a test-only reset of the 5-per-hour registration limit, which the full suite exhausts from one IP). |
| QA workflow evidence run (full stack: worker + MailDev + S3 stand-in + clamd; API killed and restarted mid-test) | 9 passed / 9 (50 screenshots) |
| Route crawl pass 2 | 183 visits: 0 horizontal overflow, 0 blank pages, 0 broken images (excluding map tiles), 0 console errors, 0 failed API calls (excluding blocked map tiles); axe serious/critical: 2 left, then fixed (§16) |
| UI authorization checks | 74/74 not rendered for the wrong caller (73 denied; `/dev-index` → "Page not found" after the fix) |
| Migrations | fresh → head (30 revisions), down 4 / up, down to base / up, triggers present (`docs/QA/evidence/migration_check.txt`) |
| Failure injection (full stack) | **23/23**: Redis 5/5, PostgreSQL 4/4, object storage 6/6 (incl. recovery and download), malware scanner 4/4, worker 2/2, SMTP 2/2 (`docs/QA/evidence/failure_injection.txt`) |
| Graceful shutdown with an open SSE stream | 5.26 s with the fix (previously held for as long as the stream stayed open) |
| `pip-audit` / `npm audit --omit=dev` / audit gate | see §15 |

## 6–7. Bugs discovered and fixed

| # | Defect (how found) | Fix | Regression evidence |
|---|---|---|---|
| 1 | `docker-compose.yml` hard-coded `MAIL_HOST: mailpit` for the API and the worker, silently overriding a Gmail setup (source review). | `${MAIL_HOST:-mailpit}`; Mailpit moved to the `mailpit` profile with `required: false`; `.env.example` documents both modes. | `docs/QA/evidence/compose_mail_modes.txt` |
| 2 | STARTTLS ran without certificate verification, exposing the Gmail App Password to interception (new TLS test). | Verifying `ssl.create_default_context()`; SMTPS (465) support added. | `test_qa_email::test_starttls_verifies_the_server_certificate`; `test_phase11` now asserts `CERT_REQUIRED` |
| 3 | A refused configuration printed every input, including `MAIL_PASSWORD` and `SECRET_KEY`, in the start-up error (new test). | `hide_input_in_errors=True`; `MAIL_PASSWORD` stored as `SecretStr`. | `test_qa_email` (config refusals; repr/dump) |
| 4 | `EMAIL_JOB_MAX_RETRIES=0` made every registration return HTTP 500 (new test). | 0 now means one attempt. | `test_zero_retries_means_one_attempt_not_a_crash` |
| 5 | Two simultaneous registrations of the same email: the second got HTTP 500 (new race test). | Insert inside a SAVEPOINT; IntegrityError → 409. | `test_qa_concurrency` (3 consecutive passes) |
| 6 | `aiosmtpd`, used by the email tests, was missing from the hashed dev lock, so CI installs could not run those tests (dependency audit). | Declared in `requirements-dev.txt`; lock regenerated (+3 pins only). | Fresh Python 3.12 install from the lock: 54 tests pass |
| 7 | Gmail misconfigurations were accepted at start-up and only failed at the first send. | Validation: credentials, TLS/port pairing, no credentials over plain SMTP. | `test_unworkable_mail_configurations_are_refused_at_startup` (7 cases) |
| 8 | Every public page logged a 401 browser-console error from `GET /auth/me` (crawl). | New public `GET /auth/session` (always 200); the SPA uses it. | Crawl: 63 → 0 console errors; `test_session_endpoint_*`; Phase H sweeps |
| 9 | `/dev-index`, a developer page listing every route, was served to anonymous users in production builds (crawl). | Registered only in dev builds. | Crawl authorization row `/dev-index` → "Page not found" |
| 10 | 56 className tokens produced no CSS, so spacing and fonts silently collapsed on several pages (new dead-class scan). | Design tokens defined from `public/reference/DESIGN-*.md`; scrollbar utilities added. | Scan: 56 → 1 (a no-op hook class) |
| 11 | Certificate page rendered broken: overlapping text and fallback fonts (screenshot review). | Redesigned with defined utilities; the fake QR icon was replaced by the real verification link. | Vitest 6/6; Playwright |
| 12 | Upload errors were not announced to screen readers (E2E). | `role="alert"` on the identity and facility upload error boxes. | Evidence W17 |
| 13 | Raw enum label "Partially_fulfilled" shown in request history (E2E). | Human-readable label. | W30–W31 |
| 14–15 | axe color-contrast failures (Analytics, certificate footer) and scrollable regions not keyboard-focusable (Analytics, hospital kanban columns). | Darker or lighter text tokens; `tabIndex=0` + `role="region"` with labels. | Crawl pass 1 → 2; browser axe re-check of the 5 affected pages: 0 serious/critical (`docs/QA/evidence/axe_recheck_after_fixes_14_15.json`) |
| 16 | A SIGTERM'd API process stayed alive for as long as any Server-Sent-Event stream was open, so streams stayed on a draining process during restarts (E2E realtime test + log review). | `--timeout-graceful-shutdown` (default 5 s) in `run_production.sh`. | `docs/QA/evidence/graceful_shutdown.txt` |
| 17 | Mail transport differed silently between processes; nothing showed which provider was in use (review). | API and worker log the same `mail transport:` line; readiness reports the provider. | Logs in the evidence run; `test_readiness_reports_the_provider_but_never_credentials` |
| 18 | **Regression introduced by fix #2, caught by the clean Playwright rerun:** the TLS context (CA bundle) was built on every send, even plain SMTP. A blood request notifies ~320 compatible donors inline when `RQ_SYNC=true`, so `POST /blood-requests` took 9.6–44 s (previously 0.2–2.5 s) and the idempotency E2E timed out. | Context built only when TLS is used, once per process (`_tls_context()`, cached). | `test_plain_smtp_never_builds_a_tls_context_and_tls_builds_it_once`; live: 3 consecutive `POST /blood-requests` 591–640 ms on the seeded E2E stack (1,002 donors) |
| 19 | Two QA test defects (not product): the secret-leak check matched the word "password" inside the route name `/auth/change-password` in the metrics listing (failed only in the full run); the QA auth E2E posted the verify call to the frontend origin when MailDev was not configured. | The check now looks for the real secret values (SECRET_KEY, DB URL and password, SMTP password, S3 secret) plus any `"password":` field, which is stricter; the E2E uses an API request context. | `test_no_secret_reaches_any_diagnostic_endpoint` (reproduces the full-run state first); Playwright run |
| 20 | Coverage gap: `GET /donations/{id}` and `GET /admin/transport-routes` were only ever exercised negatively (route log). | Success-path and authorization tests added. | `tests/test_qa_route_gaps.py` (2 passed) |

No assertion was weakened to make a test pass. Two test doubles were updated because the code legitimately changed:

- the fake SMTP class now accepts a TLS context, and the test now asserts that the context verifies certificates (stricter than before);
- one status code was corrected from 200 to 202 to match the API's documented resend response.

## 8. Regression

The full suites were run after all fixes (§5). Workflow, crawl and failure-injection evidence was gathered after the fixes listed above, except #14–15, which were re-checked afterwards with axe in the browser on the five affected pages (0 serious/critical).

## 9–10. Gmail and Mailpit

See `docs/LIFELINK_EMAIL.md`.

- **Mailpit mode:** PASS with MailDev as the stand-in. The real email was received and its link verified the account (W05–W06).
- **Gmail mode:** configured and validated, and tested against a Gmail-shaped local server with STARTTLS and authentication.
- **Live Gmail delivery:** **BLOCKED**: no credentials, and port 587 is unreachable (`docs/QA/evidence/gmail_attempt.txt`).

## 11. Docker

**BLOCKED.** The Docker daemon starts, but every image pull gets 403 (`docs/QA/evidence/docker_attempt.txt`). `docker compose config` was validated in both mail modes. Command to run it: `cd backend && cp .env.example .env && docker compose up --build`.

## 12. Redis

- Real Redis was used throughout.
- Outage: readiness reports 503, login and authenticated reads return an honest 503 (never 500), and the app recovers on restart.
- Real-time behaviour while Redis is lost is covered by the existing tests.

## 13. PostgreSQL

Migrations pass (§5). Outage: honest 503s with no internals in the response, and the connection pool recovers. Concurrency is covered by real-thread race tests (see the matrix, sections J and K).

## 14. MinIO

**BLOCKED** (the image and binary cannot be downloaded). The S3 path was exercised against the moto S3 server:

- upload, download, admin-only access, missing index, path traversal;
- storage outage returns a clean 503 and leaves no half-written row.

## 15. Security

- **Phase H sweeps:** unauthenticated, role and CSRF checks over every route all pass.
- **New IDOR/BOLA sweep:** 35 ID-taking calls made by same-role attackers, 0 leaks.
- **Manual attack paths:** token tampering and expiry, forged cookies, path traversal, SQL-injection-shaped input, stored XSS, error leakage, secret leakage, headers, CORS, role escalation — all pass.
- **Secret scan of the zip:** clean (§20).
- **Dependency audits:** `pip-audit` on the lock: none found (2026-10-06 run, unchanged runtime lock). npm runtime: 0. Dev-only: `braces` (allowlisted, no patched release) and `postcss-selector-parser` (moderate), both pulled in by Tailwind 3.

## 16. Accessibility

- **axe scan of every route at 1280 px:** pass 1 had 4 serious issues; pass 2 had 2. All were fixed (#14–15). A browser axe re-check on 2026-10-07 of the five affected pages (/analytics, /hospital, /certificate/:id, /donor/identity-verification, /hospital/verification) found 0 serious/critical violations. Evidence: `docs/QA/evidence/axe_recheck_after_fixes_14_15.json`; screenshots in screenshots package part 1, `axe-recheck/`.
- **Existing Playwright accessibility tests** (keyboard sign-in, notification panel Escape/focus return, modal focus) are part of the Playwright suite.
- **Open item:** Material Symbols icon text is included in some buttons' accessible names, e.g. "add_circle Add stock". Not flagged by axe; not changed.

## 17. Realtime

- **Automated tests:** outbox, ordering, audience, replay and resync all pass.
- **Browser evidence:** a live partial-fulfilment update appeared without a reload (W30). After the API process was killed, the page showed "Reconnecting…", then "Live" again (W45–W47).

## 18. Performance

Not re-measured in this QA round. The measurements are from the hardening round on 2026-10-06 (`docs/LIFELINK_HARDENING_REPORT.md` §4–5, raw output in `docs/perf_evidence/hardening/`), including EXPLAIN ANALYZE before and after. This round changed only:

- the anonymous session check (one fewer error, same cost);
- duplicate-registration handling (one savepoint).

## 19. Screenshot index

`docs/QA/SCREENSHOT_INDEX.md`: 50 workflow screenshots (PNG) and 257 crawl and authorization screenshots (JPEG), 307 files.

## 20–21. Remaining limitations and blocked tests

1. **BLOCKED:** live Gmail delivery (no credentials, egress blocked).
2. **BLOCKED:** `docker compose up` and Docker image build (registry 403).
3. **BLOCKED:** MinIO itself; the S3 stand-in was used instead.
4. **BLOCKED:** Mailpit itself; MailDev was used instead.
5. **Partial:** ClamAV detection breadth (EICAR-only signature database).
6. **BLOCKED:** the success path of `POST /admin/graph/resync` (Neo4j not available; it returns an honest 503).
7. **Partial:** the full performance benchmark was not re-run this round (§18). Only `POST /blood-requests` was re-measured after fix #18: 591–640 ms with 1,002 seeded donors.
8. **Not done:** map tiles are unreachable here, so maps show no tiles; this is environmental.
9. **Partial:** after the last two accessibility fixes, only the five affected pages were re-checked with axe (0 serious/critical), not the full 183-visit crawl.
10. **Out of scope:** per-unit shipment reconciliation, the plasma post-thaw rule, and the Tailwind 4 migration.

## 22. Release decision

**PASS WITH DOCUMENTED LIMITATIONS.** After every fix in §7, the final regression ran:

- **Backend:** 1270/1270 passed.
- **Playwright:** 97/97 passed.
- **Failure injection:** 23/23 passed.
- **Accessibility:** browser axe re-check of the changed pages found 0 serious/critical issues.
- **API coverage:** all 141 operations exercised.

**Still BLOCKED in this environment** (§20), with evidence:

- live Gmail delivery (no credentials, and port 587 unreachable);
- `docker compose up` (image pulls refused);
- MinIO and Mailpit themselves (tested with the moto S3 and MailDev stand-ins);
- the success path of `POST /admin/graph/resync` (needs Neo4j).
