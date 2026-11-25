# LifeLink — Phase H: Security / Privacy Hardening Pass

**Date:** 2026-10-05 · **Scope (from the Phase A plan):** "Security/privacy hardening pass + React Router upgrade decision — full IDOR/authz/upload/CSRF/CORS/session review; resolve the deferred React Router major-version advisory with real investigation (upgrade or documented non-applicability)." Scope source: the original master plan text is not retained in this workspace; the phase plan recorded at Phase A (session task list) and the Phase A matrix (`LIFELINK_PHASE_35_RECON_MATRIX.md`) are the surviving record. Earlier per-endpoint security work (Phases 7, 8, 13–16, 19, 20, 23; D, E, F) is not repeated.

## 1. What this phase found and changed

| # | Area | Finding | Action |
|---|---|---|---|
| 1 | **CORS** | `CORS_ORIGINS=*` (or any `*` entry) was only forbidden by a sentence in the deployment checklist. With `allow_credentials=True`, Starlette then echoes **any** requesting origin with `Access-Control-Allow-Credentials: true` — reproduced: a request from `https://evil.example` carrying a session cookie got `access-control-allow-origin: https://evil.example`. Any site a signed-in user visited could read their authenticated GET responses (`verify_origin` only protects state changes). | `Settings._validate_cors_origins` refuses to start with a wildcard or a non-origin entry (path / trailing slash), in every environment. Tests added. Invariant 45. |
| 2 | **React Router advisory** | Installed `react-router-dom` 6.30.6 was in range of **GHSA-wrjc-x8rr-h8h6** (open redirect via backslash in `<Link>`/`useNavigate`, `>=6.0.0 <7.18.0`) and GHSA-337j-9hxr-rhxg (SSR hydration, `>=6.4.0 <7.18.0`). Phase A had judged the *previous* advisory non-applicable (no SSR/data router); the backslash redirect is client-side and not SSR-dependent. Every dynamic navigation target was traced (`resolveNotificationPath`, `/certificate/${id}`, `/family/patients/${id}`, `state.from` on the session-expired page): all are app constants or server UUIDs, so practical exploitability was low — but not zero-cost to argue about indefinitely. | **Upgraded** to `react-router-dom` 7.18.4 (v7 keeps the `react-router-dom` entry point and the classic `BrowserRouter`/`Routes` API used here). Same pass: `axios` 1.x → 1.20.0 (12 advisories, all fixed in 1.20.0). `npm audit --omit=dev` → **0 vulnerabilities**. |
| 3 | **Dependency gate** | CI ran `npm audit --audit-level=high` — moderate advisories in shipped code passed silently. | `frontend/scripts/audit-gate.mjs` (`npm run audit:gate`, now the CI step): shipped (runtime) dependencies must have **zero** advisories of any severity; the full tree may carry a high/critical only via a dated, justified entry in `frontend/audit-allowlist.json`; an expired or incomplete entry fails. Verified it fails on an expired entry and on an empty allowlist. |
| 4 | **Python dependencies** | `pip-audit -r requirements.txt` (fresh resolution) is clean, but the project's existing virtualenv carried `urllib3` 2.7.0 (PYSEC-2026-4175/4176/4177), a transitive of boto3 — an existing install never picks up the fix on its own. | `urllib3>=2.8.0` floor added to `requirements.txt`; venv upgraded; `pip check` clean; audit of the exact installed set: **no known vulnerabilities**. |
| 5 | **Route-level guards** | No bug found — every route already had the right guards. But each previous phase verified only its own endpoints, so nothing would catch a future route that forgot one. | `tests/test_phase_h_security.py` asserts it **systemically** over the real mounted route table (138 method/path pairs) — see §2. Invariant 44. |
| 6 | Session fixation | Not previously tested. | Verified login never adopts a client-supplied session id (the planted id stays invalid) and issues a fresh id on every login. |
| 7 | Sensitive data in logs/audit | Not previously tested end-to-end. | One flow (register, failed login, login, change password, forgot/reset with OTP) at DEBUG log level: no password, OTP or session id appears in any log record or audit row. |

## 2. Systemic checks (`tests/test_phase_h_security.py`, 21 tests)

- **Static inventory** — every route is on a reviewed public list (health, pre-auth flows, public certificate verification/download-by-token) or depends on `get_current_user`; every POST/PUT/PATCH/DELETE depends on `verify_origin`; every `/admin` route allows exactly `{ADMIN}` (role set read from the `require_roles` closure).
- **Unauthenticated sweep (live)** — every protected route, path params filled, no session → **401** (never 2xx/500), including `GET /events/stream`.
- **Role sweep (live)** — every role-gated route × every disallowed role → **403** before any object lookup.
- **CSRF sweep (live)** — every state-changing route (68), called by a user whose role *is* allowed, with `Origin: https://evil.example` → **403 "Origin not allowed"**; a foreign-origin logout does not log the user out.
- **Mass assignment** — no request body model accepts a server-owned field (`role`, `verified`, `status`, `verification_status`, `fulfilled_units`, …) except five reviewed, server-validated inputs; an extra `verified/user_id/id` on `PUT /hospitals/me` is ignored.
- **Cross-tenant reads added after Phase 23** — an unrelated hospital gets 403 on shipment-by-code and unit trace, an empty shipment list, and none of the origin's inventory log.
- **Mutation check** — removing `verify_origin` from `POST /notifications/read-all` failed both the static test and the live sweep (the live sweep showed the request succeeding with 200); restored.

## 3. Areas reviewed and found already correct (with existing evidence)

| Area | Evidence |
|---|---|
| Authentication, lockout, password policy, reset OTP, email verification | Phases 7, 11, 23 tests (`test_auth_hardening_phase7.py`, `test_password_reset.py`, `test_phase23_security_hardening.py`) |
| Object-level authorization (IDOR) | Phases 8, 13, 15, 19, 23; shipments `test_unrelated_hospital_cannot_act_on_a_shipment`; donation review `test_unrelated_hospital_cannot_verify_another_facilitys_donation`; realtime audience ⊆ REST visibility (Phase F) |
| Uploads (type sniffing, size, filename, checksum, private storage) | Phases 14, 20, E (`test_phase20_secure_file_storage.py`, document versioning/checksum tests) |
| Error leakage | Phase 17/25 handlers: generic 500/503 bodies, no SQL/trace |
| Cookies | HttpOnly; Secure required in production; SameSite validated (config validator, Phase 7/28) |
| Secrets | `detect-secrets` CI gate; `.env` untracked (Phase 29) |
| XSS sinks | no `dangerouslySetInnerHTML`/`innerHTML`/`eval` in `frontend/src`; CSP from Phase 23 |
| Open redirects (backend) | no redirect responses built from user input (grep) |

## 4. Verification

| Check | Result |
|---|---|
| `tests/test_phase_h_security.py` | 21 passed |
| `test_phase28_production_config.py` (config validators) | passed alongside |
| Frontend after the upgrades: `tsc --noEmit`, ESLint (max-warnings 0), Vitest | clean, clean, **152/152** |
| Routing-heavy Playwright specs on React Router 7 (fresh e2e DB): login, responsive mobile nav, Phase 23 browser security, error pages | **24/24 passed** |
| `npm run audit:gate` / `npm audit --omit=dev` | passed / 0 vulnerabilities |
| `pip-audit -r requirements.txt`; audit of the installed venv | no known vulnerabilities (both) |

Full-suite regression for H is part of the Phase J run (`LIFELINK_PHASE_J_TESTING_REPORT.md`).

## 5. Remaining limitations

- **Tailwind 3 build toolchain** carries `braces` GHSA-vfj7-8cjw-p6xm (stack-exhaustion DoS on adversarial glob patterns). It is build-time only: the patterns are our own `tailwind.config.js` `content` globs, and it is absent from the shipped bundle. No patched `braces` exists; the only fix is the Tailwind 3 → 4 toolchain rewrite. It is allowlisted with a reason and a review date (2027-01-31) that fails CI when passed.
- Python dependencies are floor-pinned (`>=`) without a lockfile; CI resolves fresh. The `urllib3` floor fixes the known case; a hashed lockfile would make installs reproducible (not in the Phase H scope).
- The audit hash chain is tamper-evident, not immutable; external anchoring/HMAC keys were not part of the Phase H plan.
