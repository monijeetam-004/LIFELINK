# LifeLink Phase 31 — Domain Correctness Report

Companion to `docs/LIFELINK_PHASE_31_DOMAIN_AUDIT.md` (full narrative/evidence), `docs/LIFELINK_PHASE_31_DOMAIN_MODEL.md` (entity inventory), `docs/LIFELINK_INVARIANTS.md`, `docs/LIFELINK_PRIVACY_ACCESS_MATRIX.md`, and `docs/LIFELINK_THREAT_MODEL.md`. Structured per the master prompt's Section 40, in order.

## 1. Repository inspected

`backend/app/models/*.py` (all 21 model files, read directly or via targeted field-level grep this phase); `backend/app/api/routes/blood_requests.py`, `emergencies.py`, `verifications.py`, `certificates.py`, `admin.py`, `patients.py` (read in full or in large targeted sections); `backend/app/core/request_lifecycle.py`, `emergency_lifecycle.py`, `eligibility.py`, `blood_compatibility.py`; `backend/app/services/blood_request_lifecycle.py`, `emergency_lifecycle.py`, `notification_service.py`, `inventory_service.py`, `domain_service.py`; `backend/app/schemas/emergency.py`, `certificate.py`, `admin.py`; `backend/alembic/versions/` (existing migration history, for naming/style precedent); `docs/DATABASE_MAP.md` (found stale, not trusted); prior phase reports `LIFELINK_PHASE_24_*` through `LIFELINK_PHASE_30_*`. Seven parallel investigation agents additionally covered the matching engine, graph/research pages, frontend domain-truth, access control/IDOR, and notification/verification/certificate lifecycles — their findings were spot-checked, not taken on faith, per the methodology section of the audit doc.

## 2. Domain gaps discovered

**CRITICAL**: none. No finding this phase involved data corruption, a security bypass with real exploitability, or a false clinical claim already in production.

**HIGH**:
- Donor could hold simultaneous ACCEPTED commitments across open requests/emergencies (Section 11 of the audit).
- Emergency accept was missing the eligibility gate blood-request accept already enforced (Section 6).
- Verification submission had a genuine concurrency race allowing duplicate PENDING requests (Section 19/23).

**MEDIUM**:
- Emergency deadlines had no operational consequence (Section 9).
- Certificates could never be revoked once issued (Section 20).
- `notify_matching_donors` alerted donors already known to be ineligible (Section 6).

**LOW / documented limitations** (8 total — see the audit doc's summary table): no per-unit blood lifecycle, no blood component model, inventory-allocate doesn't redundantly re-check compatibility, no `PARTIALLY_FULFILLED` status, donation logging is honor-system, `TransportRoute` lacks a richer dispatch lifecycle, no audit-log hash chain, no verification document versioning across resubmission.

## 3. Logic implemented — exact behavior

1. **Emergency accept eligibility gate**: `respond_to_emergency`'s donor-accept branch now calls `is_eligible(dp.last_donation_date)` and raises HTTP 400 with the computed `eligible_on` date if false — identical behavior to `respond_to_request`.
2. **Notify-time eligibility filter**: `notify_matching_donors` skips any donor for whom `is_eligible(dp.last_donation_date)` is false, before the existing urgency/radius filters.
3. **Emergency deadline consequence**: a new `emergency_lifecycle.flag_deadline_exceeded_if_needed(db, em)` function sets `Emergency.deadline_exceeded_at` exactly once (lazy, on read or mutating action, mirroring `expire_if_stale`'s no-scheduler-exists pattern), writes an `EmergencyEvent` (`event_type="deadline_exceeded"`), an `AuditLog` entry (`action="emergency.deadline_exceeded"`), and a best-effort notification to the emergency's creator. Never changes `status`.
4. **Donor double-booking guard**: `app.services.donor_commitment.find_conflicting_commitment(db, donor_user_id, exclude_request_id=..., exclude_emergency_id=...)` checks both `DonorResponse` and `EmergencyResponse` for any other ACCEPTED row on a still-open request/emergency belonging to the same donor, called from both accept paths; raises HTTP 409 with a human-readable conflict description. Excludes HOSPITAL responders on emergencies by design.
5. **Verification submission race fix**: a partial unique index enforces "at most one PENDING `VerificationRequest` per `(user_id, kind)`" at the database level; the insert is wrapped in a SAVEPOINT so a losing concurrent request gets the same honest 409 the pre-existing pre-flight check already gave in the non-concurrent case, instead of an unhandled `IntegrityError`.
6. **Certificate revocation**: new admin-only `POST /admin/certificates/{id}/revoke` sets `revoked_at`/`revoked_by_user_id`/`revocation_reason`, writes an `AuditLog` entry, and never deletes the row. `GET /certificates/verify/{code}` now returns `valid: cert.revoked_at is None` plus the revocation details, instead of always reporting `valid: true` for any existing row.

## 4. Database changes — every migration

| Revision | File | Change | Verified |
|---|---|---|---|
| `d5e6f7a8b9c0` | `d5e6f7a8b9c0_phase31_emergency_deadline_exceeded.py` | Add nullable `emergencies.deadline_exceeded_at` (timestamptz) | upgrade→downgrade→upgrade against real dev DB, clean |
| `e6f7a8b9c0d1` | `e6f7a8b9c0d1_phase31_verification_one_pending_index.py` | Add partial unique index `uq_verification_one_pending_per_kind` on `verification_requests(user_id, kind) WHERE status='PENDING'` | upgrade→downgrade→upgrade against real dev DB, clean |
| `f7a8b9c0d1e2` | `f7a8b9c0d1e2_phase31_certificate_revocation.py` | Add nullable `certificates.revoked_at`, `revoked_by_user_id` (FK to `users`, `ON DELETE SET NULL`), `revocation_reason` | upgrade→downgrade→upgrade against real dev DB, clean |

All three are purely additive (new nullable columns or a new index) — no destructive migration, no data loss, no backfill required (every existing row's new columns default to `NULL`, which is the correct "never happened" state for each).

## 5. Backend changes — exact files/modules

New files: `app/services/donor_commitment.py`, three Alembic migrations above, `tests/test_phase31_domain_fixes.py` (21 tests).

Modified: `app/models/emergency.py` (+`deadline_exceeded_at`), `app/models/verification.py` (+partial unique index), `app/models/certificate.py` (+3 revocation columns), `app/schemas/emergency.py` (+`deadline_exceeded_at` on `EmergencyOut`), `app/schemas/certificate.py` (+`revoked_at`/`revocation_reason` on `CertificateOut`/`CertificateVerifyOut`, +new `CertificateRevokeIn`), `app/schemas/admin.py` (+`revoked_at`/`revocation_reason` on `AdminCertificateOut`), `app/api/routes/blood_requests.py` (+double-booking guard), `app/api/routes/emergencies.py` (+eligibility gate, +double-booking guard, +deadline-flag wiring into 7 call sites: `get_emergency`, list-mine, list-active-for-responder, `_load_owned` (cancel/resolve), `assistance_found`, `acknowledge_emergency`, `respond_to_emergency`), `app/api/routes/verifications.py` (+SAVEPOINT-wrapped insert), `app/api/routes/admin.py` (+`revoke_certificate` endpoint, +revocation fields on the registry list), `app/api/routes/certificates.py` (+revocation fields on donor-facing/public views), `app/services/emergency_lifecycle.py` (+`flag_deadline_exceeded_if_needed`), `app/services/notification_service.py` (+eligibility filter in `notify_matching_donors`). Two pre-existing tests (`tests/test_certificates.py`, `tests/test_phase15_certificate_security.py`) had their exact-response-shape assertions updated to include the two new, intentionally-public `CertificateVerifyOut` fields — not weakened, extended to match a deliberate schema addition.

## 6. Frontend changes — exact pages/components

None this phase. The new backend fields (`deadline_exceeded_at`, certificate `revoked_at`/`revocation_reason`) are additive API surface not yet consumed by any frontend component — flagged as the natural next step (see Section 13).

**Frontend integration: COMPLETE.** Phase 31.1 (`docs/LIFELINK_PHASE_31_1_REPORT.md`) surfaced both fields across every portal that shows the underlying resource: an independent "Past deadline — action needed" notice in `EmergencyTrackDrawer` (shared by the patient/family/donor/hospital emergency-tracking UI), and a Valid/Revoked distinction plus an admin revoke action across the public certificate-verification page, the donor's own certificate list and detail view, and the admin certificate registry. No backend logic changed in that follow-up phase.

## 7. Tests before/after

**Backend**: 853 passed, 0 failed, 0 errors, 0 skipped (`ENVIRONMENT=test python -m pytest`, real local PostgreSQL `lifelink_test` + Redis db 15) — up from Phase 30's baseline of 833 collected (832 passed, 1 skipped). The 1 previously-skipped test now passes as part of the same run (unrelated to this phase's changes — a pre-existing conditional skip whose condition no longer applies in this environment). 21 new tests added this phase, all in `tests/test_phase31_domain_fixes.py`.

**Migration**: all three new revisions verified upgrade→downgrade→upgrade against the real dev database (`lifelink`), clean each time, per Section 4 above.

**Ruff**: `ruff check app/ tests/ alembic/` — 85 pre-existing findings, all in files this phase did not touch (mostly line-length in older test files); zero findings in any file this phase created or modified.

**Mypy**: `mypy app/` — 3 pre-existing errors, all in `app/api/routes/verifications.py` (lines unrelated to this phase's edits — a pre-existing `HospitalProfile`/`DonorProfile` type mismatch) and `app/api/routes/donors.py` (a pre-existing `str | None` narrowing issue); zero new errors from this phase's changes, confirmed by running mypy on the exact set of touched files both before and after the full-app run.

**TypeScript/ESLint/frontend build**: not run this phase — no frontend files were changed (see Section 6).

**Playwright**: not run this phase — no frontend behavior changed for an E2E browser test to exercise differently; the backend-level equivalent (full pytest suite exercising every changed route via real HTTP requests against real PostgreSQL) was run instead and is the actual verification for this phase's changes.

**Accessibility**: unchanged from Phase 27's baseline — no frontend/markup was touched this phase, so there is nothing new to regress.

**Performance**: not re-benchmarked this phase — all six fixes add either a single indexed lookup (`donor_commitment`, two extra `SELECT`s per accept, each hitting an existing index) or a lazy check that only ever writes when it actually has something to flag (`flag_deadline_exceeded_if_needed`, same cost profile as the pre-existing `expire_if_stale`) — no new N+1 pattern or unindexed scan was introduced, and no code path this phase touched was previously identified as a Phase 24/25/27 performance hotspot.

## 8. Concurrency verification — each scenario + actual result

| Scenario | Test | Result |
|---|---|---|
| Two concurrent verification submissions of the same kind for the same user | `test_concurrent_duplicate_verification_submissions_only_one_succeeds` (real `threading.Barrier(2)`, real Postgres) | Exactly one 201, one 409; exactly one PENDING row ever exists |
| Donor accepts a second open blood request while already committed | `test_donor_cannot_accept_two_open_blood_requests_at_once` | Second accept → 409 |
| Donor accepts an open emergency while holding a blood-request commitment | `test_donor_cannot_accept_open_emergency_while_holding_a_blood_request_commitment` | 409 |
| Donor accepts two open emergencies | `test_donor_cannot_accept_two_open_emergencies_at_once` | Second accept → 409 |
| Hospital accepts two open emergencies (should be allowed) | `test_hospital_responder_is_not_subject_to_the_double_booking_guard` | Both succeed (200) |
| Guard releases once the first commitment closes | `test_double_booking_guard_frees_up_after_the_first_commitment_closes` | Second request succeeds after the first is cancelled |
| Same donor re-accepts the same request (idempotent replay) | Assertion inside `test_donor_cannot_accept_two_open_blood_requests_at_once` | 200, never blocked |
| Emergency-deadline flag under repeated reads | `test_deadline_exceeded_flag_is_set_exactly_once` | Exactly one `AuditLog` row, one notification, across two reads |

All concurrency tests use real threads against the real local PostgreSQL test database — never a `time.sleep`-based approximation, per Section 23's explicit requirement.

## 9. Security verification — actual tests listed

`test_only_admin_can_revoke_a_certificate` (non-admin gets 404 on the wrong route, 403 on the real admin route), `test_revoke_requires_a_real_reason` (422 below the 3-character minimum), `test_revoking_an_unknown_certificate_404s`. Pre-existing IDOR/authorization suites (`test_phase14_verification_security.py`, `test_phase15_certificate_security.py`, `test_phase13_family_link_security.py`) re-run and confirmed unaffected by this phase's schema additions. Full IDOR probe list and results: `docs/LIFELINK_PRIVACY_ACCESS_MATRIX.md`. Full threat-class coverage: `docs/LIFELINK_THREAT_MODEL.md`.

## 10. Mathematical/research verification

No mathematical or research-mode claim was changed this phase. Re-verified (not re-implemented): the matching engine's staged pipeline, tuple-based deterministic ranking (no fabricated weighted score), and the graph engine's honest `GRAPH`/`HAVERSINE_FALLBACK` provenance labeling — all found already correct, per the audit doc's Sections 7, 15, and 24-27.

## 11. External-service verification

Carried forward unchanged from Phase 28/30 (no external-service code was touched this phase):

| Service | Classification |
|---|---|
| PostgreSQL (local dev/test) | LOCAL-VERIFIED — every test and migration this phase ran against it directly |
| Redis (local) | LOCAL-VERIFIED — used by rate limiting and the RQ-adjacent test suite, unchanged |
| Neo4j Aura | NOT-LIVE-VERIFIED — standing condition since Phase 28, this sandbox has no reachable instance |
| SMTP / Gmail | NOT-LIVE-VERIFIED for real delivery; LOCAL-VERIFIED for the inline/local-dev delivery path used by tests |
| S3 / MinIO | NOT-LIVE-VERIFIED; LOCAL-VERIFIED for the local-disk storage backend used by tests |
| GitHub Actions CI | CI-VERIFIED for the existing Phase 29 pipeline, unchanged — this phase added no new CI job, and none was needed (no new external dependency, no new deployment surface) |
| Staging | UNCHANGED FROM PHASE 29 — not redeployed this phase |

## 12. Remaining limitations

Eight documented, justified limitations (full reasoning in the audit doc): no per-unit blood lifecycle; no blood component model (explicitly withheld per Section 35's medical-safety instruction); inventory-allocate doesn't redundantly re-check compatibility (currently unreachable in practice, not unsafe today); no `PARTIALLY_FULFILLED` status; donation logging remains donor-self-attested (certificate revocation is the actual remedy, not a preventive fix); `TransportRoute` has a binary rather than richer dispatch lifecycle; no audit-log hash chain (no real threat model currently justifies one); no verification document versioning across resubmission. None of these are hidden — each is written up in `docs/LIFELINK_PHASE_31_DOMAIN_MODEL.md` and/or the audit doc at the exact entity/section it concerns, not only in this summary.

The two new nullable fields this phase's fixes introduced (`deadline_exceeded_at`, certificate `revoked_at`/`revocation_reason`) were not yet surfaced in any frontend component as of this phase — **since resolved by Phase 31.1** (`docs/LIFELINK_PHASE_31_1_REPORT.md`), which added the frontend UI for both without touching any backend logic described above.

## 13. Recommended next phase

~~A frontend-integration phase to surface this phase's two new backend signals~~ — **done, see Phase 31.1.** The next open item is Phase 31.1's own recommendation: introducing a component-level frontend test framework (none exists in this codebase today; Phase 31.1's frontend coverage is Playwright E2E only).
