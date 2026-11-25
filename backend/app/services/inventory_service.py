"""Hospital blood inventory actions: add / reserve / allocate / release /
expire / reconcile -- each one audited (HospitalInventoryLog) and each one
enforced, at both the application layer here and a DB CHECK constraint on
HospitalInventory, to never allow negative on-hand or over-reserved stock.

Real semantics, not a generic "set a number" endpoint:
  * ADD      -- new stock received. `units` goes up.
  * RESERVE  -- units already on hand are earmarked for a specific request.
                `units` is unchanged; `reserved_units` goes up. Must not
                exceed what's actually free (`available_units`).
  * ALLOCATE -- units are physically dispatched. `units` goes down; any
                matching reservation for that blood type is released at the
                same time (up to the amount allocated), so ALLOCATE works
                whether or not a RESERVE happened first.
  * RELEASE  -- a reservation is given up without dispatching (e.g. a
                request was cancelled/reassigned). `reserved_units` goes
                down; `units` is unchanged.
  * EXPIRE   -- units are removed as unusable. Only units that are not
                currently reserved for a request can be expired.
  * RECONCILE -- an absolute stock-take correction (what the pre-existing
                bulk PUT /hospitals/me/inventory already did): sets `units`
                to an exact new count. Cannot be set below the current
                reservation total (that would silently break an existing
                promise to a request).

Every action returns the updated HospitalInventory row and writes exactly
one HospitalInventoryLog row recording the signed delta and the resulting
totals -- nothing here silently drops history.

=============================================================================
Phase 34 -- real per-unit `BloodUnit` lifecycle, layered underneath
=============================================================================
Every function below now does two things atomically, under the same
`HospitalInventory` row lock it already took (see `_get_or_create_row`):
mutate the pre-existing aggregate counters exactly as before (same numbers,
same external API contract -- see
docs/LIFELINK_PHASE_34_BLOOD_UNIT_LIFECYCLE_REPORT.md section 8/24), AND
create/transition the real `BloodUnit` rows that back those counters, via
`app.core.blood_unit_lifecycle`'s validated transition table. The aggregate
is no longer an independent number -- it is a provably-consistent view over
real per-unit state (Invariant I6 in LIFELINK_INVARIANTS.md).

A lazy shelf-life expiry sweep (`_sweep_expired`) runs at the top of every
action here (and from `sweep_all_expired`, called by the read-only
GET /me/inventory route) -- the same "evaluate lazily, on read or mutating
action, no new scheduler" pattern already established by
`blood_request_lifecycle.expire_if_stale` and
`emergency_lifecycle.flag_deadline_exceeded_if_needed`. See report section 6.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.core import clinical_sources, component_compatibility
from app.core.blood_unit_lifecycle import validate_transition
from app.models.blood_request import BloodRequest
from app.models.blood_unit import BloodUnit
from app.models.donation import Donation
from app.models.emergency import Emergency
from app.models.enums import (
    BloodComponent,
    BloodType,
    BloodUnitProvenance,
    BloodUnitState,
    HospitalInventoryAction,
)
from app.models.hospital import HospitalInventory, HospitalInventoryLog
from app.services import graph_service


class InventoryActionError(ValueError):
    """A requested inventory action would violate a real stock constraint
    (insufficient available stock, over-releasing a reservation, etc).
    Routes translate this to HTTP 409."""


def available_units(inv: HospitalInventory) -> int:
    """Units genuinely free to promise to a new request right now."""
    return inv.units - inv.reserved_units


def _now() -> datetime:
    return datetime.now(UTC)


def effective_expired_cond(at: datetime):
    """SQL: the unit is past its EFFECTIVE expiry at `at` -- its shelf-life
    `expires_at`, or (thawed plasma) its post-thaw `thaw_expires_at`,
    whichever comes first. A unit with neither clock is never expired."""
    return or_(
        and_(BloodUnit.expires_at.isnot(None), BloodUnit.expires_at <= at),
        and_(BloodUnit.thaw_expires_at.isnot(None), BloodUnit.thaw_expires_at <= at),
    )


def usable_until_cond(at: datetime):
    """SQL: the unit will still be in date (shelf life AND post-thaw window)
    at `at`. Used to reserve only units that will still be usable when a
    pathway's delivery arrives."""
    return and_(
        or_(BloodUnit.expires_at.is_(None), BloodUnit.expires_at > at),
        or_(BloodUnit.thaw_expires_at.is_(None), BloodUnit.thaw_expires_at > at),
    )


def _get_or_create_row(
    db: Session,
    hospital_id: uuid.UUID,
    blood_type: BloodType,
    component: BloodComponent = BloodComponent.WHOLE_BLOOD,
) -> HospitalInventory:
    """Fetch (or create) this hospital's inventory row for `blood_type`,
    locked with `SELECT ... FOR UPDATE` when an existing row is found.

    Every caller (add/reserve/release/allocate/expire/reconcile) reads
    `row.units`/`row.reserved_units`, does an availability check against
    them, then mutates and flushes -- read-check-write all against one row.
    Without a lock, two concurrent allocations against the same hospital
    stock (e.g. two hospital staff, or two requests being allocated at
    once) could both read the same pre-mutation counts, both pass their
    availability check, and both deduct -- letting stock (or a
    reservation) go negative despite each individual check looking fine in
    isolation. Locking here serializes them: the second call blocks until
    the first commits, then re-reads the now-current row. This is the same
    "repeated allocation" concurrency concern the Phase 10 spec calls out,
    just scoped to inventory rather than BloodRequest.status (see
    `app.services.blood_request_lifecycle.get_for_update` for that one).
    A freshly-created row needs no lock -- nothing else can reference a row
    that doesn't exist yet.

    Phase 34: this same lock is what makes every per-unit BloodUnit
    mutation below safe too -- every reserve/release/allocate/expire call
    for this (hospital, blood_type) pair is already fully serialized by
    this one row lock before any BloodUnit row is ever touched, so no
    separate per-unit locking scheme is needed for correctness (verified
    by `tests/test_phase34_blood_unit_lifecycle.py`'s real-Postgres,
    real-thread races).

    This is also where the lazy expiry sweep (`_sweep_expired`) and the
    self-healing reconciliation catch-up (`_reconcile_missing_units`) run,
    every single time this row is touched -- by any action, or by the
    read-only GET /me/inventory route via `sweep_all_expired`. A unit
    genuinely passing its shelf life, or a shortfall being backfilled, is
    an independently true fact the instant it's detected -- it must not
    be silently undone just because the *specific* action that happened
    to trigger this lookup later fails its own validation (e.g. "not
    enough stock to reserve") and never reaches its own commit. So if
    either one actually changed anything, it's committed right here,
    before the caller ever sees the row -- which ends this transaction
    (and this row lock); the row is then re-fetched and re-locked fresh
    so the caller's own action still runs under its own real lock against
    genuinely current numbers.
    """
    row = db.scalar(
        select(HospitalInventory)
        .where(
            HospitalInventory.hospital_id == hospital_id,
            HospitalInventory.blood_type == blood_type,
            HospitalInventory.component == component,
        )
        .with_for_update()
    )
    if row is None:
        row = HospitalInventory(
            hospital_id=hospital_id, blood_type=blood_type, component=component,
            units=0, reserved_units=0,
        )
        db.add(row)
        db.flush()
        return row
    reconciled = _reconcile_missing_units(db, row)
    swept = _sweep_expired(db, row)
    if reconciled or swept:
        db.commit()
        row = db.scalar(
            select(HospitalInventory)
            .where(
                HospitalInventory.hospital_id == hospital_id,
                HospitalInventory.blood_type == blood_type,
                HospitalInventory.component == component,
            )
            .with_for_update()
        )
    return row


def _reconcile_missing_units(db: Session, row: HospitalInventory) -> int:
    """Self-healing catch-up: if this row's real `BloodUnit` rows don't
    fully account for its own `units`/`reserved_units` aggregate (the
    only way that can happen is a write that bypassed this service
    entirely -- e.g. a direct-SQL fixture, or any row that existed before
    Phase 34 and simply hasn't been touched by `_sweep_expired`'s sibling
    logic yet), backfill the shortfall the exact same honest way the
    Phase 34 migration backfills pre-existing data: as LEGACY_BACKFILL
    AVAILABLE/RESERVED units with no claimed collection date (never a
    fabricated history -- see BloodUnitProvenance).

    This never fires for inventory that only ever went through this
    service. It exists as real defense-in-depth so a row's aggregate and
    per-unit ledger drifting apart (from any cause outside this service's
    control) degrades to an honest, auditable catch-up rather than either
    a silently wrong "available" number or a hard failure on every
    subsequent call against that row.
    """
    counted = dict(
        db.execute(
            select(BloodUnit.state, func.count())
            .where(
                BloodUnit.hospital_id == row.hospital_id,
                BloodUnit.blood_type == row.blood_type,
                BloodUnit.component == row.component,
                BloodUnit.state.in_([BloodUnitState.AVAILABLE, BloodUnitState.RESERVED]),
            )
            .group_by(BloodUnit.state)
        ).all()
    )
    reserved_have = counted.get(BloodUnitState.RESERVED, 0)
    available_have = counted.get(BloodUnitState.AVAILABLE, 0)
    reserved_shortfall = max(0, row.reserved_units - reserved_have)
    available_shortfall = max(0, (row.units - row.reserved_units) - available_have)
    if reserved_shortfall:
        created = _create_units(
            db, row, reserved_shortfall, log_id=None,
            provenance=BloodUnitProvenance.LEGACY_BACKFILL,
        )
        now = _now()
        for unit in created:
            unit.state = BloodUnitState.RESERVED
            unit.state_changed_at = now
    if available_shortfall:
        _create_units(
            db, row, available_shortfall, log_id=None,
            provenance=BloodUnitProvenance.LEGACY_BACKFILL,
        )
    total_created = reserved_shortfall + available_shortfall
    if total_created:
        db.flush()
    return total_created


def _log(
    db: Session,
    row: HospitalInventory,
    action: HospitalInventoryAction,
    *,
    delta_units: int,
    delta_reserved: int,
    actor_user_id: uuid.UUID | None,
    request_id: uuid.UUID | None,
    note: str | None,
    emergency_id: uuid.UUID | None = None,
    shipment_id: uuid.UUID | None = None,
) -> HospitalInventoryLog:
    entry = HospitalInventoryLog(
        hospital_id=row.hospital_id,
        blood_type=row.blood_type,
        component=row.component,
        action=action,
        delta_units=delta_units,
        delta_reserved=delta_reserved,
        resulting_units=row.units,
        resulting_reserved=row.reserved_units,
        actor_user_id=actor_user_id,
        request_id=request_id,
        emergency_id=emergency_id,
        shipment_id=shipment_id,
        note=note[:300] if note else note,
    )
    db.add(entry)
    db.flush()
    return entry


def _sync_graph(row: HospitalInventory) -> None:
    graph_service.sync_inventory(  # best-effort; never blocks the write
        row.hospital_id, row.blood_type, row.units,
        component=row.component, available=max(0, row.units - row.reserved_units),
    )


# --------------------------------------------------------------------------- #
# Phase 34 -- per-unit helpers
# --------------------------------------------------------------------------- #

_UNIT_CODE_BLOOD_TYPE_SHORT = {
    BloodType.A_POS: "APOS", BloodType.A_NEG: "ANEG",
    BloodType.B_POS: "BPOS", BloodType.B_NEG: "BNEG",
    BloodType.AB_POS: "ABPOS", BloodType.AB_NEG: "ABNEG",
    BloodType.O_POS: "OPOS", BloodType.O_NEG: "ONEG",
}
_UNIT_CODE_COMPONENT_SHORT = {
    BloodComponent.WHOLE_BLOOD: "WB", BloodComponent.RED_BLOOD_CELLS: "RBC",
    BloodComponent.PLASMA: "FFP", BloodComponent.PLATELETS: "PLT",
}


def _generate_unit_code(blood_type: BloodType, component: BloodComponent) -> str:
    """A human-readable, barcode/QR-safe identifier for one BloodUnit --
    contains no donor PII (only the unit's own blood type, component, and
    a random hex suffix), immutable once created. Collision probability
    with a random 8-hex-char suffix is astronomically low (same trust
    level Phase 34 already places in `uuid.uuid4()` for BloodUnit.id
    itself); the DB's own `uq_blood_units_unit_code` constraint is the
    real guarantee, not this function."""
    return (
        f"LL-{_UNIT_CODE_BLOOD_TYPE_SHORT[blood_type]}-{_UNIT_CODE_COMPONENT_SHORT[component]}-"
        f"{uuid.uuid4().hex[:8].upper()}"
    )


def _create_units(
    db: Session,
    row: HospitalInventory,
    count: int,
    *,
    log_id: uuid.UUID | None,
    received_at: datetime | None = None,
    provenance: BloodUnitProvenance = BloodUnitProvenance.REAL,
    donation_id: uuid.UUID | None = None,
) -> list[BloodUnit]:
    """Create `count` new AVAILABLE BloodUnit rows backing this hospital's
    ADD/RECONCILE-up, of `row.component`. `received_at` defaults to now (a
    real ADD or a live RECONCILE correction -- the moment this stock
    became known to the system is the most honest timestamp available
    when no dedicated per-unit collection-date field is supplied by the
    caller; see report section 9/12). `expires_at` is only ever set for
    REAL provenance -- a LEGACY_BACKFILL unit's true age is unknown, so no
    shelf-life clock is started for it (never a fabricated guess; see
    BloodUnitProvenance), using the cited, sourced figure for this
    component from `app.core.clinical_sources` -- never a hardcoded
    number (Phase 35; see that module for the citations). `donation_id`
    (Phase 35) is an optional, hospital-asserted traceability link -- see
    `add_stock`'s docstring for the honesty boundary; validated by the
    caller before this function is reached, never here."""
    if count <= 0:
        return []
    ts = received_at if received_at is not None else _now()
    days = clinical_sources.shelf_life_days(row.component)
    expires_at = (
        ts + timedelta(days=days)
        if provenance == BloodUnitProvenance.REAL and days is not None
        else None
    )
    units = [
        BloodUnit(
            hospital_id=row.hospital_id,
            blood_type=row.blood_type,
            component=row.component,
            unit_code=_generate_unit_code(row.blood_type, row.component),
            donation_id=donation_id,
            state=BloodUnitState.AVAILABLE,
            provenance=provenance,
            received_at=ts,
            expires_at=expires_at,
            # Phase E: names the exact cited rule that produced
            # `expires_at` (None exactly when no clock was started).
            expiry_rule_ref=(
                clinical_sources.rule_ref(row.component) if expires_at is not None else None
            ),
            state_changed_at=ts,
            last_log_id=log_id,
        )
        for _ in range(count)
    ]
    db.add_all(units)
    db.flush()
    return units


def _select_units_for_update(
    db: Session,
    hospital_id: uuid.UUID,
    blood_type: BloodType,
    component: BloodComponent,
    state: BloodUnitState,
    limit: int,
    *,
    prefer_request_id: uuid.UUID | None = None,
    prefer_emergency_id: uuid.UUID | None = None,
    prefer_shipment_id: uuid.UUID | None = None,
    only_where=None,
) -> list[BloodUnit]:
    """Lock and return up to `limit` units of `state` for this hospital
    and blood type, in FEFO (first-expiring-first-out) order -- a
    deliberate, real inventory-management practice (not an arbitrary rule):
    units with a known nearer expiry are selected first, units with no
    known expiry (NULL -- always LEGACY_BACKFILL rows) sort last, since
    they're not demonstrably the soonest to expire.

    When `prefer_request_id`/`prefer_emergency_id`/`prefer_shipment_id` is
    given (RELEASE/ALLOCATE, which should preferentially act on the units
    actually earmarked for that context), those units sort first; FEFO
    order is the tie-breaker within and across that preference. Phase D's
    `prefer_shipment_id` is what makes `shipment_service.dispatch_shipment`
    deterministically allocate exactly the units it itself reserved for
    that shipment, never a different unit that happens to also be
    RESERVED (for some other request/emergency/shipment) at this hospital.
    """
    order_cols = []
    if prefer_request_id is not None:
        order_cols.append(
            case((BloodUnit.reserved_for_request_id == prefer_request_id, 0), else_=1).asc()
        )
    if prefer_emergency_id is not None:
        order_cols.append(
            case((BloodUnit.reserved_for_emergency_id == prefer_emergency_id, 0), else_=1).asc()
        )
    if prefer_shipment_id is not None:
        order_cols.append(
            case((BloodUnit.reserved_for_shipment_id == prefer_shipment_id, 0), else_=1).asc()
        )
    order_cols += [
        BloodUnit.expires_at.is_(None).asc(),
        BloodUnit.expires_at.asc(),
        BloodUnit.received_at.asc(),
    ]
    query = (
        select(BloodUnit)
        .where(
            BloodUnit.hospital_id == hospital_id,
            BloodUnit.blood_type == blood_type,
            BloodUnit.component == component,
            BloodUnit.state == state,
            *([only_where] if only_where is not None else []),
        )
        .with_for_update()
        .order_by(*order_cols)
        .limit(limit)
    )
    return list(db.scalars(query).all())


def _reservation_context_cond(
    request_id: uuid.UUID | None,
    emergency_id: uuid.UUID | None,
    shipment_id: uuid.UUID | None,
):
    """SQL condition: a RESERVED unit earmarked for EXACTLY this context
    (NULL-safe on each of the three context columns)."""
    def eq(col, value):
        return col.is_(None) if value is None else col == value

    return and_(
        eq(BloodUnit.reserved_for_request_id, request_id),
        eq(BloodUnit.reserved_for_emergency_id, emergency_id),
        eq(BloodUnit.reserved_for_shipment_id, shipment_id),
    )


def _consumable_reservation_cond(
    request_id: uuid.UUID | None,
    emergency_id: uuid.UUID | None,
    shipment_id: uuid.UUID | None,
):
    """Phase E fix: which RESERVED units an allocation may consume -- those
    earmarked for its OWN context, or context-free ("generic") holds.
    Never a reservation held for a DIFFERENT request/emergency/shipment."""
    own = _reservation_context_cond(request_id, emergency_id, shipment_id)
    generic = _reservation_context_cond(None, None, None)
    return or_(own, generic)


def _transition_units(
    units: list[BloodUnit],
    target_state: BloodUnitState,
    *,
    log_id: uuid.UUID | None,
    request_id: uuid.UUID | None = None,
    emergency_id: uuid.UUID | None = None,
    shipment_id: uuid.UUID | None = None,
    clear_reservation_context: bool = False,
) -> None:
    """Validate and apply `target_state` to every unit in `units`. Raises
    `InvalidBloodUnitTransitionError` (a bug, not a user-facing 409 --
    every call site below only ever selects units already known to be in
    a state with a real outgoing transition to `target_state`) if any
    unit's current state doesn't actually permit it -- this is the
    "invalid transition fails explicitly, no partial mutation" guarantee
    applied at the per-unit layer.

    Phase D: `clear_reservation_context` also clears
    `reserved_for_shipment_id` (every existing call site that passes
    `clear_reservation_context=True` -- RELEASE, EXPIRE, the lazy sweep --
    transitions to AVAILABLE or EXPIRED, never ALLOCATED, so clearing it
    unconditionally alongside the other two context columns is correct
    for every real caller). The ALLOCATE path (dispatch) instead passes
    `shipment_id` explicitly alongside `request_id`/`emergency_id`, taking
    the `else` branch below -- `reserved_for_shipment_id` is SET there,
    not cleared, and -- unlike `reserved_for_request_id`/
    `reserved_for_emergency_id`, which `allocate_stock` only sets when a
    request/emergency context was actually given -- it then stays
    permanently on the unit once ALLOCATED: a historical fact ("which
    shipment moved this physical unit") exactly like `BloodUnit.donation_id`,
    not a still-pending earmark. See `app.services.shipment_service.dispatch_shipment`.
    """
    now = _now()
    for unit in units:
        validate_transition(unit.state, target_state)
        unit.state = target_state
        unit.state_changed_at = now
        unit.last_log_id = log_id
        if clear_reservation_context:
            unit.reserved_for_request_id = None
            unit.reserved_for_emergency_id = None
            unit.reserved_for_shipment_id = None
        else:
            if request_id is not None or emergency_id is not None:
                unit.reserved_for_request_id = request_id
                unit.reserved_for_emergency_id = emergency_id
            if shipment_id is not None:
                unit.reserved_for_shipment_id = shipment_id


def _sweep_expired(db: Session, row: HospitalInventory) -> int:
    """Lazily transition every AVAILABLE/RESERVED unit of this hospital's
    (blood_type) whose shelf life has passed into EXPIRED, adjusting the
    aggregate counters and writing one summarizing audit-log row. Called
    at the top of every mutating action below (and from
    `sweep_all_expired`, for the read-only GET /me/inventory path) so no
    action ever operates against stale "available" numbers that quietly
    include units already past their shelf life -- the same
    lazy-on-every-touch pattern as `blood_request_lifecycle.expire_if_stale`.

    A unit with `expires_at IS NULL` (every LEGACY_BACKFILL row) is never
    swept -- its true age is unknown, so no automatic expiry can be
    honestly claimed for it (see report section 6/23).
    """
    now = _now()
    candidates = list(
        db.scalars(
            select(BloodUnit)
            .where(
                BloodUnit.hospital_id == row.hospital_id,
                BloodUnit.blood_type == row.blood_type,
                BloodUnit.component == row.component,
                BloodUnit.state.in_([BloodUnitState.AVAILABLE, BloodUnitState.RESERVED]),
                effective_expired_cond(now),
            )
            .with_for_update()
        ).all()
    )
    if not candidates:
        return 0
    reserved_count = sum(1 for u in candidates if u.state == BloodUnitState.RESERVED)
    total = len(candidates)
    entry = _log(
        db,
        row,
        HospitalInventoryAction.EXPIRE,
        delta_units=-total,
        delta_reserved=-reserved_count,
        actor_user_id=None,
        request_id=None,
        note=(f"Lazy shelf-life expiry sweep ({total} unit(s) past expires_at "
              "or post-thaw expiry)"),
    )
    # _log snapshots row.units/row.reserved_units into resulting_units/
    # resulting_reserved -- mutate the row and transition the units, then
    # fix up that entry's snapshot to match the post-sweep truth (the row
    # itself hasn't been decremented yet at the point _log ran above, so
    # its own "resulting" fields must be corrected before anything else
    # reads them).
    _transition_units(
        candidates, BloodUnitState.EXPIRED, log_id=entry.id, clear_reservation_context=True
    )
    row.units -= total
    row.reserved_units -= reserved_count
    db.flush()
    entry.resulting_units = row.units
    entry.resulting_reserved = row.reserved_units
    db.flush()
    _sync_graph(row)
    return total


def sweep_all_expired(db: Session, hospital_id: uuid.UUID) -> None:
    """Touch every blood type this hospital actually has an inventory row
    for, via `_get_or_create_row` -- which performs (and durably commits)
    the lazy expiry sweep and the self-healing reconciliation catch-up as
    a side effect. Called from the read-only GET /me/inventory route so a
    caller never sees a unit counted as on-hand/available after it has
    genuinely passed its shelf life, even if no mutating action has
    touched that row since (report section 5's "single source of truth
    for availability" -- a read must be honest too, not just a write)."""
    hospital_rows = db.execute(
        select(HospitalInventory.blood_type, HospitalInventory.component).where(
            HospitalInventory.hospital_id == hospital_id
        )
    ).all()
    for bt, component in hospital_rows:
        _get_or_create_row(db, hospital_id, bt, component)


def _assert_donation_can_justify(db: Session, donation_id: uuid.UUID, units: int) -> None:
    """Phase 35 traceability integrity check: a `donation_id` supplied to
    `add_stock` must point at a real `Donation`, and the units already
    linked to it (across any prior ADD call) plus this ADD's `units` must
    not exceed what the donor actually reported giving. This is the one
    guard that keeps the link honest -- without it, the same donation
    event could be cited to justify fabricating far more stock than it
    could possibly have produced.

    HONESTY BOUNDARY (stated once here, referenced from add_stock's own
    docstring): this does not verify the donation itself happened --
    `Donation` remains donor-self-attested (see docs/
    LIFELINK_PHASE_35_RECON_MATRIX.md and the Donation model). It only
    prevents OVER-claiming against whatever was self-attested.

    CONCURRENCY: locks the `Donation` row with `SELECT ... FOR UPDATE` --
    discovered by direct real-Postgres/real-thread testing (not assumed)
    that without this lock, two concurrent ADD calls citing the SAME
    donation_id but DIFFERENT blood_type/component (so they never contend
    on the same HospitalInventory row -- the only other lock in this
    module) each independently read `already_linked=0` under READ
    COMMITTED, both pass the check, and both commit, over-claiming the
    donation (observed: a 2-unit donation ending up with 4 linked units).
    Locking the donation row itself -- the one piece of state actually
    shared between any two ADDs citing the same donation, regardless of
    blood_type/component/hospital -- closes this: the second caller
    blocks here until the first's transaction ends, then re-reads a
    genuinely current `already_linked` count. See
    tests/test_phase35_blood_component.py::test_concurrent_add_against_same_donation_never_over_claims.
    """
    donation = db.scalar(
        select(Donation).where(Donation.id == donation_id).with_for_update()
    )
    if donation is None:
        raise InventoryActionError("The referenced donation does not exist")
    already_linked = db.scalar(
        select(func.count()).where(BloodUnit.donation_id == donation_id)
    ) or 0
    if already_linked + units > donation.units:
        raise InventoryActionError(
            f"Cannot link {units} unit(s) to this donation -- it reports {donation.units} "
            f"unit(s) donated and {already_linked} are already linked to other blood units"
        )


def add_stock(
    db: Session,
    hospital_id: uuid.UUID,
    blood_type: BloodType,
    units: int,
    *,
    actor_user_id: uuid.UUID | None,
    note: str | None = None,
    component: BloodComponent = BloodComponent.WHOLE_BLOOD,
    donation_id: uuid.UUID | None = None,
) -> HospitalInventory:
    """Phase 35: `donation_id` is an OPTIONAL traceability link -- see
    `app.models.blood_unit.BloodUnit.donation_id` and
    `_assert_donation_can_justify` for the honesty boundary. Omitting it
    (the overwhelming majority of real calls, exactly as before this
    phase) creates stock with no claimed donation origin, same as always.
    """
    if units <= 0:
        raise InventoryActionError("Units to add must be a positive number")
    if donation_id is not None:
        _assert_donation_can_justify(db, donation_id, units)
    row = _get_or_create_row(db, hospital_id, blood_type, component)
    row.units += units
    db.flush()
    entry = _log(db, row, HospitalInventoryAction.ADD, delta_units=units, delta_reserved=0,
                  actor_user_id=actor_user_id, request_id=None, note=note)
    _create_units(db, row, units, log_id=entry.id, donation_id=donation_id)
    _sync_graph(row)
    return row


def receive_transferred_units(
    db: Session,
    destination_hospital_id: uuid.UUID,
    source_units: list[BloodUnit],
    *,
    actor_user_id: uuid.UUID | None,
    shipment_id: uuid.UUID,
    note: str | None = None,
) -> list[BloodUnit]:
    """Phase D: the real inventory-ownership-transfer effect of
    `shipment_service.receive_shipment` -- called once, with every
    ORIGIN-side `BloodUnit` row a shipment carried (already ALLOCATED,
    terminal, at the origin -- untouched by this function). For each one,
    creates a NEW BloodUnit row at `destination_hospital_id`, honestly
    representing that THIS hospital's custody of the physical unit began
    now, while preserving the unit's own real history:

      * `provenance=TRANSFERRED_IN`, `source_shipment_id`, `source_unit_id`
        -- the traceability link back to the origin unit (which still
        carries its own `donation_id`, if any -- never duplicated here).
      * `expires_at` is COPIED from the source unit, never recomputed from
        this later `received_at` -- changing custody does not reset a
        physical unit's real shelf life (Section 10's honesty bar applies
        here exactly as it does to `clinical_sources`).
      * A unit whose `expires_at` has ALREADY passed by the moment of
        receipt (Section 15 -- expiry during transport) is created
        directly in EXPIRED state and is NOT added to the destination's
        countable `HospitalInventory.units` (which, per Invariant 18,
        only ever counts AVAILABLE+RESERVED units) -- it is still a real,
        traceable row (answers "where is unit X" honestly: "it arrived
        expired"), just never falsely offered as available stock.

    Units are grouped by (blood_type, component) since a single shipment
    can carry more than one combination; one `HospitalInventoryLog(ADD)`
    entry is written per group for the units that arrive usable, plus one
    `HospitalInventoryLog(EXPIRE)` entry per group for any that arrived
    already expired (delta_units=0 for that one -- they were never part
    of the countable aggregate to begin with, so there is nothing to
    decrement).
    """
    if not source_units:
        return []
    groups: dict[tuple[BloodType, BloodComponent], list[BloodUnit]] = {}
    for src in source_units:
        groups.setdefault((src.blood_type, src.component), []).append(src)

    now = _now()
    created: list[BloodUnit] = []
    for (blood_type, component), group in groups.items():
        row = _get_or_create_row(db, destination_hospital_id, blood_type, component)
        # Effective expiry: shelf life AND (thawed plasma) the post-thaw window.
        usable = [u for u in group
                  if (u.expires_at is None or u.expires_at > now)
                  and (u.thaw_expires_at is None or u.thaw_expires_at > now)]
        expired_on_arrival = [u for u in group if u not in usable]
        # Per-group batch of newly-created rows -- kept separate from the
        # cumulative `created` return list so each group's db.add_all/flush
        # below only touches what THIS iteration actually built, not every
        # row built by prior groups too.
        group_new: list[BloodUnit] = []

        if usable:
            row.units += len(usable)
            db.flush()
            add_entry = _log(
                db, row, HospitalInventoryAction.ADD, delta_units=len(usable), delta_reserved=0,
                actor_user_id=actor_user_id, request_id=None, shipment_id=shipment_id,
                note=note or f"Received via shipment ({len(usable)} unit(s))",
            )
            for src in usable:
                group_new.append(BloodUnit(
                    hospital_id=destination_hospital_id,
                    blood_type=blood_type,
                    component=component,
                    unit_code=_generate_unit_code(blood_type, component),
                    state=BloodUnitState.AVAILABLE,
                    provenance=BloodUnitProvenance.TRANSFERRED_IN,
                    received_at=now,
                    expires_at=src.expires_at,
                    expiry_rule_ref=src.expiry_rule_ref,
                    thawed_at=src.thawed_at,
                    thaw_expires_at=src.thaw_expires_at,
                    thaw_rule_ref=src.thaw_rule_ref,
                    state_changed_at=now,
                    source_shipment_id=shipment_id,
                    source_unit_id=src.id,
                    last_log_id=add_entry.id,
                ))

        if expired_on_arrival:
            expire_entry = _log(
                db, row, HospitalInventoryAction.EXPIRE, delta_units=0, delta_reserved=0,
                actor_user_id=actor_user_id, request_id=None, shipment_id=shipment_id,
                note=(
                    f"{len(expired_on_arrival)} unit(s) arrived already past shelf life -- "
                    "not added to available inventory"
                ),
            )
            for src in expired_on_arrival:
                group_new.append(BloodUnit(
                    hospital_id=destination_hospital_id,
                    blood_type=blood_type,
                    component=component,
                    unit_code=_generate_unit_code(blood_type, component),
                    state=BloodUnitState.EXPIRED,
                    provenance=BloodUnitProvenance.TRANSFERRED_IN,
                    received_at=now,
                    expires_at=src.expires_at,
                    expiry_rule_ref=src.expiry_rule_ref,
                    thawed_at=src.thawed_at,
                    thaw_expires_at=src.thaw_expires_at,
                    thaw_rule_ref=src.thaw_rule_ref,
                    state_changed_at=now,
                    source_shipment_id=shipment_id,
                    source_unit_id=src.id,
                    last_log_id=expire_entry.id,
                ))

        db.add_all(group_new)
        db.flush()
        _sync_graph(row)
        created.extend(group_new)
    return created


def _assert_context_compatible(
    db: Session,
    blood_type: BloodType,
    component: BloodComponent,
    request_id: uuid.UUID | None,
    emergency_id: uuid.UUID | None,
) -> None:
    """Phase 35 Section 5 defense-in-depth: `reserve_stock`/`allocate_stock`
    trust the caller to have already matched compatibility upstream (the
    donor-matching/hospital-routing code in blood_requests.py/
    emergencies.py) -- this re-verifies it independently, inside the one
    choke point every reservation/allocation must pass through, so the
    invariant holds even if a future caller (or an attacker who reaches
    this function with a crafted request) skips or gets the matching step
    wrong. Raises the same InventoryActionError shape as every other
    violation in this module -- a 409, not a silent allow.

    Looks up the REAL current blood_type recorded on the request/
    emergency row itself (never trusts a client-supplied blood_type
    alongside a request_id -- there is no such field on this function's
    own signature to begin with, by design). A `None` emergency blood_type
    (an emergency that didn't specify one) has nothing to check against
    and is allowed through, same as before this phase.
    """
    if request_id is not None:
        request_type = db.scalar(
            select(BloodRequest.blood_type).where(BloodRequest.id == request_id)
        )
        if request_type is not None and not component_compatibility.is_component_compatible(
            blood_type, request_type, component
        ):
            raise InventoryActionError(
                f"{blood_type.value} {component.value} is not compatible with blood request "
                f"{request_id} (needs {request_type.value})"
            )
    if emergency_id is not None:
        emergency_type = db.scalar(
            select(Emergency.blood_type).where(Emergency.id == emergency_id)
        )
        if emergency_type is not None and not component_compatibility.is_component_compatible(
            blood_type, emergency_type, component
        ):
            raise InventoryActionError(
                f"{blood_type.value} {component.value} is not compatible with emergency "
                f"{emergency_id} (needs {emergency_type.value})"
            )


def reserve_stock(
    db: Session,
    hospital_id: uuid.UUID,
    blood_type: BloodType,
    units: int,
    *,
    actor_user_id: uuid.UUID | None,
    request_id: uuid.UUID | None = None,
    emergency_id: uuid.UUID | None = None,
    shipment_id: uuid.UUID | None = None,
    note: str | None = None,
    component: BloodComponent = BloodComponent.WHOLE_BLOOD,
    usable_until: datetime | None = None,
) -> HospitalInventory:
    """Gap elimination: `usable_until` -- reserve only units that will still
    be in date (shelf life and post-thaw window) at that moment, e.g. a
    pathway's arrival time. Without it FEFO could reserve a unit that
    expires in transit. Checked BEFORE anything is mutated.

    Phase D: `shipment_id`, a third optional context alongside
    `request_id`/`emergency_id` -- used by
    `app.services.shipment_service.create_shipment` to earmark specific
    units for a shipment at the moment it's created, before any physical
    departure (DISPATCHED is when `allocate_stock` is called with the
    same `shipment_id`, making the earmark permanent). Not passed through
    `_assert_context_compatible` -- that check is about whether a
    donor's blood is clinically compatible with a REQUEST/EMERGENCY's
    patient; a shipment has no patient on the other end, only another
    facility, so there is nothing to compatibility-check here."""
    if units <= 0:
        raise InventoryActionError("Units to reserve must be a positive number")
    if request_id is not None or emergency_id is not None:
        _assert_context_compatible(db, blood_type, component, request_id, emergency_id)
    row = _get_or_create_row(db, hospital_id, blood_type, component)
    if units > available_units(row):
        raise InventoryActionError(
            f"Only {available_units(row)} unit(s) of {blood_type.value} are available to reserve"
        )
    only = None
    if usable_until is not None:
        only = usable_until_cond(usable_until)
        in_date = int(db.scalar(
            select(func.count()).select_from(BloodUnit).where(
                BloodUnit.hospital_id == hospital_id, BloodUnit.blood_type == blood_type,
                BloodUnit.component == component, BloodUnit.state == BloodUnitState.AVAILABLE,
                only,
            )
        ) or 0)
        if in_date < units:
            raise InventoryActionError(
                f"Only {in_date} unit(s) of {blood_type.value} {component.value} would still be "
                f"in date at {usable_until.isoformat()}"
            )
    row.reserved_units += units
    db.flush()
    entry = _log(db, row, HospitalInventoryAction.RESERVE, delta_units=0, delta_reserved=units,
                 actor_user_id=actor_user_id, request_id=request_id, emergency_id=emergency_id,
                 shipment_id=shipment_id, note=note)
    selected = _select_units_for_update(
        db, hospital_id, blood_type, component, BloodUnitState.AVAILABLE, units,
        only_where=only,
    )
    if len(selected) != units:  # pragma: no cover -- defensive; see docstring below
        raise InventoryActionError(
            "Inventory inconsistency detected while reserving stock -- aggregate counters "
            "disagreed with the real per-unit records. No change was applied."
        )
    _transition_units(
        selected, BloodUnitState.RESERVED, log_id=entry.id,
        request_id=request_id, emergency_id=emergency_id, shipment_id=shipment_id,
    )
    db.flush()
    return row


def release_reservation(
    db: Session,
    hospital_id: uuid.UUID,
    blood_type: BloodType,
    units: int,
    *,
    actor_user_id: uuid.UUID | None,
    request_id: uuid.UUID | None = None,
    emergency_id: uuid.UUID | None = None,
    shipment_id: uuid.UUID | None = None,
    note: str | None = None,
    component: BloodComponent = BloodComponent.WHOLE_BLOOD,
) -> HospitalInventory:
    if units <= 0:
        raise InventoryActionError("Units to release must be a positive number")
    row = _get_or_create_row(db, hospital_id, blood_type, component)
    if units > row.reserved_units:
        raise InventoryActionError(
            f"Cannot release {units} unit(s) -- only {row.reserved_units} are reserved"
        )
    # Gap elimination: a release made on behalf of a context (request /
    # emergency / shipment) may only free units held for THAT context, or
    # context-free holds -- never a reservation another emergency is
    # relying on (the same rule allocate_stock already applied).
    has_context = any(x is not None for x in (request_id, emergency_id, shipment_id))
    selected = _select_units_for_update(
        db, hospital_id, blood_type, component, BloodUnitState.RESERVED, units,
        prefer_request_id=request_id, prefer_emergency_id=emergency_id,
        prefer_shipment_id=shipment_id,
        only_where=(_consumable_reservation_cond(request_id, emergency_id, shipment_id)
                    if has_context else None),
    )
    if has_context and len(selected) != units:
        raise InventoryActionError(
            f"Cannot release {units} unit(s) -- only {len(selected)} are reserved for this "
            "request/emergency/shipment"
        )
    if len(selected) != units:  # pragma: no cover -- defensive; see reserve_stock
        raise InventoryActionError(
            "Inventory inconsistency detected while releasing a reservation -- aggregate "
            "counters disagreed with the real per-unit records. No change was applied."
        )
    # Mutate only once the exact units are known (nothing changes on error).
    row.reserved_units -= units
    db.flush()
    entry = _log(db, row, HospitalInventoryAction.RELEASE, delta_units=0, delta_reserved=-units,
                 actor_user_id=actor_user_id, request_id=request_id, emergency_id=emergency_id,
                 shipment_id=shipment_id, note=note)
    _transition_units(
        selected, BloodUnitState.AVAILABLE, log_id=entry.id, clear_reservation_context=True,
    )
    db.flush()
    return row


def transfer_reservation_to_shipment(
    db: Session,
    hospital_id: uuid.UUID,
    blood_type: BloodType,
    units: int,
    *,
    emergency_id: uuid.UUID,
    shipment_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    component: BloodComponent = BloodComponent.WHOLE_BLOOD,
    usable_until: datetime | None = None,
    note: str | None = None,
) -> list[BloodUnit]:
    """Audit C21 (2026-10-31): move `units` RESERVED units from an emergency's
    pathway reservation to the shipment that will carry them, WITHOUT a
    release. The units stay RESERVED the whole time, so no other request,
    emergency or shipment can take them in between (the old hand-off
    released them first and re-reserved in a second step).

    Only units reserved for exactly this emergency (and no shipment yet) are
    eligible; `usable_until` additionally requires them to be in date at the
    shipment's arrival. Aggregate counters do not change (still reserved);
    one TRANSFER ledger row records the move. Raises InventoryActionError
    without mutating anything if fewer than `units` qualify."""
    if units <= 0:
        raise InventoryActionError("Units to transfer must be a positive number")
    row = _get_or_create_row(db, hospital_id, blood_type, component)
    cond = _reservation_context_cond(None, emergency_id, None)
    if usable_until is not None:
        cond = and_(cond, usable_until_cond(usable_until))
    selected = _select_units_for_update(
        db, hospital_id, blood_type, component, BloodUnitState.RESERVED, units, only_where=cond,
    )
    if len(selected) != units:
        raise InventoryActionError(
            f"Only {len(selected)} of {units} {blood_type.value} {component.value} unit(s) "
            "reserved for this emergency are still usable -- nothing was transferred"
        )
    entry = _log(db, row, HospitalInventoryAction.TRANSFER, delta_units=0, delta_reserved=0,
                 actor_user_id=actor_user_id, request_id=None, emergency_id=emergency_id,
                 shipment_id=shipment_id, note=note)
    for u in selected:
        u.reserved_for_emergency_id = None
        u.reserved_for_shipment_id = shipment_id
        u.last_log_id = entry.id
    db.flush()
    return selected


def allocate_stock(
    db: Session,
    hospital_id: uuid.UUID,
    blood_type: BloodType,
    units: int,
    *,
    actor_user_id: uuid.UUID | None,
    request_id: uuid.UUID | None = None,
    emergency_id: uuid.UUID | None = None,
    shipment_id: uuid.UUID | None = None,
    note: str | None = None,
    component: BloodComponent = BloodComponent.WHOLE_BLOOD,
) -> HospitalInventory:
    """Physically dispatch `units`. Releases a matching reservation (up to
    `units`) at the same time, so this works whether or not the units were
    reserved first -- an unreserved request can still be allocated directly
    as long as enough is physically on hand.

    Phase 34: per-unit, this is deliberately kept as the existing
    AVAILABLE -> ALLOCATED *and* RESERVED -> ALLOCATED dual path rather
    than forcing every allocation through a reservation first -- see
    report section 11 for why AVAILABLE -> ALLOCATED is implemented as a
    real, first-class transition rather than disallowed.

    Phase 35: re-verifies compatibility against the request/emergency's
    own recorded blood type independently (`_assert_context_compatible`)
    even though the caller should already have matched upstream --
    Section 5 defense-in-depth.
    """
    if units <= 0:
        raise InventoryActionError("Units to allocate must be a positive number")
    if request_id is not None or emergency_id is not None:
        _assert_context_compatible(db, blood_type, component, request_id, emergency_id)
    row = _get_or_create_row(db, hospital_id, blood_type, component)
    if units > row.units:
        raise InventoryActionError(
            f"Cannot allocate {units} unit(s) -- only {row.units} of {blood_type.value} on hand"
        )
    # Phase E fix (found by tests/test_phase_d_concurrency.py's
    # allocation-vs-shipment race failing intermittently in the full
    # regression run): previously `released = min(units, row.reserved_units)`
    # let ANY allocation consume ANY reservation at this hospital -- a
    # context-free direct allocation could silently take units already
    # reserved for a specific shipment/request/emergency, double-committing
    # them (the shipment then still listed units that had been allocated
    # elsewhere). Now only reservations for this allocation's own context,
    # or context-free holds, are consumable; everything else must come
    # from genuinely unreserved stock. Still serialized by the row lock
    # `_get_or_create_row` already holds.
    consumable = db.scalar(
        select(func.count()).select_from(BloodUnit).where(
            BloodUnit.hospital_id == hospital_id,
            BloodUnit.blood_type == blood_type,
            BloodUnit.component == component,
            BloodUnit.state == BloodUnitState.RESERVED,
            _consumable_reservation_cond(request_id, emergency_id, shipment_id),
        )
    ) or 0
    released = min(units, consumable, row.reserved_units)
    from_available = units - released
    if from_available > available_units(row):
        held_elsewhere = row.reserved_units - released
        raise InventoryActionError(
            f"Cannot allocate {units} unit(s) of {blood_type.value} -- only "
            f"{available_units(row) + released} are available to this allocation"
            + (f" ({held_elsewhere} more are reserved for other requests/shipments)"
               if held_elsewhere > 0 else "")
        )
    row.units -= units
    row.reserved_units -= released
    db.flush()
    entry = _log(db, row, HospitalInventoryAction.ALLOCATE, delta_units=-units,
                 delta_reserved=-released, actor_user_id=actor_user_id, request_id=request_id,
                 emergency_id=emergency_id, shipment_id=shipment_id, note=note)
    from_reserved_units = _select_units_for_update(
        db, hospital_id, blood_type, component, BloodUnitState.RESERVED, released,
        prefer_request_id=request_id, prefer_emergency_id=emergency_id,
        prefer_shipment_id=shipment_id,
        only_where=_consumable_reservation_cond(request_id, emergency_id, shipment_id),
    )
    from_available_units = _select_units_for_update(
        db, hospital_id, blood_type, component, BloodUnitState.AVAILABLE, from_available,
    )
    if len(from_reserved_units) != released or len(from_available_units) != from_available:
        # pragma: no cover -- defensive; see reserve_stock's identical guard
        raise InventoryActionError(
            "Inventory inconsistency detected while allocating stock -- aggregate counters "
            "disagreed with the real per-unit records. No change was applied."
        )
    _transition_units(
        from_reserved_units + from_available_units, BloodUnitState.ALLOCATED, log_id=entry.id,
        request_id=request_id, emergency_id=emergency_id, shipment_id=shipment_id,
    )
    db.flush()
    _sync_graph(row)
    return row


def expire_units(
    db: Session,
    hospital_id: uuid.UUID,
    blood_type: BloodType,
    units: int,
    *,
    actor_user_id: uuid.UUID | None,
    note: str | None = None,
    component: BloodComponent = BloodComponent.WHOLE_BLOOD,
) -> HospitalInventory:
    if units <= 0:
        raise InventoryActionError("Units to expire must be a positive number")
    row = _get_or_create_row(db, hospital_id, blood_type, component)
    if units > available_units(row):
        raise InventoryActionError(
            f"Cannot expire {units} unit(s) -- only {available_units(row)} of {blood_type.value} "
            "are unreserved and available to remove"
        )
    row.units -= units
    db.flush()
    entry = _log(db, row, HospitalInventoryAction.EXPIRE, delta_units=-units, delta_reserved=0,
                 actor_user_id=actor_user_id, request_id=None, note=note)
    selected = _select_units_for_update(
        db, hospital_id, blood_type, component, BloodUnitState.AVAILABLE, units
    )
    if len(selected) != units:  # pragma: no cover -- defensive; see reserve_stock
        raise InventoryActionError(
            "Inventory inconsistency detected while expiring stock -- aggregate counters "
            "disagreed with the real per-unit records. No change was applied."
        )
    _transition_units(
        selected, BloodUnitState.EXPIRED, log_id=entry.id, clear_reservation_context=True,
    )
    db.flush()
    _sync_graph(row)
    return row


def reconcile_inventory(
    db: Session,
    hospital_id: uuid.UUID,
    blood_type: BloodType,
    new_units: int,
    *,
    actor_user_id: uuid.UUID | None,
    note: str | None = None,
    component: BloodComponent = BloodComponent.WHOLE_BLOOD,
) -> HospitalInventory:
    if new_units < 0:
        raise InventoryActionError("Units cannot be negative")
    row = _get_or_create_row(db, hospital_id, blood_type, component)
    if new_units < row.reserved_units:
        raise InventoryActionError(
            f"Cannot reconcile to {new_units} unit(s) -- {row.reserved_units} are already "
            "reserved against open requests; release the reservation first"
        )
    delta = new_units - row.units
    if delta == 0:
        return row
    row.units = new_units
    db.flush()
    entry = _log(db, row, HospitalInventoryAction.RECONCILE, delta_units=delta, delta_reserved=0,
                 actor_user_id=actor_user_id, request_id=None, note=note)
    if delta > 0:
        # A stock-take found more physical units than recorded. Their true
        # collection date is just as unknowable as a legacy-backfilled
        # row's -- but unlike the one-time Phase 34 migration backfill,
        # this is a live event happening right now, so "now" is a real,
        # defensible received_at (not a guess about the past) and these
        # units get a real shelf-life clock like any other REAL unit. See
        # report section 12 for this distinction.
        _create_units(db, row, delta, log_id=entry.id)
    else:
        selected = _select_units_for_update(
            db, hospital_id, blood_type, component, BloodUnitState.AVAILABLE, -delta
        )
        if len(selected) != -delta:  # pragma: no cover -- defensive; see reserve_stock
            raise InventoryActionError(
                "Inventory inconsistency detected while reconciling stock -- aggregate "
                "counters disagreed with the real per-unit records. No change was applied."
            )
        _transition_units(
            selected, BloodUnitState.EXPIRED, log_id=entry.id, clear_reservation_context=True,
        )
    db.flush()
    _sync_graph(row)
    return row


def recent_log(
    db: Session, hospital_id: uuid.UUID, *, limit: int = 50
) -> list[HospitalInventoryLog]:
    return list(
        db.scalars(
            select(HospitalInventoryLog)
            .where(HospitalInventoryLog.hospital_id == hospital_id)
            .order_by(HospitalInventoryLog.created_at.desc())
            .limit(limit)
        ).all()
    )


# --------------------------------------------------------------------------- #
# Gap elimination -- per-unit lifecycle actions: thaw, quarantine, release
# from quarantine, discard. Each acts on ONE identified unit, takes the same
# inventory row lock as every other action, keeps the aggregate counters
# equal to the per-unit truth (aggregate units = AVAILABLE + RESERVED), and
# writes one HospitalInventoryLog row.
# --------------------------------------------------------------------------- #
def _lock_unit(db: Session, hospital_id: uuid.UUID, unit_id: uuid.UUID
               ) -> tuple[HospitalInventory, BloodUnit]:
    unit = db.get(BloodUnit, unit_id)
    if unit is None or unit.hospital_id != hospital_id:
        raise InventoryActionError("Blood unit not found at this facility")
    row = _get_or_create_row(db, hospital_id, unit.blood_type, unit.component)
    unit = db.scalar(select(BloodUnit).where(BloodUnit.id == unit_id).with_for_update())
    db.refresh(unit)
    return row, unit


def thaw_plasma_unit(
    db: Session, hospital_id: uuid.UUID, unit_id: uuid.UUID, *,
    actor_user_id: uuid.UUID | None, note: str | None = None,
) -> BloodUnit:
    """Record that a frozen PLASMA unit was thawed. Starts the post-thaw
    clock from app.core.clinical_sources.THAWED_PLASMA; the unit's usable
    life then ends at min(shelf-life expiry, thaw expiry). Refuses non-plasma
    units, already-thawed units and units that are not in stock."""
    row, unit = _lock_unit(db, hospital_id, unit_id)
    if unit.component != BloodComponent.PLASMA:
        raise InventoryActionError(f"Only PLASMA units are thawed ({unit.component.value} is not)")
    if unit.thawed_at is not None:
        raise InventoryActionError("This unit has already been thawed")
    if unit.state not in (BloodUnitState.AVAILABLE, BloodUnitState.RESERVED):
        raise InventoryActionError(f"A {unit.state.value} unit cannot be thawed")
    now = _now()
    if unit.expires_at is not None and unit.expires_at <= now:
        raise InventoryActionError("This unit is past its shelf life and cannot be thawed")
    hours = clinical_sources.post_thaw_hours(unit.component)
    thaw_expiry = now + timedelta(hours=hours)
    unit.thawed_at = now
    unit.thaw_expires_at = (min(thaw_expiry, unit.expires_at)
                            if unit.expires_at is not None else thaw_expiry)
    unit.thaw_rule_ref = clinical_sources.thaw_rule_ref()
    unit.status_reason = (note or "thawed")[:300]
    entry = _log(db, row, HospitalInventoryAction.THAW, delta_units=0, delta_reserved=0,
                 actor_user_id=actor_user_id, request_id=None,
                 note=f"Unit {unit.unit_code} thawed; usable until "
                      f"{unit.thaw_expires_at.isoformat()}" + (f" -- {note}" if note else ""))
    unit.last_log_id = entry.id
    db.flush()
    return unit


def quarantine_unit(
    db: Session, hospital_id: uuid.UUID, unit_id: uuid.UUID, *,
    actor_user_id: uuid.UUID | None, reason: str,
) -> BloodUnit:
    """Hold a unit back from use (suspected damage, cold-chain doubt, ...).
    A quarantined unit is not counted as stock and can never be reserved or
    allocated until it is released from quarantine. A RESERVED unit loses
    its reservation (the holder is told by the caller's audit trail)."""
    row, unit = _lock_unit(db, hospital_id, unit_id)
    if unit.state not in (BloodUnitState.AVAILABLE, BloodUnitState.RESERVED):
        raise InventoryActionError(f"A {unit.state.value} unit cannot be quarantined")
    was_reserved = unit.state == BloodUnitState.RESERVED
    row.units -= 1
    if was_reserved:
        row.reserved_units -= 1
    db.flush()
    entry = _log(db, row, HospitalInventoryAction.QUARANTINE, delta_units=-1,
                 delta_reserved=-1 if was_reserved else 0, actor_user_id=actor_user_id,
                 request_id=unit.reserved_for_request_id,
                 emergency_id=unit.reserved_for_emergency_id,
                 note=f"Unit {unit.unit_code} quarantined: {reason}")
    _transition_units([unit], BloodUnitState.QUARANTINED, log_id=entry.id,
                      clear_reservation_context=True)
    unit.status_reason = reason[:300]
    db.flush()
    _sync_graph(row)
    return unit


def release_from_quarantine(
    db: Session, hospital_id: uuid.UUID, unit_id: uuid.UUID, *,
    actor_user_id: uuid.UUID | None, reason: str,
) -> BloodUnit:
    """A quarantined unit was reviewed and found fit: back to AVAILABLE.
    Refused if the unit's effective expiry has passed meanwhile -- an
    out-of-date unit must be discarded, not returned to stock."""
    row, unit = _lock_unit(db, hospital_id, unit_id)
    if unit.state != BloodUnitState.QUARANTINED:
        raise InventoryActionError("Only a QUARANTINED unit can be released from quarantine")
    now = _now()
    expiries = [t for t in (unit.expires_at, unit.thaw_expires_at) if t is not None]
    if expiries and min(expiries) <= now:
        raise InventoryActionError("This unit is past its expiry and must be discarded")
    row.units += 1
    db.flush()
    entry = _log(db, row, HospitalInventoryAction.RELEASE_QUARANTINE, delta_units=1,
                 delta_reserved=0, actor_user_id=actor_user_id, request_id=None,
                 note=f"Unit {unit.unit_code} released from quarantine: {reason}")
    _transition_units([unit], BloodUnitState.AVAILABLE, log_id=entry.id)
    unit.status_reason = reason[:300]
    db.flush()
    _sync_graph(row)
    return unit


def discard_unit(
    db: Session, hospital_id: uuid.UUID, unit_id: uuid.UUID, *,
    actor_user_id: uuid.UUID | None, reason: str,
) -> BloodUnit:
    """Write a unit off (destroyed, damaged, spoiled, failed review)."""
    row, unit = _lock_unit(db, hospital_id, unit_id)
    if unit.state not in (BloodUnitState.AVAILABLE, BloodUnitState.RESERVED,
                          BloodUnitState.QUARANTINED):
        raise InventoryActionError(f"A {unit.state.value} unit cannot be discarded")
    counted = unit.state in (BloodUnitState.AVAILABLE, BloodUnitState.RESERVED)
    was_reserved = unit.state == BloodUnitState.RESERVED
    if counted:
        row.units -= 1
    if was_reserved:
        row.reserved_units -= 1
    db.flush()
    entry = _log(db, row, HospitalInventoryAction.DISCARD, delta_units=-1 if counted else 0,
                 delta_reserved=-1 if was_reserved else 0, actor_user_id=actor_user_id,
                 request_id=unit.reserved_for_request_id,
                 emergency_id=unit.reserved_for_emergency_id,
                 note=f"Unit {unit.unit_code} discarded: {reason}")
    _transition_units([unit], BloodUnitState.DISCARDED, log_id=entry.id,
                      clear_reservation_context=True)
    unit.status_reason = reason[:300]
    db.flush()
    _sync_graph(row)
    return unit
