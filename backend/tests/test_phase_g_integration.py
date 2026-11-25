"""Phase G -- backend half of the frontend-integration closures.

* Admin facility views never count expired-but-unswept units as on hand
  (detail performs the real sweep; list subtracts them read-only).
* GET /hospitals/me/inventory/components: the honest, component-aware view
  the inventory console now uses (own facility only; role-gated; swept).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.blood_unit import BloodUnit
from app.models.enums import BloodUnitState
from tests.helpers import auth_client


def _hospital(stock: dict | None = None, city: str = "Metro"):
    c, _ = auth_client("HOSPITAL")
    c.put("/hospitals/me", json={"name": f"H-{uuid.uuid4().hex[:6]}", "city": city})
    for (bt, comp), n in (stock or {}).items():
        r = c.post(
            "/hospitals/me/inventory/add", json={"blood_type": bt, "units": n, "component": comp}
        )
        assert r.status_code == 200, r.text
    return c, c.get("/hospitals/me").json()["id"]


def _expire_one(hid: str, component: str = "WHOLE_BLOOD") -> uuid.UUID:
    db = SessionLocal()
    try:
        unit = db.scalars(
            select(BloodUnit).where(
                BloodUnit.hospital_id == uuid.UUID(hid),
                BloodUnit.component == component,
                BloodUnit.state == BloodUnitState.AVAILABLE,
            )
        ).first()
        unit.expires_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()
        return unit.id
    finally:
        db.close()


def _state(unit_id: uuid.UUID) -> BloodUnitState:
    db = SessionLocal()
    try:
        return db.get(BloodUnit, unit_id).state
    finally:
        db.close()


def test_admin_facility_detail_sweeps_expired_units() -> None:
    _, hid = _hospital({("O_NEG", "WHOLE_BLOOD"): 3})
    uid = _expire_one(hid)
    admin, _ = auth_client("ADMIN")
    detail = admin.get(f"/admin/facilities/{hid}").json()
    line = next(i for i in detail["inventory"] if i["blood_type"] == "O_NEG")
    assert line["units"] == 2 and line["available_units"] == 2
    assert detail["facility"]["total_units"] == 2
    assert _state(uid) == BloodUnitState.EXPIRED


def test_admin_facility_list_excludes_expired_without_writing() -> None:
    _, hid = _hospital({("O_NEG", "WHOLE_BLOOD"): 3, ("A_POS", "PLASMA"): 2})
    uid = _expire_one(hid)
    admin, _ = auth_client("ADMIN")
    items = admin.get("/admin/facilities", params={"page_size": 100}).json()["items"]
    row = next(i for i in items if i["id"] == hid)
    assert row["total_units"] == 4  # 5 on hand - 1 expired
    assert _state(uid) == BloodUnitState.AVAILABLE  # list is read-only; sweep happens on touch


def test_admin_facility_list_reserved_total_excludes_expired_reservations() -> None:
    hosp, hid = _hospital({("O_NEG", "WHOLE_BLOOD"): 3})
    assert (
        hosp.post(
            "/hospitals/me/inventory/reserve", json={"blood_type": "O_NEG", "units": 2}
        ).status_code
        == 200
    )
    db = SessionLocal()
    try:
        unit = db.scalars(
            select(BloodUnit).where(
                BloodUnit.hospital_id == uuid.UUID(hid),
                BloodUnit.state == BloodUnitState.RESERVED,
            )
        ).first()
        unit.expires_at = datetime.now(UTC) - timedelta(minutes=5)
        db.commit()
    finally:
        db.close()
    admin, _ = auth_client("ADMIN")
    row = next(
        i
        for i in admin.get("/admin/facilities", params={"page_size": 100}).json()["items"]
        if i["id"] == hid
    )
    assert row["total_units"] == 2 and row["total_reserved_units"] == 1


def test_component_inventory_lists_every_component_for_own_facility_only() -> None:
    hosp, hid = _hospital(
        {("O_NEG", "WHOLE_BLOOD"): 3, ("O_NEG", "PLASMA"): 2, ("B_POS", "PLATELETS"): 1}
    )
    other, _ = _hospital({("O_NEG", "RED_BLOOD_CELLS"): 4})
    rows = hosp.get("/hospitals/me/inventory/components").json()
    got = {(r["blood_type"], r["component"]): r["available_units"] for r in rows}
    assert got == {("O_NEG", "WHOLE_BLOOD"): 3, ("O_NEG", "PLASMA"): 2, ("B_POS", "PLATELETS"): 1}
    assert ("O_NEG", "RED_BLOOD_CELLS") not in got  # never another facility's stock


def test_component_inventory_is_swept_and_role_gated() -> None:
    hosp, hid = _hospital({("O_NEG", "PLASMA"): 2})
    _expire_one(hid, "PLASMA")
    rows = hosp.get("/hospitals/me/inventory/components").json()
    assert rows == [
        {
            "blood_type": "O_NEG",
            "component": "PLASMA",
            "units": 1,
            "reserved_units": 0,
            "available_units": 1,
        }
    ]
    for role in ("DONOR", "PATIENT", "FAMILY", "ADMIN"):
        c, _ = auth_client(role)
        assert c.get("/hospitals/me/inventory/components").status_code == 403
