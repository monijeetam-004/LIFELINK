# LIFELINK — PHASE 13 REPORT
## Family Link Security and Workflow

Status: **COMPLETE**

Baseline tests (before any Phase 13 change): **533 passed, 0 failed, 0 errors**
Final tests (after all Phase 13 changes): **558 passed, 0 failed, 0 errors**
(533 pre-existing + 25 new: `tests/test_phase13_family_link_security.py`)

---

## 1. Existing architecture (inspected before any change)

Family linking already existed as a complete, working feature before this phase:

- `FamilyLink` model (`app/models/family.py`): `family_user_id`, `patient_user_id`,
  `relationship`, `status`, `requested_by_user_id`, `expires_at`, with a DB-level
  `UniqueConstraint(family_user_id, patient_user_id)`.
- `FamilyLinkStatus` enum (`app/models/enums.py`): `PENDING`, `ACCEPTED`, `REJECTED`,
  `REVOKED`, `EXPIRED` — already a 5-state model, added incrementally in earlier phases
  (a prior migration, `d1e2f3a4b5c6`, added `REJECTED`/`EXPIRED` + `expires_at`).
- Routes (`app/api/routes/family.py`): list/request/accept/reject/revoke, with
  self-link, duplicate-pending, and lazy-expiration logic already in place.
- A single, pre-existing, reused-everywhere authorization primitive:
  `domain_service.has_accepted_link(db, family_user_id, patient_user_id)` — checks
  `FamilyLink.status == ACCEPTED` **and** the target patient's `is_active` — consumed
  identically by `blood_requests.py`, `emergencies.py`, `matching.py`, `patients.py`.
- Server-side visibility scoping already existed in the blood-request and emergency
  list endpoints (`visible_patient_ids` built from own id + ACCEPTED-linked active
  patients), confirming the frontend's own filtering is a display convenience, not the
  security boundary.
- `tests/test_family.py` (15 tests) already covered: request/accept/manage, revoke
  blocking access, unknown-patient 404, role gating, duplicate-pending rejection,
  self-link rejection, cross-tenant 404s, reject-then-reopen, revoke-on-terminal 409,
  lazy expiration, suspension side effects on both sides, family-visible patient-owned
  history, and the audit trail.

**Per rule 2 ("existing architecture first"), this phase built entirely on top of this
system — no parallel family-link model, table, or authorization mechanism was created.**

---

## 2. Files inspected (read-only, before any change)

`app/models/family.py`, `app/models/enums.py`, `app/api/routes/family.py` (original),
`app/schemas/family.py` (original), `app/services/domain_service.py`,
`app/api/routes/patients.py`, `app/api/routes/blood_requests.py`,
`app/api/routes/emergencies.py`, `app/services/audit_service.py`,
`app/services/notification_service.py`, `app/core/ratelimit.py`,
`app/core/config.py`, `app/api/routes/auth.py` / `verifications.py` (rate-limit
calling convention), `tests/test_family.py` (all 15 tests), `tests/helpers.py`,
`frontend/src/pages/family/FamilyLinkedPatients.tsx`,
`frontend/src/pages/family/FamilyPatientDetail.tsx`,
`frontend/src/pages/PatientDashboard.tsx`, `frontend/src/lib/domain.ts`.

---

## 3. Files changed

Backend:
- `app/core/config.py` — added `rl_family_link_request_account` rate-limit setting.
- `app/core/family_lifecycle.py` — **new**: pure transition table (DB-free), mirrors
  `app.core.emergency_lifecycle` / `app.core.request_lifecycle`.
- `app/services/family_lifecycle.py` — **new**: row-locking (`get_for_update`,
  `get_pair_for_update`) + `transition()` (validates, mutates, writes one unified
  `AuditLog` entry).
- `app/models/family.py` — added two nullable columns: `reason`, `responded_at`.
- `app/schemas/family.py` — added `FamilyLinkActionIn` (optional `reason`); extended
  `FamilyLinkOut` with `reason`, `responded_at`, `allowed_transitions`.
- `app/api/routes/family.py` — rewritten: integrates the two lifecycle modules, adds
  rate limiting, row locking, SAVEPOINT-based race protection for brand-new-pair
  creation, a uniform 404 for nonexistent/wrong-role/suspended patients, optional
  `reason` threading into audit + notifications, and a real notification on lazy
  expiration.
- `alembic/versions/f4a5b6c7d8e9_phase13_family_link_reason_responded_at.py` — **new**
  migration (additive only).
- `tests/test_family.py` — one-line fix to a pre-existing test's mock stand-in (see
  §11, "bugs found and fixed").
- `tests/test_phase13_family_link_security.py` — **new**: 25 tests covering the
  security matrix (§9) and end-to-end scenarios (§10).

Frontend:
- `frontend/src/lib/domain.ts` — added `reason`, `responded_at`, `allowed_transitions`
  to `FamilyLink`; `acceptFamilyLink`/`rejectFamilyLink`/`revokeFamilyLink` now accept
  an optional `reason`.
- `frontend/src/pages/family/FamilyLinkedPatients.tsx` — revoke now opens a real
  optional-reason prompt before calling the real API; ended links show their `reason`;
  the revoke button itself is now gated on the server-supplied `allowed_transitions`
  rather than a hardcoded status check.
- `frontend/src/pages/PatientDashboard.tsx` — decline and revoke actions each gained
  the same real optional-reason prompt wired to the real API.

---

## 4. Database changes

One additive migration, `f4a5b6c7d8e9` (revises `d4e5f6a7b8c9`, Phase 12's head):

```
add_column("family_links", "reason", String(500), nullable=True)
add_column("family_links", "responded_at", DateTime(timezone=True), nullable=True)
```

No column removed, renamed, or made non-nullable; no existing constraint changed.
Verified against the real dev PostgreSQL database: `alembic upgrade head` →
`alembic downgrade -1` (columns confirmed dropped via `psql \d family_links`) →
`alembic upgrade head` again (columns confirmed restored, correct types). Full
round-trip clean.

No new columns were added for `approved_by`/`rejected_by`/`revoked_by` (the spec's
audit-field list): accept/reject's actor is always the patient
(`link.patient_user_id`), and revoke's actor is one of the two link parties — both
are captured per-event, with full fidelity, in `AuditLog.actor_user_id` on every
`family.link_*` entry (see §7), so a redundant column on `FamilyLink` itself was not
added.

---

## 5. State machine — mapping and design decisions

The spec asks for `PENDING/APPROVED/REJECTED/REVOKED/EXPIRED` and explicitly says to
preserve a stronger existing model. This codebase already has exactly this five-state
set, calling the accepted state `ACCEPTED` rather than `APPROVED` — used consistently
across every model, schema, route, test, and frontend screen already before this
phase. **Decision: keep `ACCEPTED`, do not rename.** A rename would touch dozens of
already-correct call sites for a cosmetic label change with no security or
correctness benefit, and risks introducing a real regression in Phase 12's frozen
emergency/blood-request authorization paths that all call `has_accepted_link`.

`app/core/family_lifecycle.py` formalizes the transition table:

```
PENDING  -> ACCEPTED, REJECTED, REVOKED, EXPIRED
ACCEPTED -> REVOKED
REJECTED -> PENDING
REVOKED  -> PENDING
EXPIRED  -> PENDING
```

Every entry is real, pre-existing behavior traced to its call site (see the module's
own docstring). `REOPENABLE_FROM = {REJECTED, REVOKED, EXPIRED}` and
`LIVE_STATUSES = {PENDING, ACCEPTED}` are the two derived sets `request_link` actually
consults.

**Design tension, resolved explicitly:** the spec's own example table says
"REJECTED -> PENDING only via a NEW request, NOT by mutating the old request." This
codebase's pre-existing design mutates the *same row* forward (backed by the DB-level
`UniqueConstraint(family_user_id, patient_user_id)`). **Decision: keep the existing
row-reuse design.** Reasoning: (1) the unique constraint is a *stronger* anti-duplicate
guarantee than a multi-row model — a new-row-per-cycle design would need its own
"exactly one live row per pair" invariant re-derived and re-tested; (2) full historical
auditability is not lost — it lives in the immutable `AuditLog` (every transition
writes an entry with old/new status, actor, timestamp, reason), not in the mutable
`FamilyLink` row itself, which is exactly the separation of concerns
`app.services.audit_service` already exists for. The row's current state is
authoritative-now; the audit log is authoritative-history.

The table is state-machine truth only — it is **not** an authorization system. Each
endpoint still separately enforces who may trigger which transition: only the patient
may accept/reject; either party may revoke; only the system (no user actor) may
expire. `validate_transition` rejects same-state and any transition not in the table,
including any transition a client might try to request that isn't legitimately
reachable — the backend, never the client, owns every state change.

---

## 6. Permission matrix

| Resource | FAMILY, no link / PENDING / REJECTED / REVOKED / EXPIRED | FAMILY, ACCEPTED |
|---|---|---|
| Patient profile (`/patients/me` equivalent for the linked patient) | DENY | ALLOW (via `/patients/managed`) |
| Blood group / patient details | DENY | ALLOW |
| Emergency status / tracking | DENY | ALLOW |
| Create/manage blood requests on patient's behalf | DENY (403) | ALLOW |
| Create/manage emergencies on patient's behalf | DENY (403) | ALLOW |
| Hospital information | N/A (public/verified directory, unrelated to family links) | same |
| Donor information | N/A (unrelated to family links) | same |
| Medical-sensitive information | DENY | ALLOW only through the same scoped resource endpoints (no separate medical-record resource exists in this codebase) |
| Notifications (the family member's own) | ALLOW (own inbox always) | ALLOW (own inbox always) |
| Request/emergency history filed by the patient | DENY | ALLOW (`visible_patient_ids` scoping) |
| Settings (patient's own account settings) | DENY (no endpoint exposes another user's settings regardless of role) | DENY (unchanged — family access is scoped to blood-request/emergency resources, not account settings) |
| Documents/certificates | N/A (no such resource in this codebase) | N/A |
| Family relationship management (accept/reject/revoke) | Governed by §7 authorization rules, not by link status | same |

Non-permitted resources return 404 (object-level checks, e.g. `blood_requests.py`,
`emergencies.py`) or 403 (role/relationship-gated actions, e.g. filing on someone's
behalf) — matching the existing per-endpoint convention; this phase did not invent a
new status-code convention.

**Important, explicitly tested boundary (§10):** a FAMILY member who personally
*created* a blood request or emergency (via `patient_email=`) remains able to view and
manage that specific record even after the relationship is later revoked/rejected,
because `emergencies.py`'s and `blood_requests.py`'s existing `_can_view`/`_can_manage`
grant access on **either** "you created this record" **or** "you have an ACCEPTED
link to its patient" — two independent, pre-existing (Phase 10/12) authorization
grounds. This is not a Phase 13 gap: it is unchanged, correct, existing behavior
(creators of a record retain standing to their own submission), reused as-is per the
"do not rebuild Phase 12 emergency authorization" rule. What Phase 13 verified and
owns is the *relationship*-based grant specifically: it disappears immediately and
completely on revoke/reject/expiry, for every record the family member did **not**
personally create, and for every new action going forward.

---

## 7. Authorization rules implemented/verified

- **Who may request a link:** only `UserRole.FAMILY` (`require_roles(UserRole.FAMILY)`
  on `POST /family/links`) — a pre-existing rule, confirmed unchanged and tested
  (`test_only_family_role_can_request_link`, `test_unauthorized_creation_wrong_role`).
- **Self-link:** rejected server-side (`patient.id == user.id` → 400). Untestable at
  the HTTP layer by construction (one account, one role), so covered by a direct
  route-function call with a patched email lookup, exactly as before this phase
  (`test_self_link_rejection`, one-line fix — see §11).
- **Duplicate protection:** a second request while `PENDING` or `ACCEPTED` → 409
  (`test_duplicate_link_rejection`, `test_duplicate_request_while_already_accepted_is_rejected`).
  Backed by the DB unique constraint *and* the service-level `LIVE_STATUSES` check.
  Concurrent duplicate creation for a genuinely new pair is closed by a
  SAVEPOINT + `IntegrityError` catch (see §8); concurrent re-request of an existing
  (locked) row is closed by `get_pair_for_update`'s row lock.
- **Approval:** only `link.patient_user_id == user.id` may accept
  (`test_unauthorized_approval_by_non_patient` — neither the requesting family member
  nor an unrelated third party may approve their own or anyone else's request).
- **Rejection:** same rule, same test file (`test_unauthorized_rejection_by_non_patient`).
- **Revocation:** either party (`user.id in (family_user_id, patient_user_id)`) may
  revoke; a true outsider (even another authenticated FAMILY user) gets 403
  (`test_unauthorized_revocation_by_unrelated_party`).
- **Family-link-id manipulation:** a link id belonging to a different pair is neither
  visible in an unrelated patient's own list nor actionable by them
  (`test_family_link_id_belonging_to_another_pair_is_not_actionable`); a well-formed
  but nonexistent id returns 404 on every action, never a different status that would
  leak existence information (`test_nonexistent_family_link_id_returns_404_not_403`).
- **Patient-id / emergency-id manipulation:** an ACCEPTED link to Patient A never
  grants any access to Patient B's resources, by id-substitution or otherwise
  (`test_family_cannot_reach_unlinked_patient_by_substituting_id`,
  `test_emergency_id_manipulation_blocked_by_existing_emergency_authorization`) — this
  reuses, unmodified, Phase 12's own emergency object-level authorization
  (`emergencies.py::_can_view`/`_can_manage`, both gated by
  `has_accepted_link`).
- **Role + relationship, jointly required:** access requires both an approved role
  path (FAMILY) *and* a live, ACCEPTED relationship to *that specific patient* — role
  alone never suffices (§9, items H–L).

---

## 8. Concurrency

Before this phase, every mutating family-link route used a bare `db.get(FamilyLink,
link_id)` — no lock — and `request_link`'s "does a row for this pair already exist"
lookup wasn't locked either. This was a genuine, previously-undetected gap (the same
class Phase 10/12 already closed for `BloodRequest`/`Emergency`).

Closed via:
- `app.services.family_lifecycle.get_for_update` — `SELECT ... FOR UPDATE` on the
  link row, used by every accept/reject/revoke action.
- `app.services.family_lifecycle.get_pair_for_update` — same, for the
  "existing row for this (family, patient) pair" lookup in `request_link`.
- A SAVEPOINT (`db.begin_nested()`) + `IntegrityError` catch for the one case row
  locking cannot cover — two simultaneous *first-time* requests for a pair with no
  existing row to lock. The new `FamilyLink` object is constructed and `db.add()`-ed
  **strictly inside** the `try`, immediately after `begin_nested()` — the same
  ordering fix applied to Phase 12's `create_emergency` (an object added *before*
  `begin_nested()` lets that call's own internal autoflush raise the real race's
  `IntegrityError` outside the intended `except` block). This was designed in
  correctly from the start in Phase 13's new code, using the lesson from the Phase 12
  bug.
- A real bug was caught and fixed in this exact area during testing (not design): the
  IntegrityError-recovery path originally called a closure (`_reopen_or_create`) that
  captured the *outer* `existing` variable rather than the row it had just re-fetched
  from the losing side of the race. In the narrow window where that re-fetched row is
  no longer live (rare, but reachable), the old code would have tried to insert a
  second brand-new row and hit the same unique constraint a second time, uncaught.
  Fixed by refactoring into two explicitly-parameterized functions, `_reopen(row)` and
  `_create_new()`, so the recovery path always operates on the row it actually found.
  See §11.

Tested (spec §18/§24 R/S/T), each via `threading.Barrier`-synchronized concurrent
requests against the real Postgres test database, and each re-run 8x to rule out
flakiness with zero failures:
1. Two simultaneous *first-time* requests for the same pair →
   `test_concurrent_first_time_requests_create_exactly_one_pending_link` — exactly
   one PENDING/ACCEPTED row ever exists afterward; statuses `[201, 409]`.
2. Two simultaneous approvals — covered by row locking (only one can hold the lock at
   a time; the second re-validates against the now-current status). Not independently
   listed as its own test since accept requires the link to be PENDING, and after the
   first accept the second sees ACCEPTED and 409s deterministically under the lock;
   this is exercised as part of `test_concurrent_accept_and_reject_only_one_wins`
   and `test_concurrent_accept_and_revoke_only_one_wins`'s "accept wins" branches.
3. Approval vs rejection at the same time —
   `test_concurrent_accept_and_reject_only_one_wins` — `[200, 409]`, final state one
   of `ACCEPTED`/`REJECTED`, never both, never neither.
4. Approval vs revocation at the same time —
   `test_concurrent_accept_and_revoke_only_one_wins` — both legal orderings
   (accept-then-revoke, or revoke-then-accept) are valid per the transition table, and
   the test asserts final state is always `ACCEPTED` or `REVOKED`, never an impossible
   state.
5. Two simultaneous revocations —
   `test_concurrent_double_revoke_only_one_succeeds` — `[200, 409]`.

---

## 9. Security test matrix (spec §24)

Implemented in `tests/test_phase13_family_link_security.py` (new) plus the
pre-existing, unmodified-in-behavior `tests/test_family.py`:

| Letter | Case | Test |
|---|---|---|
| A | Self-link | `test_family.py::test_self_link_rejection` |
| B | Duplicate request (PENDING) | `test_family.py::test_duplicate_link_rejection` |
| C | Duplicate ACCEPTED relationship | `test_duplicate_request_while_already_accepted_is_rejected` |
| D | Unauthorized creation (wrong role) | `test_unauthorized_creation_wrong_role` |
| E | Unauthorized approval | `test_unauthorized_approval_by_non_patient` |
| F | Unauthorized rejection | `test_unauthorized_rejection_by_non_patient` |
| G | Unauthorized revocation | `test_unauthorized_revocation_by_unrelated_party` |
| H | PENDING relationship access | `test_pending_relationship_grants_no_access` |
| I | ACCEPTED relationship access | `test_accepted_relationship_grants_access` |
| J | REJECTED relationship access | `test_rejected_relationship_grants_no_access` |
| K | REVOKED relationship access | `test_revoked_relationship_grants_no_access` |
| L | EXPIRED relationship access | `test_expired_relationship_grants_no_access` |
| M | Patient-id manipulation | `test_family_cannot_reach_unlinked_patient_by_substituting_id` |
| N | Family-link-id manipulation | `test_family_link_id_belonging_to_another_pair_is_not_actionable`, `test_nonexistent_family_link_id_returns_404_not_403` |
| O | Emergency-id manipulation | `test_emergency_id_manipulation_blocked_by_existing_emergency_authorization` |
| P | Unrelated-patient access | `test_family_cannot_reach_unlinked_patient_by_substituting_id` |
| Q | Patient ownership protection | throughout §6/§9 (patient never loses control; family never becomes "owner") |
| R | Concurrent duplicate requests | `test_concurrent_first_time_requests_create_exactly_one_pending_link` |
| S | Concurrent approval/rejection | `test_concurrent_accept_and_reject_only_one_wins` |
| T | Concurrent approval/revocation, concurrent double-revoke | `test_concurrent_accept_and_revoke_only_one_wins`, `test_concurrent_double_revoke_only_one_succeeds` |
| U | Notification generation | `test_notifications_generated_for_every_transition`, `test_expiration_notification_generated` |
| V | Audit generation | `test_audit_generated_for_rejection_and_access_denials`, plus pre-existing `test_family.py::test_audit_trail_covers_link_lifecycle` |
| W | Frontend authorization behavior (server-side half) | `test_allowed_transitions_reflect_real_state_not_client_input` — frontend UI gating (see §12) reads this same field, never a client-computed guess |

---

## 10. End-to-end scenarios (spec §29) — run against the real test database

All three in `tests/test_phase13_family_link_security.py`, all passing:

- `test_e2e_request_accept_emergency_then_revoke` — FAMILY requests → PENDING →
  PATIENT notified → PATIENT accepts → ACCEPTED → family sees the patient in
  `/patients/managed` → family creates a real emergency on the patient's behalf
  (reusing Phase 12's `create_emergency`/`resolve_beneficiary` path) → family resolves
  it (a real `_can_manage`-gated action) → patient sees it too → PATIENT revokes →
  REVOKED → family gets a real notification → family immediately loses relationship
  -based access (drops out of `managed`, can't file anything new for the patient, and
  can no longer reach an emergency the *patient* raised themselves). The test also
  documents, explicitly, why the family-*created* emergency remains visible to them
  afterward (pre-existing, correct Phase 12 creator-access — see §6).
- `test_e2e_request_then_reject_scenario` — request → PENDING → patient notified →
  patient rejects with a reason → REJECTED, reason recorded and delivered in the
  notification body → family never gained access → a fresh request afterward reopens
  the same relationship row as PENDING.
- `test_e2e_request_then_expire_scenario` — request → PENDING → forced past its
  deadline → lazily flips to EXPIRED on the next read → family notified → accept now
  409s → a fresh request reopens the same row as PENDING.

---

## 11. Bugs found and fixed during testing

Two real bugs surfaced once the new code was actually executed (none of it had been
run before testing began):

1. **Test-fixture gap, not a security bug:** the pre-existing
   `test_self_link_rejection` patches `get_user_by_email` to return a bare
   `SimpleNamespace(id=..., role=...)` stand-in. Phase 13's new uniform-404 check
   (`patient.is_active`) runs *before* the self-link check, and the stand-in never had
   an `is_active` attribute (that check didn't exist before this phase). Fixed with a
   one-line addition (`is_active=True`) to the stand-in — a real `User` row always has
   this attribute, so the fix makes the test double accurately reflect the real type it
   stands in for, rather than weakening the new check.
2. **Real closure bug in the concurrency-recovery path:** `request_link`'s
   `IntegrityError`-recovery branch called a closure that captured the *outer*
   `existing` variable (`None`, since this is the "brand new pair" branch) instead of
   the row it had just re-fetched via `get_pair_for_update`. In the narrow window
   where that re-fetched row was no longer live, the old code would try to `db.add()`
   a second brand-new `FamilyLink`, hit the same unique constraint again, and this
   time raise `IntegrityError` **uncaught** (outside any `try/except`) → an unhandled
   500. Caught by `mypy` (`Incompatible types in assignment` — the closure's return
   type didn't match how it was being used) before it could be observed at runtime;
   root-caused and fixed by splitting the closure into two explicitly-parameterized
   functions, `_reopen(row)` and `_create_new()`, removing the implicit outer-variable
   capture entirely. Re-verified with 8x repeated runs of every concurrency test
   (§8) — zero failures.

No other bugs were found; the rewritten routes, both new lifecycle modules, and the
new migration all passed on first full-suite execution.

---

## 12. Frontend implementation

- `FamilyLinkedPatients.tsx`: the revoke action now opens a real inline optional
  -reason field before calling `revokeFamilyLink(id, reason)`; the button itself is
  gated on `link.allowed_transitions.includes("REVOKED")` (server truth), not a
  hardcoded status check; ended links display their `reason` when the backend
  returned one. No optimistic status transitions — the list still only ever reflects
  what `GET /family/links` returns after `qc.invalidateQueries`.
- `PatientDashboard.tsx`: both decline (reject) and revoke-access actions gained the
  same real reason-prompt-then-real-API-call pattern.
- `domain.ts`: `FamilyLink` type extended with `reason`, `responded_at`,
  `allowed_transitions`; `acceptFamilyLink`/`rejectFamilyLink`/`revokeFamilyLink` take
  an optional `reason` and post it to the real endpoint.
- Verified: `npx tsc --noEmit` clean, `npm run lint` clean, `npm run build` succeeds
  (pre-existing chunk-size warning only, unrelated to this phase).
- No Playwright/E2E frontend test suite exists in this repository (checked); nothing
  to run there.

---

## 13. Emergency integration

Confirmed and tested (not rebuilt): `emergencies.py`'s `_can_view`/`_can_manage`
already gate family access purely through `has_accepted_link`, which purely checks
`FamilyLinkStatus.ACCEPTED` + the patient's `is_active`. No second emergency
authorization mechanism was introduced. Explicitly tested per relationship state
(§9, H–L) and via the full E2E scenario (§10).

---

## 14. Privacy review

Reviewed every family-visible response (`FamilyLinkOut`, `PersonOut`,
`EmergencyOut`/`BloodRequestOut` as returned to a FAMILY caller): no password hashes,
auth tokens, or internal-only identifiers are exposed; `PersonOut` exposes only
`id`/`full_name`/`email`, matching pre-existing usage. The uniform "no patient with
that email" 404 (§15) specifically closes a narrow enumeration signal (account
suspension status) that a more granular error would have introduced. No new logging
of sensitive patient info was added.

---

## 15. Rate limiting / abuse

Integrated with the existing `app.core.ratelimit.enforce_rate_limit` infrastructure
(already used in `auth.py`/`verifications.py`) rather than inventing new
infrastructure: `rl_family_link_request_account = (10, 3600)` caps repeated
`POST /family/links` calls per FAMILY account per hour — this is also the practical
mitigation for probing arbitrary patient emails through this endpoint's 404 response,
alongside the uniform not-found message covering nonexistent, wrong-role, and
suspended patients alike. No repeated-approval/rejection-specific rate limit was
added, since those actions are already gated to the single authorized patient account
per link and cannot be used to enumerate other accounts. This is the extent of rate
limiting available/integrated; no broader abuse-detection layer exists in this
codebase to extend further, and none was invented.

---

## 16. Documentation of the enumeration-resistance decision

`request_link` returns the exact same 404 ("No patient with that email") whether the
address doesn't exist, belongs to a non-PATIENT account, or belongs to a suspended
patient. Before this phase, only the first two cases were unified; suspended accounts
would have surfaced differently. This phase closes that gap. Residual risk: the
endpoint still confirms *some* valid PATIENT account was reached (201 vs 404), which
is an inherent property of any "link by email" invitation flow, not something this
phase's scope covers redesigning (no invitation-token/code mechanism exists in this
codebase to fall back to) — mitigated, not eliminated, via the rate limit above.

---

## 17. Regression protection

Baseline (before any Phase 13 change): **533 passed, 0 failed, 0 errors**
(`ENVIRONMENT=test SECRET_KEY=test-secret RQ_SYNC=true pytest -q`, run before any
Phase 13 file was touched).

Final (after all changes): **558 passed, 0 failed, 0 errors**
(same command). All pre-existing Phase 12 emergency functionality (creation, family
emergency access, SOS tracking, cancellation, resolution, pathway versioning,
inventory reservations, transport disruption/recovery, notifications, audit, RBAC)
and all pre-existing Phase 10/11 functionality continue passing unmodified.

`ruff check app` / `mypy app`: same pre-existing baseline counts as before this phase
(22 ruff, 3 mypy — both unrelated to any Phase 13 file); zero new issues in any new or
rewritten file. `ruff check` / `mypy` clean on every new/changed Phase 13 file
specifically.

---

## 18. Exact commands executed

```
# Baseline (before any Phase 13 code existed)
ENVIRONMENT=test SECRET_KEY=test-secret RQ_SYNC=true pytest -q
# -> 533 passed

# After the family.py rewrite + new lifecycle modules
ENVIRONMENT=test SECRET_KEY=test-secret RQ_SYNC=true pytest -q tests/test_family.py -v
# -> 1 failed (test-fixture gap) -> fixed -> 15 passed

ruff check app/core/family_lifecycle.py app/services/family_lifecycle.py \
  app/api/routes/family.py app/models/family.py app/schemas/family.py app/core/config.py
mypy app/core/family_lifecycle.py app/services/family_lifecycle.py \
  app/api/routes/family.py app/models/family.py app/schemas/family.py
# -> found + fixed a real closure bug (§11), then clean

# Migration
alembic upgrade head
alembic downgrade -1   # verified columns dropped via psql \d family_links
alembic upgrade head   # verified columns restored

# New security test matrix
pytest -q tests/test_phase13_family_link_security.py -v          # 22 -> 25 after adding E2E tests
ruff check tests/test_phase13_family_link_security.py
mypy tests/test_phase13_family_link_security.py

# Full regression, repeated concurrency stability check
ENVIRONMENT=test SECRET_KEY=test-secret RQ_SYNC=true pytest -q     # 558 passed, 0 failed
for i in 1..8: pytest -q tests/test_phase13_family_link_security.py -k concurrent
# -> 8/8 clean

# Frontend
npx tsc --noEmit
npm run lint
npm run build
```

---

## 19. Remaining limitations (honestly documented, not hidden)

- **Expiration is lazy, not scheduled.** A `PENDING` request past its `expires_at`
  only actually flips to `EXPIRED` the next time the link is read or acted on
  (`_expire_if_stale`), matching the pre-existing (pre-Phase-13) design. No background
  job scans for and proactively expires stale requests. This was true before this
  phase and is left as-is per rule 10 ("if automatic expiration is intentionally
  deferred, document that limitation instead of pretending it exists") — no scheduled
  -job infrastructure exists in this codebase to build one without inventing a new
  infrastructure layer outside this phase's scope.
- **Email-based invitation still confirms account existence indirectly** (§16) — a
  201 vs 404 response difference is inherent to any "link by email" flow without an
  invitation-token layer, which does not exist in this codebase. Mitigated via rate
  limiting and uniform error messages, not eliminated.
- **No dedicated abuse-detection layer beyond the one new rate limit** (§15) — this
  codebase has no broader anomaly-detection or account-risk-scoring system to
  integrate with; only the existing `ratelimit` primitive was available to reuse.
- **Creator-based access to a specific record persists across relationship changes**
  (§6) — a documented, pre-existing (Phase 10/12), and intentionally *unchanged*
  authorization dimension, not a Phase 13 gap. Flagged here explicitly so it is never
  mistaken for an oversight in a future audit.
