# LIFELINK — PHASE 8 REPORT: RBAC and Object-Level Authorization

**Scope of this phase (as specified by the user):** a complete authorization audit —
build a formal role/resource/action permission matrix, then attack the object-level
boundaries directly (id-substitution across every ownership-scoped resource;
patient→patient, donor→donor, family→unrelated patient, hospital A→hospital B,
admin-only endpoint→every non-admin role) and confirm every one fails correctly,
plus ensure authorization-sensitive operations generate audit records. Nothing
outside authorization was touched. (Note: an earlier, unrelated phase of this
project was also informally called "Phase 8" in its own file name — this report
covers a different piece of work and is named distinctly, `..._AUTHZ_REPORT.md`,
to avoid confusion with `LIFELINK_PHASE_8_REPORT.md`, the Neo4j graph-engine phase.)

**Status: DELIVERED.** Every claim below was verified by reading every route file
in the API end to end, then running the full backend pytest suite (329 tests),
`ruff`, and `mypy`.

---

## 1. What was inspected first

Every one of the 14 route files under `app/api/routes/` was read in full —
`patients.py`, `donors.py`, `hospitals.py`, `family.py`, `emergencies.py`,
`blood_requests.py`, `matching.py`, `verifications.py`, `certificates.py`,
`notifications.py`, `admin.py`, `auth.py` — along with `app/api/deps.py` (the
single place role resolution and role gating happen) and the ownership-relevant
parts of `app/services/domain_service.py` (`has_accepted_link`,
`resolve_beneficiary`, the `get_or_create_*_profile` helpers).

The first and most important finding, checked explicitly rather than assumed:
**a user's role is never taken from anything the client sends.** `get_current_user`
resolves the session cookie to a Redis-stored session, looks up `user_id`, and
loads `User.role` from the database — the same server-side path used everywhere in
the app. A repo-wide search for every place a `User.role` is ever written
confirmed there are exactly two: `auth_service.register` (restricted to
`REGISTERABLE_ROLES`, which excludes `ADMIN`) and `admin.change_user_role` (gated
behind `require_roles(ADMIN)`, with a separate check blocking an admin from
changing their own role). There is no code path anywhere that lets a request body,
header, or query parameter influence which role a request is treated as having —
so a "manually constructed request claiming a different role" (the exact attack
the spec's ADMIN section asks about) has nothing to attach to.

---

## 2. Formal permission matrix

"View" means the resource is visible in a list or detail response; "Manage" means
state-changing actions (cancel, respond, approve, etc.). Everything not listed as
allowed is denied by the code — enforced at the object level for cross-tenant
attempts, not just hidden in the UI.

| Resource | PATIENT | FAMILY | DONOR | HOSPITAL | ADMIN |
|---|---|---|---|---|---|
| Own profile (`/patients/me`, `/donors/me`, `/hospitals/me`) | View/edit own (PATIENT) | View/edit managed patient's profile only with an ACCEPTED link | View/edit own (DONOR) | View/edit own (HOSPITAL) | Full, via `/admin/*` |
| Another user's profile | ✗ | ✗ (unless ACCEPTED link) | ✗ | ✗ | ✓ (read-only admin views; never writes another user's role-profile fields) |
| Blood request — own / for a linked patient | View, cancel, fulfill | Same, only with an ACCEPTED link to the patient | View if responded; respond | View if request's city/hospital-name matches this facility; respond/allocate/fulfill | View/list all (`/admin/blood-requests`) |
| Blood request — unrelated | ✗ (404) | ✗ (404) | ✗ unless the donor has actually responded to it | ✗ (404) outside this hospital's own city/facility-name | ✓ |
| Emergency — own / for a linked patient | View, cancel, resolve, assistance-found | Same, only with an ACCEPTED link | View/respond if open or already responded | View if open, or area-matched; acknowledge/respond | View/list all |
| Emergency — unrelated | ✗ (404) | ✗ (404) | Open ones are visible to any donor (by design — this is the emergency broadcast); management actions still require creator/patient/family/accepted-responder | ✗ outside area match, for management actions | ✓ |
| Donor identity / PII (name, contact) | Never, except a donor who explicitly accepted their own request (needed to arrange the donation) | Same as patient, for a linked patient's request | Own, always | Never (anonymized `donor_ref` only in match views) | Full (verification review, `/admin/donors` — medical fields excluded, see §4) |
| Donor donation history / certificates | ✗ | ✗ | Own only | ✗ | View/verify only (no medical detail) |
| Hospital inventory / inventory log | ✗ | ✗ | ✗ | Own facility only | View (`/admin/facilities/{id}`) |
| Hospital-to-hospital resources (routes, resource-pathways) | ✗ | ✗ | ✗ | Own facility only, even when passing another facility's id explicitly | Full |
| Family links | See own | See/manage own (as the family side) or as the patient (accept/reject/revoke) | ✗ | ✗ | View via audit log only |
| Notifications | Own only | Own only | Own only | Own only | Not exposed as a separate admin surface (audit log covers system events) |
| Verification requests (donor identity / hospital facility) | ✗ | ✗ | Submit/view own | Submit/view own | Full — the only role that can ever set `identity_verified`/`verified` |
| Admin endpoints (`/admin/*` — users, roles, suspend/reactivate, audit log, facilities, donors, certificates, analytics, transport routes, graph controls) | ✗ | ✗ | ✗ | ✗ | Full |

This matches the spec's per-role summary exactly: PATIENT gets own profile/
requests/emergencies, permitted family relationships, authorized hospital/request
information, and anonymized donor matching, and is blocked from other patients'
data, donor PII, inventory modification, verification approval, and admin APIs;
FAMILY is scoped to linked patients only; DONOR is scoped to their own profile/
responses/availability/history/certificates; HOSPITAL is scoped to its own
facility's resources; ADMIN has full access, gated by explicit server-side role
checks with no client-controllable escalation path.

---

## 3. Object-level attack testing — the spec's explicit matrix

Every pairing the spec names was tested with a real HTTP request, not just
inferred from the code:

| Attack | Result | Where |
|---|---|---|
| `GET /blood-requests/{other's id}` | 404 | pre-existing test (`test_blood_requests.py`), re-verified |
| Patient → another patient (view/cancel/fulfill a request) | 404 on all three | pre-existing + new audit assertion (`test_authz_phase8.py`) |
| Patient → another patient (view/cancel an emergency) | 404 | pre-existing (`test_emergencies_and_notifications.py`) |
| Donor → another donor (certificate) | 404 | pre-existing (`test_certificates.py`) |
| Donor → another donor (appointment cancel) | 404 | **new test** — enforcement already existed (query filtered by `donor_user_id`), no test previously exercised it directly |
| Family → unrelated patient (request/emergency) | 404 | pre-existing (`test_family.py`) |
| Family → a real link that isn't theirs to accept/reject | 403 | **new test** — enforcement existed, not previously tested |
| Hospital A → hospital B's resource-pathways (explicit facility id) | 403 "Not your facility" | **new test — this was a real, previously-untested surface.** Enforcement code already existed and was correct; there was simply no test proving it |
| Hospital A → a blood request outside its own city/facility visibility (respond/allocate/view events) | 404 on all three | **new test** — enforcement existed via `_visible_request_filter`, not previously tested end-to-end |
| Admin-only endpoint → every non-admin role | 403 on every one of 22 `/admin/*` endpoints, for PATIENT/FAMILY/DONOR/HOSPITAL | pre-existing, exhaustive (`test_admin.py`, `test_admin_phase6.py`) |

Every failing case above was independently confirmed to fail for the right
reason: in each object-level test, the *legitimate* owner/manager was also
exercised in the same test and confirmed to succeed, so a 404/403 that happened
to be a broken endpoint (rather than a real authorization boundary) would have
shown up as a false negative.

### The one genuine coverage gap found

**Hospital A querying hospital B's resource-pathways by explicit facility id**
(`GET /emergencies/{id}/resource-pathways?origin_facility_id=<facility B's id>`)
had correct enforcement (`emergencies.py` already checked
`origin.user_id != user.id → 403`) but no test had ever exercised it. This is the
one place in the entire API where a hospital passes another facility's id as an
explicit parameter — every other hospital-owned resource (inventory, requests,
routes, activity) is scoped implicitly through the authenticated user's own
`HospitalProfile`, so there was no id to substitute in the first place. Closed by
adding the missing test, not by changing any enforcement code (there was nothing
wrong with it).

---

## 4. Investigated and confirmed already correct (no change made)

- **Donor PII in matching/candidate views**: `MatchCandidate`/`EmergencyMatchResponder`
  schemas expose only an opaque `donor_ref` (first 8 chars of the user id) and
  `donor_name` only when the viewer manages that specific request (i.e., it's their
  own request and they need to know who to coordinate with) — never a browsable
  donor directory, never email/phone. This is a deliberate, already-correct design,
  not a gap.
- **Admin donor views** already exclude `medical_conditions`, `weight_kg`, and
  lat/lon at the Pydantic schema layer (`AdminDonorOut`, added in Phase 6) — role
  governance was already re-verified here rather than re-implemented.
- **Certificate download** supports two independent, correctly-scoped auth paths
  (owner/admin cookie session, or a signed single-purpose download token) with no
  third path that would let an unrelated user in.
- **Verification requests**: the only per-id `GET` is `/admin/verifications/{id}`,
  gated by `require_roles(ADMIN)`. A submitter only ever sees their own via
  `/verifications/me` (filtered by `user_id`). There is no endpoint at all that
  would let one donor or hospital view another's verification request or
  documents — the IDOR surface the spec is concerned about simply doesn't exist
  here, by construction rather than by a check that could be bypassed.
- **Role-change / suspend privilege escalation**: `change_user_role` and
  `suspend_user` both explicitly block self-action, and role assignment is only
  ever driven by the acting admin's own request body for the *target* user, never
  by anything that could let a non-admin reach these endpoints (both are behind
  `require_roles(ADMIN)`, and — see §1 — that role gate cannot be spoofed).
- **Hospital "me"-scoped resources**: inventory, inventory log, incoming requests,
  routes, and activity feed all resolve the acting hospital via
  `get_or_create_hospital_profile(db, user)` from the session-authenticated user —
  none of them accept a hospital/facility id as a request parameter, so there is
  no id for another hospital's data to be substituted into in the first place.

---

## 5. Gap closed: authorization-sensitive operations now generate audit records

This was the one genuine, spec-explicit gap. Every state-changing operation that
*succeeds* was already audited (`blood_request.created`, `emergency.cancelled`,
`hospital.inventory_add`, `admin.user_suspended`, and so on — dozens of existing
`audit_service.record(...)` calls). What was missing was any record of an
authorization *denial* — a signal that someone tried to do something they weren't
allowed to, which is exactly the kind of event a real audit trail exists to catch.
The one precedent already in the codebase was `auth_service.authenticate`, which
already audits a failed login (`user.login_failed`) rather than only rejecting
it — this phase extends that same principle to authorization.

Two audit records are new:

- **`authz.role_denied`** — added at the single choke point every role-gated
  endpoint in the app already passes through: `require_roles()` in
  `app/api/deps.py`. Every 403 it raises (all 22 `/admin/*` endpoints, and every
  other role-restricted endpoint in the app) is now recorded with the actor, the
  path, their actual role, and the roles that would have been accepted. One
  change, app-wide coverage.
- **Per-resource `<resource>.access_denied`** — added at the object-level
  ownership checks in `blood_requests.py`, `emergencies.py`, `certificates.py`,
  `hospitals.py`, `donors.py`, and `family.py`. Each one fires **only when the
  object genuinely exists but the caller isn't authorized for it** — never for a
  plain nonexistent id — so the signal stays meaningful (a real cross-tenant probe
  looks different from someone fat-fingering a UUID, and only the former is worth
  a durable record). This mirrors the exact distinction `auth_service.authenticate`
  already draws between "no such account" (not audited as a targeted attempt) and
  "wrong password for a real account" (audited).

This was verified directly, not just implemented: `test_authz_phase8.py` asserts
that specific `AuditLog` rows appear after each denial (with the right actor,
target, and attempted action) and — for the blood-request case — that a denial
against a genuinely nonexistent id does *not* add a row, confirming the signal
discipline holds.

---

## 6. Tests added

`backend/tests/test_authz_phase8.py` (9 new tests) covers:

- Admin-endpoint denial generates `authz.role_denied` with the right metadata,
  confirmed for a single endpoint and then for every non-admin role against a
  second endpoint.
- Patient→patient on blood requests (view/cancel/fulfill), with the audit record
  confirmed and a genuinely-nonexistent id confirmed to *not* generate one.
- Patient→patient and unrelated-family→patient on emergencies (view/cancel/
  resolve), with all four expected audit rows confirmed.
- Donor→donor on appointment cancellation (new coverage — see §3).
- Donor→donor on certificates, with the audit record confirmed (enforcement
  itself already covered by `test_certificates.py`).
- **Hospital A→hospital B on resource-pathways** (new coverage — the genuine gap
  from §3), confirming both the 403 and the audit record, and that hospital A
  querying its own facility for the same emergency succeeds.
- **Hospital A→hospital B via city/facility-name visibility** on
  respond/allocate/view-events (new coverage), confirming all three are denied
  for the wrong hospital, all three are audited, and the right hospital succeeds.
- Family link actions (accept/reject) by an outsider who is neither the family
  member nor the target patient, confirming the 403, the audit record, and that
  the real patient can still act on it.

---

## 7. Verification actually performed

- **Full backend test suite:** `ENVIRONMENT=test pytest` → **329 passed, 0
  failed, 0 errors** (320 pre-existing + 9 new), confirmed via JUnit XML output
  rather than the terminal summary line (this sandbox's output buffering can
  drop that line even on a clean run, as noted in earlier phase reports).
- **`ruff check`** on every file touched this phase (`app/api/deps.py`,
  `app/api/routes/blood_requests.py`, `emergencies.py`, `certificates.py`,
  `hospitals.py`, `donors.py`, `family.py`, `tests/test_authz_phase8.py`): clean.
- **`mypy app`**: 3 errors, identical to the pre-existing baseline from Phases 6–7
  (`app/api/routes/verifications.py:243-244`, `app/api/routes/donors.py:219`) —
  none in any file touched this phase. `NoReturn` annotations were added to the
  new `_deny_access`/`_deny_link_action` helpers specifically so mypy correctly
  narrows the "object is not None past this point" flow in every caller.
- **No Alembic migration**: correctly not needed — `AuditLog` already existed
  with a flexible `meta` JSONB column; this phase only added new `action` string
  values and callers, no schema changes.
- **No frontend changes**: this phase is a server-side authorization audit —
  every enforcement point lives in the API layer and cannot be affected by
  anything the frontend does or doesn't render, so no frontend re-verification
  was needed. (Client-side route/nav guards exist for UX, as in every prior
  phase, but were never relied on for security and remain unchanged.)

---

## 8. Files changed this phase

- `app/api/deps.py` — `require_roles()` now audits every role-based denial
  (`authz.role_denied`).
- `app/api/routes/blood_requests.py` — `_deny_access()` helper; used by
  `get_request`, `cancel_request`, `fulfill_request`.
- `app/api/routes/emergencies.py` — `_deny_access()` helper; used by
  `get_emergency`, `emergency_match_candidates`, `emergency_resource_pathways`,
  `_load_owned` (→ `cancel_emergency`/`resolve_emergency`), `assistance_found`;
  plus a direct audit call on the hospital-facility-ownership 403 in
  `emergency_resource_pathways`.
- `app/api/routes/certificates.py` — audit calls in `get_certificate` and
  `download_certificate`.
- `app/api/routes/hospitals.py` — `_get_visible_request()` now takes the acting
  user and an `action` label and audits denials; `respond_to_incoming` and
  `request_events` refactored to use it (previously duplicated the same check
  inline) instead of raising directly.
- `app/api/routes/donors.py` — `cancel_appointment` distinguishes "no such
  appointment" from "a real one, someone else's" for audit purposes.
- `app/api/routes/family.py` — `_deny_link_action()` helper; used by
  `accept_link`, `reject_link`, `revoke_link`.
- `tests/test_authz_phase8.py` — new, 9 tests per §6.

---

## 9. Honest scope note

The authorization architecture underneath this system was already substantially
correct going into this phase: role resolution is server-side and unspoofable,
every admin endpoint is uniformly gated, every ownership-scoped resource is
checked against the authenticated user (or a verified relationship, for FAMILY),
and the one place a facility id crosses hospital boundaries was already checked
correctly. This phase's real contribution was proving that with actual attack
requests rather than code inspection alone, closing the one place a real
enforcement check existed with no test behind it, and adding the audit trail for
authorization denials that the existing audit log — already thorough for
successful actions — was missing.
