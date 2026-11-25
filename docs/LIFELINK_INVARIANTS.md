# LifeLink — Formal Invariants (Phase 31, extended through Phase L and production hardening)

Per the master prompt's Section 22: only invariants that actually apply to the implemented domain, each with where it is enforced and how it was verified. "Enforced in application code only" is called out honestly where that's the true state — an invariant is not upgraded to "guaranteed" just because a test currently passes.

## 1. Compatibility

**Invariant**: A `DonorResponse`/`EmergencyResponse` ACCEPT by a donor is only permitted when the donor's `blood_type` is compatible with the request/emergency's `blood_type` under `app.core.blood_compatibility.can_donate_to` (standard ABO/Rh donor→recipient rules, O_NEG universal donor, AB_POS universal recipient).

**Enforced**: Application-level check in `blood_requests.respond_to_request` / `emergencies.respond_to_emergency`, immediately before any write. **Not** enforced by a DB constraint (blood type compatibility is not expressible as a simple CHECK across two rows without a trigger, and no trigger exists). **Gap**: a direct DB write (migration, admin SQL, a future buggy code path) could create an incompatible `ACCEPTED` row without this check running. Documented as a valid limitation — closing it would require a trigger or a materialized compatibility table, which was not judged worth the complexity for a decision-support system with no direct DB-write surface exposed to any user role.

**Verified**: `tests/test_blood_compatibility_matrix_phase9.py` (64-combination matrix), `tests/test_donor_matching.py`.

## 2. Inventory non-negative

**Invariant**: `HospitalInventory.units >= 0` and `HospitalInventory.reserved_units >= 0` always, and `reserved_units <= units` (a hospital cannot have more reserved than it holds).

**Enforced**: DB-level CHECK constraints (verified by direct inspection of the `HospitalInventory` table definition and `inventory_service.py`'s mutation functions, which raise before ever emitting an UPDATE that would violate them) *and* application-level validation in `inventory_service.py` (belt-and-suspenders — the CHECK is the real guarantee; the application check gives an honest 400/409 instead of an unhandled `IntegrityError`).

**Verified**: `tests/test_concurrency.py` (concurrent reservation attempts against the same row, real PostgreSQL, real threads — not a unit-test mock).

## 3. Reservation exclusivity (no double-allocation)

**Invariant**: The same inventory cannot be reserved twice for the same demand, and a reservation cannot be allocated past what was actually reserved.

**Enforced**: `inventory_service.py` locks the `HospitalInventory` row (`SELECT ... FOR UPDATE`) before any reserve/allocate/release mutation, serializing concurrent callers. `HospitalInventoryLog`'s `resulting_units`/`resulting_reserved` snapshot on every row makes any violation independently detectable after the fact (an auditor can replay the log and confirm monotonic consistency without trusting the current row alone).

**Verified**: `tests/test_concurrency.py`, real PostgreSQL, real threads with `threading.Barrier`.

## 4. Idempotency

**Invariant**: Retrying the same client-supplied `idempotency_key` for blood request creation, donation logging, or emergency creation returns the original result rather than creating a duplicate.

**Enforced**: DB-level unique constraints — `uq_blood_request_idempotency_key`, `uq_donation_idempotency_key`, `uq_emergency_idempotency_key` (all scoped per-actor, not global, so two different users can't collide on the same client-generated key by coincidence) — paired with a payload-hash column so a genuinely different request replayed under an accidentally-reused key is rejected rather than silently returning the wrong prior result.

**Extended this phase**: `uq_verification_one_pending_per_kind` (partial unique index) closes the equivalent "at most one open X per (actor, kind)" invariant for verification submissions, which previously only had an application-level pre-flight check (a real TOCTOU race — see the Phase 31 audit's concurrency section and the fix's own migration/tests).

**Verified**: `tests/test_phase19_blood_request_idempotency.py`, `tests/test_phase26_donation_idempotency.py`, `tests/test_phase31_domain_fixes.py::test_concurrent_duplicate_verification_submissions_only_one_succeeds` (real threads, real Postgres, proves the race is closed, not merely that the happy path works).

## 5. State transitions

**Invariant**: `BloodRequest.status`, `Emergency.status`, and `VerificationRequest.status`/`Certificate` trust state only ever move along their declared transition tables; an illegal transition (e.g. `RESOLVED → ACTIVE`) is always rejected.

**Enforced**: Pure transition-table modules (`app.core.request_lifecycle`, `app.core.emergency_lifecycle`) validated on every write via their paired DB-touching service module (`app.services.blood_request_lifecycle.transition`, `app.services.emergency_lifecycle.transition`), which raise `InvalidTransitionError` → HTTP 409 rather than silently applying an illegal move. Row-locked (`get_for_update`) before validation, so two concurrent transition attempts on the same row serialize rather than both reading stale state and both "succeeding."

**Verified**: `tests/test_blood_request_lifecycle_phase10.py`, `tests/test_phase12_emergency_sos_engine.py` (including the concurrent-double-transition tests using `threading.Barrier`).

## 6. Authorization

**Invariant**: A user can only read/modify resources their role and relationship (owner, linked family, assigned hospital, admin) entitle them to; there is no resource whose access control lives only in the frontend.

**Enforced**: Every route checks authorization server-side via `require_roles`, `_can_view`/`_can_manage`-style per-resource helper functions, or explicit ownership comparisons — verified directly in `blood_requests.py`, `emergencies.py`, `certificates.py`, `verifications.py`, `family.py` (no route was found during this phase's investigation that trusted a client-supplied "I'm allowed" flag).

**Verified**: See `docs/LIFELINK_PRIVACY_ACCESS_MATRIX.md` for the full ROLE × RESOURCE × ACTION matrix and the specific IDOR probes run against it.

## 7. Family links

**Invariant**: A FAMILY user can only act on behalf of a PATIENT they hold an `ACCEPTED` (not PENDING/REJECTED/REVOKED/EXPIRED) `FamilyLink` with; at most one active (PENDING or ACCEPTED) link exists per `(family_user_id, patient_user_id)` pair.

**Enforced**: `has_accepted_link` / `resolve_beneficiary` (`app/services/domain_service.py`) check `FamilyLinkStatus.ACCEPTED` explicitly before granting any on-behalf-of action; duplicate-link prevention is an application-level pre-flight check in `family.py` (not a DB constraint — a documented, minor gap: like the verification race this phase fixed, this is theoretically racy, but was not prioritized for a DB-level fix this phase since a duplicate link causes no data-integrity damage, only a confusing UI state that the family/patient can resolve by rejecting the extra invite).

**Verified**: `tests/test_family.py`, `tests/test_phase13_family_link_security.py`.

## 8. Certificates

**Invariant**: A `Certificate` is 1:1 with a `Donation` (never two certificates for one donation); once revoked, a certificate's public verification always reports its current (non-)trust status rather than reverting to "valid" or disappearing.

**Enforced**: DB-level unique constraint on `Certificate.donation_id`; `certificate_service.get_or_issue` locks the `Donation` row before checking-then-inserting, closing the same class of race the Phase 15 audit already found and fixed for this exact entity. Revocation (new this phase) is a pure additive state (`revoked_at`/`revoked_by_user_id`/`revocation_reason`) that never deletes the row — the factual "this was issued" record is permanent; only its current trust status changes.

**Verified**: `tests/test_phase15_certificate_security.py`, `tests/test_phase31_domain_fixes.py` (revocation + public-verify-reflects-it + only-admin-can-revoke).

## 9. Notifications

**Invariant**: Every domain event that should notify a user produces at least one in-app `Notification` row (best-effort — never guaranteed exactly-once across the optional email/SMS side-channels, and this is documented, not hidden); a donor blocked from accepting on eligibility grounds is not alerted for a request they can't act on.

**Enforced**: `notification_service.notify` / `notify_matching_donors` — the in-app row is created synchronously in the same DB transaction as the triggering event (so it can never be "lost" the way an async-only side channel could be), while email/SMS failures are caught and logged without rolling back the primary write. **New this phase**: `notify_matching_donors` now filters out donors who are not currently `is_eligible` (the same 56-day rule already enforced at accept-time) — previously an ineligible donor was still alerted, creating a false sense of reachable supply.

**Verified**: `tests/test_phase31_domain_fixes.py::test_ineligible_donor_is_not_alerted_for_a_new_matching_request`; delivery-semantics classification in the Phase 31 audit, Section 16.

## 10. Emergency pathways

**Invariant**: At most one `ACTIVE` `EmergencyPathway` per emergency at any time; a pathway that depended on a route which is later `DISRUPTED` does not remain silently `ACTIVE`.

**Enforced**: Partial unique index `uq_emergency_pathway_one_active`; `emergency_pathway_service.handle_route_disrupted` synchronously re-optimizes every affected open emergency's pathway the moment a route is disrupted, superseding the stale `ACTIVE` version rather than leaving it in place.

**Verified**: `tests/test_phase16_graph_engine_security.py`, `tests/test_phase12_emergency_sos_engine.py`.

## 11. Donor single-commitment (new this phase)

**Invariant**: A donor cannot hold two simultaneous `ACCEPTED` commitments across open blood requests and open emergencies at the same time — a physical donor can only actually donate in one place.

**Enforced**: `app.services.donor_commitment.find_conflicting_commitment`, called from both `respond_to_request` and `respond_to_emergency`'s accept path, checking both tables for any other `ACCEPTED` response on a still-open request/emergency belonging to the same donor. Deliberately excludes HOSPITAL responders on emergencies (an institution, not a single body, can legitimately help with several concurrent emergencies). Application-level, not a DB constraint — a genuinely cross-table invariant (spanning `DonorResponse` and `EmergencyResponse`) that PostgreSQL cannot express as a single CHECK or unique index without a trigger; judged acceptable because the check runs inside the same transaction as the accept itself, immediately before the row is written, closing the realistic race window.

**Verified**: `tests/test_phase31_domain_fixes.py` (five tests: same-table blocked, cross-table blocked, hospital exempted, guard releases after the first commitment closes, idempotent replay of the same accept is never blocked).

## 12. Emergency deadlines have a real consequence (new this phase)

**Invariant**: A still-open emergency whose `deadline` has passed is flagged exactly once (`deadline_exceeded_at`, an `EmergencyEvent`, an `AuditLog` entry, and a notification to the creator) — never silently ignored, and never used to auto-change `status` (this system is decision-support only and must not represent a missed deadline as an autonomous clinical/operational decision).

**Enforced**: `emergency_lifecycle.flag_deadline_exceeded_if_needed`, called from every route that fetches an `Emergency` row (read and mutating alike), following the same lazy-on-read pattern `blood_request_lifecycle.expire_if_stale` already established (no scheduler exists anywhere in this codebase).

**Verified**: `tests/test_phase31_domain_fixes.py` (flagged-once, never-flagged-early, status-unchanged, notification-sent, triggered-from-a-mutating-route-too).

## 13. Blood unit state validity (new Phase 34)

**Invariant**: Every `BloodUnit` has exactly one current `state` from the closed set `{AVAILABLE, RESERVED, ALLOCATED, EXPIRED}` at all times; there is no "in between" or compound state.

**Enforced**: DB-level `Enum("blood_unit_state")` column type (Postgres rejects any value outside the four at the driver/DB level, not just in application code) plus `server_default=AVAILABLE` so a row can never exist with a null state.

**Verified**: `tests/test_phase34_blood_unit_lifecycle.py` (per-unit state asserted after every service action across add/reserve/release/allocate/expire/reconcile).

## 14. Blood unit valid transitions only (new Phase 34)

**Invariant**: A `BloodUnit` only ever moves along the declared transition table — `AVAILABLE → {RESERVED, ALLOCATED, EXPIRED}`, `RESERVED → {AVAILABLE, ALLOCATED, EXPIRED}`, and `ALLOCATED`/`EXPIRED` are terminal (no outgoing edges). An illegal move (e.g. `ALLOCATED → AVAILABLE`, or any transition out of `EXPIRED`) is always rejected.

**Enforced**: Pure, DB-free transition-table module `app.core.blood_unit_lifecycle` (`validate_transition`, mirroring the existing `request_lifecycle`/`emergency_lifecycle` pattern), called by `inventory_service._transition_units` before every per-unit mutation; raises `InvalidBloodUnitTransitionError` rather than silently applying an illegal move. No route accepts a client-supplied target state at all — every transition is driven entirely by which service action (reserve/release/allocate/expire) was called, so there is no input surface for a caller to request an illegal transition directly.

**Verified**: `tests/test_phase34_blood_unit_lifecycle.py` (pure transition-table unit tests covering every legal and illegal edge; terminality of `ALLOCATED`/`EXPIRED` explicitly asserted).

## 15. Allocation safety / no double-allocation (new Phase 34)

**Invariant**: Once a `BloodUnit` is `ALLOCATED` it can never be allocated again, reserved, or returned to the available pool — the same physical unit cannot satisfy two demands.

**Enforced**: `ALLOCATED` is terminal in the transition table (invariant 14); `inventory_service._select_units_for_update` only ever selects units currently in `AVAILABLE`/`RESERVED` state under `SELECT ... FOR UPDATE` against the locked `HospitalInventory` row, so two concurrent `allocate_stock` calls serialize rather than both reading the same not-yet-committed unit as available.

**Verified**: `tests/test_phase34_blood_unit_lifecycle.py::test_concurrent_double_allocation_never_allocates_the_same_unit_twice` (real PostgreSQL, real `threading.Barrier`-synchronized threads — not a mock).

## 16. Reservation safety / no double-reservation (new Phase 34)

**Invariant**: The same `BloodUnit` cannot be concurrently reserved by two competing callers, and a unit already `RESERVED` for one request/emergency is not silently reassigned to another.

**Enforced**: Same row-level lock as invariant 15 (`SELECT ... FOR UPDATE` on `HospitalInventory`, the single serialization point for every per-unit mutation under it — no separate per-`BloodUnit` lock is needed because a unit is only ever reachable through its parent inventory row).

**Verified**: `tests/test_phase34_blood_unit_lifecycle.py::test_concurrent_double_reservation_never_reserves_the_same_unit_twice`, `::test_release_versus_allocate_race_never_double_counts_a_unit` (real PostgreSQL, real threads).

## 17. Expiry safety (new Phase 34)

**Invariant**: A `BloodUnit` past its `expires_at` is never reserved or allocated — expiry is checked honestly on every action, not just at read time — and a `LEGACY_BACKFILL` unit (whose true collection date is unknowable) is never auto-expired, since it has no `expires_at` clock to begin with.

**Enforced**: `inventory_service._get_or_create_row` runs `_sweep_expired` (lazy, on-read-and-on-write, mirroring the existing `expire_if_stale`/`flag_deadline_exceeded_if_needed` pattern — no scheduler exists anywhere in this codebase) and commits+re-locks the row *before* the caller's own action runs, so `reserve_stock`/`allocate_stock` always act against a freshly-swept, current set of `AVAILABLE`/`RESERVED` units. `_create_units` only assigns `expires_at` for `REAL` provenance; `_sweep_expired`'s query filters on `expires_at IS NOT NULL AND expires_at <= now()`, so a `LEGACY_BACKFILL` unit (`expires_at = NULL`) can never match and is never swept.

**Verified**: `tests/test_phase34_blood_unit_lifecycle.py` (past-due sweep, boundary-not-yet-expired, reserved-units-can-still-expire, legacy-backfill-never-swept, and `test_reservation_versus_lazy_expiry_race_never_reserves_a_unit_past_its_shelf_life` — the one real production bug this phase found and fixed; see the Phase 34 report's Section 15/"Errors and fixes").

## 18. Availability truth (new Phase 34)

**Invariant**: `HospitalInventory.units`/`reserved_units` (and the derived `available_units = units - reserved_units` the API returns) always honestly reflect the real count of per-unit `BloodUnit` rows in each state for that hospital/blood-type — the aggregate is never allowed to drift ahead of or behind the per-unit ground truth it summarizes.

**Enforced**: `inventory_service._reconcile_missing_units`, run automatically inside `_get_or_create_row` on every fetch of an existing row, detects any shortfall between the aggregate counters and real per-unit counts (the shortfall case this phase actually found: pre-Phase-34 rows, and test fixtures that mutate `HospitalInventory` directly, bypassing the service) and self-heals it by backfilling the missing `BloodUnit` rows as `LEGACY_BACKFILL`, honestly, rather than rejecting the subsequent action with a false "inconsistency" error. Combined with invariant 17's lazy sweep (also run inside `_get_or_create_row`, before the caller's action), every action-path read of the row is reconciled-and-swept first.

**Verified**: `tests/test_phase34_blood_unit_lifecycle.py::test_a_row_seeded_outside_the_service_is_backfilled_honestly_not_rejected`; per-unit-vs-aggregate consistency (`_assert_consistent` helper) asserted after every add/reserve/release/allocate/expire/reconcile test in the file. **Known gap, documented not hidden**: `app/api/routes/admin.py`'s facility inventory views read `HospitalInventory` directly, not through `inventory_service`, so they do not get this reconcile-and-sweep honesty benefit — they can show a stale aggregate until some other route touches that row. No data-integrity risk (the underlying numbers are never wrong in the DB, only potentially stale in that one read path), but noted as a Phase 35 candidate.

## 19. Historical integrity (extended Phase 34)

**Invariant**: Every change to `HospitalInventory`'s aggregate counters — including ones caused by the lazy expiry sweep or self-healing reconciliation, not just direct user-driven actions — is captured by an immutable `HospitalInventoryLog` row with a `resulting_units`/`resulting_reserved` snapshot, so the full history can be replayed and audited independently of trusting the current row.

**Enforced**: `_sweep_expired` writes its own summarizing `HospitalInventoryLog` entry (action describing the sweep, snapshot taken *after* the mutation) before committing; every one of the six action functions (`add_stock`/`reserve_stock`/`release_reservation`/`allocate_stock`/`expire_units`/`reconcile_inventory`) continues to call the pre-existing `_log()` helper exactly as before Phase 34. Each `BloodUnit` row also carries a `last_log_id` FK pointing at the log entry that most recently changed it, reusing the existing audit system rather than introducing a second, parallel history table.

**Verified**: `tests/test_phase34_blood_unit_lifecycle.py` (log entry asserted present after a lazy sweep, independent of any other action in the same test).

## 20. Component isolation (new Phase 35)

**Invariant**: A hospital's inventory for `(blood_type, component)` is tracked completely independently of every other component of that same blood type -- a `HospitalInventory` row, its `BloodUnit` pool, its FEFO selection, and its reservation/allocation state for `RED_BLOOD_CELLS`/`PLASMA`/`PLATELETS` never mix with, or get counted as, `WHOLE_BLOOD` (the pre-existing, only-ever-tracked-before-this-phase component). The pre-existing `GET/PUT /hospitals/me/inventory` contract stays pinned to the `WHOLE_BLOOD` row specifically, byte-identical to before this phase, even once other components exist for the same hospital/blood_type.

**Enforced**: `component` joins the key on `HospitalInventory` (`uq_hospital_inventory_type_component`) and the hot-path index on `BloodUnit` (`ix_blood_units_hospital_type_component_state`); every `inventory_service` function (`add_stock`/`reserve_stock`/`release_reservation`/`allocate_stock`/`expire_units`/`reconcile_inventory`/`sweep_all_expired`) takes `component` as an explicit parameter (defaulting to `WHOLE_BLOOD`) and threads it through every row lock, selection, and log write. `app/api/routes/hospitals.py`'s `_inventory_map` (backing the legacy GET/PUT endpoints) explicitly filters `component == WHOLE_BLOOD` -- without this filter, a hospital with both a `WHOLE_BLOOD` and a `RED_BLOOD_CELLS` row for the same blood_type would collapse two real rows onto one dict key non-deterministically.

**Verified**: `tests/test_phase35_blood_component.py::test_legacy_get_inventory_ignores_non_whole_blood_components`, `::test_components_are_fully_separate_pools`, `::test_reserving_one_component_does_not_touch_another`. **Known, documented limitation**: `app/api/routes/admin.py`'s `_pick_disruption_probe` (route-disruption impact simulation) still picks any component with stock > 0 as its probe scenario -- acceptable since it never displays component-specific numbers to a user, only uses the row as a simulation seed; not revisited this phase.

## 21. Donation traceability integrity (new Phase 35)

**Invariant**: The cumulative `units` of `BloodUnit` rows linking to the same `Donation` (via `add_stock`'s optional `donation_id`) can never exceed that donation's own self-reported `units` -- a single donation event can never be cited to justify fabricating more physical stock than it could possibly have produced, even across multiple, concurrent `add_stock` calls citing the same `donation_id`.

**Enforced**: `inventory_service._assert_donation_can_justify`, called from `add_stock` whenever `donation_id` is supplied, before any mutation -- locks the `Donation` row itself (`SELECT ... FOR UPDATE`) and re-counts already-linked units under that lock. The lock is on the `Donation` row specifically (not a `HospitalInventory` row) because it is the only state actually shared between two `add_stock` calls citing the same donation but different `blood_type`/`component`/`hospital_id` combinations, each of which would otherwise take an entirely independent, non-contending `HospitalInventory` row lock.

**HONESTY BOUNDARY**: this does not verify the donation event itself happened -- `Donation` remains donor-self-attested, exactly as before this phase (see `docs/LIFELINK_PHASE_35_RECON_MATRIX.md`). It only prevents over-claiming against whatever was self-attested.

**Verified**: `tests/test_phase35_blood_component.py::test_add_rejects_linking_more_units_than_donation_reports`, `::test_add_rejects_cumulative_over_claim_across_two_calls`, `::test_add_rejects_nonexistent_donation_id`, and the real-Postgres/real-thread race `::test_concurrent_add_against_same_donation_never_over_claims` -- which, before the `SELECT ... FOR UPDATE` lock was added, was confirmed by direct experiment to let two concurrent `add_stock` calls (different blood types, so no `HospitalInventory`-level contention) both pass the check and jointly over-claim a 2-unit donation into 4 linked units. The regression test deliberately uses two different blood types so it cannot pass by accidentally serializing on the unrelated `HospitalInventory` unique constraint.

## 22. Component-aware compatibility is re-verified, not trusted (new Phase 35)

**Invariant**: `reserve_stock`/`allocate_stock` against a real `request_id`/`emergency_id` re-verify that the blood being moved is actually compatible with that request/emergency's own recorded `blood_type` -- for the *specific component* being moved, not just red-cell ABO/Rh -- independently of whatever matching/routing already happened upstream (the donor-matching/hospital-routing code in `blood_requests.py`/`emergencies.py`). `WHOLE_BLOOD`/`RED_BLOOD_CELLS` continue to use the unchanged, pre-existing red-cell ABO/Rh engine; `PLASMA` uses a separately-sourced, ABO-only table that is the *inverse* of red-cell compatibility (AB is the universal plasma donor, O the universal plasma recipient); `PLATELETS` is never ABO-restricted.

**Enforced**: `inventory_service._assert_context_compatible`, called from `reserve_stock`/`allocate_stock` whenever a `request_id` or `emergency_id` is given, looks up the REAL current `blood_type` from the `BloodRequest`/`Emergency` row itself (there is no client-supplied blood_type parameter for the request/emergency side of this check to begin with) and dispatches through `app.core.component_compatibility.is_component_compatible`, sourced from the UTMB Blood Bank ABO compatibility chart (see that module's docstring for the full citation).

**Verified**: `tests/test_phase35_blood_component.py::test_allocate_rejects_plasma_from_a_donor_incompatible_with_the_request` (an O_NEG donor -- universal for red cells -- is correctly rejected for PLASMA against an A_POS request, via the generic `/hospitals/me/inventory/allocate` action with an explicit `request_id`, which unlike the dedicated `/requests/{id}/allocate` endpoint does not itself enforce an exact blood_type match and so is the actual path this defense-in-depth guard exists to cover) and `::test_whole_blood_and_red_cells_use_the_unchanged_existing_engine` (a genuine ABO/Rh mismatch is still rejected exactly as before this phase).

## 23. Unit in at most one active shipment (new Phase D)

**Invariant**: A `BloodUnit` can belong to at most one shipment whose own lifecycle is still active (`CREATED`/`DISPATCHED`/`IN_TRANSIT`/`ARRIVED` -- anything not yet `RECEIVED`/`CANCELLED`/`FAILED`) at a time. The same physical unit cannot be simultaneously earmarked or physically moving on two different shipments.

**Enforced**: `ShipmentItem.is_active` plus a Postgres **partial unique index** on `(blood_unit_id) WHERE is_active` -- `uq_shipment_items_active_unit` -- declared both in the SQLAlchemy model (`app/models/shipment.py`, so the test suite's `Base.metadata.create_all()` schema gets it too, not only a real `alembic upgrade`) and created directly in the migration, the same pattern already established by `EmergencyPathway.uq_emergency_pathway_one_active` / `VerificationRequest.uq_verification_one_pending_per_kind`. This is DB-level, not just application logic -- a second `ShipmentItem` row for a unit still active on another shipment is rejected by Postgres itself (`IntegrityError`), even against a bug or a future direct-SQL mistake, exactly the same defense-in-depth philosophy as `HospitalInventory`'s non-negative `CHECK` constraints (invariant 2). Application logic never even attempts this today -- `shipment_service`/`app.api.routes.shipments.create_shipment` only ever reserves units that `inventory_service.reserve_stock`'s own row-locked FEFO selection returns as genuinely `AVAILABLE` -- so this index is real defense-in-depth, not the only thing standing between the system and the violation.

**Verified**: `tests/test_phase_d_shipment.py::test_unit_cannot_be_added_to_two_active_shipments_at_db_level` (a direct second `ShipmentItem` insert for a unit still active on its first shipment is confirmed to raise `IntegrityError`); `tests/test_phase_d_concurrency.py::test_two_concurrent_shipment_creations_cannot_both_claim_the_same_units` and `::test_direct_allocation_vs_shipment_creation_cannot_double_commit_stock` (real PostgreSQL, real threads -- the application-level path that would otherwise be the only guard).

## 24. Shipment terminal states are immutable (new Phase D)

**Invariant**: Once a `Shipment` reaches `RECEIVED`, `CANCELLED`, or `FAILED`, nothing about its lifecycle can change again -- no further dispatch/receive/cancel/fail/delay action succeeds, and its `ShipmentItem` rows are permanently deactivated (`is_active = False`), freeing their units for invariant 23's purposes without reopening the shipment itself.

**Enforced**: `app.core.shipment_lifecycle.TRANSITIONS` gives every terminal state an empty outgoing-edge set (`frozenset()`), so `validate_transition`/`shipment_service.transition` rejects any further transition attempt with `InvalidShipmentTransitionError` (-> HTTP 409) -- including a same-state call, the same "no idempotent-retry-returns-200 semantics" rule invariant 14/15's `BloodUnitState` already applies. `report_delay`'s own `can_report_delay` check independently rejects delay annotations once a shipment is terminal (delay is legal only while `DISPATCHED`/`IN_TRANSIT`). Every one of `receive_shipment`/`cancel_shipment`/`fail_shipment` deactivates `ShipmentItem` rows in the same transaction as the terminal transition itself, never a separate cleanup job.

**Verified**: `tests/test_phase_d_shipment.py::test_cannot_dispatch_a_received_shipment`, `::test_cannot_receive_a_cancelled_shipment`, `::test_cannot_receive_a_failed_shipment`, `::test_dispatch_after_cancel_is_rejected`, `::test_cannot_cancel_after_dispatch`, `::test_cannot_report_delay_before_dispatch_or_after_terminal`.

## 25. Received unit is never simultaneously available at both ends (new Phase D)

**Invariant**: A unit that has been `RECEIVED` by its destination is never, at any point, counted as available stock at BOTH the origin and the destination simultaneously -- physical custody of one real unit is held by exactly one facility's countable inventory at a time.

**Enforced**: By construction of the ownership-transfer design, not a runtime check: the ORIGIN-side `BloodUnit` row reaches `ALLOCATED` (terminal, per invariant 15) at **dispatch** time -- the moment it stops being `AVAILABLE`/`RESERVED` and so stops being counted in the origin's `HospitalInventory.units` at all -- which happens strictly before the shipment can ever reach `RECEIVED` (dispatch is a precondition of every state that leads there: `DISPATCHED -> IN_TRANSIT -> ARRIVED -> RECEIVED`). The DESTINATION-side `BloodUnit` row does not exist at all until `inventory_service.receive_transferred_units` creates it, at `RECEIVED` time -- so there is no window in which both a countable origin row and a countable destination row exist for the same physical unit. The two rows are linked for traceability (`source_unit_id`/`source_shipment_id`) but are deliberately two separate, honestly-timestamped rows rather than one row being reassigned a new `hospital_id` -- reassignment would either have to happen too early (destination counts it before it physically arrived) or require a third, ambiguous "in transit, not countable anywhere" representation this design avoids by using `Shipment.status` itself as that in-between state instead of mutating `BloodUnit.hospital_id`.

**Verified**: `tests/test_phase_d_shipment.py::test_full_happy_path_lifecycle_transfers_ownership` (asserts origin `units` decremented at dispatch, origin units stay `ALLOCATED` through to and after receipt, destination units appear only after receipt, and the destination/origin aggregate counts both independently check out); `::test_fail_after_dispatch_does_not_release_units` (confirms the origin side of this invariant holds even when a shipment never reaches `RECEIVED`).

## 26. Every shipment lifecycle transition is auditable (new Phase D)

**Invariant**: Every real state change a `Shipment` goes through -- who did it, what changed (old state -> new state), when, and why -- is recorded durably and is never silently lost, reusing the existing audit architecture rather than a new, parallel mechanism.

**Enforced**: `app.services.shipment_service.transition` writes exactly one `ShipmentEvent` row (the shipment's own user-facing timeline -- `GET /shipments/{id}/timeline`, same shape/convention as `BloodRequestEvent`/`EmergencyEvent`) and exactly one `AuditLog` entry (via the existing `audit_service.record`, the admin-facing trail) in the same transaction as every transition, with `old_status`/`new_status`/`reason`/actor/shipment id in the `AuditLog` `meta`. `report_delay` (an annotation, not a `Shipment.status` transition) independently writes its own `ShipmentEvent`('delayed')/`AuditLog`('shipment.delayed') pair rather than being silently unaudited just because it doesn't go through `transition()`. No second, shipment-specific audit table was created (Section 29/34's explicit instruction) -- chain-of-custody questions (who/what/when/where/which-shipment/which-unit/old-new-state/why) are answered by combining `ShipmentEvent` + `AuditLog` + `BloodUnit`'s own traceability columns (`source_shipment_id`/`source_unit_id`/`reserved_for_shipment_id`), never a fourth table.

**Verified**: `tests/test_phase_d_shipment.py::test_full_happy_path_lifecycle_transfers_ownership` (timeline event-type sequence and strictly non-decreasing, server-written timestamps asserted end to end); `::test_delay_annotation_recorded_and_history_preserved` (two separate delay reports both preserved, never overwritten).

## 27. Receipt transfers a unit's ownership at most once (new Phase D)

**Invariant**: `receive_shipment` -- the one action that performs the real inventory-ownership transfer -- can never apply twice for the same shipment, even under two near-simultaneous calls. A duplicate or racing receipt never creates a second set of destination-side units or double-counts the transfer.

**Enforced**: `shipment_service.get_for_update` locks the `Shipment` row (`SELECT ... FOR UPDATE`) before `transition()` validates and applies `ARRIVED -> RECEIVED`; the second of two concurrent callers blocks until the first's transaction commits, then re-reads the now-`RECEIVED` (non-`ARRIVED`) status and gets a deterministic `InvalidShipmentTransitionError` -> HTTP 409 from `validate_transition` itself -- the same "second caller gets a clean 409" convention this codebase uses everywhere instead of idempotent-retry-returns-200 semantics (invariant 24). Because the status transition and the `inventory_service.receive_transferred_units` call happen in the same transaction, a loser's call never reaches the inventory-mutation step at all.

**Verified**: `tests/test_phase_d_shipment.py::test_double_receive_does_not_double_apply` (sequential double-call: second is 409, destination ends with exactly the original unit count, not doubled); `tests/test_phase_d_concurrency.py::test_two_simultaneous_receives_exactly_one_inventory_transfer` (real PostgreSQL, real `threading.Barrier`-synchronized threads -- Section 12's named race B).

---

Invariants 28–38 were added in **Phase E**. Each one is also a SQL check in `backend/app/services/invariant_check.py`, run after a real end-to-end scenario by `backend/tests/test_phase_e_invariants.py` (which also proves each check fires on a deliberately corrupted row), and runnable against any real database with `python backend/scripts/check_invariants.py`. Against the migrated dev database all 11 hold.

## 28. Fulfilled quantity is bounded (new Phase E)

**Invariant**: `0 ≤ BloodRequest.fulfilled_units ≤ units_needed`.

**Enforced**: Postgres `CHECK` constraints `ck_blood_request_fulfilled_non_negative` / `ck_blood_request_fulfilled_not_over` (DB-level), plus `request_fulfillment.validate_allocation` before any inventory is touched.

**Verified**: `test_phase_e_fulfillment.py::test_db_check_constraint_blocks_over_fulfilment`, `::test_over_allocation_is_409_with_no_inventory_side_effect`, races A–D.

## 29. Status agrees with quantity (new Phase E)

**Invariant**: `PENDING`/`MATCHING` ⇒ `fulfilled_units = 0`; `PARTIALLY_FULFILLED` ⇒ `0 < fulfilled_units < units_needed`; `FULFILLED` ⇒ `fulfilled_units = units_needed`.

**Enforced**: application code only — every quantity change and its status bucket are written in the same transaction under the request row lock (`target_status_after_allocation`; the manual fulfil paths set `fulfilled_units := units_needed` with the transition). Not a DB constraint (it spans a status enum and a number; kept in application code like every other lifecycle rule here).

**Verified**: invariant check #29 after the E2E scenario and after every race; `test_check_29_detects_status_quantity_disagreement`.

## 30. Per-hospital breakdown never exceeds the total (new Phase E)

**Invariant**: `Σ HospitalRequestResponse.units_allocated ≤ BloodRequest.fulfilled_units`, with equality when every unit came through a hospital path.

**Enforced**: application code — both are incremented in the same transaction under the request row lock; the hospital manual-fulfil path attributes the declared gap to the acting hospital.

**Verified**: `_assert_invariants` in every `test_phase_e_fulfillment.py` scenario; `test_check_30_detects_per_hospital_over_attribution`.

## 31. A donation decision is attributed (new Phase E)

**Invariant**: a `VERIFIED`/`REJECTED` donation records `verified_by_user_id` and `verified_at`.

**Enforced**: the only code that sets those statuses (`donation_review.verify_donation`/`reject_donation`) sets all three together.

**Verified**: invariant check #31; `test_linked_hospital_can_verify`.

## 32. No one reviews their own donation (new Phase E)

**Invariant**: `donations.verified_by_user_id ≠ donor_user_id`.

**Enforced**: role gate — the review endpoints accept only HOSPITAL/ADMIN, and a donation belongs to a DONOR account. Hospitals are further restricted to their own linked facility.

**Verified**: `test_donor_cannot_verify_or_reject_own_donation`; `test_check_32_detects_self_review`.

## 33. Verification versions are contiguous and never rewritten (new Phase E)

**Invariant**: for each `(user, kind)`, versions are exactly `1..n`; an earlier version's documents/checksums/decision are never modified by a later submission.

**Enforced**: unique constraint `uq_verification_version` (DB) + version computed from the previous row inside the same SAVEPOINT as the PENDING partial-unique index; no update/delete path for documents exists.

**Verified**: `test_phase_e_document_versioning.py` (incl. concurrent resubmission), invariant check #33.

## 34. A failed shipment always carries a reconciliation state (new Phase E)

**Invariant**: `reconciliation_status IS NOT NULL` ⇔ `status = FAILED`.

**Enforced**: `fail_shipment` sets `REQUIRED` in the same transaction as the `FAILED` transition; only `reconcile_shipment` changes it, once. The migration backfilled `REQUIRED` for pre-existing failed shipments.

**Verified**: `test_failed_shipment_requires_reconciliation`, `test_non_failed_shipment_has_no_reconciliation_and_cannot_be_reconciled`, `test_check_34_detects_failed_shipment_without_reconciliation`.

## 35. A returned unit traces to a unit its failed shipment carried (new Phase E)

**Invariant**: any unit whose `source_shipment_id` is a FAILED shipment has a `source_unit_id` that is one of that shipment's items.

**Enforced**: returns are created only by `receive_transferred_units` from exactly the shipment's own item units, after full unit-code confirmation.

**Verified**: `test_returned_to_origin_restocks_with_original_expiry_and_lineage`, invariant check #35.

## 36. Units return to stock only by an explicit, attested decision (new Phase E)

**Invariant**: returned units exist only for `RETURNED_TO_ORIGIN` reconciliations and only at the origin; nothing restocks automatically. Returned units keep their original `expires_at`; an already-expired unit comes back `EXPIRED`, never `AVAILABLE`. The original `ALLOCATED` rows are never mutated (ALLOCATED stays terminal).

**Enforced**: `reconcile_shipment` — origin or admin only, once (row lock + `REQUIRED` check), requires every carried unit code and `cold_chain_maintained=true`.

**Verified**: `test_phase_e_shipment_reconciliation.py` (incl. concurrent double-restock → exactly once), invariant check #36.

## 37. The audit chain has no gaps or forks (new Phase E)

**Invariant**: sealed `chain_seq` values are exactly `1..n`, each `prev_hash` equals the previous `entry_hash`, and each `entry_hash` recomputes. **Tamper-evident, not immutable** — see `LIFELINK_AUDIT_CHAIN.md`.

**Enforced**: unique `chain_seq`; sealing only under a single advisory lock, outside business transactions.

**Verified**: `test_phase_e_audit_chain.py` (incl. 16 concurrent writers and concurrent row-locking business writes), invariant check #37, `verify_chain` on the 7k-row dev database.

## 38. Every new expiry clock names the rule that started it (new Phase E)

**Invariant**: a REAL unit created after Phase E with `expires_at` set also has `expiry_rule_ref` (e.g. `shelf_life.RED_BLOOD_CELLS@2026.10.05-1`). Pre-Phase-E units legitimately have none (never guessed).

**Enforced**: `inventory_service._create_units` sets both together from `clinical_sources`.

**Verified**: `test_phase_e_clinical_sources.py::test_real_unit_expiry_uses_cited_rule_and_records_it` (all four components), invariant check #38.

## 39. A reservation can only be consumed by its own context (new Phase E — bug fix)

**Invariant**: an allocation may consume only RESERVED units earmarked for its *own* context (the same request / emergency / shipment) or context-free holds — never units reserved for a different request, emergency or shipment. This is the per-context refinement of invariants 3 and 15 (no double-allocation).

**Why it's new**: before Phase E, `inventory_service.allocate_stock` computed `released = min(units, row.reserved_units)` and checked only total on-hand units, so a context-free direct allocation could take units already reserved for a shipment (the shipment kept listing units that had been allocated elsewhere). Found when `tests/test_phase_d_concurrency.py::test_direct_allocation_vs_shipment_creation_cannot_double_commit_stock` failed intermittently in the Phase E full regression run (whenever the shipment's reservation committed first); it passed in isolation, which is why it had looked stable.

**Enforced**: `allocate_stock` counts and selects only consumable reservations (`_consumable_reservation_cond`), requires the remainder to fit in genuinely unreserved stock, and runs under the inventory row lock it already held.

**Verified**: deterministic regressions `test_phase_e_fulfillment.py::test_direct_allocation_cannot_take_units_reserved_for_a_shipment`, `::test_request_allocation_cannot_take_another_requests_reservation` (both confirmed to FAIL against the old logic and pass with the fix), `::test_generic_reservation_is_still_consumable` (pre-existing generic behaviour kept); the original Phase D race test passed 10/10 consecutive isolated runs after the fix.

---

Invariants 40–43 were added in **Phases F and G**.

## 40. An event exists if and only if its change committed (new Phase F)

**Invariant**: every real-time event describes a change that committed, and every committed change to an evented resource has an event.

**Enforced**: the outbox row is written by a `before_flush` listener inside the same transaction as the change (`realtime_service._collect`); a rollback removes both. Sequencing/publication happen only after the transaction has ended.

**Verified**: `test_phase_f_realtime.py::test_business_change_creates_sequenced_published_event`, `::test_rolled_back_change_creates_no_event`, `::test_one_event_per_resource_per_transaction`.

## 41. An event's audience never exceeds REST visibility (new Phase F)

**Invariant**: a user receives an event about a resource only if the REST API would let them read that resource; notifications only reach their owner.

**Enforced**: `realtime_audience.py` mirrors each resource's read rule; the stored audience is applied both to the live stream and to replay (SQL filter). Payloads carry no PII.

**Verified**: `test_audience_never_exceeds_rest_visibility_for_requests_and_emergencies` (every audience member checked against the real `_can_view`), `test_blood_request_audience_isolation`, `test_shipment_and_inventory_audience_is_party_hospitals_only`, `test_notification_read_emits_update_for_owner_only`; live-stream isolation in `test_phase_f_realtime_stream.py` (other patient, non-responding donor, unauthenticated, logged out).

## 42. Event sequence is gap-free and published in order (new Phase F)

**Invariant**: `realtime_events.seq` is exactly 1..n over committed events and Redis publication is strictly increasing in `seq`, so `Last-Event-ID` replay is exact.

**Enforced**: unique `seq`; assignment and publication under one session-level advisory lock held across the Redis publish; per-database channel namespace.

**Verified**: `test_concurrent_writers_get_gap_free_ordered_publication` (8 concurrent writers × 3 requests: contiguous seq, sorted and duplicate-free publication), `test_redis_outage_keeps_events_and_republishes_in_order`, `test_reconnect_with_last_event_id_replays_missed_events`, `test_channel_is_namespaced_by_database`.

## 43. Admin inventory views never count expired units as on hand (new Phase G)

**Invariant**: the admin facility list and detail never report units past their shelf life as on hand / available, even if the facility's own lazy sweep has not run yet.

**Enforced**: detail runs `inventory_service.sweep_all_expired` (as the hospital's own GET does); the list subtracts expired-but-unswept AVAILABLE/RESERVED units in SQL without writing.

**Verified**: `test_phase_g_integration.py::test_admin_facility_detail_sweeps_expired_units`, `::test_admin_facility_list_excludes_expired_without_writing`, `::test_admin_facility_list_reserved_total_excludes_expired_reservations`.

## 44. Every route is authenticated, CSRF-checked and role-gated by construction (new Phase H)

**Invariant**: every mounted route is either on the reviewed public list or depends on `get_current_user`; every POST/PUT/PATCH/DELETE depends on `verify_origin`; every `/admin` route allows exactly `{ADMIN}`; no request body model accepts a server-owned field outside a reviewed list.

**Enforced**: FastAPI dependencies on each route (backend, never the frontend).

**Verified**: `test_phase_h_security.py` — static inventory over the real route table (138 method/path pairs), plus live sweeps: unauthenticated → 401 on every protected route; every role-gated route → 403 for every disallowed role; every state-changing route → 403 "Origin not allowed" for a foreign Origin even for an allowed user. Mutation-checked: removing `verify_origin` from one route fails both the static and the live test.

## 45. Credentialed CORS is never granted to an arbitrary origin (new Phase H)

**Invariant**: the API never answers a credentialed cross-origin request from an origin outside the exact configured list.

**Enforced**: `Settings._validate_cors_origins` refuses to start with a wildcard or a non-origin entry (any environment) — with `allow_credentials=True`, Starlette would otherwise echo any requesting origin.

**Verified**: `test_wildcard_cors_origin_is_refused_at_startup`, `test_non_origin_cors_entries_are_refused`, `test_exact_origins_are_accepted_and_foreign_origins_get_no_cors_grant`.

## 46. A locking read returns the row it locked (new Phase J)

**Invariant**: every ORM `SELECT ... FOR UPDATE` (including `Session.get(..., with_for_update=True)`) yields the row's committed values at lock time, even when the session already holds that object. Every read-check-write that relies on a row lock therefore checks current data.

**Enforced**: `app/db/session.py` — a `do_orm_execute` hook adds `populate_existing` to every locking ORM select. (Sessions use `expire_on_commit=False`; without this, a re-lock after the inventory sweep's mid-transaction commit returned stale counts and an aggregate lost a committed addition.)

**Verified**: `tests/test_phase_j_locking_reads.py` (deterministic: select-for-update and get-for-update see another session's committed change; plain reads keep normal identity-map behaviour) — all fail with the hook disabled; `tests/test_phase_j_cross_workflow_races.py::test_r4_…` (real threads; reproduced the lost update before the fix).

## 47. Every committed real-time event is published, and retention actually runs (new Phase K)

**Invariant**: no committed event is left unsequenced or unpublished because of batch size; published events past retention are deleted in ordinary traffic; unpublished events are never deleted. *(Hardening: publication now runs on the per-process background publisher, so it completes shortly **after** the write returns rather than before; a backlog with no further writes is drained by the publisher's periodic run — `tests/test_hardening_realtime.py::test_backlog_without_any_further_write_is_drained_by_the_periodic_run`.)*

**Enforced**: `realtime_service.publish_pending_now` drains until a run is not full; prune runs whenever the sequence crosses a multiple of `PRUNE_EVERY`; prune deletes only `published_at IS NOT NULL`.

**Verified**: `tests/test_phase_k_performance.py` (1,037-event burst fully published as seq 1…1,037; retention under single-event traffic; unpublished rows survive prune); invariant 42 tests still pass (gap-free ordered publication).

## 48. An unverified account cannot use the API (new, production hardening)

**Invariant**: every authenticated route except `/auth/me`, `/auth/logout`, `/auth/change-password` refuses (403, `X-LifeLink-Reason: email-unverified`) an account whose email is not verified. Verification tokens are single-use, expiring and stored hashed.

**Enforced**: `app/api/deps.py::get_current_user` — the dependency every authenticated route uses (invariant 44), so there is no per-route opt-in.

**Verified**: `tests/test_hardening_email_verification.py` (7 tests); `frontend/src/components/RequireAuth.test.tsx` (redirect to `/verify-email`).

## 49. Only a document that passed a real malware scan is ever served or approved (new, production hardening)

**Invariant**: a verification document is downloadable, and a verification request approvable, only when every document's `scan_status` is `CLEAN` from a real scanner. Infected uploads are refused and never stored; a scanner error/timeout/outage quarantines (`SCAN_FAILED`), never "clean"; `DOCUMENT_SCANNER=none` leaves documents `PENDING_SCAN` (unservable), and production refuses to start with it unless explicitly overridden.

**Enforced**: `app/services/document_quarantine.py` (`assert_downloadable`, `all_clean`, quarantine prefix), `routes/verifications.py` (upload/download/approve/rescan), `core/config.py::_validate_document_scanner`.

**Verified**: `tests/test_hardening_malware_scanning.py` (16, incl. real clamd with the EICAR signature, fake clamd hang/garbage/close, unreachable scanner, legacy documents, admin-only rescan); `VerificationsQueue.test.tsx`.

## 50. Audit entries are append-only for the application, and a recompute or truncation is detectable (new, production hardening)

**Invariant**: the application's ordinary statements cannot UPDATE a sealed audit entry, change an unsealed entry's content, DELETE or TRUNCATE audit entries. With signed checkpoints configured, a recomputed chain or truncation below the latest checkpoint fails verification, as does a forged, edited, removed or reordered checkpoint.

**Enforced**: PostgreSQL triggers (`app/core/audit_guard.py`, migration `c9d0e1f2a3b4`) — **lifted by the table owner**, so the API must run as a non-owner role (deployment step, not verified here); HMAC-SHA256 checkpoints with a key outside the database (`app/services/audit_checkpoint_service.py`). Not immutability: an attacker with the owner role **and** the checkpoint key is not detected; removing the newest checkpoint lines needs append-only storage to detect.

**Verified**: `tests/test_hardening_audit_chain.py` (14); `tests/test_phase_e_audit_chain.py` (tamper tests now lift the guard as an owner would).

## 51. One Redis subscription per API process; publication off the request thread (new, production hardening)

**Invariant**: open SSE streams share one Redis pub/sub connection per process (event loop); a lagging stream or a lost Redis connection yields `resync`, never silent loss; event publication runs on the background publisher thread.

**Enforced**: `app/api/routes/events.py::_Broker`; `app/services/realtime_service.py::_BackgroundPublisher`.

**Verified**: `tests/test_hardening_realtime.py` (7); load test: 200 open streams on 2 worker processes held exactly 2 Redis pub/sub connections (`docs/perf_evidence/hardening/loadtest_sse.txt`).

## 52. The shipped frontend loads no third-party fonts or images (new, production hardening)

**Invariant**: fonts and images are served from the app's own origin; the production CSP allows `font-src 'self'` and only OpenStreetMap tiles as an external image host.

**Enforced**: bundled npm fonts, local SVGs in `frontend/public/`, `vite-csp-plugin.ts`.

**Verified**: `frontend/src/lib/selfHostedAssets.test.ts` (scans every source file and `index.html`; checks the built CSP).
