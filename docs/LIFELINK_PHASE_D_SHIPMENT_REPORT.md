# LifeLink — Phase D: Shipment / Transport Lifecycle + Chain of Custody

## 1. Objective

Build a genuinely operational shipment/transport lifecycle and chain-of-custody record for inter-facility blood movement — not a cosmetic status enum bolted onto `TransportRoute`. Close the gap `app/core/blood_unit_lifecycle.py`'s own Phase 34 docstring disclosed: *"ALLOCATE already means 'physically dispatched' ... there is no shipment-level receiving confirmation ... anywhere in the product."* Reuse this codebase's existing architecture throughout — no second idempotency mechanism, no second audit table, no second event bus, no duplicated concurrency-safety pattern — and never let frontend button visibility stand in for a real backend authorization/state guard.

## 2. Current-state audit (before this phase)

`TransportRoute` (`app/models/transport.py`, introduced earlier for the graph/routing engine) is an undirected facility-pair edge with exactly `OPERATIONAL`/`DISRUPTED` condition — a capacity/routing-graph abstraction, not a per-dispatch entity. `BloodUnitState.ALLOCATED` (Phase 34) already meant "physically dispatched, terminal" with no further state; there was no concept of a specific movement with its own manifest, no receiving confirmation at a destination, and no way to answer "which physical units are currently on which truck, and who confirmed they arrived." `docs/LIFELINK_PHASE_35_RECON_MATRIX.md` had already identified this exact gap and scoped the fix: *"A real per-dispatch shipment entity with its own lifecycle and unit-level manifest ... `ALLOCATED` stays the per-unit terminal state, a shipment is a grouping of already-allocated units for chain-of-custody, not a new blood-unit state."* Phase D is that scoped fix, built to the letter of that recon finding — not a reinvention of it.

## 3. Existing architecture this phase builds on, unchanged

- `BloodUnitState` — still exactly four states (`AVAILABLE`/`RESERVED`/`ALLOCATED`/`EXPIRED`); `ALLOCATED` is still terminal there. Phase D never reopens it.
- `TransportRoute` / `TransportRouteStatus` — still exactly `OPERATIONAL`/`DISRUPTED`; still an admin-managed routing-graph edge. Phase D references it (optionally, informationally) and never mutates it from shipment logic, nor lets its condition change a shipment's own status (Section 16 — see `LIFELINK_SHIPMENT_LIFECYCLE.md` §6).
- `inventory_service.py`'s row-locked, FEFO-aware primitives (`reserve_stock`/`release_reservation`/`allocate_stock`) — called by the new shipment routes with the same signatures used everywhere else; `receive_transferred_units` is a new primitive added to this same module (not a new module) specifically for the destination-side ownership transfer.
- `audit_service.record` — the one audit mechanism in this codebase; Phase D writes to it exactly like every other domain, with no parallel table.
- `notification_service.notify` — the one notification mechanism; Phase D calls it at every real transition.
- The `ShipmentEvent`/`BloodRequestEvent`/`EmergencyEvent` append-only-timeline convention — Phase D's event table is the fourth of this same shape, not a new design.
- The thin-DB-primitives-service-plus-inline-route-orchestration split already established by `app.services.blood_request_lifecycle` + `app.api.routes.hospitals`/`emergencies` — Phase D's `shipment_service.py` (locking, transition, code generation only) + `app.api.routes.shipments.py` (all real orchestration) follows this exact precedent rather than inventing a large, all-in-one shipment-service module.
- The "second caller gets a clean 409" concurrency convention (row lock + validated state-machine transition that rejects same-state calls) — used throughout this codebase instead of idempotent-retry-returns-200 REST semantics; Phase D's lifecycle actions follow it exactly.

## 4. What was built

**Backend** — three new tables (`shipments`, `shipment_items`, `shipment_events`; migration `c2d3e4f5a6b7_phase_d_shipment_lifecycle.py`), three new `BloodUnit` columns (`reserved_for_shipment_id`, `source_shipment_id`, `source_unit_id`, all indexed), one new enum (`ShipmentStatus`, 7 values), a pure transition-table module (`app/core/shipment_lifecycle.py`, 94 lines), a thin DB-primitives service module (`app/services/shipment_service.py`, 148 lines), one new `inventory_service` primitive (`receive_transferred_units`), a full REST API (`app/api/routes/shipments.py`, 850 lines, 13 endpoints), and Pydantic schemas (`app/schemas/shipment.py`, 142 lines).

**Tests** — `tests/test_phase_d_shipment.py` (777 lines, 27 tests) and `tests/test_phase_d_concurrency.py` (301 lines, 5 real-PostgreSQL real-thread tests) — 32 new tests total.

**Frontend** — `frontend/src/pages/hospital/Shipments.tsx` (508 lines, master/detail create+act+inspect UI), `frontend/src/pages/admin/AdminShipments.tsx` (414 lines, read-only oversight), new types/API functions in `src/lib/domain.ts`, routing in `App.tsx`, nav entries in `adminNav.ts`, and a link from `ResourcePathways.tsx`'s Logistics section.

**Docs** — Invariants 23–27 appended to `docs/LIFELINK_INVARIANTS.md`; this report; `docs/LIFELINK_SHIPMENT_LIFECYCLE.md` (the full domain/architecture reference — see that document for the lifecycle diagram, complete transition table, authorization matrix, inventory-effect table, concurrency-race table, and API surface; this report does not duplicate that material in full).

The full state machine, authorization model, inventory effects, chain-of-custody design, idempotency pattern, concurrency guarantees, notification types, API surface, frontend integration, and disclosed limitations are documented in **`docs/LIFELINK_SHIPMENT_LIFECYCLE.md`** — written as this phase's permanent architecture reference, not restated section-by-section here.

## 5. Ownership-transfer design (the core correctness decision)

A shipment's lifecycle is the record of a real, physical movement; the actual inventory-ownership transfer is implemented as two separate, honestly-timestamped `BloodUnit` rows rather than one row being reassigned a new `hospital_id`:

- **Create** reserves (not allocates) specific origin-side units against the shipment (`reserved_for_shipment_id`).
- **Dispatch** allocates them — origin-side, terminal, irreversible, exactly matching Phase 34's pre-existing `ALLOCATED` semantics.
- **Receive** creates brand-new destination-side `BloodUnit` rows (`provenance=TRANSFERRED_IN`, `expires_at` copied — never recomputed — `source_shipment_id`/`source_unit_id` set for traceability) and never reassigns the origin row's `hospital_id`.

This was chosen specifically because reassigning `hospital_id` in place would force an arbitrary choice between the destination counting the unit before it physically arrived, or inventing a third, ambiguous "in transit, counted nowhere" row-state — `Shipment.status` itself already *is* that in-between state; duplicating it onto `BloodUnit` would be redundant and harder to reason about. This design is what makes Invariant 25 ("a unit is never simultaneously available at both ends") true by construction rather than by a runtime check — see `LIFELINK_INVARIANTS.md` §25 for the full argument and its test evidence.

## 6. Section 14 — the disclosed limitation, by design

A `FAILED` shipment's origin-side units stay `ALLOCATED` — they are **never** automatically returned to `AVAILABLE`. This was a direct, deliberate reading of the master prompt's Section 14 instruction ("never silently return blood to AVAILABLE on failure") rather than a convenience shortcut: this system has no way to honestly verify that failed-in-transit stock is physically safe to restore (it could be destroyed, contaminated, or genuinely lost), so restoring it automatically would be a claim about physical reality the software cannot back up. Recovering from a real failure requires a human to perform a separate, explicit reconciliation action (e.g. re-`ADD`ing genuinely-recovered stock with a note). Verified by `tests/test_phase_d_shipment.py::test_fail_after_dispatch_does_not_release_units`.

## 7. Section 16 — route condition independence

`TransportRoute.status` and `Shipment.status` are provably independent state machines: disrupting or recovering a route only ever writes to the `TransportRoute` row and its own graph-service side effects; no code path in `app/api/routes/shipments.py` reads `TransportRoute.status` as an authority over `Shipment.status`, and no code path in `app/api/routes/admin.py`'s disrupt/recover handlers touches `Shipment` at all. Verified directly (`test_route_disruption_does_not_block_or_corrupt_shipment_lifecycle`) and end-to-end by the full scenario test (§10), which disrupts a route mid-`IN_TRANSIT`, confirms the shipment is completely untouched, reports a delay citing the disruption, recovers the route, and carries the shipment through to `RECEIVED`.

## 8. Concurrency — the five named races

Real PostgreSQL, real `threading.Thread`/`threading.Barrier` tests, the same `TestClient`-shared-across-threads pattern already established in `tests/test_phase34_blood_unit_lifecycle.py` and `tests/test_concurrency.py` — no mocks. All five races the master prompt names are covered; race C ("receipt vs. cancellation") does not literally apply to this state machine (`CANCELLED` is reachable only from `CREATED`, `RECEIVED` only from `ARRIVED` — the two preconditions can never hold simultaneously on one shipment, by construction of the transition table, not by omission of a test) and is honestly substituted with the real analogous fork in the state machine — `CREATED`'s two outgoing edges (dispatch vs. cancel) raced against each other. Full race-to-test mapping and outcomes are in `LIFELINK_SHIPMENT_LIFECYCLE.md` §12. All 5 pass.

## 9. Performance — EXPLAIN ANALYZE (Section 30)

Run against the real dev PostgreSQL database (`lifelink`, 157 real hospital facilities, 11,752 real `blood_units` rows at the time of this measurement) after seeding 900 realistic shipments / 3,150 shipment items / 3,100 shipment events spread across a realistic status distribution (CREATED/DISPATCHED/IN_TRANSIT/ARRIVED each ~100, RECEIVED ~300, CANCELLED/FAILED ~100 each) and a 60-day creation-time spread — the seed data was deleted immediately after capturing these plans; it is not part of the permanent dev database.

| Query (as actually issued by `app/api/routes/shipments.py`) | Plan | Execution time |
|---|---|---|
| List outgoing shipments for one facility (`ix_shipments_origin_status`) | Bitmap Index Scan | 0.15 ms |
| List all shipments for one facility, either direction (`BitmapOr` across `ix_shipments_origin_status` + `ix_shipments_destination_status`) | BitmapOr → Bitmap Heap Scan | 0.18 ms |
| Facility dashboard: incoming + status filter (`ix_shipments_destination_status`, composite) | Index Scan | 0.03 ms |
| Lookup by `shipment_code` (`uq_shipments_shipment_code`) | Index Scan | 0.03 ms |
| Shipment → its items (`ix_shipment_items_shipment_id`) | Bitmap Heap Scan | 0.02 ms |
| Items → units `IN()` join (`_to_out`'s second half) | Nested Loop + `blood_units_pkey` | 0.09 ms |
| Unit trace by `unit_code` (`uq_blood_units_unit_code`) | Index Scan | 0.05 ms |
| Timeline, ordered (`ix_shipment_events_shipment_id`) | Bitmap Heap Scan + sort | 0.05 ms |
| Active items for a shipment (dispatch/receive/cancel's row-locked read) | Bitmap Heap Scan + Filter | 0.02 ms |
| Admin-wide list, status filter only, no facility scope (`ix_shipments_status`) | Bitmap Index Scan | 0.29 ms |
| Admin-wide list, no filters at all | **Seq Scan** (900 rows, cost 34) | 0.42 ms |

Every query that will actually be issued by a real facility user (the overwhelming majority of traffic) uses an existing index and runs in well under a millisecond at this volume. **No new index was added** — every access path already had one from the original Phase D.3 migration (`ix_shipments_origin_status`, `ix_shipments_destination_status`, the unique `shipment_code`/`unit_code` indexes, the `shipment_items`/`shipment_events` foreign-key indexes), and adding more at this row count would not be justified by any plan above. The one honest caveat: an unfiltered, unscoped admin "list everything" call does a sequential scan rather than an index scan — correct and appropriate at the current ~900-row scale (a sort of 900 rows costs less than maintaining/using an index for it), but if real shipment volume grows by multiple orders of magnitude *and* that specific unscoped admin query pattern remains common, a plain `created_at` index would let Postgres satisfy the `ORDER BY ... LIMIT 200` without a full-table sort. This is noted here as a disclosed, future consideration — not implemented now because the evidence at hand does not justify it.

## 10. The end-to-end scenario (Section 32)

`tests/test_phase_d_shipment.py::test_e2e_full_scenario_allocate_ship_disrupt_delay_recover_receive` — one continuous story through the real HTTP API: Hospital A adds stock → an admin registers a real `TransportRoute` between A and B (geographic coordinates, real haversine-derived distance/transit-time) → Hospital A creates a shipment of 4 O_NEG units referencing that route → dispatch (origin units `RESERVED → ALLOCATED`, aggregate counters checked) → in-transit (ETA derived from the route) → the route is disrupted mid-transit (shipment status asserted untouched) → Hospital A reports a delay citing the disruption → the route recovers (shipment status still untouched; delay history preserved) → arrival → Hospital B receives (destination gets 4 new `TRANSFERRED_IN` units, each traced back to its exact origin unit and `expires_at` copied not recomputed; both facilities' `HospitalInventory` aggregates checked against the real per-unit rows) → the shipment is now terminal (a stale dispatch/cancel attempt both correctly 409) → the full `ShipmentEvent` timeline is read back and asserted to be exactly `[created, dispatched, in_transit, delayed, arrived, received]`, strictly chronological → the destination unit is independently traced by its printed code back to this exact shipment, with no donor PII exposed. Passes.

## 11. Test inventory and results

| File | Tests | Result |
|---|---|---|
| `tests/test_phase_d_shipment.py` | 27 | All pass |
| `tests/test_phase_d_concurrency.py` | 5 | All pass |
| **Phase D total** | **32** | **32 passed, 0 failed** |

Coverage highlights: creation (reservation-not-allocation, idempotent replay + conflicting-payload rejection, facility/verification/self-shipment/insufficient-stock validation), the full happy-path lifecycle with real ownership-transfer assertions, every illegal transition the spec names explicitly (RECEIVED→DISPATCHED, CANCELLED→RECEIVED, FAILED→RECEIVED, dispatch-after-cancel, double-receive, mutate-after-terminal), cancellation-releases/failure-does-not-release, Invariant 23 at the DB level (direct `IntegrityError` assertion), route-disruption independence, in-transit expiry honesty, authorization (unrelated hospital, wrong-direction action), delay annotation + history preservation, unit-code traceability (including the 403 when tracing a unit no longer on your books), direction-filtered listing, and the full E2E scenario (§10).

**Full backend regression** — the entire suite was run in four sequential batches (to avoid the single-run 10-minute timeout this phase's own concurrency tests, stacked on every prior phase's, produce) rather than skipped or estimated:

| Batch | Files | Result |
|---|---|---|
| 1 | 15 files (admin, auth, authz, blood requests, certificates, concurrency, donor matching, emergencies...) | All pass, exit 0 |
| 2 | 15 files (emergency network, family, graph ×4, health, hospital, matching, notifications, password reset, phase 11–13...) | All pass, exit 0 |
| 3 | 14 files (phase 14–26) | All pass, exit 0, **1 pre-existing skip** (unrelated to Phase D) |
| 4 | 16 files (phase 27–35, Phase D files, profiles, rate limit, seed, sms, verifications) | All pass, exit 0 |

**925 tests collected across the full suite; 924 passed, 1 skipped, 0 failed.** This is a genuine, complete, just-executed count — not an estimate and not carried forward from an earlier phase's report.

## 12. Invariants added

Five new invariants appended to `docs/LIFELINK_INVARIANTS.md` (23–27), each with its enforcement mechanism and the specific test(s) that verify it:

23. A `BloodUnit` belongs to at most one *active* shipment at a time — DB-level partial unique index, not just application logic.
24. Shipment terminal states are immutable — empty outgoing-edge sets in the transition table, re-checked on every action including delay.
25. A received unit is never simultaneously countable at both the origin and destination — true by the two-separate-rows design (§5), not a runtime check.
26. Every shipment lifecycle transition is auditable — one `ShipmentEvent` + one `AuditLog` entry per transition, reusing existing mechanisms, no new table.
27. Receipt transfers ownership at most once — row lock + validated transition, the same "second caller gets a clean 409" convention as everywhere else.

## 13. Security / authorization

No new input surface accepts a client-supplied target status directly — every transition is driven by which already-role-gated action endpoint was called, mirroring Phase 34's own finding for `BloodUnitState`. Every mutating action is re-validated server-side (existence, current state, actor's facility membership, active-units-present where relevant) under the row lock, regardless of what `allowed_transitions` told the frontend to display. `ADMIN` gets full cross-facility access through the identical endpoints a hospital uses (`_actor_facility` returning `None` bypasses ownership checks) — no separate, differently-audited admin API was built. `verify_origin`'s existing CSRF defense gates every mutating endpoint identically to the rest of the codebase. Unit-trace lookups never cross a facility's own boundary and never expose donor identity, matching every other cross-facility read in this application.

## 14. Frontend integration

`Shipments.tsx` (hospital) and `AdminShipments.tsx` (admin) — both built directly against the backend's real response shapes (`ShipmentOut.allowed_transitions`, `items`, `route_status`/`route_disrupted_reason`), with every action button's enabled/disabled state driven by `allowed_transitions` purely as a UI convenience, never as the actual authorization boundary (§13; `LIFELINK_SHIPMENT_LIFECYCLE.md` §7). `AdminShipments.tsx` is read-only by frontend choice — the backend endpoints it calls are the same ones a hospital calls, scoped by the same `_actor_facility` logic, so admin write access already exists server-side without a dedicated admin write UI. Neither page includes a blood-component selector; this frontend has had no `BloodComponent` type or UI anywhere since Phase 35, and Phase D did not introduce or fix that — shipment creation in the UI defaults every line to whole blood, consistent with that pre-existing scope boundary, while the backend itself fully supports per-component lines via the API directly.

## 15. Known, disclosed limitations

- No automatic recovery path from `FAILED` (§6) — intentional, not an oversight.
- No live GPS/fleet tracking; `estimated_arrival_at` is a one-time static estimate.
- No blood-component selector in the shipment-creation UI (pre-existing frontend gap, not newly introduced).
- Admin-wide, fully-unscoped shipment listing uses a sequential scan at current volume (§9) — correct now; flagged as a future index candidate only if that specific access pattern becomes common at much higher volume.
- A shipment's optional `transport_route_id` link is not validated against the shipment's own facility pair; ETA/route-status display always derives from the real facility pair regardless (§9 of `LIFELINK_SHIPMENT_LIFECYCLE.md`), so a mismatched link has no user-visible effect beyond the stored field itself — noted for completeness, not treated as a defect requiring a fix in this phase.

## 16. Files changed/added — summary

Backend: `app/models/shipment.py` (new), `app/models/blood_unit.py` (3 columns), `app/models/enums.py` (`ShipmentStatus`), `app/core/shipment_lifecycle.py` (new), `app/services/shipment_service.py` (new), `app/services/inventory_service.py` (`receive_transferred_units` added; one pre-existing bug in it fixed during this phase — see §17), `app/schemas/shipment.py` (new), `app/api/routes/shipments.py` (new), `app/main.py` (router registration), one new Alembic migration. Tests: two new files, 32 tests. Frontend: two new pages, `domain.ts`/`App.tsx`/`adminNav.ts`/`ResourcePathways.tsx` updated. Docs: `LIFELINK_INVARIANTS.md` extended, `LIFELINK_SHIPMENT_LIFECYCLE.md` and this report added.

## 17. Bugs found and fixed during this phase

Two real bugs were found and fixed while building and testing this phase (both pre-existing-pattern mistakes in Phase D's own new code, not in anything from an earlier phase):

1. `inventory_service.receive_transferred_units` built its new `BloodUnit` list across a `(blood_type, component)` grouping loop but accumulated into the same outer list every iteration before calling `db.add_all`/`db.flush`, causing every group after the first to redundantly re-process all prior groups' units. Fixed by scoping a `group_new` list per iteration.
2. `_deactivate_items` originally used a SQLAlchemy Core bulk `UPDATE`, which bypasses the ORM session's identity map — already-loaded `ShipmentItem` Python objects in the same request session kept a stale `is_active=True` in memory even after the bulk update committed. Fixed by querying and mutating the actual ORM objects directly (`item.is_active = False`), matching this codebase's existing object-mutation convention elsewhere.

Both were caught by this phase's own test suite before being considered complete, not discovered later.

## 18. Completion-gate checklist

- [x] Real operational shipment/transport lifecycle with a validated state machine — not a cosmetic enum.
- [x] No second idempotency mechanism, audit table, event bus, or concurrency-safety pattern introduced.
- [x] Real PostgreSQL, real-thread concurrency tests for all 5 named races (one honestly substituted per its actual non-applicability, documented as such).
- [x] Frontend button visibility never trusted as the real authorization/state guard — every action re-validated server-side.
- [x] Section 14: failure never silently returns blood to AVAILABLE — disclosed, real limitation.
- [x] Section 16: route condition kept separate from shipment lifecycle; verified directly and end-to-end.
- [x] `docs/LIFELINK_INVARIANTS.md` extended with invariants (23–27) that accurately reflect the final design.
- [x] `docs/LIFELINK_SHIPMENT_LIFECYCLE.md` written as the permanent architecture reference.
- [x] EXPLAIN ANALYZE run against real data volume; no index added without evidence; one future consideration disclosed.
- [x] One complete, realistic E2E scenario test, passing.
- [x] Full backend regression suite run to completion (not skipped, not estimated): 925 collected, 924 passed, 1 pre-existing skip, 0 failed.
- [x] This report written with only real, actually-observed numbers.

## 19. What Phase D deliberately did not touch

Phase E's own named scope (fulfillment states, donation verification, shelf-life sourcing, document versioning, audit hash chain), Phase F (real-time event layer), and everything else in the Phase A master plan's remaining phases (G–L) were left untouched — no speculative work was done against any of them, and nothing in this phase's design forecloses any of them.

### PHASE D COMPLETE

**What changed**: A real, backend-enforced `Shipment`/`ShipmentItem`/`ShipmentEvent` lifecycle (7 states, validated transition table) with a genuine origin→destination inventory-ownership transfer, full chain-of-custody traceability (timeline + unit-code lookup + existing audit log, no new audit mechanism), idempotent creation, DB-level enforcement of "one active shipment per unit," and a hospital + admin frontend — all reusing this codebase's existing architectural conventions throughout.

**Tests**: 32 new tests (27 functional + 5 real-PostgreSQL concurrency), all passing. Full-suite regression: 925 collected, 924 passed, 1 pre-existing unrelated skip, 0 failed.

**Failures**: None outstanding. Two bugs found during this phase's own development were fixed before completion (§17).

**Remaining, disclosed limitations**: No automatic `FAILED`-state inventory recovery (by design — §6/§15); no live GPS/ETA tracking (static estimate only); no blood-component selector in the shipment UI (pre-existing frontend gap); admin-wide unscoped listing uses a sequential scan at current volume (correct now, flagged for future re-evaluation only at much higher volume).

**Next recommended Phase E work**: fulfillment states, donation verification, shelf-life sourcing, document versioning, and the audit hash chain — per the master plan's own Phase E scope. Per the master prompt's explicit instruction, Phase E is **not** started here. Stopping now.
