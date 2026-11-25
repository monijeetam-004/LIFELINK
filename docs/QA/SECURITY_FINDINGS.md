# Security and privacy findings

**Scope.** All testing was authorised, and ran only against disposable local stacks: databases `lifelink_qa`, `lifelink_test` and `lifelink_e2e`, plus Redis databases 5 and 6. No production system was touched, and only synthetic accounts were used.

**What this is not.** It is not a penetration test by an independent team. It combines automated suites, a live black-box probe, static analysis and dependency audits. Coverage is listed below so that nothing is overstated.

## 1. Methods and tools

| Method | Tool / file | Scope | Result |
|---|---|---|---|
| In-process security suites | `tests/test_phase23_security_hardening.py` (54 tests), `test_phase_h_security.py` (CSRF/origin sweep over all 160 operations), `test_qa_attack_paths.py`, `test_authz_phase8.py`, `test_phase13_family_link_security.py`, `test_phase14_verification_security.py`, `test_phase15_certificate_security.py`, `test_phase16_graph_engine_security.py`, `test_phase20_secure_file_storage.py`, `test_gap_audit_security.py`, `test_hardening_audit_chain.py`, `test_gap_rate_limit.py`, and others | Access control, IDOR, sessions, CSRF/origin, uploads, certificates, audit chain, rate limits | All pass in the final regression (FINAL_AUDIT_REPORT.md §4) |
| Live black-box probe (real uvicorn, production entrypoint, real Redis/PostgreSQL) | `docs/QA/tools/security_probe.py` → `evidence/security/security_probe_live.txt` | Cookies, headers, CORS, origin check, vertical and horizontal access, injection and malformed input, path traversal, forged X-Forwarded-For, logout invalidation, anonymous access, error leakage | **27/27 PASS** |
| Malicious-upload handling | EICAR, a suspicious PDF and the ClamAV test files against real clamd (`test_gap_clamav_real.py`, 7 tests), plus a live scanner outage | MIME and magic bytes, size limit, quarantine | Pass. Scanner unavailable → `SCAN_FAILED`, never CLEAN |
| Static analysis | bandit 1.9.4 → `evidence/security/bandit_app.txt` | `backend/app` | 0 high, 1 medium, 4 low (triaged in §3) |
| Python dependencies | pip-audit → `evidence/security/pip_audit_requirements_lock.txt` | `requirements.lock` | **No known vulnerabilities** |
| npm dependencies | `npm audit --omit=dev`, `npm audit`, project gate `npm run audit:gate` → `evidence/security/npm_audit_*.txt` | Shipped bundle and full tree | Shipped bundle: **0**. Full tree: 7 advisories (5 high, 2 moderate), all dev-only build tooling. Gate passes |
| Secrets | Repository and evidence scan (FINAL_AUDIT_REPORT.md §7) | Code, docs, evidence, screenshots, frontend bundle | See the final report |
| Frontend route authorisation | `frontend/scripts/qa-crawl.mjs` → `evidence/frontend-routes/crawl-results.json` | Every guarded route, anonymous and with a wrong role | **75/75 denied** (redirect or Access Denied) |

## 2. Findings fixed in this round

Full root cause, fix and before/after tests for each are in DEFECTS_FIXED.md.

| ID | Severity | Finding | Retest |
|---|---|---|---|
| D1 | Medium | Race in the donor double-booking guard: concurrent accepts both succeeded | Concurrent tests pass, 5 repetitions × 2 entry points |
| D2 | Medium | The shipped frontend (nginx) sent no anti-framing or nosniff headers: clickjacking was possible | Headers verified on the real container, at `/` and on assets |
| D3 | Low | The API image CMD did not bound graceful shutdown, so it was SIGKILLed past Docker's stop timeout | 5.22 s exit with an open stream through the production entrypoint |
| D4 | Low | A start-up storage failure was swallowed with no log (bandit B110) | Warning logged with the exception type only |
| D5 | Low | A public page triggered an authenticated API call (401 and a console error) | The crawl and a vitest regression |

## 3. Findings accepted, with rationale

| Finding | Severity | Location | Why it is accepted |
|---|---|---|---|
| bandit B108: hard-coded `/tmp` heartbeat path | Low (bandit: Medium/Medium) | `app/workers/deadline_monitor.py` | Container-local file, touched only (no content); overridable with `DEADLINE_MONITOR_HEARTBEAT`. Nothing is read from it except its age, by the health check |
| bandit B101: `assert` used for type narrowing | Low | `app/core/graph.py`, `app/services/sms/msg91.py` | Each `assert` follows an explicit configuration check that raises first. Under `python -O` the narrowing is lost, but the guard is not |
| bandit B311: `random.Random` | Low | `cold_chain_service` telemetry **simulator** | Generates labelled SIMULATED demo readings; refused in production; no security use |
| npm: braces (GHSA-vfj7-8cjw-p6xm), high, via Tailwind 3 → chokidar / micromatch / fast-glob | Dev-only | Build tooling | Not in the shipped bundle. Allowlisted with a review-by date (2027-01-31) in `audit-allowlist.json`. The only fix is the Tailwind 4 migration |
| npm: postcss-selector-parser < 7.1.6 (GHSA-rj75-hqrm-r3gf), moderate, CPU exhaustion | Dev-only | Build tooling (Tailwind 3 → postcss-nested) | Parses the project's own CSS at build time, not user input; same Tailwind 4 dependency. **New since the previous round**: below the gate's high threshold, so it is not allowlisted; recorded here |
| No `Secure` flag on the session cookie in development | Informational | `SESSION_COOKIE_SECURE=false` in `.env.example` | Documented to be `true` in production. `test_phase28_production_config` refuses insecure production settings |
| The API sends no `Content-Security-Policy` header | Informational | `app/core/security_headers.py` | The API serves JSON only. Its own module documents why a CSP there would protect nothing |

## 4. What the live probe checked

Excerpts are in `evidence/security/security_probe_live.txt`. The session token is redacted there.

* **Session cookie.** HttpOnly and SameSite=lax. A captured cookie is rejected with **401 after logout**: server-side revocation.
* **Origin and CORS.**
  * A state change with a foreign `Origin` is refused with **403**, and so is one with no `Origin` at all (CSRF defence for cookie auth).
  * A foreign-origin CORS preflight is not granted.
* **Vertical access.** A donor gets 403 on admin users, verifications, transport routes and audit-chain verification, and cannot create routes.
* **Horizontal access.** A donor cannot cancel another user's request (404). Unknown objects are 404, not 500.
* **Injection and malformed input.**
  * SQL-like, script and path-traversal strings in query parameters are handled with no error or leak.
  * Malformed JSON returns 422 and a non-UUID path returns 422, both without a stack trace.
  * A path traversal in a document index is rejected.
* **Rate-limit bypass.** A forged `X-Forwarded-For` on every request does not reset the per-IP login limit: **429 after 3 failures**, because the probe had already used part of the budget. The production entrypoint trusted no proxy for this test (`FORWARDED_ALLOW_IPS=192.0.2.1`).
* **Anonymous access.** Notifications, admin users, hospital units and the event stream all return 401.

## 5. Not covered, and why

* **No authenticated fuzzing of every request schema.** Malformed input was checked for representative endpoints. Every operation's validation is exercised by the suites (`test_phase_h_security` sweeps all 160 operations for origin and authentication, not for every payload).
* **TLS, HSTS and reverse-proxy configuration of a real deployment.** There is no deployment here. `SESSION_COOKIE_SECURE` and the proxy-header trust must be set at deploy time (OPERATIONS_RUNBOOK.md).
* **Audit-log tampering by a database owner.** The design gives tamper *evidence*, not prevention. Detection of owner tampering, disabled triggers and checkpoint mismatch is covered by `test_gap_audit_security.py` and `test_hardening_audit_chain.py`, which pass.
* **Social engineering, infrastructure and physical security.** Out of scope.
