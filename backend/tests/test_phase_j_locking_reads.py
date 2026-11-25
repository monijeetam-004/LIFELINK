"""Phase J -- a locking read returns the row it locked.

Root cause of the R4 race (tests/test_phase_j_cross_workflow_races.py):
with `expire_on_commit=False`, a `SELECT ... FOR UPDATE` of an object the
session had already loaded returned the stale in-memory values. These
tests pin the fix in `app/db/session.py` directly, without threads:
session A loads a row, session B changes and commits it, session A then
locks it -- and must see B's value.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.enums import BloodComponent, BloodType
from app.models.hospital import HospitalInventory
from tests.helpers import auth_client


def _row_id() -> uuid.UUID:
    c, _ = auth_client("HOSPITAL")
    fid = c.put("/hospitals/me", json={"name": f"Lock-{uuid.uuid4().hex[:6]}"}).json()["id"]
    assert (
        c.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 3}).status_code
        == 200
    )
    db = SessionLocal()
    try:
        return db.scalar(
            select(HospitalInventory.id).where(
                HospitalInventory.hospital_id == uuid.UUID(fid),
                HospitalInventory.blood_type == BloodType.O_NEG,
                HospitalInventory.component == BloodComponent.WHOLE_BLOOD,
            )
        )
    finally:
        db.close()


def _bump(row_id: uuid.UUID, by: int) -> None:
    other = SessionLocal()
    try:
        row = other.get(HospitalInventory, row_id)
        row.units += by
        other.commit()
    finally:
        other.close()


def test_select_for_update_refreshes_an_already_loaded_object() -> None:
    row_id = _row_id()
    a = SessionLocal()
    try:
        loaded = a.get(HospitalInventory, row_id)
        assert loaded.units == 3
        a.commit()  # expire_on_commit=False: `loaded` keeps units == 3
        _bump(row_id, 5)
        locked = a.scalar(
            select(HospitalInventory).where(HospitalInventory.id == row_id).with_for_update()
        )
        assert locked is loaded
        assert locked.units == 8
    finally:
        a.rollback()
        a.close()


def test_session_get_with_for_update_refreshes_an_already_loaded_object() -> None:
    row_id = _row_id()
    a = SessionLocal()
    try:
        kept = a.get(HospitalInventory, row_id)
        a.commit()
        _bump(row_id, 2)
        locked = a.get(HospitalInventory, row_id, with_for_update=True)
        assert locked is kept
        assert locked.units == 5
    finally:
        a.rollback()
        a.close()


def test_plain_reads_keep_identity_map_semantics() -> None:
    """Only locking reads are changed: an ordinary re-select still returns
    the session's own in-memory state (unchanged ORM behaviour)."""
    row_id = _row_id()
    a = SessionLocal()
    try:
        kept = a.get(HospitalInventory, row_id)  # held: the identity map is weak-referencing
        a.commit()
        _bump(row_id, 4)
        plain = a.scalar(select(HospitalInventory).where(HospitalInventory.id == row_id))
        assert plain is kept
        assert plain.units == 3
    finally:
        a.rollback()
        a.close()
