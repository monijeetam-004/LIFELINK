# LifeLink — Phase 10 Report: Production Blood Request State Machine

## 0. Scope and approach

Phase 10 asked for a strict, server-side blood request lifecycle: an
explicit transition table (no arbitrary status changes), a defined
authorization matrix per transition, concurrency protection (two hospitals
fulfilling the same request, two users cancelling simultaneously, duplicate
submissions, repeated allocation), a full audit trail (old state, new
state, actor, timestamp, reason, request ID), a consistent API shape
(current status, allowed transitions, timestamps, relevant actors), and
tests for every valid/invalid transition, concurrency, duplicates, expiry,
and cancellation-after-fulfilment.

As with every prior phase: audit first, then implement for real, preserving
every genuine pre-existing behavior rather than silently narrowing it to
match the spec's example table, and disclosing scope decisions rather than
fabricating coverage.

## 1. Audit findings

**The states already existed.** `BloodRequestStatus` (`app/models/enums.py`)
already had exactly the five states the spec suggests — PENDING, MATCHING,
FULFILLED, CANCELLED, EXPIRED — and the Postgres `blood_request_status` enum
type has carried all five since the very first migration (`0002_phase2_domain.py`).
Nothing needed adding to the enum. What was missing: EXPIRED existed in the
type system but nothing in application code ever set it — it was dead,
unreachable state.

**Transitions were real but scattered, not centralized.** Three different
call sites each mutated `BloodRequest.status` directly, with their own
copy-pasted "is this open?" guard and no shared table:
`blood_requests.py::respond_to_request` (PENDING→MATCHING on donor accept,
and an undocumented MATCHING→PENDING "reopen" when every accepted donor
withdrew), `blood_requests.py::fulfill_request` and
`blood_requests.py::cancel_request` (→FULFILLED / →CANCELLED from either
open state), and `hospitals.py::fulfill_from_hospital` (→FULFILLED directly
from PENDING, with no donor ever having accepted — confirmed real and
tested via `test_hospital_allocate_and_fulfill_request`, which never routes
through MATCHING at all). Two of these were not in the spec's example
transition table (`PENDING→FULFILLED` direct-from-hospital, and
`MATCHING→PENDING` reopen) but are genuine, tested, useful behavior — per
"preserve existing states if the repository already has a stronger model,"
both are kept and formalized as real table entries, documented in code as to
exactly which pre-existing call site each one traces to.

**No locking anywhere on the request row.** `db.get(BloodRequest, id)` was a
plain read with no `FOR UPDATE` on every one of these mutation paths, and
`hospitals.py::allocate_to_request` had the identical gap. This is precisely
the "two hospitals fulfilling the same request" / "repeated allocation" race
the spec names — a real, unaddressed gap, not a hypothetical one.

**`app/services/inventory_service.py::_get_or_create_row`** (used by all six
inventory-mutating functions: add/reserve/release/allocate/expire/reconcile)
also had no locking — a second, independent concurrency gap: two concurrent
`/allocate` calls against the same hospital stock could each read the same
pre-mutation `units`/`reserved_units`, each pass their own availability
check, and both deduct, oversubscribing stock despite each check looking
correct in isolation.

**Two separate, pre-existing audit trails**, both worth keeping distinct:
`BloodRequestEvent` (append-only, user-facing — shown in the request's
`events` timeline) and `AuditLog` via `audit_service.record()`
(admin-facing compliance trail, queryable at `GET /admin/audit-log`). Before
this phase, three different `AuditLog` action strings existed for what are
really the same kind of event (`"blood_request.cancelled"`,
`"blood_request.fulfilled"`, plus no entry at all for the MATCHING/PENDING
flips) — confirmed via search that nothing depends on those specific old
action strings, so they were safe to consolidate into one unified
`"blood_request.transition"` action carrying `old_status`/`new_status`/
`reason` in `meta`. Separately, and *not* touched: `notification_service.notify(...,
type="blood_request.cancelled"/"blood_request.fulfilled", ...)` — these
`Notification.type` strings feed the SMS template router
(`app/services/sms/templates.py`) and are a completely different mechanism
from the AuditLog action names that happened to share a similar spelling.

**The existing "lazy expiry" pattern.** There is no scheduled job anywhere
in this system. `FamilyLink` already handles its own time-based expiry the
same way (`_expire_if_stale` in `app/api/routes/family.py`): check on every
read/action, flip and commit if stale. Phase 10 reuses this exact pattern
for `BloodRequest`, keyed off the existing `needed_by` field, rather than
inventing a scheduler this codebase has never had.

## 2. The formalized state machine

**New: `app/core/request_lifecycle.py`** — pure, DB-free, in the same style
as `app.core.blood_compatibility` / `app.core.matching_engine` /
`app.core.eligibility`. It owns the transition table and nothing else:

```python
TRANSITIONS = {
    PENDING:   {MATCHING, FULFILLED, CANCELLED, EXPIRED},
    MATCHING:  {PENDING, FULFILLED, CANCELLED, EXPIRED},
    FULFILLED: {},
    CANCELLED: {},
    EXPIRED:   {},
}
```

`validate_transition(current, target)` raises `InvalidTransitionError`
(routes turn this into HTTP 409) for anything not in the table — including
a same-state "transition," which is always rejected rather than treated as
an idempotent no-op, since every real caller already gates on
`status in OPEN_STATUSES` before reaching here. `allowed_transitions(current)`
is the same function the API exposes to clients (§7) — one source of truth
for "what can happen next" used by both server-side validation and the
client-facing field.

**New: `app/services/blood_request_lifecycle.py`** — the DB-touching half.
`get_for_update()` fetches the row with `SELECT ... FOR UPDATE` held for the
rest of the transaction. `transition()` validates against the table, applies
the status change plus the matching timestamp column
(`fulfilled_at`/`cancelled_at`/`expired_at`), and writes both audit trails
in one call — a `BloodRequestEvent` and one unified `AuditLog` entry (old
state, new state, actor, timestamp, reason, request ID, plus an optional
`extra_meta` merge-in so per-caller detail like `donations_recorded` isn't
lost by the consolidation described in §1). `expire_if_stale()` is the lazy
expiry check, called at the top of every route that fetches a request.

## 3. State transitions

| From | To | Trigger | Status |
|---|---|---|---|
| PENDING | MATCHING | A compatible donor accepts | Pre-existing, now audited as a transition |
| PENDING | FULFILLED | A hospital fulfills directly from inventory, no donor ever accepted | Pre-existing, preserved (not in the spec's example table) |
| PENDING | CANCELLED | Requester cancels before any donor responds | Pre-existing |
| PENDING | EXPIRED | `needed_by` passes with the request still open | **New** — the status existed, this path didn't |
| MATCHING | FULFILLED | Normal donor-matched completion, or a hospital fulfills after a donor accepted | Pre-existing |
| MATCHING | CANCELLED | Requester cancels after a donor accepted | Pre-existing |
| MATCHING | PENDING | Every accepted donor withdraws — the request reopens | Pre-existing, preserved (not in the spec's example table) |
| MATCHING | EXPIRED | `needed_by` passes while still matching | **New**, same mechanism as PENDING→EXPIRED |
| FULFILLED / CANCELLED / EXPIRED | — | (none) | Terminal — no outgoing transition |

No other transition is reachable; `validate_transition` rejects everything
else, including cancelling a fulfilled request, fulfilling a cancelled one,
and any action at all on an expired one (verified in tests — §8).

## 4. Authorization

| Actor | Action | Enforcement |
|---|---|---|
| Patient / accepted Family manager | Create a request; cancel their own request (PENDING or MATCHING) | `_can_manage` — requester, patient, or an accepted `FamilyLink` for that patient |
| Donor | Accept/decline/not-now (PENDING↔MATCHING) | `require_roles(DONOR)` + blood-type compatibility + eligibility checks |
| Hospital | Allocate inventory against, and fulfill, requests visible to them (same city or same named facility) | `require_roles(HOSPITAL)` + `_get_visible_request`/`_visible_request_filter` |
| System | Expire a stale request (`needed_by` passed) | No actor — `expire_if_stale`, `actor_user_id=None`, triggered lazily on any read/action, never directly callable by a client |
| Admin | Force-cancel or force-expire a request (moderation, duplicate, policy call) | `require_roles(ADMIN)` via the new `POST /admin/blood-requests/{id}/transition`, restricted to CANCELLED/EXPIRED targets only (see §7) |

An admin cannot force MATCHING or FULFILLED — those states assert a real
donor match or a real fulfilment happened, which an admin action can't
manufacture on someone else's behalf; that request is rejected with 400
before the state machine is even consulted (a 409 is reserved for "not
reachable from the current state").

## 5. Concurrency

Every mutation path that can change `BloodRequestStatus` now fetches the row
via `blood_request_lifecycle.get_for_update()` (`SELECT ... FOR UPDATE`)
before validating or applying a transition:
`blood_requests.py::cancel_request`, `::fulfill_request`,
`::respond_to_request`; `hospitals.py::fulfill_from_hospital` and
`::allocate_to_request` (the latter doesn't itself transition the request,
but must not race a concurrent cancel/fulfil that would close the request
the instant after its status check passes); and the new admin transition
endpoint. Two concurrent requests touching the same row serialize: the
second blocks until the first commits (releasing the lock), then
re-validates against the now-current status — so "two hospitals fulfilling
the same request" and "two users cancelling simultaneously" resolve to
exactly one winner and one `409 Conflict`, never both succeeding and never
a 500. Verified under real concurrent threads against the actual Postgres
test database in `test_two_hospitals_fulfilling_the_same_request_only_one_wins`
and `test_two_managers_cancelling_simultaneously_only_one_wins` (§8).

`expire_if_stale()` commits immediately, but only in the (rare) case where a
transition actually fires — the common "nothing to expire" path does not
commit, so a caller already holding a `get_for_update` lock keeps it for the
rest of its own transaction unless expiry itself just ended the request's
lifecycle. When expiry does fire, the lock is released early, but this is
still safe: EXPIRED is terminal and already durably committed, so any
transition attempted afterward in the same request handler is correctly and
safely rejected by `validate_transition` against the now in-memory-updated
status — there is no race window that matters.

Separately, `inventory_service._get_or_create_row` now locks
(`.with_for_update()`) the hospital's inventory row for the given blood
type before any read-check-mutate sequence (add/reserve/release/allocate/
expire/reconcile) — this closes the "repeated allocation" gap at the
inventory layer, independent of the BloodRequest-row lock above.
Verified in `test_repeated_allocation_never_oversells_inventory`: two
concurrent `/allocate` calls for 2 units each against 3 units on hand can
never both succeed (that would require 4); exactly one does, the other gets
409, and stock never goes negative.

Duplicate submissions/operations (double-cancel, double-fulfill) are
rejected as a same-state "transition," per §2 — never silently accepted as
a no-op, and never double-executing a side effect (e.g. fulfilling twice
never records donation history twice, because the second call never
reaches that code once the first commit lands).

## 6. Audit trail

Every transition writes both trails in the same call
(`blood_request_lifecycle.transition()`):

- **`BloodRequestEvent`** (user-facing timeline): `event_type`, `note`,
  `actor_user_id`, `created_at`, shown in `GET /blood-requests/{id}`'s
  `events` array.
- **`AuditLog`** (admin-facing compliance trail, one unified action —
  `"blood_request.transition"`): `actor_user_id`, `target_type`/`target_id`,
  `created_at`, and `meta.old_status` / `meta.new_status` / `meta.reason` —
  every field the spec's AUDIT section asks for. A system-triggered expiry
  carries `actor_user_id=None`, distinguishing it from every actor-driven
  transition. The old `"blood_request.fulfilled"` action's
  `donations_recorded` detail is preserved via `transition()`'s
  `extra_meta` parameter rather than lost in the consolidation — verified
  in `test_fulfillment_audit_still_carries_donations_recorded`.
  Queryable (admin-only) at `GET /admin/audit-log?action=blood_request.transition`.

The donor-response-specific audit entry (`"blood_request.responded"`,
recording which action a donor took and whether they withdrew) is a
separate, additional entry alongside the transition audit when a response
also causes a state change — a different concern (what the donor did) from
the state machine's own record (what changed), so both are kept.

## 7. API

`BloodRequestOut` (and everything that extends it — `BloodRequestDetailOut`,
`DonorOpenRequestOut`, `DonorMatchOut`, `HospitalRequestItem`) now carries:

- `status` — current state (unchanged field, now backed by the formal table)
- `allowed_transitions` — `allowed_transitions(status)` from the core
  module; empty for a terminal request; lets clients render valid next
  actions without re-deriving the transition table client-side
- `fulfilled_at` (existing), `cancelled_at`, `expired_at` (**new**) — each
  set exactly once, the moment the request enters that state

`AdminBloodRequestOut` gained the same four fields for admin listings.
**New endpoint**: `POST /admin/blood-requests/{id}/transition` — body
`{target_status, reason}` (reason required, 3–500 chars), admin-only,
restricted to CANCELLED/EXPIRED targets (§4), returns the updated
`AdminBloodRequestOut` with its own current `allowed_transitions`. Every
existing request-returning endpoint (`GET/POST /blood-requests/...`,
`GET /hospitals/me/requests`, `GET /admin/blood-requests`) now also runs
`expire_if_stale` before serializing, so status is never stale by the time
a client sees it.

## 8. Tests

**New file: `backend/tests/test_blood_request_lifecycle_phase10.py`** (30
tests), covering every section of the spec:

- **Every valid transition** (9 tests): PENDING→MATCHING,
  PENDING→FULFILLED (direct hospital path), PENDING→CANCELLED,
  MATCHING→FULFILLED, MATCHING→CANCELLED, MATCHING→PENDING (reopen),
  PENDING→EXPIRED, MATCHING→EXPIRED, and confirmation that a request with
  no `needed_by` never auto-expires.
- **Every invalid transition** (6 tests): double-cancel, cancel-after-fulfil,
  fulfil-a-cancelled-request, double-fulfil, any action on an expired
  request, and admin attempting to force MATCHING/FULFILLED (400) or an
  unreachable admin-allowed target (409).
- **Authorization** (5 tests): admin force-cancel/force-expire, a non-admin
  forbidden from the admin endpoint, a stranger forbidden from cancelling
  someone else's request, a donor forbidden from fulfilling.
- **Concurrency** (3 tests, real `threading` + the actual Postgres test DB,
  mirroring the pre-existing pattern in `tests/test_concurrency.py`): two
  hospitals racing to fulfill the same request, a patient and their
  accepted family manager racing to cancel the same request, two
  concurrent over-allocation attempts against limited inventory. Re-run
  three times each with no flakiness observed.
- **Duplicate operations** (2 tests): duplicate cancel and duplicate fulfil
  both rejected with 409, never silently accepted.
- **Audit trail** (3 tests): a transition writes both the event and the
  unified `AuditLog` entry with the right fields; system expiry is audited
  with `actor_user_id=None`; the fulfilment audit still carries
  `donations_recorded` after the consolidation in §6.
- **API shape** (2 tests): the response carries status/allowed_transitions/
  timestamps correctly; the hospital-facing list view carries
  `allowed_transitions` too.

All 30 pass, including three repeated runs of the concurrency tests alone
to check for flakiness (none observed).

## 9. Verification performed

- **Backend**: full suite, **480 tests, 0 failures / 0 errors** (up from
  Phase 9's 450 — this phase adds 30 new tests, no existing test was
  weakened or deleted to make anything pass). Every pre-existing blood
  request/hospital/donor-matching/admin test file
  (`test_blood_requests.py`, `test_hospital.py`, `test_donor_matching.py`,
  `test_admin.py`, `test_concurrency.py`, and others) still passes
  unmodified against the new locking/transition machinery.
- **`alembic upgrade head`**: the new migration
  (`b7c8d9e0f1a2`, adding `cancelled_at`/`expired_at` — no enum change
  needed, per §1) applies cleanly on top of the existing head.
- **`ruff check app`**: the same pre-existing 21-finding baseline as Phase
  9 (`UP042`, all on enum classes this phase did not touch) — nothing new.
  Every file this phase edited or added is independently clean.
- **`mypy app`**: the same pre-existing 3-error baseline
  (`app/api/routes/verifications.py` ×2, `app/api/routes/donors.py` ×1) —
  unchanged. Every file this phase edited or added passes mypy cleanly on
  its own.
- **Frontend**: `tsc --noEmit`, `eslint src --max-warnings 0`, and
  `vite build` all clean after adding `cancelled_at`/`expired_at`/
  `allowed_transitions` to the `BloodRequest` TypeScript interface (no
  frontend UI changes were otherwise needed — Phase 10 is a backend-facing
  state machine; no code constructs a `BloodRequest` object literal that
  the new required fields could break).

## 10. Files changed

**New:**
- `backend/app/core/request_lifecycle.py`
- `backend/app/services/blood_request_lifecycle.py`
- `backend/alembic/versions/b7c8d9e0f1a2_phase10_blood_request_lifecycle.py`
- `backend/tests/test_blood_request_lifecycle_phase10.py`

**Backend, edited:**
- `app/models/blood_request.py` (`cancelled_at`, `expired_at` columns)
- `app/api/routes/blood_requests.py` (`cancel_request`, `fulfill_request`,
  `respond_to_request` now lock + go through `blood_request_lifecycle`;
  `_to_out` now populates `allowed_transitions`; lazy expiry on every read)
- `app/api/routes/hospitals.py` (`_get_visible_request` gains a
  `for_update` param; `allocate_to_request` and `fulfill_from_hospital` use
  it; `fulfill_from_hospital` now goes through `blood_request_lifecycle`;
  `incoming_requests` populates `allowed_transitions` and runs lazy expiry)
- `app/api/routes/admin.py` (new `POST /admin/blood-requests/{id}/transition`;
  `list_blood_requests` now populates `allowed_transitions` and runs lazy expiry)
- `app/services/inventory_service.py` (`_get_or_create_row` now locks)
- `app/schemas/blood_request.py` (`BloodRequestOut` gains `cancelled_at`,
  `expired_at`, `allowed_transitions`)
- `app/schemas/admin.py` (`AdminBloodRequestOut` gains the same three
  fields; new `AdminBloodRequestTransitionIn`)

**Frontend, edited:**
- `src/lib/domain.ts` (`BloodRequest` interface gains `cancelled_at`,
  `expired_at`, `allowed_transitions`)

## 11. Honest limitations

- Admin intervention is deliberately narrow: only CANCELLED/EXPIRED are
  reachable via the admin endpoint. This was a design choice (§4), not an
  oversight — forcing MATCHING or FULFILLED would fabricate a donor match
  or a real-world fulfilment that didn't happen.
- `expire_if_stale` is lazy, not a scheduled sweep — a request whose
  `needed_by` has passed shows as EXPIRED only the next time it's read or
  acted on (via any of the routes this phase updated to call it), matching
  this codebase's pre-existing, established pattern for `FamilyLink`
  expiry. There is still no background job anywhere in this system; adding
  one was out of scope for this phase and would be a separate, disclosed
  change if wanted.
- A request with no `needed_by` set can never auto-expire — there's
  nothing to compare against. This is a real, intentional scope limit
  (tested explicitly), not an oversight.
- The MATCHING↔PENDING donor-driven transitions do not currently carry a
  human-supplied `reason` the way cancel/admin-transition do (the "reason"
  recorded for those is a fixed, code-generated string like "A donor
  accepted" / "All matched donors withdrew") — there's no user-facing
  input for one on the `/respond` endpoint, and inventing one would mean
  putting words in the donor's mouth rather than reporting what actually
  happened.
