# LifeLink — Phase 34: Real Blood-Unit Lifecycle + Inventory State Machine

## 1. Objective

Replace the implicit "blood is available" assumption baked into `HospitalInventory`'s plain integer counters (`units`, `reserved_units`) with a formally defined, server-authoritative, per-unit `BloodUnit` lifecycle — a real state machine with a validated transition table, real concurrency safety, real shelf-life/expiry semantics, and a real audit trail — while changing zero bytes of the external API contract and breaking zero pre-existing tests. This is a domain-correctness phase layered underneath the existing, already-correct aggregate system, not a rewrite of it, not a frontend redesign, and not a license to invent medical rules this codebase has no authority to assert.

## 2. Current-state audit (before this phase)

`HospitalInventory` (one row per `(hospital_id, blood_type)`) held only `units` (on hand) and `reserved_units` (earmarked), mutated by `inventory_service.py`'s six actions (ADD/RESERVE/RELEASE/ALLOCATE/EXPIRE/RECONCILE), each one row-locked (`SELECT ... FOR UPDATE`) and each one logged to `HospitalInventoryLog` with a signed delta and a resulting-totals snapshot. This was already correct at the aggregate level — DB CHECK constraints enforced non-negativity and `reserved_units <= units`, and `tests/test_concurrency.py` already proved the row lock closes the double-reservation race at that level. What did not exist anywhere: a notion of an individual physical unit, its own age, its own expiry, or its own current state. "5 units available" was a number with no units behind it.

## 3. Existing inventory architecture (what this phase builds on, unchanged)

- `HospitalInventory` / `HospitalInventoryLog` (`app/models/hospital.py`) — untouched schema; every pre-existing column, constraint, and index is exactly as it was.
- `inventory_service.py`'s six action functions — same signatures, same validation order, same error messages, same return type (`HospitalInventory`), same `HospitalInventoryLog` entry per call.
- `graph_service.sync_inventory` — still mirrors `row.units` only; the per-unit layer is invisible to the facility graph by design (the graph models aggregate capacity for routing, not individual units).
- `app/api/routes/hospitals.py`'s `InventoryItem`/`_to_item` — `units`, `reserved_units`, `available_units = units - reserved_units` — byte-identical before and after this phase.
- Admin facility inventory views (`app/api/routes/admin.py` ~line 960-1075) — read `HospitalInventory` directly, bypassing `inventory_service` entirely; unaffected by this phase (see section 25's known limitation).

## 4. Domain gap identified

No per-unit identity, age, or lifecycle state existed. Concretely, before this phase: (a) there was no way to express "this specific unit is 34 days old and about to expire" — only "this hospital has 12 units of O_NEG"; (b) a reservation didn't claim a specific unit, so there was no way to prefer dispatching an older unit first (FEFO); (c) nothing ever expired automatically — stock only ever left via an explicit EXPIRE call a human remembered to make. Phase 31's own domain audit (`docs/LIFELINK_PHASE_31_DOMAIN_REPORT.md`, `docs/LIFELINK_PHASE_31_DOMAIN_MODEL.md`) explicitly considered per-unit blood tracking and declined it at the time, calling it "decorative complexity" without a real barcode/collection pipeline, and listed "no per-unit blood lifecycle" / "no blood component model" as documented LOW-severity limitations. Phase 34 is the later phase explicitly commissioned to close that gap — this report's design is deliberately scoped to what that audit found missing, not an invented superset.

## 5. Final blood-unit state machine

Four states: `AVAILABLE`, `RESERVED`, `ALLOCATED` (terminal), `EXPIRED` (terminal). Not the illustrative nine-state COLLECTED→TESTING→CLEARED→AVAILABLE→RESERVED→ALLOCATED→DISPATCHED→RECEIVED→TRANSFUSED pipeline the master prompt explicitly warned against blindly implementing. Every state below maps to a real, already-supported capability; no state was added because a generic blood-bank lifecycle sketch suggests it should exist.

- **AVAILABLE** — on hand, unreserved, selectable for a new reservation or a direct allocation.
- **RESERVED** — on hand, earmarked for one specific request/emergency, not yet physically dispatched.
- **ALLOCATED** — physically dispatched. Terminal — matches the pre-existing `allocate_stock` semantics, which already treats allocation as "left the building"; there is no un-allocate capability anywhere in the existing product.
- **EXPIRED** — permanently removed from the usable pool (natural shelf-life expiry, a manual EXPIRE, or a downward RECONCILE). Terminal.

States deliberately **not** implemented, and why (full reasoning also in `app/core/blood_unit_lifecycle.py`'s module docstring):

- **COLLECTED/TESTING/CLEARED/FAILED** — no lab-testing workflow exists anywhere in this application. A unit becomes AVAILABLE the moment a hospital runs the existing ADD action; there is no intermediate "awaiting clearance" state for software to track honestly without inventing a lab-results data model from nothing.
- **DISPATCHED/RECEIVED/TRANSFUSED** — ALLOCATE already means "physically dispatched" per `allocate_stock`'s own pre-existing docstring; there is no shipment-level receiving confirmation or transfusion-confirmation step anywhere in the product (confirmed by reading `app/models/transport.py` in full — `TransportRoute` is a facility-to-facility graph edge with `TransportRouteStatus.OPERATIONAL/DISRUPTED` only, not a per-shipment/per-unit dispatch entity). Claiming these states would mean inventing a workflow no UI, route, or data model backs.
- **CANCELLED/LOST/DAMAGED** — there is no un-allocate capability in the existing product (ALLOCATED has always had zero outgoing transitions), so these states have no real entry point to implement honestly this phase.
- **RELEASED** (as a separate state) — modeled instead as a `RESERVED → AVAILABLE` transition. The existing `HospitalInventoryAction.RELEASE` audit-log action already carries this distinction; a dedicated state would duplicate it without adding information.

## 6. Transition matrix

| From \ To | AVAILABLE | RESERVED | ALLOCATED | EXPIRED |
|---|---|---|---|---|
| **AVAILABLE** | — | ✓ (RESERVE) | ✓ (direct ALLOCATE) | ✓ (manual EXPIRE / lazy sweep) |
| **RESERVED** | ✓ (RELEASE) | — | ✓ (allocate-after-reserve) | ✓ (lazy sweep only — see below) |
| **ALLOCATED** | ✗ | ✗ | — | ✗ |
| **EXPIRED** | ✗ | ✗ | ✗ | — |

`RESERVED → EXPIRED` is reachable *only* via the lazy date-based sweep (`_sweep_expired`), never via the manual EXPIRE action, which — matching its pre-existing, preserved behavior — only ever targets unreserved stock. A reserved unit can still physically reach the end of its shelf life before being allocated; the sweep must not silently ignore that real event just because a reservation exists.

Encoded in `app/core/blood_unit_lifecycle.py`:

```python
TRANSITIONS: dict[S, frozenset[S]] = {
    S.AVAILABLE: frozenset({S.RESERVED, S.ALLOCATED, S.EXPIRED}),
    S.RESERVED: frozenset({S.AVAILABLE, S.ALLOCATED, S.EXPIRED}),
    S.ALLOCATED: frozenset(),
    S.EXPIRED: frozenset(),
}
```

A same-state "transition" (e.g. `AVAILABLE → AVAILABLE`) is always rejected — every real caller gates on the unit's actual current state before reaching `validate_transition`, so a same-state call indicates a stale read, not an intentional idempotent replay.

## 7. Transition authorization

No route accepts a client-supplied target state at all. Every `BloodUnit` transition is driven entirely by which pre-existing, already-authorized service action was called (`add_stock`/`reserve_stock`/`release_reservation`/`allocate_stock`/`expire_units`/`reconcile_inventory`), each reached only through the pre-existing `HOSPITAL`-role-gated routes in `app/api/routes/hospitals.py`. There is no new endpoint, and therefore no new input surface, for a caller to request a `BloodUnit`'s state directly — a malformed request attempting `{"state": "AVAILABLE"}` against any existing route has no field that reaches `BloodUnit.state` (verified by reading every route in `hospitals.py` that touches inventory; none deserializes a per-unit state from the request body). See section 22 for the deliberate absence of a dedicated "submit a raw state" test, and why.

## 8. Availability model

`available_units = units - reserved_units`, exactly as before. What changed underneath: `units` and `reserved_units` are no longer independent integers trusted at face value — they are a view that `inventory_service` keeps provably consistent with real per-unit `BloodUnit` rows, self-healing (section 13) and self-sweeping (section 9) on every touch. The external contract (`InventoryItem.units`/`reserved_units`/`available_units`) is byte-identical; no frontend change was needed (section 23).

## 9. Expiry behavior

Lazy, on-read-and-on-write — no new scheduler, matching the existing `blood_request_lifecycle.expire_if_stale` / `emergency_lifecycle.flag_deadline_exceeded_if_needed` pattern (this codebase has no scheduler/cron anywhere). `_sweep_expired` runs at the top of `_get_or_create_row`, which every one of the six actions calls, *and* is wired into the read-only `GET /hospitals/me/inventory` route via `sweep_all_expired` — a read must be as honest as a write; a unit that has genuinely passed its shelf life must not still be counted as available merely because no mutating action has touched its row since. `expires_at = received_at + settings.blood_unit_shelf_life_days` (default 35 days) for REAL-provenance units only; `LEGACY_BACKFILL` units get `expires_at = NULL` and are therefore never swept (their true age is unknowable — no shelf-life clock can be honestly started from an unknown collection date). The sweep commits immediately and durably the instant it finds anything (the "commit-and-relock" pattern — section 15/19) rather than only taking effect as a side-effect of some other action's successful commit.

The shelf-life number itself (`blood_unit_shelf_life_days = 35`) is explicitly documented in `app/core/config.py` as an operational parameter, not a cited clinical/regulatory standard — this phase did not verify a real medical source for it, consistent with the master prompt's explicit prohibition on inventing unsupported medical rules. It is a single, clearly-labeled, easily-changed setting; nothing in the lifecycle logic hardcodes the number.

## 10. Reservation model

`reserve_stock` now selects real `AVAILABLE` units (FEFO order — section 14) under the same row lock, up to the requested count, and transitions them to `RESERVED`, recording `reserved_for_request_id`/`reserved_for_emergency_id` so a later RELEASE/ALLOCATE for that same context can prefer exactly those units (section 14). The aggregate-level validation (`units > available_units(row)` → `InventoryActionError`) is unchanged; the per-unit selection happens only after that check passes.

## 11. Allocation model

Deliberately preserves the existing dual path: `AVAILABLE → ALLOCATED` (direct allocation, no prior reservation) *and* `RESERVED → ALLOCATED` (allocate-after-reserve) both remain real, first-class transitions — `allocate_stock`'s pre-existing docstring already states it "works whether or not a RESERVE happened first," and this phase does not force every allocation through a reservation step first, since that would change real, tested, pre-existing behavior for no domain-correctness gain. `allocate_stock` releases a matching reservation (up to the amount allocated) at the same time it allocates, exactly as before; per-unit, this now pulls first from `RESERVED` units matching the context (preferred) and then from plain `AVAILABLE` units for the remainder, both via FEFO-ordered row locks.

## 12. Dispatch/receipt integration

None implemented, deliberately (section 5) — there is no shipment-level dispatch/receipt entity in this codebase to integrate with (`app/models/transport.py`'s `TransportRoute` is a facility-graph edge, not a per-shipment record). ALLOCATED already represents "physically dispatched" for every existing purpose this product serves.

## 13. Donation-to-unit relationship

A donor's `Donation` record and a hospital's `BloodUnit` inventory remain two separate, non-linked concepts, exactly as they were before this phase (a `Donation` is a donor-side record of "I gave blood on this date, logged by/for this hospital"; a `BloodUnit` is a hospital-side inventory item created only via the explicit ADD action). This phase does not invent a traceability link between a specific donation and a specific physical unit — no such linkage existed in the data model, no route collects a per-donation unit identifier, and the master prompt's "minimal complete state machine that fits the actual system" instruction means this non-linkage is preserved honestly rather than papered over. Characterized explicitly by `tests/test_phase34_blood_unit_lifecycle.py`'s donation→unit non-linkage test, which asserts that logging a donation has zero effect on any `BloodUnit` row.

## 14. Audit/history design

No second history table. Each `BloodUnit` carries a `last_log_id` FK into the pre-existing `HospitalInventoryLog`, pointing at the log entry that most recently changed it — the existing audit system (already proven correct and already relied upon by every pre-existing test) is reused rather than duplicated with a parallel `BloodUnitTransition` table. `_sweep_expired` writes its own summarizing log entry (one row per sweep, not one per unit) with `resulting_units`/`resulting_reserved` corrected to the true post-sweep totals.

## 15. Concurrency analysis

Single serialization point: `SELECT ... FOR UPDATE` on the `HospitalInventory` row inside `_get_or_create_row`, exactly as before this phase — no separate per-`BloodUnit` lock scheme was introduced, because every per-unit mutation for a given `(hospital_id, blood_type)` pair is only ever reachable through that one row's lock first. Verified with real PostgreSQL and real `threading.Barrier`-synchronized `threading.Thread`s (never a mock), mirroring `tests/test_concurrency.py`'s established style:

- Double-reservation race: two threads concurrently try to reserve more total stock than exists; exactly one succeeds, the loser gets a real `409`, and per-unit state is never double-assigned.
- Double-allocation race: same shape, for `allocate_stock`.
- Release-vs-allocate race: one thread releases a reservation while another concurrently allocates against the same units; no unit is ever double-counted into both outcomes.
- Reservation-vs-lazy-expiry race: a unit sitting exactly at its expiry boundary, with one thread trying to reserve it while the sweep is concurrently trying to expire it — proves a unit is never reserved past its real shelf life (this race surfaced the one real production bug this phase found; section 19).

## 16. PostgreSQL locking strategy

`SELECT ... FOR UPDATE` on `HospitalInventory`, not on `BloodUnit` directly — deliberate, because every `BloodUnit` row is only ever selected/mutated after that parent lock is already held (`_select_units_for_update`'s own `.with_for_update()` is then uncontended in practice, since no other transaction can reach these specific units without first acquiring the same parent lock). This avoids a second, independent lock-ordering concern (which would raise real deadlock risk) in favor of one well-understood serialization point this codebase already relies on elsewhere.

## 17. API changes

None, by design. No new route, no changed request/response schema, no changed status code on any existing endpoint. `GET /hospitals/me/inventory` gained one internal side effect (`sweep_all_expired`, section 9) with no visible contract change beyond the numbers becoming honestly current rather than possibly stale.

## 18. Migration details

Revision `a9b0c1d2e3f4`, `down_revision = f7a8b9c0d1e2` (confirmed as the actual pre-Phase-34 head via `alembic current` and a grep of every `down_revision` value in `backend/alembic/versions/`). Creates `blood_units` (10 columns: `id`, `hospital_id`, `blood_type`, `state`, `provenance`, `received_at`, `expires_at`, `state_changed_at`, `reserved_for_request_id`, `reserved_for_emergency_id`, `last_log_id`, plus `created_at`/`updated_at` from the standard timestamp convention) and 6 indexes (`hospital_id`; `state`; the composite `(hospital_id, blood_type, state)` matching the actual `_select_units_for_update` query shape; `expires_at` for the sweep's range scan; `reserved_for_request_id`; `reserved_for_emergency_id`). References the existing shared `blood_type` enum with `create_type=False` + the full member list (the established convention from `a1b2c3d4e5f6`). Defines `blood_unit_state`/`blood_unit_provenance` as plain `sa.Enum(...)` locals inside `upgrade()`, letting `op.create_table`'s own default `create_type=True` create them — manually pre-creating them as well (an earlier draft's mistake; section 20) double-creates the type and fails, matching `d4e5f6a7b8c9`'s own documented precedent for this exact situation.

Backfill: for every pre-existing `(hospital_id, blood_type)` row with `units > 0`, inserts one `BloodUnit` row per unit via a Python-side `uuid.uuid4()` + SQLAlchemy Core `sa.insert()` (no `pgcrypto`/`gen_random_uuid()` dependency). Of each row's `units`, the first `reserved_units` backfilled rows are created `RESERVED` (matching the aggregate's own `reserved_units <= units` invariant) and the rest `AVAILABLE`; which *specific* units are "the reserved ones" is arbitrary, since the aggregate never recorded that distinction at the per-unit level either. Every backfilled row is `provenance=LEGACY_BACKFILL`, `received_at` = the migration's own run time (never a guessed earlier date), `expires_at = NULL`.

**Real-database verification** (dev DB `lifelink`, not a mock, not SQLite): `alembic upgrade head` → 536 pre-existing `HospitalInventory` rows (with `units > 0`) → 11,752 `BloodUnit` rows created, 0 inconsistencies found on a post-upgrade reconciliation check, 24 zero-unit rows correctly skipped. `alembic downgrade -1` → clean drop of the table, indexes, and both new enum types. `alembic upgrade head` again → clean re-upgrade, head confirmed back at `a9b0c1d2e3f4`. Upgrade→downgrade→upgrade cycle completed with no manual intervention.

## 19. Invariants

Full invariant list, each with its enforcement mechanism and verifying test, lives in `docs/LIFELINK_INVARIANTS.md` sections 13–19 (added this phase): state validity, valid-transitions-only, allocation safety, reservation safety, expiry safety, availability truth, and historical integrity. Not duplicated here in full to avoid the two documents drifting apart — see that file as the source of truth.

## 20. Discrete-mathematics representation

The blood-unit lifecycle as a directed graph `G_state = (S, T)`:

```
S = {AVAILABLE, RESERVED, ALLOCATED, EXPIRED}

T = {
  (AVAILABLE, RESERVED),
  (AVAILABLE, ALLOCATED),
  (AVAILABLE, EXPIRED),
  (RESERVED, AVAILABLE),
  (RESERVED, ALLOCATED),
  (RESERVED, EXPIRED),
}
                                    ┌──────────┐
                         ┌─────────▶│ ALLOCATED│ (terminal, out-degree 0)
                         │          └──────────┘
                         │               ▲
              RESERVE    │               │ ALLOCATE
        ┌───────────────▶│   ┌───────────┘
        │                │   │
  ┌───────────┐   ALLOCATE  ┌───────────┐
  │ AVAILABLE │◀────────────│ RESERVED  │
  └───────────┘   RELEASE   └───────────┘
        │                        │
        │ EXPIRE (manual/lazy)   │ EXPIRE (lazy sweep only)
        ▼                        ▼
                  ┌──────────┐
                  │ EXPIRED  │ (terminal, out-degree 0)
                  └──────────┘
```

Properties verified by `tests/test_phase34_blood_unit_lifecycle.py`'s pure transition-table tests: `|S| = 4`, `|T| = 6`; `ALLOCATED` and `EXPIRED` have out-degree 0 (terminal, matching `BLOOD_UNIT_TERMINAL_STATES`); every state is reachable from `AVAILABLE`; the graph is **not** strongly connected (no edge returns from either terminal state — a deliberate, enforced property, not an oversight); `RESERVED → EXPIRED` exists in `T` but is reachable in practice through exactly one code path (the lazy sweep), never through the manual EXPIRE action — a transition being present in the pure table does not imply every service-level caller is allowed to trigger it directly (see section 7's authorization note).

## 21. Performance/index analysis

Indexes were added based on the actual query shapes `inventory_service.py` issues, not speculatively: `ix_blood_units_hospital_type_state` matches `_select_units_for_update`'s `WHERE hospital_id = ? AND blood_type = ? AND state = ?` exactly (the hot path — every reserve/release/allocate/expire call); `ix_blood_units_expires_at` matches `_sweep_expired`'s `WHERE expires_at <= now()` range scan; the plain `hospital_id`/`reserved_for_request_id`/`reserved_for_emergency_id` indexes support `_reconcile_missing_units`'s aggregate count and the (not yet built, but plausible future) "find units reserved for this request" lookup. No formal before/after query-plan benchmark (`EXPLAIN ANALYZE`) was run this phase — the real-Postgres backfill of 11,752 rows and the full concurrency test suite completing in normal time (no multi-second stalls observed in any test run) is the evidence gathered, not a dedicated benchmark. Documented honestly as not done, rather than claimed without having run it.

## 22. Security review

No new endpoint exists, so no new direct input surface was added (section 7). The existing authorization gates on every inventory route (`HOSPITAL` role + the hospital's own facility, via the same `_hospital`/`get_or_create_hospital_profile` dependency every pre-existing inventory route already used) are unchanged and still apply identically. IDOR: a `BloodUnit.id` is never accepted as client input anywhere — there is no route of the shape `POST /blood-units/{id}/...` — so there is no object reference for a client to manipulate in the first place; the mass-assignment/arbitrary-state-injection concern the master prompt asks about (section 27/33) reduces to "can a client's JSON body reach `BloodUnit.state`," and the answer, confirmed by reading every inventory route handler in `hospitals.py`, is no — request bodies there only ever carry `units`/`blood_type`/`note`/`request_id`/`emergency_id`, never a state enum value. A dedicated "submit `{state: AVAILABLE}` directly" test was not written as a new test, since there is no endpoint shape that would make such a request meaningful (there is no field for it to deserialize into) — this is reported honestly as "verified by code inspection of the full route surface," not as "verified by a dedicated malformed-request test," per the master prompt's own directive to distinguish these two levels of evidence.

## 23. Frontend impact

None. `InventoryItem { blood_type, units, reserved_units, available_units }` (`frontend/src/lib/domain.ts`) and its consumption in `frontend/src/pages/hospital/InventoryConsole.tsx` are unchanged in shape and in value — confirmed by grepping every reference to `units`/`available`/`InventoryItem` across the frontend, and by `hospital_workflow.spec.ts`'s full Playwright suite (5/5 tests) passing unmodified, including its real-backend "inventory modal opens with real data and a saved change persists through the real backend" test. Zero frontend files were touched this phase.

## 24. Tests added

26 new tests in `backend/tests/test_phase34_blood_unit_lifecycle.py`:
- Pure transition-table unit tests (no DB) — every legal and illegal edge, terminality of `ALLOCATED`/`EXPIRED`.
- Per-unit-vs-aggregate consistency through the real HTTP API for add/reserve/release/allocate/expire/reconcile (one test per action, via a shared `_assert_consistent` helper).
- FEFO ordering — 2 tests (plain nearest-expiry-first; context-preference ordering for RELEASE/ALLOCATE).
- Lazy expiry — 4 tests (past-due sweep fires; a unit not yet past `expires_at` is left alone; a `RESERVED` unit can still expire; a `LEGACY_BACKFILL` unit (`expires_at = NULL`) is never swept).
- Self-healing reconciliation — 1 test, directly creating a `HospitalInventory` row via the ORM (bypassing the service, the same way some pre-existing Phase 12 test fixtures do) and confirming the next service call backfills honestly rather than rejecting.
- Donation→unit non-linkage — 1 characterization test (section 13).
- Concurrency — 4 tests, real PostgreSQL + real `threading.Barrier`-synchronized threads (section 15).

## 25. Full regression

- **Backend** (`pytest`, real PostgreSQL, real Redis): 878 passed, 1 skipped (pre-existing, unrelated to this phase), 0 failed, across 879 collected tests. Includes the 26 new Phase 34 tests and every pre-existing test file.
- **Frontend unit/component** (`vitest run`): 127 passed, 0 failed, across 14 test files.
- **TypeScript** (`npm run typecheck`, `tsc --noEmit`): clean, 0 errors.
- **ESLint** (`npm run lint`): clean, 0 errors/warnings.
- **Production build** (`npm run build`): succeeds (pre-existing chunk-size warning on `index-*.js`, unrelated to this phase and unchanged by it).
- **Playwright E2E** (`npx playwright test`, real backend + real built frontend, Redis flushed before the run to avoid Phase-33-documented rate-limiter contamination): 58 passed, 10 failed, 3 skipped. See section 26 for the full classification of every failure — none are caused by this phase's changes.

## 26. Known limitations (Section 35 failure classification)

All 10 Playwright failures were independently re-run in isolation (not only as part of the full suite) for clean reproduction evidence, per the master prompt's explicit "do not call something flaky without reproduction evidence" instruction. Every one reproduces identically in isolation. All 10 trace to a **single root cause**: this sandbox has no running Docker daemon (`docker ps` → "Cannot connect to the Docker daemon"), and the backend's object-storage backend is configured for S3/MinIO (`backend/.env`: `S3_ENDPOINT_URL=http://localhost:9000`), which only runs via `backend/docker-compose.yml`'s `minio` service. Every failure's real backend log line is identical: `storage put_bytes failed (backend=s3): EndpointConnectionError` / `storage ensure_bucket failed (backend=s3): EndpointConnectionError` (`/tmp/uvicorn.log`), confirmed to correlate in time with each isolated test run. `app/core/storage.py`'s `put_bytes` re-raises on any exception (never swallows it), so this becomes a real, honest `503`/exception at the route layer — exactly the behavior the application is supposed to have when its storage backend is down, not a bug introduced by this phase.

| # | Test | Failing assertion | Real cause | Classification |
|---|---|---|---|---|
| 1 | `document_storage_security.spec.ts` (A) donor uploads verification document | `docLink` never becomes visible — upload never completes | `/verifications` POST calls `storage.put_bytes` → `EndpointConnectionError` | **E** — environment (no MinIO) |
| 2 | `document_storage_security.spec.ts` (B) hospital uploads facility verification document | same shape as #1 | same | **E** |
| 3 | `document_storage_security.spec.ts` (C) admin downloads submitted document privately | document was never actually stored (upload failed upstream) | same | **E** |
| 4 | `document_storage_security.spec.ts` (E) upload failure shows honest error, retry succeeds | the *recovery* half of this test expects a retry to succeed once storage is back; storage is never back in this sandbox | same | **E** |
| 5 | `admin_notification_surface.spec.ts` bell shows new verification submission | `expect(submit.ok()).toBeTruthy()` fails — the `/verifications` POST itself fails | same storage call, different route (verification submission, not certificate) | **E** |
| 6 | `admin_verification_notifications.spec.ts` submission surfaces a notification | same assertion, same cause | same | **E** |
| 7 | `phase23_security_hardening.spec.ts` (E) certificate download requires real ownership | `expect(cert.ok()).toBeTruthy()` fails — `POST /certificates` fails | certificate issuance calls `storage.put_bytes` to store the generated PDF (`app/services/certificate_service.py:165`) → same `EndpointConnectionError` | **E** |
| 8 | `phase31_1_domain_signals.spec.ts` admin revokes a certificate | `expect(certRes.ok()).toBeTruthy()` fails — certificate creation fails before revocation can be tested | same certificate-issuance storage call | **E** |
| 9 | `phase31_1_domain_signals.spec.ts` public verify page axe scan | page under test never has a valid certificate to render (issuance failed in setup) | same | **E** |
| 10 | `phase31_1_domain_signals.spec.ts` donor milestones page axe scan (revoked cert present) | `expect(revokeRes.ok()).toBeTruthy()` fails — nothing to revoke, since issuance failed | same | **E** |

**Changed-file relationship**: `git status --short` shows exactly 9 files touched by this phase (5 modified: `hospitals.py`, `config.py`, `models/__init__.py`, `enums.py`, `inventory_service.py`; 4 new: the migration, `blood_unit_lifecycle.py`, `blood_unit.py`, the new test file). None of the 10 failing specs' code paths (`verifications.py`, `certificate_service.py`, `app/core/storage.py`, the document/certificate frontend pages) appear anywhere in that list. Independent, direct evidence: `hospital_workflow.spec.ts` — the one Playwright suite that *does* directly exercise this phase's inventory changes through the real UI — passed completely (5/5), including its real-backend "a saved change persists through the real backend" test.

**Classification: all 10 are E (environment/infrastructure)** — a pre-existing sandbox limitation (no Docker daemon, hence no MinIO) unrelated to this phase's changes, not A (caused by Phase 34), not B (an unrelated pre-existing application bug), not C (timing), not D (test-state/rate-limit contamination — the Redis-flush precaution from Phase 33 was applied before every run in this phase and did not change the outcome).

**Other known limitations, carried over honestly rather than hidden**:
- Admin facility inventory views (`admin.py`) read `HospitalInventory` directly, not through `inventory_service`, so they do not get the reconcile-and-sweep honesty benefit on every read — they can show a stale aggregate until some other route touches that row. No data-integrity risk; a documented staleness gap, not a correctness bug.
- `blood_unit_shelf_life_days = 35` is an operational placeholder, not a verified clinical/regulatory figure (section 9).
- No per-unit donation traceability (section 13) — unchanged from before this phase, and out of scope for it.
- No `EXPLAIN ANALYZE` performance benchmark was run (section 21).

## 27. Deferred to a future phase

Component-level blood tracking (red cells / plasma / platelets as distinct trackable items rather than one undifferentiated "unit"), a real donation-to-unit traceability chain (would require a barcode/collection-event data model that does not exist), shipment-level dispatch/receipt confirmation, and a verified, cited clinical shelf-life figure to replace the current operational placeholder. None of these were judged in scope for "the minimal complete state machine that fits the actual system" — each would require inventing a data model or workflow this product does not currently have any real entry point for.

## 28. Final quality-gate checklist

- [x] Repository actually inspected before design (sections 2–4); the illustrative 9-state pipeline was not blindly implemented.
- [x] Minimal, bounded state machine justified state-by-state against real existing capability (section 5).
- [x] Transition table is pure and DB-free, mirroring existing `*_lifecycle` modules.
- [x] Every transition enforced server-side; no client-supplied state ever reaches a `BloodUnit` row (sections 6–7, 22).
- [x] Real PostgreSQL concurrency testing with real threads, not mocks (sections 15, 24).
- [x] Alembic migration verified upgrade → downgrade → upgrade against the real dev DB (section 18).
- [x] No invented medical/clinical authority — the shelf-life figure is explicitly labeled a placeholder (sections 9, 26).
- [x] Zero external API contract changes; zero frontend changes (sections 17, 23).
- [x] Zero pre-existing tests broken; two real regressions found by the process itself were root-caused and fixed, not worked around (section 29).
- [x] Full regression suite run and its results honestly reported, including failures (sections 25–26).
- [x] Every Playwright failure classified with reproduction evidence, not labeled "flaky" without proof (section 26).
- [x] `docs/LIFELINK_INVARIANTS.md` updated with only invariants actually implemented and tested (section 19 / the invariants file itself).
- [x] This report follows the mandated structure and reports limitations honestly rather than omitting them.

## 29. Errors found and fixed during this phase (process record)

Three real, non-obvious correctness problems were found only through actually running things (never apparent from code review alone), each fixed at the root cause:

1. **Enum double-creation in the migration** — an early draft manually created `blood_unit_state`/`blood_unit_provenance` as module-level `postgresql.ENUM(..., create_type=True)` objects and called `.create(bind, checkfirst=True)` before `op.create_table`, which *also* auto-creates the same enum — `psycopg.errors.DuplicateObject`. Fixed by defining them as plain `sa.Enum(...)` locals inside `upgrade()` and letting `create_table`'s own default behavior create them, matching `d4e5f6a7b8c9`'s own documented precedent for exactly this situation.
2. **False "inventory inconsistency" errors against pre-existing tests** — running the pre-existing Phase 12 emergency/SOS suite revealed that its `_stock()` test helper directly mutates `HospitalInventory` via the ORM, bypassing `inventory_service` entirely, so no backing `BloodUnit` rows existed when a later `reserve_stock` call tried to select real units. Fixed with `_reconcile_missing_units` — self-healing, honest `LEGACY_BACKFILL` catch-up on every row fetch — rather than rewriting the many pre-existing test fixtures across the codebase that rely on this same shortcut.
3. **Lazy-sweep mutations silently rolled back** — found by this phase's own new concurrency test (`test_reservation_versus_lazy_expiry_race_...`), not by code review: when `_sweep_expired` ran inside the same transaction as a subsequent action that then failed its own validation (e.g. insufficient stock to reserve), the whole request's transaction was never committed (routes only commit on the success path), silently undoing the sweep's genuinely-true EXPIRE transition along with the failed action. Fixed with the "commit-and-relock" restructuring of `_get_or_create_row`: an independently-true side effect (sweep or reconciliation) is committed immediately, ending that transaction, and the row is re-fetched and re-locked fresh before the caller's own action runs against guaranteed-current numbers.

## 30. Verdict

**GO-WITH-LIMITATIONS.**

GO on everything this phase set out to build: a real, minimal, justified blood-unit state machine; server-authoritative transitions with no client input surface; real-Postgres concurrency safety including one genuine bug found and fixed by the process itself; a cleanly reversible migration verified against the real dev DB; zero external contract breakage; zero frontend changes needed; full backend/frontend regression green (878/878 non-skipped backend, 127/127 frontend unit/component, clean typecheck/lint/build).

WITH-LIMITATIONS because 10 Playwright E2E specs fail in this sandbox — all 10 independently reproduced, root-caused to a single pre-existing environment gap (no Docker/MinIO available here, affecting any test that uploads a document or issues a certificate), and confirmed structurally unrelated to any file this phase touched. This is an infrastructure limitation of the current sandbox, not a defect in this phase's work, and not something any further code change in this phase could fix without the object-storage backend being reachable.

---

*Changed files: `backend/app/api/routes/hospitals.py`, `backend/app/core/config.py`, `backend/app/models/__init__.py`, `backend/app/models/enums.py`, `backend/app/services/inventory_service.py` (modified); `backend/alembic/versions/a9b0c1d2e3f4_phase34_blood_unit_lifecycle.py`, `backend/app/core/blood_unit_lifecycle.py`, `backend/app/models/blood_unit.py`, `backend/tests/test_phase34_blood_unit_lifecycle.py` (new); `docs/LIFELINK_INVARIANTS.md` (extended).*
