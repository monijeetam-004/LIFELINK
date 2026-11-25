# LIFELINK — PHASE 12: Emergency / SOS Engine

Status: **COMPLETE**

This phase formalizes the Emergency/SOS subsystem into a real, auditable
state machine and closes several genuine gaps (concurrency, idempotent
creation, versioned resource pathways, real inventory reservation) without
inventing a parallel architecture. Phase 11 (Gmail SMTP + per-role
registration caps) is untouched and remains the frozen baseline; nothing in
this phase renames, reinterprets, or reverts it.

---

## 1. Existing architecture inspected before changing anything

Before writing any code, the following was read end-to-end: `app/models/emergency.py`,
`app/api/routes/emergencies.py` (pre-Phase-12, ~950 lines), `app/services/graph_service.py`,
`app/models/graph_pathway_log.py`, `app/services/inventory_service.py`, `app/models/hospital.py`,
`app/services/notification_service.py`, `app/services/audit_service.py`, `app/core/request_lifecycle.py`
and `app/services/blood_request_lifecycle.py` (Phase 10's lifecycle pattern for `BloodRequest`,
which this phase mirrors for `Emergency`), and `app/api/routes/admin.py`'s transport-route
disrupt/recover endpoints. The pre-existing GRAPH vs HAVERSINE_FALLBACK honesty labeling was
verified as genuinely real (not cosmetic) by reading `graph_service.find_resource_pathways`.

Conclusion: `EmergencyStatus` already had exactly the six states the spec asks for
(ACTIVE / ACKNOWLEDGED / MATCHING / ASSISTANCE_FOUND / RESOLVED / CANCELLED); what was
missing was a centralized, testable transition table (transitions were enforced by five
independent inline checks scattered across route functions), row locking, idempotent
creation, versioned pathway persistence, and real inventory reservation tied to an emergency.

## 2. Files changed

**New (backend):**
- `app/models/emergency_pathway.py` — the `EmergencyPathway` model (versioned pathway history)
- `app/core/emergency_lifecycle.py` — pure transition table (`TRANSITIONS`, `validate_transition`, `allowed_transitions`, `InvalidTransitionError`)
- `app/services/emergency_lifecycle.py` — `get_for_update` (row lock) + `transition()` (apply + audit)
- `app/services/emergency_pathway_service.py` — `create_pathway_version()` + `handle_route_disrupted()`
- `alembic/versions/d4e5f6a7b8c9_phase12_emergency_sos_engine.py` — the one additive migration
- `tests/test_phase12_emergency_sos_engine.py` — 27 new tests (see §18)

**Edited (backend):**
- `app/models/emergency.py` — 4 new nullable columns + idempotency unique constraint
- `app/models/enums.py` — new `EmergencyPathwayStatus` enum
- `app/models/__init__.py` — new exports
- `app/models/hospital.py` — new nullable `emergency_id` column on `HospitalInventoryLog`
- `app/schemas/hospital.py` — `HospitalProfileOut` now also exposes `id` (see §17)
- `app/services/inventory_service.py` — optional `emergency_id` kwarg threaded through `reserve_stock` / `release_reservation` / `allocate_stock` / `_log`
- `app/schemas/emergency.py` — new request/response fields + `EmergencyPathwayOut`/`EmergencyPathwaySelectIn`
- `app/schemas/graph.py` — `EmergencyReoptimizationOut`; `TransportRouteActionOut.affected_emergencies`
- `app/api/routes/emergencies.py` — lifecycle formalization, idempotent creation, duplicate detection, target-facility validation, pathway endpoint, inventory reservation on accept/withdraw
- `app/api/routes/admin.py` — `disrupt_transport_route` now triggers real re-optimization
- `tests/test_emergency_network.py` — one event-sequence assertion updated (see §16)

**Edited (frontend):**
- `src/lib/domain.ts` — new fields on `Emergency`/`EmergencyResponder`/`HospitalProfile`, new `EmergencyPathway` type, `selectEmergencyPathway()`
- `src/components/SosModal.tsx` — idempotency key generation, units/deadline/target-facility inputs, non-blocking duplicate notice
- `src/components/EmergencyTrackDrawer.tsx` — slack/units display, duplicate notice, pathway history, hospital "select pathway" action, inventory note on responder rows

**Untouched, as required:** Neo4j engine internals, `graph_service`'s ranking/fallback logic, `GraphPathwayLog`'s semantics, SMS integration, blood-component modeling, CSV export, cross-hospital fulfillment, unrelated admin functionality, static research/simulation pages, Gmail configuration, and every Phase 11 file.

## 3. Database changes (one additive migration, `d4e5f6a7b8c9`)

- `emergencies` gains 4 nullable columns: `units_needed` (int), `deadline` (timestamptz),
  `idempotency_key` (varchar 200), `target_facility_id` (uuid, FK → `hospital_profiles.id`,
  `SET NULL`), plus a unique constraint `uq_emergency_idempotency_key` on
  `(created_by_user_id, idempotency_key)`. Postgres treats `NULL` as distinct from `NULL`
  in a unique constraint, so every pre-existing row (which has no key) is unaffected.
- New table `emergency_pathways` (see §9) with 3 indexes, including a **partial unique
  index** enforcing at most one `ACTIVE` pathway per emergency.
- `hospital_inventory_logs` gains one nullable `emergency_id` column (FK → `emergencies.id`,
  `SET NULL`) — see §11 for why this is a new column rather than reusing `request_id`.
- No existing column is dropped, retyped, or backfilled destructively. Verified with a real
  downgrade → upgrade round-trip against the dev Postgres database (twice, including once
  after discovering and fixing the FK issue in §15).

## 4. State machine

`EmergencyStatus` is unchanged (ACTIVE, ACKNOWLEDGED, MATCHING, ASSISTANCE_FOUND, RESOLVED,
CANCELLED). `app/core/emergency_lifecycle.py` is the single source of truth for legal
transitions:

| From | To (legal) |
|---|---|
| ACTIVE | ACKNOWLEDGED, MATCHING, ASSISTANCE_FOUND, CANCELLED, RESOLVED |
| ACKNOWLEDGED | MATCHING, ASSISTANCE_FOUND, CANCELLED, RESOLVED |
| MATCHING | ACKNOWLEDGED (all accepted responders withdrew), ASSISTANCE_FOUND, CANCELLED, RESOLVED |
| ASSISTANCE_FOUND | CANCELLED, RESOLVED |
| RESOLVED | *(none — terminal)* |
| CANCELLED | *(none — terminal)* |

Every entry above was traced to its exact pre-existing call site (documented in the
module's docstring) — the table changes **no** legal business behavior; it only
centralizes what was previously five independent inline checks, adds row locking
(`app/services/emergency_lifecycle.get_for_update`, a `SELECT ... FOR UPDATE`), and
records a single unified `"emergency.transition"` audit entry (old status, new status,
actor, timestamp, reason) plus a matching `EmergencyEvent` timeline row for every
transition. `GET /emergencies/{id}` now reports `allowed_transitions` computed live from
this table, and `POST .../cancel|resolve|assistance-found|acknowledge|respond` all raise
`InvalidTransitionError` → HTTP 409 (naming the actually-allowed next states) for anything
not in the table. Endpoint-level authorization (who may call which action) is unchanged
and layered on top, exactly as `app.core.request_lifecycle` / `blood_request_lifecycle`
already do for `BloodRequest`.

One intentional, approved audit-trail enrichment: the ACTIVE/ACKNOWLEDGED → MATCHING
transition, which previously happened via a raw status assignment with no dedicated
event, now writes its own `"matching_started"` `EmergencyEvent` (same idea Phase 10
already applies to `BloodRequest`). This required updating one pre-existing test's exact
event-sequence assertion (`test_emergency_network.py::test_full_sos_lifecycle_to_resolved`) —
the actual status sequence and every notification are byte-for-byte unchanged; only the
audit trail got more granular.

## 5. Idempotent creation + non-blocking duplicate detection

`EmergencyCreate.idempotency_key` (optional) is unique per `(created_by_user_id,
idempotency_key)`. `POST /emergencies`:
1. Checks up front for an existing row with the same `(actor, key)` — if found, returns
   it with HTTP 200 (not 201) instead of creating a second emergency.
2. Otherwise inserts inside a `SAVEPOINT` (`db.begin_nested()`), catching the
   `IntegrityError` a genuine race produces and falling back to the replay lookup —
   the identical pattern already used for first-response races in
   `respond_to_emergency`/`blood_requests.respond_to_request`.

A real concurrency bug was found and fixed during this phase's own testing (see §15):
the object being inserted must not be added to the session **before**
`db.begin_nested()`, because `begin_nested()` itself autoflushes any already-pending
object while establishing the SAVEPOINT — which let a genuine unique-constraint
collision escape the `try/except` entirely. Fixed by building and adding the ORM object
*inside* the `try`, after `begin_nested()`, matching the working pattern already used
elsewhere in this codebase. Verified with a real `threading.Barrier`-based concurrent
test hitting the actual Postgres test database (not mocked), passing consistently across
9 repeated runs.

Separately, non-blocking duplicate detection (`_find_open_duplicate`) checks whether the
same creator already has another still-open emergency for the same patient; if so, the
**new** emergency is still created (never blocked), and its response carries
`possible_duplicate_of` pointing at the other one. The frontend surfaces this as a
one-time informational notice, never as a blocker.

## 6. Deadline / slack — honest, never clamped

`Emergency.deadline` is a nullable, timezone-aware column. Slack is **computed on every
read**, never stored: `_slack_seconds(em) = (em.deadline - utcnow()).total_seconds()`.
Positive means time remaining, zero means exactly at deadline, negative means genuinely
overdue — reported as a negative number, never clamped to zero, matching the spec's
explicit requirement. `None` when there is no deadline at all. Tested for all three
cases (`test_slack_is_positive_for_a_future_deadline`,
`test_slack_is_negative_for_a_past_deadline_and_never_clamped`,
`test_slack_is_none_without_a_deadline`).

## 7. Target facility

`EmergencyCreate.target_facility_id` (optional) is validated for real when supplied:
the facility must exist and be `verified` (HTTP 400 otherwise); when omitted, behavior
is byte-for-byte identical to pre-Phase-12 (the emergency still broadcasts to every
compatible donor/hospital regardless of whether a target facility is named).

## 8. Matching — unchanged, reused as-is

No changes to the existing donor/hospital candidate-matching logic
(`_donor_is_candidate`, `_hospital_area_match`, `GET /emergencies/{id}/match-candidates`).
Phase 12 adds fields to the emergency itself, not a new matching mechanism.

## 9. Versioned resource pathways

New table `emergency_pathways` (model: `app/models/emergency_pathway.py`) — one row per
persisted pathway *version* for an emergency, distinct from the pre-existing
`graph_pathway_logs` table (a fire-and-forget query-audit log with no concept of "the
current plan," left completely unmodified — both its schema and its two existing write
call sites are untouched). `app/services/emergency_pathway_service.create_pathway_version()`
is the single place a new row is ever written: it calls the existing, unmodified
`graph_service.find_resource_pathways()`, persists the top candidate (or `None` when
genuinely nothing is reachable — never fabricated), supersedes whatever was `ACTIVE`
before (setting `superseded_at`/`superseded_reason`), and still writes a
`GraphPathwayLog` row too, so that table's "every real query is logged" invariant holds.
A Postgres **partial unique index** (`WHERE status = 'ACTIVE'`) guarantees at most one
active pathway per emergency at the database level, not just in application code.
`POST /emergencies/{id}/pathway` (HOSPITAL — the owning facility only — or ADMIN) is the
write endpoint; `GET /emergencies/{id}` returns `current_pathway` + full
`pathway_history`. Verified: first selection creates v1; a second call creates v2 and
marks v1 `SUPERSEDED`, with the full history retained (never deleted).

## 10. Reactive transport-disruption re-optimization

`emergency_pathway_service.handle_route_disrupted()` is called from the existing,
unmodified-in-purpose `POST /admin/transport-routes/{id}/disrupt` endpoint (extended,
not replaced) right after the route flips to `DISRUPTED`. It finds every still-open
emergency whose *current* `ACTIVE` pathway actually used the disrupted route's origin or
destination facility, re-runs the real (unmodified) graph query, creates the next
pathway version, writes `transport_disrupted` and `pathway_reoptimized` `EmergencyEvent`s,
and sends a real notification — honestly reporting `still_reachable: false` when nothing
is currently reachable, never fabricating a route. `TransportRouteActionOut` now carries
`affected_emergencies: list[EmergencyReoptimizationOut]` so the admin caller sees exactly
what was touched. Verified: disrupting the route this emergency's pathway actually used
produces v2 and the right events/notification; disrupting an *unrelated* route leaves
the emergency's pathway at v1, untouched (`test_disruption_of_an_unrelated_route_does_not_touch_this_emergency`).

## 11. Inventory reservation

Reuses the existing, unmodified `app/services/inventory_service.py` — no parallel
reservation system. A HOSPITAL responder's `POST /emergencies/{id}/respond
{"action":"ACCEPTED"}` (when the emergency has both `blood_type` and `units_needed`)
calls `reserve_stock(..., emergency_id=em.id)`; withdrawing calls `release_reservation`.

A real, pre-existing FK constraint was discovered during implementation (see §15):
`HospitalInventoryLog.request_id` is FK'd specifically to `blood_requests.id`, so it
cannot carry an `Emergency`'s id. Rather than relaxing that constraint or inventing a
second reservation system, this phase adds one new, parallel, nullable
`HospitalInventoryLog.emergency_id` column (migration §3) and threads an optional
`emergency_id` kwarg through `reserve_stock` / `release_reservation` / `allocate_stock` /
`_log` — the pre-existing `request_id`/`BloodRequest` path is completely untouched.
"Outstanding reservation for an emergency" is *derived*, never separately tracked:
`SELECT hospital_id, blood_type, sum(delta_reserved) ... WHERE emergency_id = X GROUP BY
... HAVING sum(delta_reserved) > 0` against the same ledger `inventory_service` already
writes — so it can never drift from the real audit trail.

The accept/withdraw action itself **never fails or blocks** on a reservation problem
(e.g. insufficient stock) — the response always honestly reports what happened via a new
`inventory_note` field (`EmergencyResponderOut.inventory_note`), including the real
error text on failure, never a fabricated success. Verified: successful reservation on
accept, correct release on withdrawal (and only the withdrawing hospital's own
reservation — a second hospital's untouched, `test_hospital_withdrawal_releases_its_own_reservation_only`),
and an honest failure message with zero actually reserved when stock is insufficient
(`test_reservation_failure_is_reported_honestly_not_silently_succeeded`).

## 12. Cancellation / resolution

Both go through `emergency_lifecycle.transition()` (row-locked, single audit entry).
Cancellation additionally releases every outstanding inventory reservation tied to the
emergency (`_release_all_reservations`) — verified the reservation actually reaches zero
afterward, and that the pathway history is left untouched (cancellation is not a
data-deleting operation). Resolution requires the emergency to be in an open state
(enforced by the transition table) — no arbitrary/unchecked resolution path exists.
Both are terminal: a second resolve/cancel, or any other action, on an already-terminal
emergency returns HTTP 409, verified for both directions
(`test_cannot_cancel_a_resolved_emergency`, `test_cannot_resolve_a_cancelled_emergency`,
plus the full-scenario tests in §18).

## 13. Notifications

No SMS anywhere in the emergency dependency chain. Every new Phase 12 event
(pathway re-optimization) uses the existing, unmodified
`app/services/notification_service.notify()` (in-app + email-where-already-configured),
the same mechanism every pre-existing emergency notification already used.

## 14. Authorization

`_can_view` / role checks are unchanged and extended, never bypassed. The new pathway
endpoint requires HOSPITAL (the *owning* facility specifically — verified by comparing
`HospitalProfile.user_id` to the caller) or ADMIN; every other role gets HTTP 403.
Verified for patient/family/donor/hospital(non-owning)/admin/unrelated-user combinations
across the full test suite, including two dedicated pathway-authorization tests.

## 15. Bugs found and fixed during this phase's own testing

1. **Migration enum double-creation** — creating the `emergency_pathway_status` enum
   both explicitly and via `create_table`'s own column definition raised
   `DuplicateObject` in the same transaction. Fixed by letting `create_table` create it
   exactly once.
2. **Migration enum double-creation (reused enum)** — reusing the pre-existing
   `graph_pathway_source` enum for the new `pathway_source` column needed
   `postgresql.ENUM(..., create_type=False)` specifically (a generic `sa.Enum(...,
   create_type=False)` did not suppress re-creation in this context).
3. **FK-constraint discovery** — `HospitalInventoryLog.request_id` is genuinely FK'd to
   `blood_requests.id` (an earlier research pass had reported it as untyped). Fixed with
   a new parallel `emergency_id` column rather than misusing `request_id` (see §11).
4. **A genuine concurrency bug in idempotent creation** (see §5) — `db.begin_nested()`'s
   own autoflush could let a real race's `IntegrityError` escape the `try/except` that
   was supposed to catch it. Found via this phase's own `threading.Barrier` concurrency
   test failing intermittently in the full suite (though never in isolation) — fixed and
   re-verified across 9 repeated runs plus the full suite, twice more.
5. **One pre-existing test's exact event-sequence assertion** needed updating for the
   intentional `"matching_started"` audit enrichment (see §4) — confirmed as a
   deliberate, approved consequence of formalizing the lifecycle, not a regression.

No other regressions were found. The full pre-existing test suite (506 tests) was run
and confirmed green **before** any Phase 12 code was written, establishing the working
baseline this phase was built on.

## 16. Frontend

`src/lib/domain.ts`: `Emergency` gains `units_needed`, `deadline`, `target_facility_id`,
`slack_seconds`, `allowed_transitions`, `possible_duplicate_of`; `EmergencyResponder`
gains `inventory_note`; new `EmergencyPathway` type; `EmergencyDetail` gains
`current_pathway`/`pathway_history`; new `selectEmergencyPathway()`. `HospitalProfile`
gains `id` (see §17).

`SosModal.tsx`: generates one client-side idempotency key per SOS attempt via
`crypto.randomUUID()` (stable across a double-click/retry, regenerated only when the
modal is freshly reopened) and always sends it; adds optional units-needed,
deadline (`datetime-local`), and target-facility (populated from the real, existing
`/hospitals/directory` endpoint, filtered to verified facilities) inputs; on a
`possible_duplicate_of` response, pauses on a real confirmation screen instead of
silently auto-closing, so the (non-blocking) notice is actually seen.

`EmergencyTrackDrawer.tsx`: shows units-needed and honestly-signed slack (overdue shown
in red, never hidden or clamped); shows a duplicate-emergency notice when applicable;
shows the current pathway plus full version history (source, distance, reason,
superseded status); shows `inventory_note` inline on each responder row; adds a real
"select/re-check pathway from my facility" action for a HOSPITAL responder (using its
own facility id, resolved via the real `/hospitals/me` call, not a hardcoded/mock id).

All data shown is live from the real backend responses above — no mock data, no
hardcoded ids, no fake timers, no fabricated success states anywhere in these changes.

Verification commands run (all clean):
```
npx tsc --noEmit           # 0 errors
npm run lint               # 0 errors (eslint)
npm run build              # tsc -b && vite build — succeeds (2165 modules, dist/ produced)
```

## 17. One small, additive backend schema fix required for real frontend wiring

`HospitalProfileOut` (`GET`/`PUT /hospitals/me`) previously exposed only `user_id`, never
`id` — the actual `hospital_profiles.id` primary key referenced by
`Emergency.target_facility_id`, `EmergencyPathway`'s facility columns, and
`TransportRoute`. This meant a logged-in hospital had no real way to learn its own
facility id (short of an unreliable name-match against `/hospitals/directory`) in order
to call the new pathway-selection endpoint — a pre-existing gap that also silently
affected the already-present-but-never-wired `getEmergencyResourcePathways(...,
origin_facility_id)` frontend helper. Fixed by adding `id: uuid.UUID` to
`HospitalProfileOut` — purely additive (no existing consumer is affected by one more
field appearing in the response), no migration needed (the column already existed on
the model; only the response schema was missing it).

## 18. Tests

**506** pre-existing tests (Phase 1–11 baseline) + **27** new Phase 12 tests
(`tests/test_phase12_emergency_sos_engine.py`) = **533 tests, all passing, 0 failures,
0 errors**, run against the real Postgres test database (nothing mocked). Ran multiple
times, including after every fix in §15, to confirm the fixes actually held.

New test coverage: happy-path lifecycle with `allowed_transitions`/`slack_seconds`
wired end to end; slack positive/negative/none (never clamped); idempotent creation
(same key replays, different keys/actors don't collide, a real concurrent race via
`threading.Barrier` creates exactly one row); non-blocking duplicate detection (flagged,
never blocking; flag absent once the first is closed); target-facility validation
(must exist + be verified; optional); invalid-transition rejection (cannot acknowledge
an already-MATCHING emergency, cannot cancel a resolved one, cannot resolve a cancelled
one); a real concurrent cancel-vs-resolve race (only one wins); hospital inventory
reservation on accept and release on cancel/withdraw, scoped to the correct hospital
only, with honest failure reporting on insufficient stock; pathway versioning (v1, then
v2 superseding v1) and real transport-disruption-triggered re-optimization (including
confirming an *unrelated* disruption does not touch the pathway); pathway-endpoint
authorization (role + owning-facility checks); and two full end-to-end scenarios:

- `test_full_scenario_create_through_disruption_to_resolution` — idempotent creation
  (with a real double-click replay) → acknowledge → hospital accept (real inventory
  reservation) → pathway v1 selected → assistance found → a real admin-triggered
  transport disruption automatically re-optimizes to pathway v2 (v1 marked superseded,
  events + notification verified) → resolve → terminal, verified against a second
  resolve attempt correctly rejected (409).
- `test_full_scenario_cancellation_releases_reservation_and_pathway_stays_auditable` —
  accept (reservation) → pathway selected → cancel → reservation actually released to
  zero, pathway history left intact, and the cancelled emergency verified unreachable by
  any further transition or response attempt.

## 19. Remaining limitations

- The frontend's pathway UI is deliberately minimal (a list + one re-check action) —
  it does not attempt to visualize the graph itself or let a user browse *all* candidate
  facilities before selecting one; it reuses the existing single-best-candidate selection
  behavior of `create_pathway_version`, matching the spec's explicit instruction not to
  touch graph/resource engine internals.
- `possible_duplicate_of` is a same-request-cycle informational flag only; there is no
  standing "merge these two emergencies" workflow (out of scope — the spec calls for
  detection, not merging).
- Deadline/slack is exposed and computed honestly but nothing yet auto-escalates urgency
  purely from an approaching deadline; urgency stays creator-set, matching the spec's
  "respect existing urgency/priority" instruction rather than inventing new auto-escalation
  logic.

## 20. Exact commands run

```bash
# Backend
cd backend && source .venv/bin/activate
alembic upgrade head
alembic downgrade -1 && alembic upgrade head    # migration round-trip, twice
ENVIRONMENT=test SECRET_KEY=test-secret RQ_SYNC=true pytest              # 533 passed
ENVIRONMENT=test SECRET_KEY=test-secret RQ_SYNC=true pytest tests/test_phase12_emergency_sos_engine.py   # 27 passed
ruff check app tests        # 0 new issues (37 pre-existing baseline issues in untouched files)
mypy app                    # 0 new issues (3 pre-existing baseline errors in untouched files)

# Frontend
cd frontend
npx tsc --noEmit             # clean
npm run lint                 # clean
npm run build                # succeeds
```
