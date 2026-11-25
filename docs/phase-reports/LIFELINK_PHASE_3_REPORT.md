# LifeLink — Phase 3 Report: Family / Attendant Portal

**Scope:** Phase 3 (complete Family/Attendant portal) — real family-link authorization, patient access scoping, emergency co-management, and a significant, unrelated production bug found and fixed along the way. Backend + frontend both changed this phase, with a new Alembic migration.

---

## Starting point

Phase 2's audit had already found the Family portal (`pages/family/*`) substantially real and honestly built: linked-patient cards, a request/accept/revoke link workflow, per-patient request/emergency filing and tracking, and its own distinct navigation (confirmed **not** a disguised Patient Dashboard — no `PatientDashboard` references exist anywhere under `pages/family/`). Reading the code closely this phase, plus the real `family` backend router/model, surfaced several concrete gaps against the Phase 3 spec, listed below with what was done about each.

---

## What was actually wrong, and what changed

### 1. No real "reject" distinct from "revoke"
The spec asks for three separate patient actions: **approve, reject, revoke**. The backend only had accept/revoke — declining a pending request and ending a granted one were the same `REVOKED` status, which also loses the distinction in the audit trail (a decline vs. a later access-removal look identical).

**Fixed:** added a `REJECTED` status and a new `POST /family/links/{id}/reject` endpoint (patient-only, PENDING-only), with its own notification (`family.link_rejected`) and audit action. `revoke` is now restricted to `PENDING`/`ACCEPTED` links (acting on an already-terminal link now returns `409` instead of silently "succeeding"). The frontend's "Decline" button on a pending request now calls `reject`; "Revoke access" on an accepted link still calls `revoke`.

### 2. Link requests never expired
A `PENDING` request sat forever with no expiry — not what "expired link" as a named test scenario implies, and not realistic (a family member could accept a stale request from a year ago).

**Fixed:** added `expires_at` to `FamilyLink` (set to +30 days on every `PENDING` transition, cleared once accepted/rejected/revoked). There's no scheduled job in this system, so expiry is enforced lazily — checked and flipped to `EXPIRED` the moment a link is read or acted on (`list_links`, `accept`, `reject`, `revoke`), rather than a background task that could silently never run.

### 3. Family could act for a deactivated patient
`has_accepted_link()` (the gate used everywhere family access is checked) only checked the link's own status, never whether the *patient's account* was still active. An admin-suspended patient's family members could still file requests "on their behalf."

**Fixed:** `has_accepted_link()` now also requires `patient.is_active`. Same fix applied to `/patients/managed` (which had its own separate, unguarded query). The `FamilyLink` row itself is left untouched by this — it's not deleted, so the historical relationship and audit trail survive a patient's deactivation; access is what's blocked, not the record.

### 4. Family only saw requests/emergencies **they personally filed** — not the patient's own
This was the single biggest functional gap, and it was already honestly documented in the old `FamilyDashboard.tsx` file comment: `GET /blood-requests` and `GET /emergencies` filtered by `requester_user_id == me OR patient_user_id == me`. For a FAMILY user, `patient_user_id` never equals their own id, so the clause silently collapsed to "only what I filed." A patient managing their own blood pressure meds and filing their own emergency would be invisible to the family member "monitoring" them — the opposite of what a family dashboard is for.

**Fixed:** both list endpoints now also include every request/emergency belonging to any patient the caller has an `ACCEPTED` (and active) link to. The `FamilyDashboard`/`FamilyPatientDetail` file comments and the stale "isn't listed here yet" disclaimer were removed and replaced with an accurate description.

### 5. Family couldn't finish managing an emergency the patient started themselves
`cancel`/`resolve`/`assistance-found` were gated to `created_by_user_id == user.id` only — a family member could **view** a patient-raised emergency (via `_can_view`, which already checked the family link) but not act on it, even with a full `ACCEPTED` link. This directly contradicts "Authorized family members should be able to initiate or **assist with** eligible emergency workflows."

**Fixed:** added `_can_manage()` (mirroring the blood-request equivalent that already existed) — creator, the patient themselves, or an accepted-link family member. Used by `cancel`, `resolve`, and `assistance-found`.

### 6. No "Requested by family member" anywhere
Verified via grep before starting: zero occurrences of that phrase, or any equivalent, in the entire frontend.

**Fixed:** `BloodRequestOut`/`EmergencyOut` now carry a real `filed_by_family` flag plus `requester_name`/`created_by_name` (derived server-side from `requester_user_id != patient_user_id`, not guessed client-side). Wired into the shared `RequestTrackDrawer`/`EmergencyTrackDrawer` (used by every role that views request/emergency detail) and into the Family portal's own list rows.

### 7. Missing test coverage for every named scenario
`test_family.py` had 4 tests covering the happy path. Added 15 more, one per scenario the spec named, run against the real Postgres test DB (not mocks): self-link rejection (see note below), duplicate-link rejection, unauthorized patient access (404, not 403 — doesn't leak whether a request exists), pending → reject, pending → accept, revoke, revoke-on-terminal-link rejection, expired link, family member suspended (their session is rejected on the very next request), patient deactivated (family drops out of "managed" and is blocked from filing), family seeing the patient's own-filed items, the `filed_by_family` label round-tripping to the patient's own view, and an audit-trail check via the real admin `/admin/audit-log` endpoint.

*Note on self-link rejection:* the guard (`patient.id == user.id`) genuinely can't be triggered through the API today — a user account has exactly one role, so the same id can never simultaneously pass `require_roles(FAMILY)` and resolve as a `PATIENT`-role account by email. Rather than skip it, the test calls the route function directly with the lookup patched, to prove the guard itself is correct if that invariant were ever violated elsewhere — documented in the test itself so it isn't mistaken for a realistic HTTP-level repro.

---

## A significant, unrelated bug found during live verification

While driving the real login form in an actual browser (not the test client, and not a cookie injected via API) to verify Phase 3's mobile rendering, **login failed outright** with `net::ERR_FAILED` on `/auth/login`. Root cause: Phase 1 (this project's earlier work) added an `X-Request-Id` header to every request in `lib/api.ts`, but the backend's CORS `allow_headers` list was never updated to include it. Every real browser sends a CORS preflight for a custom header; the backend rejected it with `400 Disallowed CORS headers`, and the browser silently blocks the real request that follows.

This is invisible to `TestClient`/`httpx`-based tests (they don't enforce CORS) and to any Playwright check that obtains its session cookie via a direct API call rather than the actual login form — which is exactly how this phase's own earlier mobile-overflow checks, and Phase 2's, were done. **The real interactive login flow had likely been broken for every user in a real browser since Phase 1**, undetected because nothing in the test suite or prior verification passes exercised the browser's own CORS enforcement.

**Fixed:** added `X-Request-Id` to `CORSMiddleware`'s `allow_headers` in `app/main.py`. Re-verified with a fresh Playwright session that fills the real form and presses Enter (no cookie shortcuts): login now succeeds and lands on the correct role-based dashboard.

---

## Verified, not assumed

- **224 backend tests pass** (`pytest tests/ -q`, exit 0), including the 19 in the expanded `test_family.py`.
- **Live HTTP verification against the real running server** (not TestClient): a 34-step script drives the actual `/family/*`, `/blood-requests`, `/emergencies`, and `/admin/*` endpoints end-to-end — request → duplicate-rejected → reject → accept → family-sees-patient's-own-request → family-manages-patient's-own-emergency → revoke → expiry (forced via direct DB write, then lazily discovered on read, exactly as a real stale request would be) → patient deactivation blocks family → family-account suspension blocks the family member → audit trail confirmed via `/admin/audit-log`. All 34 checks passed.
- **Real browser verification** (Playwright driving the actual login form, not an injected cookie) confirms login works and, at all five required widths (320/375/390/430/768px), `/family` and `/family/patients` show zero horizontal overflow.
- `npm run typecheck`, `npm run lint`, and `npm run build` all pass clean (same pre-existing bundle-size *warning*, not an error, as prior phases).
- Re-confirmed: Family renders its own distinct layout/nav, never `PatientDashboard` — no shared references anywhere under `pages/family/`.

---

## Files touched

**Backend:** `app/models/enums.py`, `app/models/family.py`, `app/schemas/family.py`, `app/api/routes/family.py`, `app/services/domain_service.py`, `app/api/routes/patients.py`, `app/api/routes/blood_requests.py`, `app/schemas/blood_request.py`, `app/api/routes/emergencies.py`, `app/schemas/emergency.py`, `app/main.py` (CORS fix), new migration `alembic/versions/d1e2f3a4b5c6_phase9_family_link_reject_expiry.py`, `tests/test_family.py`.

**Frontend:** `src/lib/domain.ts` (new types/fields, `rejectFamilyLink`), `src/pages/PatientDashboard.tsx` (Decline → reject), `src/pages/family/FamilyLinkedPatients.tsx` (REJECTED/EXPIRED statuses, corrected doc comment), `src/pages/family/FamilyDashboard.tsx`, `src/pages/family/FamilyPatientDetail.tsx` (corrected disclaimers, "Requested by family member" labels), `src/components/RequestTrackDrawer.tsx`, `src/components/EmergencyTrackDrawer.tsx` (shared "Requested by family member" badge).

---

## What's next

Field-level privacy was checked, not just assumed: the only patient data a family member's session can reach is what already flows through blood-request/emergency records (name, blood type, request details) plus `PersonOut` (id/name/email) on the link itself — there's no endpoint that hands a family member the patient's medical-notes/address profile fields, so nothing further needed hiding there. The natural next candidates are the same dead-button/fabricated-claim sweep already applied to Patient, now applied to Hospital/Donor/Admin dashboards, and deciding whether the CORS fix warrants a quick audit of any *other* custom headers added since Phase 1 that might have the same blind spot.
