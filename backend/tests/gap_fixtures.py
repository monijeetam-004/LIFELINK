"""Builders for the gap-elimination tests: facilities, routes and real
per-unit stock created straight through the ORM and the inventory service
(the same code paths production uses), so the pathway engine is tested
against real rows in real PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select

from app.core.security import hash_password
from app.core.time import utcnow
from app.db.session import SessionLocal
from app.models.blood_unit import BloodUnit
from app.models.emergency import Emergency
from app.models.enums import (
    BloodComponent,
    BloodType,
    BloodUnitState,
    EmergencyStatus,
    FacilityType,
    TransportRouteStatus,
    Urgency,
)
from app.models.hospital import HospitalProfile
from app.models.transport import TransportRoute
from app.models.user import User, UserRole
from app.services import inventory_service


def facility(
    db,
    name: str,
    lat: float | None = None,
    lon: float | None = None,
    *,
    verified: bool = True,
    city: str = "Metro",
    active: bool = True,
) -> HospitalProfile:
    u = User(
        email=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}@example.com",
        password_hash=hash_password("a decent passphrase"),
        full_name=name,
        role=UserRole.HOSPITAL,
        is_email_verified=True,
        is_active=active,
        password_changed_at=utcnow(),
    )
    db.add(u)
    db.flush()
    hp = HospitalProfile(
        user_id=u.id,
        name=name,
        city=city,
        latitude=lat,
        longitude=lon,
        verified=verified,
        facility_type=FacilityType.HOSPITAL,
    )
    db.add(hp)
    db.flush()
    return hp


def route(
    db,
    a: HospitalProfile,
    b: HospitalProfile,
    km: float,
    minutes: int,
    status: TransportRouteStatus = TransportRouteStatus.OPERATIONAL,
) -> TransportRoute:
    o, d = sorted((a.id, b.id), key=str)
    r = TransportRoute(
        origin_facility_id=o,
        destination_facility_id=d,
        distance_km=km,
        transit_minutes=minutes,
        status=status,
    )
    db.add(r)
    db.flush()
    return r


def stock(
    db, hp: HospitalProfile, blood_type: str, units: int, component: str = "WHOLE_BLOOD"
) -> list[BloodUnit]:
    inventory_service.reconcile_inventory(
        db,
        hp.id,
        BloodType(blood_type),
        units,
        actor_user_id=None,
        component=BloodComponent(component),
        note="test stock",
    )
    db.flush()
    return list(
        db.scalars(
            select(BloodUnit).where(
                BloodUnit.hospital_id == hp.id,
                BloodUnit.blood_type == BloodType(blood_type),
                BloodUnit.component == BloodComponent(component),
                BloodUnit.state == BloodUnitState.AVAILABLE,
            )
        ).all()
    )


def set_expiry(db, units: list[BloodUnit], minutes_from_now: float) -> None:
    for u in units:
        u.expires_at = utcnow() + timedelta(minutes=minutes_from_now)
    db.flush()


def patient(db) -> User:
    u = User(
        email=f"patient-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("a decent passphrase"),
        full_name="Patient",
        role=UserRole.PATIENT,
        is_email_verified=True,
        is_active=True,
        password_changed_at=utcnow(),
    )
    db.add(u)
    db.flush()
    return u


def emergency(
    db,
    *,
    blood_type: str,
    units: int = 1,
    component: str = "WHOLE_BLOOD",
    urgency: Urgency = Urgency.CRITICAL,
    deadline_minutes: float | None = None,
    target: HospitalProfile | None = None,
    lat=None,
    lon=None,
) -> Emergency:
    p = patient(db)
    em = Emergency(
        created_by_user_id=p.id,
        patient_user_id=p.id,
        blood_type=BloodType(blood_type),
        urgency=urgency,
        status=EmergencyStatus.ACTIVE,
        units_needed=units,
        component=BloodComponent(component),
        deadline=(
            utcnow() + timedelta(minutes=deadline_minutes) if deadline_minutes is not None else None
        ),
        target_facility_id=target.id if target else None,
        latitude=lat,
        longitude=lon,
    )
    db.add(em)
    db.flush()
    return em


def session():
    return SessionLocal()
