# LIFELINK — PHASE 7 REPORT: Production Authentication Hardening

**Scope of this phase (as specified by the user):** audit and harden authentication
end-to-end — registration, login, logout, session handling, token refresh, email
verification, password reset, password change, session expiry, account suspension,
account reactivation, and invalid-token handling — plus password hashing, token
lifecycle (expiration/rotation/revocation/replay resistance/storage/refresh), cookie
configuration, rate limiting, and account-security protections (brute force,
credential stuffing, repeated reset requests, verification abuse). Nothing outside
authentication was touched.

**Status: DELIVERED.** Every claim below was verified by actually running the code:
the full backend pytest suite (320 tests), `ruff`, `mypy`, and a manual read-through
of every line of the authentication stack. No Alembic migration was needed — every
change in this phase is to configuration, service, and route logic, not to the
database schema.

---

## 1. What was inspected first

Before writing a single line of code, the entire authentication stack was read in
full, end to end:

- `app/api/routes/auth.py` — every endpoint (register, login, logout, me,
  verify-email, verify-email/resend, forgot-password, reset-password,
  change-password).
- `app/services/auth_service.py` — the actual business logic behind each endpoint.
- `app/core/security.py` — password hashing, token/OTP generation, HMAC signing,
  constant-time comparison.
- `app/services/session_service.py` — the Redis-backed session store.
- `app/api/deps.py` — how a request is turned into an authenticated user
  (`get_current_user`, `require_roles`, `verify_origin`).
- `app/api/cookies.py` — how the session cookie is set/cleared.
- `app/core/config.py` and `app/core/ratelimit.py` — cookie settings and the
  fixed-window Redis rate limiter.
- `app/schemas/auth.py` — request validation.
- Every existing auth test file (`test_auth_flow.py`, `test_password_reset.py`,
  `test_change_password.py`, `test_rate_limit_and_authz.py`), so nothing here would
  duplicate coverage that already existed.

This turned out to already be a genuinely strong design, not a prototype:

- **Session architecture is server-side, opaque, cookie-based — not JWT.** The
  cookie holds only a random 256-bit id (`secrets.token_urlsafe(32)`); all session
  state (`user_id`, `issued_at`, `ip`, `user_agent`) lives in Redis under
  `session:{sid}`, with a parallel `user_sessions:{user_id}` set that makes
  "revoke every session for this user" a real, immediate operation (used by
  password reset). This sidesteps the entire class of JWT problems (can't revoke a
  still-valid signed token, must keep a blocklist, etc.) by construction.
- **Passwords are Argon2-hashed** (`argon2.PasswordHasher`), never stored in
  plaintext anywhere, with an automatic rehash-on-login upgrade path
  (`password_needs_rehash`) if the hashing parameters are ever strengthened later.
- **Email verification tokens and password-reset OTPs are never stored raw.** Only
  `sign(value)` — a keyed HMAC-SHA256 digest — is persisted; the raw value exists
  only transiently, to be emailed and matched against. Even a full database dump
  doesn't hand an attacker usable tokens.
- **Constant-time comparison** (`hmac.compare_digest`) is used for OTP matching,
  preventing timing side-channels.
- **Both tokens are already single-use and properly scoped:** verification tokens
  and reset OTPs are consumed (`consumed_at`) on success, all prior outstanding
  ones are superseded when a new one is issued, and reset OTPs are additionally
  attempt-limited (`password_reset_max_attempts`) independent of the account-level
  rate limit.
- **Rate limiting already covered every sensitive endpoint the spec lists** —
  login (per-IP and per-account), registration (per-IP), password reset request
  and submission (per-account), verification resend (per-account), and password
  change (per-account) — via a fixed-window Redis limiter
  (`app/core/ratelimit.py`). See §5 for the exact numbers.
- **A lightweight CSRF defense already existed:** `verify_origin` rejects any
  `/auth/*` request whose `Origin` header is present but not in the allow-list
  (a request with no `Origin` at all — i.e. a non-browser client — is let through,
  since it can't be the target of a cross-site browser attack).
- **Self-registration already can't be used for privilege escalation:**
  `REGISTERABLE_ROLES` excludes `ADMIN` at the schema layer, so no request body can
  register an admin account.
- **Generic, non-enumerating error messages** are already used everywhere it
  matters: wrong email vs. wrong password on login both return the same 401
  "Invalid email or password"; forgot-password always returns 202 "If an account
  exists..." whether or not the email is registered, and only actually sends an
  email when it does.

Given that, this phase's job was to find the genuine remaining gaps — not to
rebuild something that already works — and to close exactly those, honestly.

---

## 2. Gaps found and closed

Four real gaps were identified. Each is described below with what was missing, why
it mattered, and exactly what was implemented.

### 2.1 No production cookie-security enforcement

**Gap:** `SESSION_COOKIE_SECURE` and `SESSION_COOKIE_SAMESITE` were plain settings
with no validation. Nothing stopped a production deployment from silently starting
with `SESSION_COOKIE_SECURE=false` — the session cookie would then be sent over
plain HTTP, exactly the scenario cookie hardening exists to prevent.

**Fix:** a Pydantic `model_validator(mode="after")` on `Settings`
(`app/core/config.py`) that **fails closed** — it raises at settings-construction
time (i.e., at app startup), not a warning that's easy to miss in logs:

- `ENVIRONMENT=production` (or `prod`) with `SESSION_COOKIE_SECURE=false` →
  refuses to start.
- `SESSION_COOKIE_SAMESITE=none` with `SESSION_COOKIE_SECURE=false` → refuses to
  start (browsers drop such a cookie outright anyway, so this is always a
  misconfiguration, never a legitimate choice).
- `SESSION_COOKIE_SAMESITE` set to anything other than `lax`/`strict`/`none` →
  refuses to start.

This deliberately does **not** force one specific SameSite value — whether Lax,
Strict, or None is correct depends on whether the frontend and backend share a
registrable domain in a given deployment, which is a real architectural choice,
not a bug. Only the combinations that are unsafe or that browsers will silently
reject are refused.

### 2.2 No session refresh / absolute lifetime cap

**Gap:** the spec explicitly asks to "implement/fix... token refresh" and to audit
session expiration. The existing session TTL (`session_ttl_seconds`, 14 days) was a
fixed Redis expiry set once at login and never touched again — so an active user
mid-session, right up against that boundary, would be logged out even while
actively using the app. There was also no upper bound at all on how long a session
could be kept alive: if a session id were ever stolen, an attacker who kept it
"warm" (any authenticated request) could make it live forever.

**Fix, in two parts:**

- **Sliding/idle refresh** (`session_service.touch_session`, called from
  `app/api/deps.py::_load_user_from_request` on every authenticated request):
  extends the Redis TTL on both `session:{sid}` and `user_sessions:{user_id}` to
  `session_ttl_seconds` again. An actively-used session is never interrupted by
  the idle timeout; only genuine inactivity for that long logs the user out.
- **Absolute lifetime cap** (`session_absolute_ttl_seconds`, 90 days): measured
  from the session's original `issued_at` — never reset by `touch_session` — and
  checked on every request before the sliding refresh happens. Even a session
  touched on every single request cannot outlive this cap. This is the actual
  backstop against a stolen-and-kept-warm session id; the sliding timeout alone
  cannot provide it. When a session exceeds the cap, it's deleted immediately
  (not just left to expire), so the same session id can't be reused again by
  accident or attack.

Both settings are configurable via environment variables with safe production
defaults (14 days idle / 90 days absolute) already checked in.

### 2.3 Reactivation didn't revoke stale pre-suspension sessions

**Gap:** `POST /admin/users/{id}/suspend` correctly took effect immediately (via
`get_current_user`'s `is_active` check — no separate revocation step was even
needed there, since every subsequent request is already rejected). But
`POST /admin/users/{id}/reactivate` only flipped `is_active` back to `True`. Any
session that existed *before* the suspension was never deleted — it was just
lazily rejected while `is_active=False`. The moment `is_active` flipped back to
`True`, that pre-suspension session would silently resume working with no fresh
authentication at all. For an admin action whose entire point is often "I suspect
this account is compromised, cut it off," letting reactivation quietly resurrect
the attacker's still-live session defeats the purpose.

**Fix:** `reactivate_user` (`app/api/routes/admin.py`) now calls
`session_service.delete_user_sessions(target.id)`, exactly mirroring what a
password reset already does, and records the count of revoked sessions in the
audit log entry's metadata. Reactivation now always requires a fresh login, same
as a password reset.

### 2.4 No weak/common-password blocklist

**Gap:** `RegisterIn.password` / `ResetPasswordIn.new_password` /
`ChangePasswordIn.new_password` only enforced a length bound (8–128 characters).
Nothing stopped `password123` or `qwertyuiop`.

**Fix:** a small, deliberately short blocklist (`app/schemas/auth.py`) of the
passwords most likely to appear in a credential-stuffing wordlist, checked
case-insensitively via a Pydantic `AfterValidator`. This intentionally follows
**NIST 800-63B** guidance rather than legacy complexity rules: no forced
uppercase/digit/symbol requirements (which push users toward predictable
patterns like `Password1!`), just length plus a breach-list check — the
combination NIST and most current security guidance actually recommend. All
three password-accepting fields (register, reset, change) share one
`PasswordStr` type, so the check is applied uniformly rather than duplicated
per-endpoint.

---

## 3. Investigated and deliberately left unchanged

Two things were looked at closely and intentionally **not** changed, because
changing them would have meant fabricating a fix for something that either isn't
broken or is an existing, tested product decision:

- **Unverified users can still log in** (`is_email_verified=False` doesn't block
  `authenticate()`). This is covered by an existing, passing test
  (`test_unverified_user_can_still_login_but_flag_is_false`) and is a legitimate
  product choice many apps make (let the user in, gate specific *actions* on
  verification rather than gating login itself) — not an authentication defect.
  Changing it would silently alter product behavior the spec never asked to
  change.
- **"Last remaining admin" style guards were *not* added** to suspend/role-change,
  by the same reasoning already applied and documented in the Phase 6 admin
  report: self-action is already blocked earlier in both endpoints, and the
  acting admin's own existence always satisfies "another admin still exists" — so
  such a guard would be unreachable dead code, not real protection. Phase 7 did
  not reintroduce it.

---

## 4. Token/session property audit (as the spec requested)

| Property | Session id | Email verification token | Password reset OTP |
|---|---|---|---|
| **Storage** | Redis only, opaque random id in an httpOnly cookie; browser never sees any session *data*, only the id | HMAC-SHA256 digest at rest (`token_hash`); raw value never persisted | HMAC-SHA256 digest at rest (`otp_hash`); raw value never persisted |
| **Expiration** | 14-day idle TTL (sliding) **+ 90-day absolute cap** (new, §2.2) | `email_verification_ttl_seconds` = 24h | `password_reset_otp_ttl_seconds` = 10 min |
| **Rotation** | New session id issued on every login and on password change (old one explicitly deleted first) | New token supersedes (consumes) any outstanding one on re-issue | New OTP supersedes (consumes) any outstanding one on re-issue |
| **Revocation** | Single-session (`delete_session`, used by logout) and all-sessions (`delete_user_sessions`, used by password reset **and now reactivation**, §2.3) | Consumed on first use; superseded on reissue | Consumed on first use; superseded on reissue |
| **Replay resistance** | Constant-time cookie compare is unnecessary (Redis key lookup is already exact-match, not comparison-based); a session deleted or expired is immediately absent — no window for replay after revocation (verified by test, §6) | `hmac.compare_digest`-backed lookup via the signed hash; single-use `consumed_at` prevents replay | `hmac.compare_digest` explicitly used for the OTP match; single-use `consumed_at`, plus a hard attempt cap (`password_reset_max_attempts`) independent of the outer rate limiter |
| **Refresh** | **New in this phase:** sliding idle-timeout refresh on every authenticated request, bounded by the absolute cap (§2.2) | Explicit resend endpoint (`/auth/verify-email/resend`), itself rate-limited | Explicit re-request via `/auth/forgot-password`, itself rate-limited (both per-IP and per-account) |

---

## 5. Rate limiting — confirmed complete against the spec's list

The spec asks for rate limiting on: login, registration, password reset,
OTP/email verification, and sensitive endpoints. All were already covered before
this phase (`app/core/config.py`, enforced via `app/core/ratelimit.py`'s
fixed-window Redis limiter); no gaps were found here:

| Endpoint | Limit | Window |
|---|---|---|
| Login, per IP | 10 | 5 min |
| Login, per account | 5 | 5 min |
| Registration, per IP | 5 | 1 hour |
| Verification resend, per account | 3 | 1 hour |
| Forgot-password, per IP | 10 | 1 hour |
| Forgot-password, per account | 3 | 1 hour |
| Reset-password, per account | 10 | 1 hour |
| Change-password, per account | 5 | 1 hour |
| Verification document submit, per account | 5 | 1 hour |

Login and registration are limited by both IP and account, which is what actually
blunts credential stuffing (many accounts from one IP) and distributed brute
force (many IPs against one account) as two distinct attack shapes — a single
limiter axis would only catch one.

---

## 6. Tests added

A new file, `backend/tests/test_auth_hardening_phase7.py`, covers exactly the
spec's TEST list. Items already covered by the existing suite are referenced
rather than duplicated (see the file's own header comment for the full map):

- **Expired token** — email verification token, and password reset OTP —
  backdated directly via a raw DB session and confirmed rejected (400).
- **Revoked token (session replay)** — two distinct scenarios, both replaying the
  *raw* session cookie value on a fresh client (not just via the original
  TestClient's own cookie jar, which wouldn't prove replay resistance):
  - after logout,
  - after a password reset (mass session revocation).

  Both correctly return 401.
- **Suspended account, fresh login** — distinct from the existing coverage (which
  tests an *already-logged-in* session getting rejected after suspension): this
  test suspends an account that was never logged in on that client, then attempts
  a brand-new login with fully correct credentials, and confirms 403 "This
  account has been disabled".
- **Reset token reuse** — a reset OTP is used successfully once, then the exact
  same OTP is submitted again; the second attempt is rejected (400), and the
  password from the *first* reset is confirmed to still be the active one (i.e.
  the reuse attempt was a true no-op, not a partial state change).
- **Session expiration** — the new absolute lifetime cap (§2.2) is exercised
  directly: a session's `issued_at` is backdated in Redis past
  `session_absolute_ttl_seconds`, and the session is confirmed rejected (401) and
  deleted.
- **Concurrent sessions per policy** — the policy is confirmed to be "unlimited
  concurrent sessions, each independently valid": two simultaneous logins for the
  same account both remain valid, and logging out of one has no effect on the
  other.

Invalid password and wrong-role are exercised by the existing suite
(`test_login_wrong_password_is_401_generic`, `test_wrong_role_gets_403`, and the
full parametrized 403 matrix in `test_admin.py`/`test_admin_phase6.py`) and were
re-verified as part of the full run below rather than duplicated.

---

## 7. Frontend audit

`frontend/src/lib/auth.tsx` and `frontend/src/lib/api.ts` were read in full since
client-side auth handling wasn't examined in earlier phases:

- The session is **cookie-only** — the frontend never reads, stores, or handles a
  session token itself; `axios` is configured with `withCredentials: true` so the
  httpOnly cookie is sent automatically, and `/auth/me` is the only source of
  truth for "who is logged in."
- No `localStorage`/`sessionStorage` use anywhere in the auth code path.
- Password `<input>` fields use `type="password"` with an explicit show/hide
  toggle (`AuthPage.tsx`), not a permanently-unmasked field.
- A 401 on any authenticated call after initial load (as opposed to the expected
  401s on `/auth/me`, `/auth/login`, `/auth/register`, `/auth/logout` themselves)
  is treated as "session died server-side" and clears local user state
  immediately via a dedicated event, rather than leaving the UI in a stale
  logged-in state.

No client-side gaps were found; nothing here needed changing.

---

## 8. Verification actually performed

- **Full backend test suite:** `ENVIRONMENT=test pytest` → **320 passed, 0
  failed, 0 errors** (312 pre-existing + 8 new), confirmed via JUnit XML output
  (`tests="320" ... failures="0" errors="0" skipped="0"`) rather than relying on
  the terminal summary line, which this sandbox's output buffering sometimes
  drops even on a clean run.
- **`ruff check`** on every file touched this phase (`app/core/config.py`,
  `app/schemas/auth.py`, `app/api/deps.py`, `app/services/session_service.py`,
  `app/api/routes/admin.py`, `tests/test_auth_hardening_phase7.py`): clean.
- **`mypy app`**: 3 errors, identical to the pre-existing baseline from Phase 6
  (`app/api/routes/verifications.py:243-244`, `app/api/routes/donors.py:219`) —
  none in any file touched this phase.
- **`python -c "import app.main"`**: succeeds (confirms the new startup-time
  cookie-security validator doesn't break app boot under the current, safe
  development `.env`).
- **No Alembic migration**: correctly not needed — no model/schema changes in
  this phase.
- **No frontend code changes**: correctly not needed — the frontend audit (§7)
  found no gaps, so no `npm run typecheck`/`lint`/`build` re-run was required.

---

## 9. Files changed this phase

**Backend:**
- `app/core/config.py` — `session_absolute_ttl_seconds` setting, `is_production`
  property, `_validate_cookie_security` model validator.
- `app/schemas/auth.py` — common-password blocklist, `PasswordStr` type shared by
  register/reset/change.
- `app/services/session_service.py` — `touch_session()`.
- `app/api/deps.py` — absolute-lifetime-cap check + `touch_session()` call in
  `_load_user_from_request`.
- `app/api/routes/admin.py` — `reactivate_user` now revokes all sessions for the
  target user.
- `tests/test_auth_hardening_phase7.py` — new, 8 tests per the spec's TEST list.

**Frontend:** none (audited, no gaps found — see §7).

---

## 10. Honest scope note

This phase closed four specific, real gaps found by reading the actual code, on
top of an authentication system that was already using solid primitives (Argon2,
opaque server-side sessions, HMAC-signed tokens, constant-time comparison,
single-use tokens, generic error messages, existing rate limiting on every
sensitive endpoint). It deliberately did not rebuild anything that was already
correct, and did not add protections — like the "last admin" guard considered and
rejected in Phase 6 — that would look like security but do nothing.
