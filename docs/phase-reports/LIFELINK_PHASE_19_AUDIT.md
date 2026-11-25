# LifeLink — Phase 19 Audit

**Scope of this document:** what the repository actually contains *before* any
Phase 19 code was written, read directly from the source (never assumed),
covering: the blood-request creation flow and state machine, the DB schema
and authorization model, existing idempotency patterns, existing
search/history/analytics functionality per role, the admin notification
architecture, and existing hospital/offline E2E coverage. It closes with the
exact gaps found and the minimal changes proposed to close them.

Baseline going in: **621 backend tests passing**, Phases 0–18 complete and
regression-clean (see `LIFELINK_PHASE_18_REPORT.md`).

---

## 1. Blood-request creation flow (as it existed pre-Phase-19)

`POST /blood-requests` (`app/api/routes/blood_requests.py::create_request`):

1. Authenticates the caller (PATIENT or FAMILY only — donors/hospitals/admins
   get 403).
2. Resolves the beneficiary: a PATIENT files for themselves; a FAMILY caller
   may pass `patient_email` to file on behalf of a linked, ACCEPTED patient
   (`resolve_beneficiary`) — this already enforces the family-link
   authorization boundary.
3. Builds a `BloodRequest` row (status `PENDING`), commits, then:
   - runs the real matching engine to find compatible donors and notifies
     them (`notification_service.notify_matching_donors`),
   - writes an audit log entry,
   - appends a `BloodRequestEvent` (`CREATED`).
4. Returns `201` with the created request.

**Before Phase 19, there was no idempotency mechanism at all** on this
endpoint: two identical POSTs (a double-click, a client retry after a lost
response) created two separate rows, two separate donor-notification waves,
and two separate audit events. This is the primary gap Phase 19 closes —
see `LIFELINK_PHASE_19_IDEMPOTENCY_AUDIT.md` for the full design.

### State machine

`app/core/request_lifecycle.py` already centralizes legal transitions
(`PENDING → MATCHING/CANCELLED/EXPIRED`, `MATCHING → FULFILLED/CANCELLED`,
etc.) in the same pure, DB-free, single-source-of-truth style as
`emergency_lifecycle.py` and (as of Phase 13) `family_lifecycle.py`. Nothing
here needed to change for Phase 19 — request creation is the one transition
(`(none) → PENDING`) that lifecycle module doesn't govern, and idempotency
only concerns what happens *before* a row exists.

## 2. DB schema (blood-request-relevant, pre-Phase-19)

`blood_requests` (`app/models/blood_request.py`): `id`, `requester_user_id`,
`patient_user_id`, `blood_type`, `units_needed`, `urgency`, `status`,
`hospital_name`, `city`, `note`, `needed_by`, `accepted_count`,
`created_at`/`updated_at`. No column existed for a client-supplied
idempotency token. `blood_request_events` (append-only timeline, already
real) exists separately and was not modified.

## 3. Authorization model (pre-Phase-19, verified by reading each route)

- **Patient**: full CRUD-equivalent on their own requests only (`patient_id`
  scoping via `_get_owned_or_404`-style helpers throughout
  `blood_requests.py`).
- **Family**: acts only on behalf of a patient with an ACCEPTED
  `FamilyLink` — enforced at creation (`resolve_beneficiary`) and at
  every subsequent action.
- **Donor**: read-only visibility into requests they're a compatible
  candidate for (via `/blood-requests/{id}/respond` and matching
  endpoints) — cannot create requests (403, now covered by
  `test_phase19_blood_request_idempotency.py::test_donor_role_cannot_create_even_with_key`).
- **Hospital**: scoped to requests in its own city/facility via
  `_get_visible_request()` (`app/api/routes/hospitals.py`) — already
  returns a real 404 (not a leaked 403) for an out-of-scope request, and
  already calls `audit_service.record(db, "hospital.request_access_denied",
  ...)` on a genuine cross-tenant attempt. This existed before Phase 19;
  it had no dedicated test proving the property, which Phase 19 adds
  (`test_phase19_security.py`).
- **Admin**: full read access via `/admin/blood-requests`,
  `/admin/emergencies`, `/admin/*` generally, gated by a role check
  (`_admin` dependency) — already had zero cross-role leakage in the
  routes read, but (like hospital scoping) had thin dedicated test
  coverage for authorization specifically, which Phase 19 also adds.

## 4. Existing idempotency patterns (the emergency precedent)

`Emergency` (Phase 12) already has a proven, working idempotency
mechanism: a nullable `idempotency_key` column plus
`UniqueConstraint("created_by_user_id", "idempotency_key")` (Postgres
treats every `NULL` as distinct, so existing keyless rows are unaffected).
`create_emergency` does a fast-path lookup by `(actor, key)` before any
validation and returns the existing row with `200` (not `201`) on a
replay; the actual insert is wrapped in `db.begin_nested()` (a SAVEPOINT)
so a genuine concurrent collision raises `IntegrityError`, which is
caught, rolled back to the savepoint, and re-resolved by re-fetching the
existing row.

**One property this precedent does *not* have: payload-mismatch
detection.** A replay with the same key but different request content is
served the *original* payload's result silently. Phase 19's spec
explicitly and repeatedly requires this for blood requests ("This is
critical"), so the blood-request implementation is a deliberate,
documented extension of this pattern, not a straight copy — see
`LIFELINK_PHASE_19_IDEMPOTENCY_AUDIT.md` section 3.

## 5. Search / history / analytics — existing functionality per role (audited before writing anything new)

This was the single biggest finding of the audit: **most of what section
1 of the Phase 19 prompt asks for already existed, was already real, and
was already reasonably safe.** Concretely, read directly from source:

| Surface | Pre-existing state |
|---|---|
| `GET /admin/users` | Already real: `query` (ilike on name/email/id), `role` filter, bounded `page`/`page_size` (`ge=1`/`le=100`), real `COUNT` + pagination. |
| `GET /admin/certificates` | Same pattern: ilike search over serial/verify_code/donor name+email/hospital_name, bounded pagination. |
| `GET /admin/analytics/overview` | Already fully real — every number is a live, freshly-computed DB aggregate (`_count_by()` grouping real columns) with an explicit pre-existing code comment to that effect; a bounded `days` param (`ge=1`, `le=90`) drives a real per-day time series. **Had zero authorization test** before Phase 19 (see section 7). |
| `GET /admin/blood-requests`, `/admin/emergencies` | Bounded `limit` (1–200) + status filter, but **no free-text search** — the one genuine, small gap found (see section 8). |
| `GET /admin/admin/verifications` (`verifications.py`) | Same bounded-`limit`/status-filter-but-no-search gap. |
| Patient search (donors/blood requests/hospitals) | `GET /blood-requests` (own), `GET /donors/search` or equivalent matching endpoints, all pre-existing and role-scoped from earlier phases; nothing missing that the current product design calls for. |
| Donor: donation/appointment history | `GET /blood-requests/{id}/respond`-adjacent read endpoints and donor profile history already existed from earlier phases. |
| Hospital: inventory/donor directory/requests/routes | `GET /hospitals/me/inventory`, `GET /hospitals/me/requests`, `GET /hospitals/me/routes` all pre-existing, real, and scoped to `/me` only (no id-addressable cross-hospital surface exists at all — confirmed by grep, and now explicitly tested in `test_phase19_security.py::test_hospital_inventory_is_scoped_to_me_only_no_id_parameter_exists`). |
| History/timeline | `BloodRequestEvent` (append-only, real, pre-existing) already gives patients/hospitals a real creation → state-change → matching → assignment → cancellation/resolution timeline with timestamps and responsible-role attribution, exposed via `GET /blood-requests/{id}` (detail, includes events) and `GET /hospitals/me/requests/{id}/events`. |

**Conclusion:** per the spec's own instruction ("implement ONLY missing
functionality," "do not create parallel implementations," "avoid
unnecessary new abstractions"), Phase 19 does **not** rebuild any of the
above. It implements exactly the one gap found: free-text search on the
three admin list endpoints that had bounded pagination/filtering but no
`query` parameter, using the *exact same* `ilike`-search convention
already proven on `/admin/users` and `/admin/certificates` (see section
8).

## 6. Admin notification architecture (pre-Phase-19)

- A real, working, non-fake notification system already existed (Phase 17/18):
  `NotificationService` → in-app (real, active) + email (real, active) +
  SMS (deliberately dormant provider class — never touched).
- `NotificationsBell.tsx` — a shared, self-contained, role-agnostic React
  component (unread count, dropdown list, mark-read/mark-all-read,
  loading/error/empty states with Retry) — was already used by every
  **non-admin** dashboard (`PatientDashboard`, `DonorDashboard`,
  `HospitalDashboard`, `FamilyDashboard`).
- **The Admin console had none of this outside its own Overview page.**
  `AdminOverview.tsx` has its own hand-rolled inline "Your Notifications"
  panel (wired to the same `GET /notifications` backend, just a different
  frontend widget). The other 7 admin sub-pages
  (`UserManagement`, `AdminFacilities`, `AdminDonors`, `TransportRoutes`,
  `AdminAuditFeed`, `AdminAnalytics`, `VerificationsQueue`) had **no
  notification surface of any kind** — confirmed by grep (zero matches for
  `NotificationsBell` or "Your Notifications" outside `AdminOverview.tsx`)
  and by `login.spec.ts`'s existing Phase 18 test, which explicitly
  documents "admin has no shared NotificationsBell in the header." This is
  the Phase 18 limitation section 1 of the Phase 19 prompt lists as
  **MUST FIX**.
- The 7 admin pages' header JSX was confirmed, via `diff`, to be
  byte-identical across all of them for the relevant block — an
  opportunity to add the bell once, consistently, without touching the
  (unrelated, duplicated-but-out-of-scope) sidebar layout code those pages
  also share.

## 7. Security gaps found in existing (pre-Phase-19) code, by reading, not guessing

- Hospital-vs-hospital hard IDOR boundary (`_get_visible_request` returning
  404 out-of-scope) was real and correct, but had **no dedicated test**.
- `/admin/analytics/overview` role gate (`_admin` dependency) was real and
  correct, but had **no dedicated test** — a real gap given analytics is
  exactly the kind of endpoint that's easy to accidentally leave
  under-protected.
- No endpoint in the codebase exposes a user-controlled `sort` parameter,
  so there is nothing to write an "invalid sort field" test against (noted
  as a real absence, not an oversight — see the Final Report's Remaining
  Limitations).

## 8. Exact gaps identified and the minimal changes proposed

1. **Blood-request creation has no idempotency.** *(Primary objective —*
   *full design in `LIFELINK_PHASE_19_IDEMPOTENCY_AUDIT.md`.)* Proposed:
   mirror the proven Phase 12 Emergency pattern — nullable
   `idempotency_key` + `UniqueConstraint(requester_user_id, idempotency_key)`
   + savepoint-guarded insert — with one deliberate extension (a SHA-256
   payload-hash column) to add the payload-mismatch → 409 behavior the
   emergency precedent lacks but this spec explicitly requires. **Requires
   an Alembic migration** (two new nullable columns + one unique
   constraint on `blood_requests`).
2. **Admin sub-pages beyond Overview have no notification surface.**
   Proposed: drop the existing, unmodified `NotificationsBell` into the
   7 admin pages that lack it, using the identical byte-for-byte header
   slot `AdminOverview` doesn't use for it (Overview keeps its own inline
   panel — not replaced, since the spec didn't ask for that and it already
   works). No backend changes needed — this is purely wiring an existing
   component to existing data on pages that weren't yet using it. New:
   `resolveNotificationPath()` so a `verification.submitted` notification's
   title becomes a real link into `/admin/verifications` (deliberately
   *not* mapping `verification.approved`/`rejected` there — those
   notify the non-admin submitter, who has no access to that route).
3. **Hospital workflow and offline/network-failure scenarios have no E2E
   coverage.** Proposed: two new Playwright spec files exercising the real
   backend/build — hospital login/dashboard/inventory-modal-persistence/
   cross-hospital-IDOR/inventory-failure, and list/notification/
   inventory/form-submission network failures with real `page.route()`
   aborts and real recovery. Auditing the pages these tests exercise
   surfaced two real, pre-existing frontend bugs (below) — fixed before
   the tests that would exercise them were written.
4. **Two real frontend honesty bugs**, found while auditing every
   `useQuery` on the hospital dashboard in preparation for the
   offline-failure E2E tests (not hypothetical — confirmed by reading the
   JSX): `HospitalInventoryModal.tsx` and `HospitalDashboard.tsx`'s
   inventory-summary and request-kanban panels checked `isLoading` but
   never `isError`, so a genuinely failed fetch fell through to the empty-
   data render — visually identical to "this hospital has zero
   units/zero requests," which is exactly the kind of fake-honesty gap
   the spec's anti-fake-functionality rule targets. Fixed with the same
   `isError` + Retry-button convention already used by
   `NotificationsBell`/`NotificationsCenter`.
5. **Three admin list endpoints have bounded pagination but no free-text
   search** (`/admin/blood-requests`, `/admin/emergencies`,
   `/admin/admin/verifications`), unlike their sibling `/admin/users` and
   `/admin/certificates`. Proposed: add the identical `ilike`-based
   `query` parameter, same bound (`max_length=200`), no response-shape
   change (non-breaking for existing frontend consumers).

## 9. Migration requirement

Yes — one new Alembic revision is required (idempotency columns +
constraint on `blood_requests`). No other Phase 19 change touches the
schema. See `LIFELINK_PHASE_19_IDEMPOTENCY_AUDIT.md` section 4 for the
verified upgrade/downgrade/upgrade proof against real Postgres.

## 10. Risks identified going in

- **Concurrency correctness** of the idempotency insert path is the
  highest-risk piece — mitigated by copying the exact, already-proven
  Emergency savepoint pattern rather than inventing a new one, and by
  writing a real two-thread `threading.Barrier` concurrency test rather
  than trusting sequential-only coverage.
- **Cross-suite E2E fixture collisions**: the five seeded E2E users are
  shared, persistent, real accounts reused across every spec file and
  every historical run of this suite (not reset per-run). Several
  *existing* Phase 18 spec files turned out to be sensitive to this
  (accumulated notification rows, an already-ACCEPTED family link from an
  earlier run, and Redis-backed rate-limit counters exhausted by the
  sheer number of manual reruns during this phase's own development) —
  addressed by adding idempotent cleanup steps to the affected tests and
  scoping brittle exact-text assertions to `.first()` where multiple
  genuine past rows can legitimately exist. None of this reflects an
  application bug; it's the E2E suite's own repeatability against a
  long-lived shared dev database, documented in full in the Final
  Report's Remaining Limitations.
- **Scope discipline**: given the enormous breadth of the full spec, the
  explicit risk was over-building (parallel search/analytics subsystems
  the product doesn't need) rather than under-building — mitigated by
  doing this audit first and confirming, by reading, that only one real
  search gap existed.
