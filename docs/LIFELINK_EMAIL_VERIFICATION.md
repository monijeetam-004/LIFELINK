# LifeLink — Email Verification

Production hardening. Code: `backend/app/api/deps.py::get_current_user`, the existing `/auth/verify-email` and `/auth/resend-verification` routes, `frontend/src/components/RequireAuth.tsx`. Tests: `backend/tests/test_hardening_email_verification.py` (7), `frontend/src/components/RequireAuth.test.tsx` (verify-email redirect).

## What was wrong

Verification tokens and the verify/resend endpoints already existed, but nothing **enforced** verification: an unverified account could sign in and use every API route. The frontend did not redirect either.

## What is enforced now

- **Server side:** `get_current_user` (the dependency behind every authenticated route) returns **403** with header `X-LifeLink-Reason: email-unverified` and detail *"Verify your email address to continue"* for an unverified account on any route except `/auth/me`, `/auth/logout`, `/auth/change-password`. Because every role-gated route goes through this dependency (invariant 44), there is no per-route opt-in to forget.
- **Client side:** `RequireAuth` sends an unverified user to `/verify-email` (with the original path in state); the API's 403 is the real control, the redirect is UX.
- **Tokens:** single-use, expiring, stored only as a keyed hash (`sign()`); a resend supersedes the previous token; expired, reused and invalid tokens are rejected and never activate the account.
- **No account enumeration:** resend answers identically for unknown, unverified and already-verified addresses.
- **The link** points at the frontend's `/verify-email?token=…` and carries no session.

## Tests (all executed, all pass)

`test_unverified_account_is_blocked_server_side_until_verified`, `test_unverified_account_may_still_sign_out_and_change_password`, `test_token_is_single_use_hashed_and_expiring`, `test_expired_and_invalid_tokens_are_rejected_and_do_not_activate`, `test_resend_supersedes_the_previous_token`, `test_resend_is_generic_for_unknown_and_already_verified_accounts`, `test_verification_link_points_at_the_frontend_and_carries_no_session`.

The pre-existing test helpers create verified users (as real seeding does), so the rest of the suite exercises verified accounts; the E2E fixture users are seeded verified.

## Not verified here

Delivery through a real SMTP provider (Gmail SMTP per the README) — in this sandbox mail goes to the in-memory test outbox / an unreachable SMTP host. Email *content* and links are asserted; *delivery* is not.
