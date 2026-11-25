# LifeLink — Shipment / Transport Lifecycle & Chain of Custody (Phase D)

This document describes the real, backend-enforced lifecycle introduced in Phase D: a specific, unit-level-traceable movement of already-on-hand blood stock from one verified facility to another, closing the gap `app/core/blood_unit_lifecycle.py`'s own module docstring disclosed since Phase 34 — "ALLOCATE already means 'physically dispatched' ... there is no shipment-level receiving confirmation ... anywhere in the product." It does not reopen `BloodUnitState` (still exactly four states: `AVAILABLE`/`RESERVED`/`ALLOCATED`/`EXPIRED`, `ALLOCATED` still terminal there) and it does not overload `TransportRoute` (still exactly `OPERATIONAL`/`DISRUPTED` — a route's own physical/operational condition, kept deliberately separate from any one shipment's own lifecycle; see §6).

## 1. Domain model

Three tables, deliberately minimal (`app/models/shipment.py`):

| Table | Purpose | Owns |
|---|---|---|
| `shipments` | One specific movement of stock from an `origin_facility_id` `HospitalProfile` to a `destination_facility_id` `HospitalProfile`. The thing with a lifecycle. | `shipment_code` (human-readable, PII-free, globally unique, immutable), `status` (`ShipmentStatus`), one nullable timestamp per real transition (`dispatched_at`/`in_transit_at`/`arrived_at`/`received_at`/`cancelled_at`/`failed_at`), `estimated_arrival_at` (explicitly an estimate — §9), `delayed_at`/`delay_reason` (annotation, not a state — §5), `cancel_reason`/`failure_reason`, optional `transport_route_id` (informational only — §6), idempotency fields (§11). |
| `shipment_items` | Which specific, already-existing `BloodUnit` rows this shipment carries — one row per unit, never an aggregate `quantity` column, so every unit stays individually traceable (§10) through its own row. | `is_active` (false once the parent shipment reaches any terminal state), and the DB-level partial unique index enforcing Invariant 23 (§12). |
| `shipment_events` | Append-only operational timeline — identical shape to `BloodRequestEvent`/`EmergencyEvent` (same columns, same "one row per real thing that happened" convention), not a fourth, shipment-specific event-log design. Powers `GET /shipments/{id}/timeline`. | `event_type`, `actor_user_id`, `note`, server-written `created_at`. |

Chain-of-custody is **not** a separate, fourth table: `ShipmentEvent` (the operational timeline) plus the existing global `AuditLog` (written alongside it by `app.services.shipment_service.transition`, exactly like every other domain's lifecycle transitions already do) together answer every chain-of-custody question this phase's spec asks for — who / what / when / where / which shipment / which unit / old state / new state / why (§10).

`BloodUnit` (`app/models/blood_unit.py`) gained three Phase D columns, all indexed, none duplicating Phase 34 state:

- `reserved_for_shipment_id` — mirrors `reserved_for_request_id`/`reserved_for_emergency_id`: which shipment this unit is currently earmarked for (`CREATED`) or was physically allocated against (`DISPATCHED`). Cleared only on cancel (`RESERVED → AVAILABLE`); never cleared by dispatch/receive — once `ALLOCATED` (terminal, per Phase 34), it stays set as this unit's permanent record of which shipment moved it.
- `source_shipment_id` / `source_unit_id` — destination-side only, set together only by `inventory_service.receive_transferred_units` on the **new** `BloodUnit` row it creates at the destination. `source_unit_id` points back at the origin-side row (now permanently `ALLOCATED` there); following it reaches that row's own `donation_id` if one was ever recorded, so donation/component data is never duplicated across the two rows.

## 2. Lifecycle diagram

```
                 ┌───────────┐
          ┌──────┤  CREATED  │
          │      └─────┬─────┘
     CANCEL│            │DISPATCH
          │            ▼
          │      ┌───────────┐
          │      │ DISPATCHED│──────┐
          │      └─────┬─────┘      │FAIL
          ▼            │IN_TRANSIT  │
    ┌───────────┐      ▼            │
    │ CANCELLED │(terminal)   ┌───────────┐
    └───────────┘             │IN_TRANSIT │──────┐
                               └─────┬─────┘      │FAIL
                                     │ARRIVE      │
                                     ▼            │
                               ┌───────────┐      │
                               │  ARRIVED  │──────┤
                               └─────┬─────┘      │
                                     │RECEIVE     ▼
                                     ▼       ┌───────────┐
                               ┌───────────┐ │  FAILED   │
                               │ RECEIVED  │ │(terminal) │
                               │(terminal) │ └───────────┘
                               └───────────┘
```

Seven states (`app/models/enums.py::ShipmentStatus`), not the illustrative `CREATED → READY_FOR_DISPATCH → ... → COMPLETED` sketch a generic shipment-tracking system could have — two states from that generic sketch are deliberately collapsed here, each because, in *this* implementation, it would have had no distinct backing capability (the same "no decorative states" discipline Phase 34 already applied to `BloodUnitState`):

- **`READY_FOR_DISPATCH` is not separate from `CREATED`** — this system reserves a shipment's units atomically at creation time (`create_shipment` calls `inventory_service.reserve_stock` inline, in the same request), so nothing distinguishes "created" from "ready" the way it would in a system where items are added incrementally after an empty draft is opened.
- **`COMPLETED` is not separate from `RECEIVED`** — `RECEIVED` is already the moment the real inventory-ownership transfer happens (`receive_shipment` creates the destination-side `BloodUnit` rows); nothing further actually happens afterward in this product, so a trailing `COMPLETED` would be a status with no real transition into it.

## 3. State reference

| State | Meaning | Unit-level reality |
|---|---|---|
| `CREATED` | Shipment exists; its units are `RESERVED` (not yet `ALLOCATED`) at the origin, earmarked specifically for this shipment. | `BloodUnit.reserved_for_shipment_id` set; `BloodUnitState.RESERVED`. |
| `DISPATCHED` | Physically left the origin. | Units move `RESERVED → ALLOCATED` at the origin — terminal there per `BloodUnitState`; this system never un-allocates. |
| `IN_TRANSIT` | Transport-underway confirmed — a separate, cheap-to-support confirmation step; may follow `DISPATCHED` immediately in practice for a small deployment with no live fleet tracking (§9). | No further unit-level change. |
| `ARRIVED` | Physically at the destination's dock, not yet formally received/counted into destination inventory. | No further unit-level change. |
| `RECEIVED` | Terminal, happy path. Destination has taken real custody. | New destination-side `BloodUnit` rows created (§4); origin's `ALLOCATED` rows are untouched and remain terminal there. |
| `CANCELLED` | Terminal. Only legal from `CREATED` — before physical dispatch. | Reservation released back to `AVAILABLE` at the origin (safe only because nothing has reached terminal `ALLOCATED` yet). |
| `FAILED` | Terminal. Legal from `DISPATCHED`/`IN_TRANSIT`/`ARRIVED` — something went wrong after physical departure. | Does **not** release or return the origin's now-`ALLOCATED` units (§8 — a disclosed, real limitation, not an oversight). |

`DELAYED` is deliberately not a state — see §5.

## 4. Transition table

Encoded in `app/core/shipment_lifecycle.py` (pure, DB-free, in the same style as `app.core.request_lifecycle`/`app.core.emergency_lifecycle`/`app.core.blood_unit_lifecycle`):

```python
TRANSITIONS: dict[S, frozenset[S]] = {
    S.CREATED:    frozenset({S.DISPATCHED, S.CANCELLED}),
    S.DISPATCHED: frozenset({S.IN_TRANSIT, S.FAILED}),
    S.IN_TRANSIT: frozenset({S.ARRIVED, S.FAILED}),
    S.ARRIVED:    frozenset({S.RECEIVED, S.FAILED}),
    S.RECEIVED:   frozenset(),
    S.CANCELLED:  frozenset(),
    S.FAILED:     frozenset(),
}
```

A same-state "transition" (e.g. `CREATED → CREATED`, a stale-read double-submit) is always rejected by `validate_transition`, exactly like every other lifecycle module in this codebase (`BloodUnitState`, `BloodRequest`) — there is no idempotent-retry-returns-200 semantics at the transition layer; a second identical action call gets a deterministic HTTP 409 (Section 11's own idempotent-*create* pattern is unrelated and lives only at `POST /shipments` — see §11). `allowed_transitions(current)` is what `ShipmentOut.allowed_transitions` surfaces to the frontend, purely as a UI hint — see §7 for why it is never trusted as the real authorization boundary.

## 5. DELAYED is an annotation, not a state

`report_delay` (`POST /shipments/{id}/delay`) is legal only while `status` is `DISPATCHED` or `IN_TRANSIT` (`app.core.shipment_lifecycle.can_report_delay` / `DELAYABLE_STATES`), and does not change `Shipment.status` at all. It sets `delayed_at`/`delay_reason` (reflecting the *most recent* report only) and writes its own independent `ShipmentEvent`('delayed')/`AuditLog`('shipment.delayed') pair — so the full history of every delay report a shipment ever received is preserved in its timeline even though the mutable "most recent" fields on the row itself are overwritten each time. A delayed shipment is still, truthfully, `DISPATCHED` or `IN_TRANSIT` — it just hasn't arrived on the schedule anyone hoped for. This exactly mirrors how `TransportRoute.status` (`OPERATIONAL`/`DISRUPTED`) is kept separate from a shipment's own lifecycle (§6) — in both cases, a real-world operational fact is recorded as its own orthogonal signal rather than contorting the primary state machine to carry it.

## 6. Route condition vs. shipment lifecycle (Section 16)

`TransportRoute.status` (admin-managed, `POST /admin/transport-routes/{id}/disrupt`/`/recover`) describes the physical/operational condition of a facility-pair *route* — a routing-graph edge shared by every shipment that might ever use it. A `Shipment.transport_route_id` is purely informational: which registered route this shipment is using, read only to compute `estimated_arrival_at` (§9) and to surface `route_status`/`route_disrupted_reason` on `ShipmentOut` for display. **A disrupted route never auto-cancels, blocks, or mutates a shipment already in flight on it** — disrupting or recovering a route only ever touches the `TransportRoute` row and its own graph-service side effects; it is never read as an authority over `Shipment.status`, and no code path transitions a shipment because a route changed condition. Verified end-to-end by `tests/test_phase_d_shipment.py::test_route_disruption_does_not_block_or_corrupt_shipment_lifecycle` and by the full E2E scenario (`::test_e2e_full_scenario_allocate_ship_disrupt_delay_recover_receive`), which disrupts the route mid-`IN_TRANSIT`, asserts the shipment is untouched, reports a delay citing the disruption, recovers the route, and carries the same shipment through to `RECEIVED` — all while the shipment's own status only ever moves forward through its own transition table.

## 7. Authorization

`_actor_facility(db, user)` (`app/api/routes/shipments.py`) returns the caller's own `HospitalProfile` for a `HOSPITAL` user, or `None` for `ADMIN` (who has no dedicated facility profile — the existing convention from every other admin-vs-hospital surface in this codebase). `None` bypasses every facility-ownership check below, giving `ADMIN` full cross-facility read/write access through the exact same endpoints a hospital uses — no separate admin-only shipment API exists, and none was built, because none is needed.

| Action | Who | Check |
|---|---|---|
| Create | Origin facility (or admin) | `hp.id == payload.origin_facility_id` |
| Dispatch | Origin facility only (or admin) | `_assert_is_origin` |
| Mark in-transit, mark arrived, report delay, fail | Either party (or admin) | `_assert_is_party` |
| Receive | Destination facility only (or admin) | `_assert_is_destination` |
| Cancel | Origin facility only (or admin) | `_assert_is_origin` |
| Read (get/list/timeline/by-code) | Either party (or admin) | `_assert_is_party` |
| Trace a unit by code | The facility currently holding that unit's row (or admin) | `hp.id == unit.hospital_id` |

Every precondition — shipment exists, is in the right current state, actor is authorized, has active units where relevant — is **re-checked server-side on every single action**, under the `SELECT ... FOR UPDATE` row lock from `shipment_service.get_for_update` (§12). `ShipmentOut.allowed_transitions` is sent to the frontend purely so the UI can grey out buttons that would certainly fail, but the frontend's choice of which buttons to render is never trusted as the real authorization or state guard — a client that calls `POST /shipments/{id}/dispatch` on an already-`RECEIVED` shipment, or one it is not a party to, gets the same HTTP 409/403 the UI was trying to prevent, not a silent success. `verify_origin` (the existing lightweight CSRF defense for cookie-authenticated state changes) gates every mutating action identically to every other mutating endpoint in this codebase — no new CSRF mechanism was introduced.

## 8. Inventory effects, failure, and cancellation (Section 13/14)

| Action | `inventory_service` call | Effect |
|---|---|---|
| Create | `reserve_stock` per line | Origin: `AVAILABLE → RESERVED`, specific FEFO-selected units earmarked (`reserved_for_shipment_id`). `HospitalInventory.units` unchanged; `reserved_units` up. |
| Dispatch | `allocate_stock` per `(blood_type, component)` group of this shipment's active units | Origin: `RESERVED → ALLOCATED` (terminal). `HospitalInventory.units` down; `reserved_units` down. |
| Cancel (from `CREATED` only) | `release_reservation` per group | Origin: `RESERVED → AVAILABLE`. Safe **only** because cancellation is unreachable once dispatch has happened — nothing has reached terminal `ALLOCATED` yet, so there is no double-allocation risk in releasing it (Section 13's "do not automatically release inventory if that could create an unsafe double-allocation" is satisfied by construction, not by an extra runtime check). |
| Receive | `receive_transferred_units` | Destination: brand-new `BloodUnit` rows created, `provenance=TRANSFERRED_IN`, `expires_at` **copied** from the origin unit (never recomputed — a unit does not get a fresher shelf life just because it moved), `source_shipment_id`/`source_unit_id` set. `HospitalInventory.units` up at the destination. Origin's `ALLOCATED` rows are never touched. |
| Fail (from `DISPATCHED`/`IN_TRANSIT`/`ARRIVED`) | *(none)* | **Deliberately does not release or return the origin's now-`ALLOCATED` units.** This is Section 14's instruction taken literally: "never silently return blood to AVAILABLE on failure." A failed shipment requires a separate, explicit reconciliation action a human performs — e.g. a hospital admin re-adding genuinely-recovered stock via the existing `ADD` action with a note explaining why — rather than an automatic reversal this system has no way to honestly verify actually happened (the units could be destroyed, contaminated, or genuinely lost; auto-restoring them to `AVAILABLE` would be a claim about physical reality the software cannot back up). This is a **disclosed, real limitation**, not an oversight: see `tests/test_phase_d_shipment.py::test_fail_after_dispatch_does_not_release_units`, which asserts the origin's `HospitalInventory.units` stays exactly as it was at dispatch time after a `FAILED` transition. |

Every call above happens inside the same database transaction as the `Shipment.status` transition itself (one `db.commit()` at the end of each route handler) — there is no window in which the status changes but the matching inventory effect does not, or vice versa.

## 9. Estimated arrival (Section 17)

`estimated_arrival_at` is set once, at the `IN_TRANSIT` transition, from the linked `TransportRoute.transit_minutes` if one is linked (`utcnow() + timedelta(minutes=route.transit_minutes)`) — and is **explicitly labeled an estimate** everywhere it appears (field name, docstrings, this document). There is no live-GPS/fleet-tracking integration anywhere in this product, so it is always a static, one-time-computed figure, never a real-time ETA that updates as a shipment actually moves. A delay report may optionally revise it (`ShipmentDelayIn.new_estimated_arrival_at`), but nothing in this system claims to know a shipment's real-time position.

## 10. Chain of custody & traceability (Section 9/10/25)

Answering "where is this unit, where has it been, who touched it, and when" never requires a new table:

- **`GET /shipments/{id}/timeline`** — every `ShipmentEvent` for a shipment, in chronological order (`created_at ASC`), every timestamp server-written, never client-supplied (Section 23).
- **`GET /shipments/units/{unit_code}`** — look up one unit by its printed/barcoded `unit_code` and get its current state, hospital, component, donation reference (not donor identity — no donor PII is ever exposed through this endpoint), and both directions of shipment history: `carrying_shipment_id`/`carrying_shipment_code` (if it's currently reserved/allocated on an in-flight shipment) and `source_shipment_id`/`source_shipment_code`/`source_unit_id` (if it's a destination-side unit created by a past receipt). A facility may only trace units currently on its own books; the origin-side predecessor of a received unit is reachable only by following `source_unit_id`, never directly, exactly like every other cross-facility boundary in this app.
- **`AuditLog`** (admin-facing, via `audit_service.record`) — `shipment.created`/`shipment.transition`/`shipment.delayed` entries, each carrying `old_status`/`new_status`/`reason`/actor/shipment id in `meta`.
- Shipment codes (`SHP-<10 hex chars>`) and unit codes are both PII-free by construction — no donor identity, patient identity, or medical detail is ever embedded in either (Section 10).

## 11. Idempotency (Section 11)

`POST /shipments` accepts an optional client-supplied `idempotency_key`, scoped per-creator (`UniqueConstraint(created_by_user_id, idempotency_key)`) — the same Phase 19/26 precedent applied a third time (`uq_blood_request_idempotency_key`/`uq_donation_idempotency_key`), not a new mechanism. A replay with the same key and the same canonical payload (SHA-256 hash over the fields that actually define the request) returns `200` with the original shipment, unchanged; a replay with the same key and a *different* payload is a `409`. The race-safe insert path (`db.begin_nested()` savepoint, catch `IntegrityError` on the unique constraint, re-read-and-compare) mirrors `donors.log_donation`'s identical pattern exactly. Every other action endpoint (dispatch/receive/cancel/...) has no idempotency key of its own — a second identical call there is handled by the state machine itself (§4): the second caller's `get_for_update` sees the now-already-transitioned status and gets a deterministic 409, the same "second caller gets a clean 409" convention this codebase uses throughout rather than inventing REST-generic idempotent-retry semantics for every action endpoint.

## 12. Concurrency guarantees (Section 12)

Every endpoint that can change `Shipment.status` fetches the row through `shipment_service.get_for_update` (`SELECT ... FOR UPDATE`), so two concurrent actions on the same shipment serialize: the second waits for the first's commit (which releases the lock), then re-validates against the now-current status rather than both reading the same stale value and both succeeding. Combined with `ShipmentItem`'s DB-level partial unique index (Invariant 23) and `inventory_service`'s own row-locked `HospitalInventory` primitives, this is the single concurrency-safety mechanism used throughout — no second, shipment-specific locking or idempotency scheme was invented. Real PostgreSQL, real-thread (`threading.Thread`/`threading.Barrier`, same `TestClient`) tests cover all five races the Phase D spec names (`tests/test_phase_d_concurrency.py`):

| # | Race | Test | Outcome |
|---|---|---|---|
| A | Two users dispatch the same shipment simultaneously. | `test_two_simultaneous_dispatches_exactly_one_succeeds` | Exactly one `200`, one `409`. |
| B | Two receivers receive the same shipment simultaneously. | `test_two_simultaneous_receives_exactly_one_inventory_transfer` | Exactly one inventory transfer — the destination ends with exactly one set of new units, never doubled. |
| C | "Receipt vs. cancellation race." In this state machine, `CANCELLED` is reachable only from `CREATED` and `RECEIVED` only from `ARRIVED` — the two preconditions can never both hold on the same shipment at the same instant, so a literal receive-vs-cancel race cannot occur here *by construction*, not by omission. The closest genuine analogue — `CREATED`'s two outgoing edges, dispatch vs. cancel, raced against each other — is substituted and exercised instead. | `test_dispatch_vs_cancel_race_on_a_created_shipment` | Exactly one of {dispatch, cancel} wins; the loser gets a deterministic `409`; the resulting inventory state matches whichever one actually won. |
| D | The same limited pool of units claimed by two shipments created at once. | `test_two_concurrent_shipment_creations_cannot_both_claim_the_same_units` | Only one claim succeeds for the contended units — the DB-level partial unique index (Invariant 23) and/or `reserve_stock`'s own row lock prevent a double-claim. |
| E | A direct, non-shipment allocation racing a shipment creation over the same stock. | `test_direct_allocation_vs_shipment_creation_cannot_double_commit_stock` | No unit is ever simultaneously promised to both paths. |

## 13. Notifications

`notification_service.notify` is called at every real transition, to the party that doesn't already know what just happened (destination on create/dispatch/in-transit/arrive/delay; origin on receive; both on cancel/fail) — reusing the existing notification infrastructure, not a new channel. Types: `shipment.created`, `shipment.dispatched`, `shipment.in_transit`, `shipment.delayed`, `shipment.arrived`, `shipment.received`, `shipment.cancelled`, `shipment.failed`.

## 14. API surface

All under `/shipments`, all requiring authentication (`HOSPITAL` or `ADMIN`):

| Verb | Path | Purpose |
|---|---|---|
| POST | `/shipments` | Create (reserve units; idempotent — §11). |
| POST | `/shipments/{id}/dispatch` | `CREATED → DISPATCHED`; allocates units. |
| POST | `/shipments/{id}/in-transit` | `DISPATCHED → IN_TRANSIT`; derives `estimated_arrival_at`. |
| POST | `/shipments/{id}/delay` | Annotation only — §5. |
| POST | `/shipments/{id}/arrive` | `IN_TRANSIT → ARRIVED`. |
| POST | `/shipments/{id}/receive` | `ARRIVED → RECEIVED`; real ownership transfer. |
| POST | `/shipments/{id}/cancel` | `CREATED → CANCELLED`; releases reservation. |
| POST | `/shipments/{id}/fail` | `{DISPATCHED,IN_TRANSIT,ARRIVED} → FAILED`; does not release units (§8). |
| GET | `/shipments` | List, filterable by `status`/`direction` (`incoming`/`outgoing`), scoped to the caller's facility (or unscoped for admin). |
| GET | `/shipments/by-code/{shipment_code}` | Lookup by human-readable code. |
| GET | `/shipments/units/{unit_code}` | Unit-level trace (§10). |
| GET | `/shipments/{id}` | Detail. |
| GET | `/shipments/{id}/timeline` | Full `ShipmentEvent` history, chronological. |

## 15. Frontend

`frontend/src/pages/hospital/Shipments.tsx` — master/detail (`?shipment=<id>` query-param pattern, mirroring `DonorDirectoryAndLogistics`'s existing `?request=<id>` convention) hospital-facing UI: create, act on (buttons gated by `allowed_transitions`, never the sole guard — §7), and inspect a shipment's full timeline and unit list. Linked from `ResourcePathways.tsx`'s Logistics section. `frontend/src/pages/admin/AdminShipments.tsx` — a read-only admin oversight page reusing `AdminFacilities.tsx`'s shell pattern, over the exact same backend endpoints (no separate admin API — §7). Neither page exposes a blood-component selector; this frontend has no `BloodComponent` type/UI anywhere (a pre-existing gap from before this phase, not introduced here), so shipment creation defaults every line to whole blood, consistent with the rest of the current frontend's scope.

## 16. Known, disclosed limitations

- **No automatic recovery from `FAILED`** (§8) — by design, not oversight: this system cannot honestly auto-verify that failed-in-transit stock is safe to restore.
- **No live GPS/fleet tracking** — `estimated_arrival_at` is a one-time static estimate (§9), never a live position.
- **No blood-component selector in the shipment UI** — a pre-existing frontend gap (no `BloodComponent` type anywhere in the frontend as of Phase 35), not newly introduced; the backend itself fully supports per-component shipment lines.
- **A shipment references at most one `TransportRoute`**, chosen by the creator; nothing prevents choosing a route whose facility pair doesn't match the shipment's actual origin/destination pair — `_find_route` looks up a route by the shipment's own facility pair for ETA/display purposes regardless of which (if any) `transport_route_id` was supplied, so a mismatched explicit link affects only `transport_route_id`'s own stored value, never the ETA or route-status display shown to users, which always comes from the real facility pair.
