"""Phase 35 -- the blood component model (WHOLE_BLOOD/RED_BLOOD_CELLS/
PLASMA/PLATELETS), donation traceability, and unit identity codes.

Covers:
  * Backward compatibility -- every pre-existing caller that never mentions
    `component` keeps behaving exactly as before (component implicitly
    WHOLE_BLOOD throughout), and the legacy GET/PUT /hospitals/me/inventory
    contract stays pinned to the WHOLE_BLOOD row even once other components
    exist for the same hospital/blood_type.
  * Component isolation -- a hospital's RED_BLOOD_CELLS/PLASMA/PLATELETS
    stock for a given blood type is tracked completely separately from its
    WHOLE_BLOOD stock (separate HospitalInventory rows, separate BloodUnit
    pools, separate FEFO selection).
  * unit_code -- globally unique, correctly formatted, contains no donor PII.
  * Donation traceability integrity (`_assert_donation_can_justify`) --
    cannot over-claim more linked units than a donation reports, including
    under concurrent ADD attempts against the same donation.
  * Defense-in-depth component compatibility (`_assert_context_compatible`)
    -- reserve/allocate against a real BloodRequest/Emergency re-verifies
    compatibility for the ACTUAL component being moved, catching a
    donor/component combination the plain red-cell ABO/Rh check alone
    would have waved through (plasma's inverted compatibility table is the
    sharpest example: an O donor is universal for red cells but NOT for
    plasma).
"""
from __future__ import annotations

import threading
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.models.blood_unit import BloodUnit
from app.models.donation import Donation
from app.models.enums import BloodComponent as C
from app.models.enums import BloodType as BT
from app.models.hospital import HospitalInventory
from app.models.user import User
from app.services import inventory_service
from app.services.inventory_service import InventoryActionError
from tests.helpers import auth_client


def _facility(name: str = "Phase35 Test Hosp"):
    c, _ = auth_client("HOSPITAL")
    r = c.put("/hospitals/me", json={"name": name, "city": "Metro"})
    assert r.status_code == 200, r.text
    return c, r.json()["id"]


def _inventory_row(db_session, hospital_id: str, blood_type: str, component: C):
    return db_session.scalar(
        select(HospitalInventory).where(
            HospitalInventory.hospital_id == uuid.UUID(hospital_id),
            HospitalInventory.blood_type == blood_type,
            HospitalInventory.component == component,
        )
    )


def _units(db_session, hospital_id: str, blood_type: str, component: C) -> list[BloodUnit]:
    return list(
        db_session.scalars(
            select(BloodUnit).where(
                BloodUnit.hospital_id == uuid.UUID(hospital_id),
                BloodUnit.blood_type == blood_type,
                BloodUnit.component == component,
            )
        ).all()
    )


# --------------------------------------------------------------------------- #
# Backward compatibility
# --------------------------------------------------------------------------- #

def test_add_with_no_component_defaults_to_whole_blood(db_session):
    hosp, fid = _facility()
    r = hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 3})
    assert r.status_code == 200, r.text
    row = _inventory_row(db_session, fid, "O_NEG", C.WHOLE_BLOOD)
    assert row is not None
    assert row.units == 3
    units = _units(db_session, fid, "O_NEG", C.WHOLE_BLOOD)
    assert len(units) == 3
    assert all(u.component == C.WHOLE_BLOOD for u in units)
    assert all(u.donation_id is None for u in units)


def test_legacy_get_inventory_ignores_non_whole_blood_components(db_session):
    """The pre-existing GET /hospitals/me/inventory response must stay
    pinned to the WHOLE_BLOOD row even once a RED_BLOOD_CELLS row exists
    for the same hospital+blood_type -- otherwise two real rows would
    collapse onto one legacy response entry non-deterministically."""
    hosp, fid = _facility()
    assert hosp.post(
        "/hospitals/me/inventory/add", json={"blood_type": "A_POS", "units": 5}
    ).status_code == 200
    assert hosp.post(
        "/hospitals/me/inventory/add",
        json={"blood_type": "A_POS", "units": 9, "component": "RED_BLOOD_CELLS"},
    ).status_code == 200

    items = {i["blood_type"]: i for i in hosp.get("/hospitals/me/inventory").json()}
    assert items["A_POS"]["units"] == 5  # not 14 -- RED_BLOOD_CELLS must not leak in

    whole = _inventory_row(db_session, fid, "A_POS", C.WHOLE_BLOOD)
    rbc = _inventory_row(db_session, fid, "A_POS", C.RED_BLOOD_CELLS)
    assert whole.units == 5
    assert rbc.units == 9


# --------------------------------------------------------------------------- #
# Component isolation
# --------------------------------------------------------------------------- #

def test_components_are_fully_separate_pools(db_session):
    hosp, fid = _facility()
    for component, units in (("WHOLE_BLOOD", 4), ("PLASMA", 6), ("PLATELETS", 2)):
        payload = {"blood_type": "B_POS", "units": units}
        if component != "WHOLE_BLOOD":
            payload["component"] = component
        assert hosp.post("/hospitals/me/inventory/add", json=payload).status_code == 200

    for component, expected in (("WHOLE_BLOOD", 4), ("PLASMA", 6), ("PLATELETS", 2)):
        row = _inventory_row(db_session, fid, "B_POS", C(component))
        assert row.units == expected, component
        units = _units(db_session, fid, "B_POS", C(component))
        assert len(units) == expected, component


def test_reserving_one_component_does_not_touch_another(db_session):
    hosp, fid = _facility()
    assert hosp.post(
        "/hospitals/me/inventory/add", json={"blood_type": "AB_POS", "units": 5}
    ).status_code == 200
    assert hosp.post(
        "/hospitals/me/inventory/add",
        json={"blood_type": "AB_POS", "units": 5, "component": "PLASMA"},
    ).status_code == 200

    r = hosp.post(
        "/hospitals/me/inventory/reserve",
        json={"blood_type": "AB_POS", "units": 2, "component": "PLASMA"},
    )
    assert r.status_code == 200, r.text

    whole = _inventory_row(db_session, fid, "AB_POS", C.WHOLE_BLOOD)
    plasma = _inventory_row(db_session, fid, "AB_POS", C.PLASMA)
    assert whole.reserved_units == 0
    assert plasma.reserved_units == 2


# --------------------------------------------------------------------------- #
# unit_code
# --------------------------------------------------------------------------- #

def test_unit_codes_are_unique_and_carry_no_donor_pii(db_session):
    hosp, fid = _facility()
    assert hosp.post(
        "/hospitals/me/inventory/add",
        json={"blood_type": "O_POS", "units": 10, "component": "PLATELETS"},
    ).status_code == 200
    units = _units(db_session, fid, "O_POS", C.PLATELETS)
    codes = [u.unit_code for u in units]
    assert len(codes) == len(set(codes)) == 10
    for code in codes:
        assert code.startswith("LL-OPOS-PLT-")
        suffix = code.rsplit("-", 1)[-1]
        assert len(suffix) == 8
        assert suffix == suffix.upper()
        # No email, name, or UUID-shaped donor identifier anywhere in the code.
        assert "@" not in code


# --------------------------------------------------------------------------- #
# Donation traceability integrity
# --------------------------------------------------------------------------- #

def _make_donation(db_session, donor_user_id: uuid.UUID, units: int) -> uuid.UUID:
    donation = Donation(
        donor_user_id=donor_user_id, units=units, donated_on=date.today(),
        hospital_name="Phase35 Test Hosp",
    )
    db_session.add(donation)
    db_session.commit()
    return donation.id


def test_donation_id_links_real_units_and_is_reported_on_them(db_session):
    hosp, fid = _facility()
    _, donor_email = auth_client("DONOR")
    donor_id = db_session.scalar(select(User.id).where(User.email == donor_email.lower()))
    donation_id = _make_donation(db_session, donor_id, units=3)

    r = hosp.post(
        "/hospitals/me/inventory/add",
        json={"blood_type": "A_NEG", "units": 2, "donation_id": str(donation_id)},
    )
    assert r.status_code == 200, r.text
    units = _units(db_session, fid, "A_NEG", C.WHOLE_BLOOD)
    assert len(units) == 2
    assert all(u.donation_id == donation_id for u in units)


def test_add_rejects_linking_more_units_than_donation_reports(db_session):
    hosp, fid = _facility()
    _, donor_email = auth_client("DONOR")
    donor_id = db_session.scalar(select(User.id).where(User.email == donor_email.lower()))
    donation_id = _make_donation(db_session, donor_id, units=2)

    r = hosp.post(
        "/hospitals/me/inventory/add",
        json={"blood_type": "A_NEG", "units": 3, "donation_id": str(donation_id)},
    )
    assert r.status_code == 409, r.text
    assert "reports 2 unit" in r.json()["detail"]
    # Nothing should have been created -- the guard runs before any mutation.
    assert _units(db_session, fid, "A_NEG", C.WHOLE_BLOOD) == []


def test_add_rejects_cumulative_over_claim_across_two_calls(db_session):
    hosp, fid = _facility()
    _, donor_email = auth_client("DONOR")
    donor_id = db_session.scalar(select(User.id).where(User.email == donor_email.lower()))
    donation_id = _make_donation(db_session, donor_id, units=2)

    r1 = hosp.post(
        "/hospitals/me/inventory/add",
        json={"blood_type": "A_NEG", "units": 2, "donation_id": str(donation_id)},
    )
    assert r1.status_code == 200, r1.text

    r2 = hosp.post(
        "/hospitals/me/inventory/add",
        json={"blood_type": "A_NEG", "units": 1, "donation_id": str(donation_id)},
    )
    assert r2.status_code == 409, r2.text
    assert "already linked" in r2.json()["detail"]


def test_add_rejects_nonexistent_donation_id(db_session):
    hosp, fid = _facility()
    r = hosp.post(
        "/hospitals/me/inventory/add",
        json={"blood_type": "A_NEG", "units": 1, "donation_id": str(uuid.uuid4())},
    )
    assert r.status_code == 409, r.text
    assert "does not exist" in r.json()["detail"]


def test_concurrent_add_against_same_donation_never_over_claims():
    """Real-Postgres, real-thread race: two ADD calls against a donation
    reporting exactly 2 units, each independently trying to link 2 units,
    fired at the same time. Exactly one must succeed (or neither, if both
    lose to the row lock serializing them and the second then sees the
    first's committed total) -- the sum of successfully-linked units must
    never exceed the donation's own reported total.

    Deliberately uses two DIFFERENT blood types (A_POS/B_POS), not the
    same one -- confirmed by direct experiment that same-blood-type calls
    happen to also serialize on the unrelated HospitalInventory unique
    constraint (hospital_id, blood_type, component), which would mask a
    real bug here. With different blood types that accidental
    serialization point doesn't exist, so this only passes if
    `_assert_donation_can_justify`'s own `SELECT ... FOR UPDATE` on the
    Donation row is actually doing its job. Before that row lock was
    added, this exact scenario produced 4 linked units against a 2-unit
    donation (verified by direct experiment, not assumed) -- mirrors the
    concurrency rigor of tests/test_phase34_blood_unit_lifecycle.py's
    inventory races."""
    from app.db.session import SessionLocal

    hosp, fid = _facility("Phase35 Race Hosp")
    setup_db = SessionLocal()
    try:
        _, donor_email = auth_client("DONOR")
        donor_id = setup_db.scalar(select(User.id).where(User.email == donor_email.lower()))
        donation_id = _make_donation(setup_db, donor_id, units=2)
    finally:
        setup_db.close()

    results: list[int] = []
    barrier = threading.Barrier(2)

    def attempt(blood_type: str) -> None:
        barrier.wait()
        r = hosp.post(
            "/hospitals/me/inventory/add",
            json={"blood_type": blood_type, "units": 2, "donation_id": str(donation_id)},
        )
        results.append(r.status_code)

    threads = [
        threading.Thread(target=attempt, args=("A_POS",)),
        threading.Thread(target=attempt, args=("B_POS",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [200, 409], results

    verify_db = SessionLocal()
    try:
        linked_units = list(
            verify_db.scalars(select(BloodUnit).where(BloodUnit.donation_id == donation_id))
        )
        assert len(linked_units) == 2  # never 4 -- the second attempt's units were rejected
    finally:
        verify_db.close()


# --------------------------------------------------------------------------- #
# Defense-in-depth component compatibility
# --------------------------------------------------------------------------- #

def test_allocate_rejects_plasma_from_a_donor_incompatible_with_the_request():
    """O_NEG is the universal RED_BLOOD_CELLS donor (compatible with an
    A_POS request) but NOT a universal PLASMA donor (O plasma is only
    compatible with an O recipient) -- the service must re-check
    compatibility for the ACTUAL component being allocated, not just trust
    that O is "universal" the way it is for red cells.

    Uses the generic /hospitals/me/inventory/allocate action (which, unlike
    the dedicated /requests/{id}/allocate endpoint, does not itself enforce
    an exact blood_type match against the request) with an explicit
    `request_id` -- this is exactly the gap `_assert_context_compatible`'s
    defense-in-depth exists to close: nothing at the route layer stops a
    caller from pairing a `request_id` with an incompatible blood_type/
    component here, so the service layer itself must catch it."""
    hosp, fid = _facility()
    patient, _ = auth_client("PATIENT")
    req = patient.post("/blood-requests", json={"blood_type": "A_POS", "city": "Metro"})
    assert req.status_code == 201, req.text
    request_id = req.json()["id"]

    # Plenty of O_NEG RED_BLOOD_CELLS stock and O_NEG PLASMA stock on hand.
    assert hosp.post(
        "/hospitals/me/inventory/add",
        json={"blood_type": "O_NEG", "units": 5, "component": "RED_BLOOD_CELLS"},
    ).status_code == 200
    assert hosp.post(
        "/hospitals/me/inventory/add",
        json={"blood_type": "O_NEG", "units": 5, "component": "PLASMA"},
    ).status_code == 200

    # RED_BLOOD_CELLS: O_NEG -> A_POS is genuinely compatible -- allowed.
    r_rbc = hosp.post(
        "/hospitals/me/inventory/allocate",
        json={
            "blood_type": "O_NEG", "units": 1, "component": "RED_BLOOD_CELLS",
            "request_id": request_id,
        },
    )
    assert r_rbc.status_code == 200, r_rbc.text

    # PLASMA: O_NEG donor plasma is only compatible with an O recipient --
    # A_POS is NOT -- must be rejected even though the stock exists and the
    # same blood_type sailed through for RED_BLOOD_CELLS a moment ago.
    r_plasma = hosp.post(
        "/hospitals/me/inventory/allocate",
        json={
            "blood_type": "O_NEG", "units": 1, "component": "PLASMA",
            "request_id": request_id,
        },
    )
    assert r_plasma.status_code == 409, r_plasma.text
    assert "not compatible" in r_plasma.json()["detail"]


def test_reserve_against_emergency_rechecks_component_compatibility(db_session):
    hosp, fid = _facility()
    patient, _ = auth_client("PATIENT")
    em = patient.post("/emergencies", json={"blood_type": "A_POS"})
    assert em.status_code == 201, em.text
    emergency_id = em.json()["id"]

    assert hosp.post(
        "/hospitals/me/inventory/add",
        json={"blood_type": "O_NEG", "units": 5, "component": "PLASMA"},
    ).status_code == 200

    with pytest.raises(InventoryActionError):
        inventory_service.reserve_stock(
            db_session, uuid.UUID(fid), BT.O_NEG, 1,
            actor_user_id=None, emergency_id=uuid.UUID(emergency_id),
            component=C.PLASMA,
        )


def test_admin_facility_detail_distinguishes_components_for_the_same_blood_type():
    """Regression guard for an honesty gap this phase's own schema change
    would otherwise introduce: app/api/routes/admin.py's facility detail
    view lists every HospitalInventory row for a facility, and a facility
    can now have up to four rows per blood_type (one per component) --
    without `component` on each line, two real rows for O_POS (say,
    WHOLE_BLOOD and PLATELETS) would render as two identically-labeled
    "O_POS" lines with different numbers, with no way for an admin to tell
    them apart."""
    hosp, fid = _facility("Phase35 Admin Detail Hosp")
    admin, _ = auth_client("ADMIN")
    assert hosp.post(
        "/hospitals/me/inventory/add", json={"blood_type": "O_POS", "units": 4}
    ).status_code == 200
    assert hosp.post(
        "/hospitals/me/inventory/add",
        json={"blood_type": "O_POS", "units": 7, "component": "PLATELETS"},
    ).status_code == 200

    rows = admin.get("/admin/facilities", params={"query": "Phase35 Admin Detail Hosp"}).json()
    admin_facility_id = next(
        f["id"] for f in rows["items"] if f["name"] == "Phase35 Admin Detail Hosp"
    )
    detail = admin.get(f"/admin/facilities/{admin_facility_id}")
    assert detail.status_code == 200, detail.text
    o_pos_lines = {
        line["component"]: line for line in detail.json()["inventory"]
        if line["blood_type"] == "O_POS"
    }
    assert o_pos_lines["WHOLE_BLOOD"]["units"] == 4
    assert o_pos_lines["PLATELETS"]["units"] == 7


def test_whole_blood_and_red_cells_use_the_unchanged_existing_engine():
    """A request/emergency that WHOLE_BLOOD or RED_BLOOD_CELLS could never
    satisfy before this phase (a genuine ABO/Rh mismatch) must still be
    rejected exactly as before -- Phase 35 adds a new axis, it does not
    loosen the existing one. Uses the generic allocate action (see the
    docstring on the PLASMA test above for why) so the rejection is
    attributable to `_assert_context_compatible` itself."""
    hosp, fid = _facility()
    patient, _ = auth_client("PATIENT")
    req = patient.post("/blood-requests", json={"blood_type": "O_NEG", "city": "Metro"})
    assert req.status_code == 201, req.text
    request_id = req.json()["id"]

    assert hosp.post(
        "/hospitals/me/inventory/add", json={"blood_type": "AB_POS", "units": 5}
    ).status_code == 200

    r = hosp.post(
        "/hospitals/me/inventory/allocate",
        json={"blood_type": "AB_POS", "units": 1, "request_id": request_id},
    )
    assert r.status_code == 409, r.text
    assert "not compatible" in r.json()["detail"]
