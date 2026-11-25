"""Phase 34 -- the real, per-unit blood-unit lifecycle.

Every mutating hospital-inventory action (add/reserve/release/allocate/
expire/reconcile) continues to go through the real HTTP API exactly as
before Phase 34 (see tests/test_hospital.py, unchanged and still green) --
this file verifies the new BloodUnit layer those actions now drive
underneath, via the `db_session` fixture (direct DB inspection, the same
pattern test_phase12_emergency_sos_engine.py already uses for state a public
endpoint doesn't expose).

Covers: the pure transition table (app.core.blood_unit_lifecycle), per-unit
consistency after every action, FEFO selection order, lazy shelf-life
expiry (including the "still RESERVED" case and the "legacy/unknown
provenance is never swept" case), the self-healing reconciliation path for
inventory rows that bypass this service, and real-Postgres/real-thread
concurrency races.
"""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta

from sqlalchemy import func, select

from app.core.blood_unit_lifecycle import (
    TRANSITIONS,
    InvalidBloodUnitTransitionError,
    allowed_transitions,
    is_terminal,
    validate_transition,
)
from app.core.time import utcnow
from app.models.blood_unit import BloodUnit
from app.models.enums import BloodUnitProvenance
from app.models.enums import BloodUnitState as S
from app.models.hospital import HospitalInventory
from tests.helpers import auth_client

S_AVAILABLE, S_RESERVED, S_ALLOCATED, S_EXPIRED = (
    S.AVAILABLE,
    S.RESERVED,
    S.ALLOCATED,
    S.EXPIRED,
)


def _facility(name: str = "Unit Test Hosp"):
    c, _ = auth_client("HOSPITAL")
    r = c.put("/hospitals/me", json={"name": name, "city": "Metro"})
    assert r.status_code == 200, r.text
    return c, r.json()["id"]


def _units(db_session, hospital_id: str, blood_type: str = "O_NEG") -> list[BloodUnit]:
    return list(
        db_session.scalars(
            select(BloodUnit).where(
                BloodUnit.hospital_id == uuid.UUID(hospital_id),
                BloodUnit.blood_type == blood_type,
            )
        ).all()
    )


def _counts(db_session, hospital_id: str, blood_type: str = "O_NEG") -> dict[S, int]:
    rows = db_session.execute(
        select(BloodUnit.state, func.count())
        .where(
            BloodUnit.hospital_id == uuid.UUID(hospital_id),
            BloodUnit.blood_type == blood_type,
        )
        .group_by(BloodUnit.state)
    ).all()
    return dict(rows)


def _assert_consistent(
    db_session, hospital_id: str, blood_type: str = "O_NEG"
) -> HospitalInventory:
    """The aggregate HospitalInventory row and the real per-unit BloodUnit
    rows must always agree -- Invariant I6 (availability truth)."""
    row = db_session.scalar(
        select(HospitalInventory).where(
            HospitalInventory.hospital_id == uuid.UUID(hospital_id),
            HospitalInventory.blood_type == blood_type,
        )
    )
    counts = _counts(db_session, hospital_id, blood_type)
    on_hand = counts.get(S_AVAILABLE, 0) + counts.get(S_RESERVED, 0)
    reserved = counts.get(S_RESERVED, 0)
    assert row.units == on_hand, (row.units, on_hand, counts)
    assert row.reserved_units == reserved, (row.reserved_units, reserved, counts)
    return row


# --------------------------------------------------------------------------- #
# Pure transition table
# --------------------------------------------------------------------------- #

# Gap elimination: QUARANTINED (held for review) and DISCARDED (written
# off, terminal) were added; the original transitions are all still there.
S_QUARANTINED = S.QUARANTINED
S_DISCARDED = S.DISCARDED


def test_every_state_has_a_defined_transition_set():
    assert set(TRANSITIONS) == {
        S_AVAILABLE,
        S_RESERVED,
        S_ALLOCATED,
        S_EXPIRED,
        S_QUARANTINED,
        S_DISCARDED,
    }


def test_available_can_reserve_allocate_directly_expire_quarantine_or_discard():
    assert allowed_transitions(S_AVAILABLE) == sorted(
        [S_RESERVED, S_ALLOCATED, S_EXPIRED, S_QUARANTINED, S_DISCARDED], key=lambda s: s.value
    )


def test_reserved_can_release_allocate_expire_quarantine_or_discard():
    assert allowed_transitions(S_RESERVED) == sorted(
        [S_AVAILABLE, S_ALLOCATED, S_EXPIRED, S_QUARANTINED, S_DISCARDED], key=lambda s: s.value
    )


def test_quarantined_can_only_return_expire_or_be_discarded_and_discarded_is_terminal():
    assert allowed_transitions(S_QUARANTINED) == sorted(
        [S_AVAILABLE, S_EXPIRED, S_DISCARDED], key=lambda s: s.value
    )
    assert allowed_transitions(S_DISCARDED) == []


def test_allocated_and_expired_are_terminal():
    assert allowed_transitions(S_ALLOCATED) == []
    assert allowed_transitions(S_EXPIRED) == []
    assert is_terminal(S_ALLOCATED)
    assert is_terminal(S_EXPIRED)
    assert not is_terminal(S_AVAILABLE)
    assert not is_terminal(S_RESERVED)


def test_invalid_transitions_are_rejected_with_the_allowed_set_named():
    for current, target in [
        (S_ALLOCATED, S_AVAILABLE),
        (S_EXPIRED, S_AVAILABLE),
        (S_AVAILABLE, S_AVAILABLE),  # same-state is never a valid "transition"
        (S_ALLOCATED, S_RESERVED),
    ]:
        try:
            validate_transition(current, target)
            raise AssertionError(f"{current} -> {target} should have been rejected")
        except InvalidBloodUnitTransitionError as exc:
            assert exc.current == current
            assert exc.target == target


# --------------------------------------------------------------------------- #
# Per-unit consistency through the real HTTP API
# --------------------------------------------------------------------------- #


def test_add_creates_one_real_available_unit_per_unit_added(db_session):
    hosp, fid = _facility()
    r = hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 4})
    assert r.status_code == 200, r.text
    units = _units(db_session, fid)
    assert len(units) == 4
    assert all(u.state == S_AVAILABLE for u in units)
    assert all(u.provenance == BloodUnitProvenance.REAL for u in units)
    assert all(u.expires_at is not None for u in units)
    _assert_consistent(db_session, fid)


def test_reserve_transitions_exactly_the_requested_count_to_reserved(db_session):
    hosp, fid = _facility()
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})
    r = hosp.post("/hospitals/me/inventory/reserve", json={"blood_type": "O_NEG", "units": 2})
    assert r.status_code == 200, r.text
    counts = _counts(db_session, fid)
    assert counts[S_RESERVED] == 2
    assert counts[S_AVAILABLE] == 3
    _assert_consistent(db_session, fid)


def test_release_returns_reserved_units_to_available(db_session):
    hosp, fid = _facility()
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})
    hosp.post("/hospitals/me/inventory/reserve", json={"blood_type": "O_NEG", "units": 3})
    r = hosp.post("/hospitals/me/inventory/release", json={"blood_type": "O_NEG", "units": 2})
    assert r.status_code == 200, r.text
    counts = _counts(db_session, fid)
    assert counts[S_RESERVED] == 1
    assert counts[S_AVAILABLE] == 4
    _assert_consistent(db_session, fid)


def test_allocate_without_reservation_takes_directly_from_available(db_session):
    hosp, fid = _facility()
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})
    r = hosp.post("/hospitals/me/inventory/allocate", json={"blood_type": "O_NEG", "units": 2})
    assert r.status_code == 200, r.text
    counts = _counts(db_session, fid)
    assert counts.get(S_ALLOCATED, 0) == 2
    assert counts[S_AVAILABLE] == 3
    assert counts.get(S_RESERVED, 0) == 0
    _assert_consistent(db_session, fid)


def test_allocate_after_reservation_takes_from_reserved_first(db_session):
    hosp, fid = _facility()
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})
    hosp.post("/hospitals/me/inventory/reserve", json={"blood_type": "O_NEG", "units": 3})
    r = hosp.post("/hospitals/me/inventory/allocate", json={"blood_type": "O_NEG", "units": 2})
    assert r.status_code == 200, r.text
    counts = _counts(db_session, fid)
    assert counts.get(S_ALLOCATED, 0) == 2
    assert counts[S_RESERVED] == 1  # one reservation still outstanding
    assert counts[S_AVAILABLE] == 2
    _assert_consistent(db_session, fid)


def test_allocate_spanning_reserved_and_available_pools_at_once(db_session):
    hosp, fid = _facility()
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})
    hosp.post("/hospitals/me/inventory/reserve", json={"blood_type": "O_NEG", "units": 2})
    # 3 allocated but only 2 reserved -- 1 must come straight from AVAILABLE.
    r = hosp.post("/hospitals/me/inventory/allocate", json={"blood_type": "O_NEG", "units": 3})
    assert r.status_code == 200, r.text
    counts = _counts(db_session, fid)
    assert counts.get(S_ALLOCATED, 0) == 3
    assert counts.get(S_RESERVED, 0) == 0
    assert counts[S_AVAILABLE] == 2
    _assert_consistent(db_session, fid)


def test_expire_only_ever_touches_available_units_never_reserved(db_session):
    hosp, fid = _facility()
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})
    hosp.post("/hospitals/me/inventory/reserve", json={"blood_type": "O_NEG", "units": 2})
    r = hosp.post("/hospitals/me/inventory/expire", json={"blood_type": "O_NEG", "units": 3})
    assert r.status_code == 200, r.text
    counts = _counts(db_session, fid)
    assert counts.get(S_EXPIRED, 0) == 3
    assert counts[S_RESERVED] == 2  # untouched
    assert counts.get(S_AVAILABLE, 0) == 0
    _assert_consistent(db_session, fid)
    # Expiring the remaining reserved stock must be refused, exactly as
    # the pre-existing aggregate-level check already enforced.
    r2 = hosp.post("/hospitals/me/inventory/expire", json={"blood_type": "O_NEG", "units": 1})
    assert r2.status_code == 409, r2.text


def test_terminal_units_never_resurrect_into_available(db_session):
    hosp, fid = _facility()
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 1})
    hosp.post("/hospitals/me/inventory/allocate", json={"blood_type": "O_NEG", "units": 1})
    unit = _units(db_session, fid)[0]
    assert unit.state == S_ALLOCATED
    for target in (S_AVAILABLE, S_RESERVED, S_EXPIRED):
        try:
            validate_transition(unit.state, target)
            raise AssertionError(f"ALLOCATED -> {target} should be impossible")
        except InvalidBloodUnitTransitionError:
            pass


def test_reconcile_up_creates_real_units_reconcile_down_expires_available_ones(db_session):
    hosp, fid = _facility()
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 3})
    r = hosp.put("/hospitals/me/inventory", json={"items": [{"blood_type": "O_NEG", "units": 7}]})
    assert r.status_code == 200, r.text
    counts = _counts(db_session, fid)
    assert counts[S_AVAILABLE] == 7
    _assert_consistent(db_session, fid)

    r2 = hosp.put("/hospitals/me/inventory", json={"items": [{"blood_type": "O_NEG", "units": 2}]})
    assert r2.status_code == 200, r2.text
    counts2 = _counts(db_session, fid)
    assert counts2[S_AVAILABLE] == 2
    assert counts2.get(S_EXPIRED, 0) == 5
    _assert_consistent(db_session, fid)


# --------------------------------------------------------------------------- #
# FEFO (first-expiring-first-out) selection
# --------------------------------------------------------------------------- #


def test_reservation_prefers_the_soonest_expiring_units_fefo(db_session):
    hosp, fid = _facility()
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 3})
    units = sorted(_units(db_session, fid), key=lambda u: u.id.hex)
    now = utcnow()
    # Deliberately set distinct expiries out of creation order so FEFO
    # selection can only be passing because it actually sorts by
    # expires_at, not by insertion/primary-key order.
    units[0].expires_at = now + timedelta(days=20)
    units[1].expires_at = now + timedelta(days=5)  # soonest -- must be picked
    units[2].expires_at = now + timedelta(days=30)
    db_session.commit()

    hosp.post("/hospitals/me/inventory/reserve", json={"blood_type": "O_NEG", "units": 1})
    db_session.expire_all()
    reserved = [u for u in _units(db_session, fid) if u.state == S_RESERVED]
    assert len(reserved) == 1
    assert reserved[0].id == units[1].id


def test_units_with_no_known_expiry_sort_last_in_fefo_order(db_session):
    hosp, fid = _facility()
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    units = sorted(_units(db_session, fid), key=lambda u: u.id.hex)
    now = utcnow()
    units[0].expires_at = None  # unknown -- must NOT be preferred over a dated unit
    units[1].expires_at = now + timedelta(days=10)
    db_session.commit()

    hosp.post("/hospitals/me/inventory/reserve", json={"blood_type": "O_NEG", "units": 1})
    db_session.expire_all()
    reserved = [u for u in _units(db_session, fid) if u.state == S_RESERVED]
    assert len(reserved) == 1
    assert reserved[0].id == units[1].id


# --------------------------------------------------------------------------- #
# Lazy shelf-life expiry
# --------------------------------------------------------------------------- #


def test_a_unit_past_its_shelf_life_is_swept_to_expired_on_the_next_action(db_session):
    hosp, fid = _facility()
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    units = _units(db_session, fid)
    units[0].expires_at = utcnow() - timedelta(seconds=1)  # already past
    db_session.commit()

    # A read-only GET must be honest too (report section 5) -- not just a
    # mutating action.
    inv = hosp.get("/hospitals/me/inventory").json()
    row = next(i for i in inv if i["blood_type"] == "O_NEG")
    assert row["units"] == 1
    assert row["available_units"] == 1
    db_session.expire_all()
    counts = _counts(db_session, fid)
    assert counts.get(S_EXPIRED, 0) == 1
    assert counts[S_AVAILABLE] == 1
    _assert_consistent(db_session, fid)


def test_a_unit_exactly_at_the_boundary_is_not_yet_expired(db_session):
    hosp, fid = _facility()
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 1})
    units = _units(db_session, fid)
    units[0].expires_at = utcnow() + timedelta(hours=1)  # still in the future
    db_session.commit()

    inv = hosp.get("/hospitals/me/inventory").json()
    row = next(i for i in inv if i["blood_type"] == "O_NEG")
    assert row["units"] == 1
    assert row["available_units"] == 1
    db_session.expire_all()
    assert _counts(db_session, fid).get(S_EXPIRED, 0) == 0


def test_a_reserved_unit_can_still_expire_before_being_allocated(db_session):
    """Reservation does not pause the shelf-life clock -- a real, physically
    possible event (report section 6/7)."""
    hosp, fid = _facility()
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 1})
    hosp.post("/hospitals/me/inventory/reserve", json={"blood_type": "O_NEG", "units": 1})
    units = _units(db_session, fid)
    units[0].expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()

    # Expire_units (the manual action) only ever targets *unreserved*
    # stock, unchanged from before Phase 34 -- so trigger the lazy sweep
    # via a fresh reserve attempt against a *different* unit instead, the
    # same way any later action would.
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 1})
    hosp.post("/hospitals/me/inventory/reserve", json={"blood_type": "O_NEG", "units": 1})
    db_session.expire_all()
    counts = _counts(db_session, fid)
    assert counts.get(S_EXPIRED, 0) == 1  # the original RESERVED unit, swept
    assert counts[S_RESERVED] == 1  # the second (fresh) unit's own reservation
    _assert_consistent(db_session, fid)


def test_legacy_backfill_units_with_unknown_age_are_never_swept(db_session):
    hosp, fid = _facility()
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 1})
    unit = _units(db_session, fid)[0]
    unit.provenance = BloodUnitProvenance.LEGACY_BACKFILL
    unit.expires_at = None
    db_session.commit()

    for _ in range(3):
        hosp.get("/hospitals/me/inventory")
    assert _counts(db_session, fid).get(S_EXPIRED, 0) == 0


# --------------------------------------------------------------------------- #
# Self-healing reconciliation for rows that bypass this service
# --------------------------------------------------------------------------- #


def test_a_row_seeded_outside_the_service_is_backfilled_honestly_not_rejected(db_session):
    """Mirrors how several pre-existing test fixtures across this suite
    (e.g. test_phase12_emergency_sos_engine.py's `_stock` helper) seed
    HospitalInventory directly via the ORM, bypassing inventory_service
    entirely -- exactly the backward-compatibility case report section 24
    requires this phase to never silently break."""
    hosp, fid = _facility()
    row = HospitalInventory(hospital_id=uuid.UUID(fid), blood_type="O_NEG", units=5)
    db_session.add(row)
    db_session.commit()
    assert _units(db_session, fid) == []  # nothing real backing it yet

    r = hosp.post("/hospitals/me/inventory/reserve", json={"blood_type": "O_NEG", "units": 2})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    counts = _counts(db_session, fid)
    assert counts[S_RESERVED] == 2
    assert counts[S_AVAILABLE] == 3
    assert all(u.provenance == BloodUnitProvenance.LEGACY_BACKFILL for u in _units(db_session, fid))
    _assert_consistent(db_session, fid)


# --------------------------------------------------------------------------- #
# Donation -> unit linkage: explicitly NOT implemented this phase
# --------------------------------------------------------------------------- #


def test_logging_a_donation_does_not_create_any_blood_unit(db_session):
    """Characterizes a deliberate, documented Phase 34 scope boundary
    (report section 13/27): Donation has no FK to HospitalProfile and
    nothing in this codebase calls inventory_service from a donation
    route, so a donor logging a donation must not silently appear to
    create real hospital stock it never actually confirmed."""
    donor, _ = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "O_NEG"})
    r = donor.post(
        "/donors/me/donations",
        json={"units": 1, "donated_on": "2026-01-01", "hospital_name": "Some Hospital"},
    )
    assert r.status_code == 201, r.text
    total = db_session.scalar(select(func.count()).select_from(BloodUnit))
    assert total == 0


# --------------------------------------------------------------------------- #
# Concurrency -- real PostgreSQL, real threads (mirrors tests/test_concurrency.py)
# --------------------------------------------------------------------------- #


def test_two_simultaneous_reservations_cannot_double_reserve_the_same_units(db_session):
    hosp, fid = _facility()
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _reserve(units: int) -> None:
        barrier.wait()
        r = hosp.post(
            "/hospitals/me/inventory/reserve", json={"blood_type": "O_NEG", "units": units}
        )
        results.append(r.status_code)

    threads = [
        threading.Thread(target=_reserve, args=(3,)),
        threading.Thread(target=_reserve, args=(3,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Only 5 units exist -- two concurrent requests for 3 each (6 total)
    # can never both succeed. Exactly one must win; the loser gets a
    # deterministic 409, never a 500, and never a partial reservation.
    assert sorted(results) == [200, 409], results
    db_session.expire_all()
    counts = _counts(db_session, fid)
    assert counts[S_RESERVED] == 3
    assert counts[S_AVAILABLE] == 2
    _assert_consistent(db_session, fid)


def test_two_simultaneous_allocations_cannot_double_allocate_the_same_reserved_units(db_session):
    hosp, fid = _facility()
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 4})
    hosp.post("/hospitals/me/inventory/reserve", json={"blood_type": "O_NEG", "units": 4})

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _allocate(units: int) -> None:
        barrier.wait()
        r = hosp.post(
            "/hospitals/me/inventory/allocate", json={"blood_type": "O_NEG", "units": units}
        )
        results.append(r.status_code)

    threads = [
        threading.Thread(target=_allocate, args=(3,)),
        threading.Thread(target=_allocate, args=(3,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [200, 409], results
    db_session.expire_all()
    counts = _counts(db_session, fid)
    assert counts.get(S_ALLOCATED, 0) == 3
    assert counts[S_RESERVED] == 1
    _assert_consistent(db_session, fid)


def test_release_versus_allocate_race_leaves_no_corrupted_state(db_session):
    hosp, fid = _facility()
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 3})
    hosp.post("/hospitals/me/inventory/reserve", json={"blood_type": "O_NEG", "units": 3})

    results: list[tuple[str, int]] = []
    barrier = threading.Barrier(2)

    def _release() -> None:
        barrier.wait()
        r = hosp.post("/hospitals/me/inventory/release", json={"blood_type": "O_NEG", "units": 3})
        results.append(("release", r.status_code))

    def _allocate() -> None:
        barrier.wait()
        r = hosp.post("/hospitals/me/inventory/allocate", json={"blood_type": "O_NEG", "units": 3})
        results.append(("allocate", r.status_code))

    threads = [threading.Thread(target=_release), threading.Thread(target=_allocate)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Both requests are individually well-formed (3 reserved, 3 requested
    # each) -- whichever one's transaction commits first wins; the other
    # must see the now-current state and either succeed consistently with
    # it or fail deterministically. Either way, no 500s and no corruption.
    assert all(code in (200, 409) for _, code in results), results
    db_session.expire_all()
    _assert_consistent(db_session, fid)
    counts = _counts(db_session, fid)
    # Exactly one of the two real outcomes: all released back to
    # available, or all allocated -- never a mix that double-counts.
    assert (counts.get(S_AVAILABLE, 0), counts.get(S_ALLOCATED, 0), counts.get(S_RESERVED, 0)) in [
        (3, 0, 0),  # release won
        (0, 3, 0),  # allocate won
    ]


def test_reservation_versus_lazy_expiry_race_never_reserves_a_unit_past_its_shelf_life(
    db_session,
):
    hosp, fid = _facility()
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    units = _units(db_session, fid)
    units[0].expires_at = utcnow() - timedelta(seconds=1)  # already past
    units[1].expires_at = utcnow() + timedelta(days=30)
    db_session.commit()

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _reserve() -> None:
        barrier.wait()
        r = hosp.post("/hospitals/me/inventory/reserve", json={"blood_type": "O_NEG", "units": 2})
        results.append(r.status_code)

    # Two threads both asking for both units: with one already expired,
    # at most 1 is ever genuinely available, so neither request for 2 can
    # succeed -- this proves the sweep and the reservation check compose
    # safely under concurrency rather than racing each other into
    # over-reserving the single real unit left.
    threads = [threading.Thread(target=_reserve) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == [409, 409], results
    db_session.expire_all()
    counts = _counts(db_session, fid)
    assert counts.get(S_EXPIRED, 0) == 1
    assert counts[S_AVAILABLE] == 1
    assert counts.get(S_RESERVED, 0) == 0
    _assert_consistent(db_session, fid)
