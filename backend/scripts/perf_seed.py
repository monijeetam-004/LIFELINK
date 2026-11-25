"""Phase K -- volume data for query-plan analysis (never for production).

Run AFTER `python -m scripts.seed_demo_data` against a disposable
database:

    DATABASE_URL=.../lifelink_perf python -m scripts.perf_seed

Adds, at the default scale (roughly 1-2 orders of magnitude above the
demo seed, so the planner faces real choices):

  * ~117k blood units: every facility x 8 blood types x 2 components x
    120 units, created through `inventory_service.add_stock` (the real
    path: aggregates, logs and unit rows stay consistent); a share then
    reserved and allocated through the real service, and ~8% given a past
    expiry WITHOUT sweeping (what a facility looks like between visits).
  * 500 synthetic patients, 10k blood requests across every status, with
    donor responses on the open ones.
  * 2k emergencies across every status.
  * ~250k notifications (70% read) for donors/patients.
  * 30k audit entries through `audit_service.record` (sealed chain).
  * realtime_events are produced by the outbox listener as a side effect.

Synthetic users share one precomputed password hash (no per-user Argon2:
these accounts are never logged into). Every synthetic email is under
`perf.lifelink-demo.example.com`. Refuses to run with ENVIRONMENT=production.
"""

from __future__ import annotations

import random
import sys
import time
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, update

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.blood_request import BloodRequest, DonorResponse
from app.models.blood_unit import BloodUnit
from app.models.emergency import Emergency, EmergencyResponse
from app.models.enums import (
    BloodComponent,
    BloodRequestStatus,
    BloodType,
    BloodUnitState,
    DonorResponseStatus,
    EmergencyStatus,
    Urgency,
)
from app.models.hospital import HospitalProfile
from app.models.notification import Notification
from app.models.user import User, UserRole
from app.services import audit_service, inventory_service

DOMAIN = "perf.lifelink-demo.example.com"
SEED = 2026


def _now() -> datetime:
    return datetime.now(UTC)


def _log(msg: str) -> None:
    print(f"[perf_seed {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def seed_units(db, rng, per_bucket: int) -> None:
    facilities = db.scalars(select(HospitalProfile)).all()
    _log(f"units: {len(facilities)} facilities x 16 buckets x {per_bucket}")
    for i, hp in enumerate(facilities):
        for bt in BloodType:
            for comp in (BloodComponent.WHOLE_BLOOD, BloodComponent.RED_BLOOD_CELLS):
                inventory_service.add_stock(
                    db, hp.id, bt, per_bucket, actor_user_id=None, note="perf seed", component=comp
                )
            inventory_service.reserve_stock(
                db, hp.id, bt, per_bucket // 12, actor_user_id=None, note="perf seed reserve"
            )
            inventory_service.allocate_stock(
                db, hp.id, bt, per_bucket // 6, actor_user_id=None, note="perf seed allocate"
            )
        db.commit()
        if i % 10 == 0:
            _log(f"  facility {i + 1}/{len(facilities)}")
    # ~8% of still-held units past expiry, NOT swept (realistic lag).
    ids = db.scalars(
        select(BloodUnit.id).where(
            BloodUnit.state.in_([BloodUnitState.AVAILABLE, BloodUnitState.RESERVED])
        )
    ).all()
    expired = rng.sample(list(ids), k=len(ids) * 8 // 100)
    for chunk in range(0, len(expired), 5000):
        db.execute(
            update(BloodUnit)
            .where(BloodUnit.id.in_(expired[chunk : chunk + 5000]))
            .values(expires_at=_now() - timedelta(hours=rng.randint(1, 72)))
        )
    db.commit()
    _log(f"units: {len(ids)} held, {len(expired)} given a past expiry (unswept)")


def seed_people(db, n: int, role: UserRole, pw_hash: str) -> list[uuid.UUID]:
    existing = db.scalars(
        select(User.id).where(User.email.like(f"%{role.value.lower()}%@{DOMAIN}"))
    ).all()
    if existing:
        return list(existing)
    users = [
        User(
            email=f"{role.value.lower()}{i:05d}@{DOMAIN}",
            password_hash=pw_hash,
            full_name=f"Perf {role.value.title()} {i}",
            role=role,
            is_email_verified=True,
            is_active=True,
            password_changed_at=_now(),
        )
        for i in range(n)
    ]
    db.add_all(users)
    db.commit()
    return [u.id for u in users]


def seed_requests(db, rng, patients, donors, cities, n: int) -> None:
    statuses = (
        [BloodRequestStatus.PENDING] * 15
        + [BloodRequestStatus.MATCHING] * 10
        + [BloodRequestStatus.PARTIALLY_FULFILLED] * 5
        + [BloodRequestStatus.FULFILLED] * 50
        + [BloodRequestStatus.CANCELLED] * 15
        + [BloodRequestStatus.EXPIRED] * 5
    )
    for start in range(0, n, 2000):
        batch = []
        for _ in range(min(2000, n - start)):
            st = rng.choice(statuses)
            need = rng.randint(1, 4)
            done = (
                need
                if st == BloodRequestStatus.FULFILLED
                else (
                    rng.randint(1, need - 1)
                    if st == BloodRequestStatus.PARTIALLY_FULFILLED and need > 1
                    else 0
                )
            )
            if st == BloodRequestStatus.PARTIALLY_FULFILLED and need == 1:
                st = BloodRequestStatus.PENDING
            p = rng.choice(patients)
            batch.append(
                BloodRequest(
                    requester_user_id=p,
                    patient_user_id=p,
                    blood_type=rng.choice(list(BloodType)),
                    units_needed=need,
                    fulfilled_units=done,
                    urgency=rng.choice(list(Urgency)),
                    status=st,
                    city=rng.choice(cities),
                    note="perf seed",
                    needed_by=date.today() + timedelta(days=rng.randint(-30, 30)),
                )
            )
        db.add_all(batch)
        db.flush()
        responses = []
        for req in batch:
            if req.status in (
                BloodRequestStatus.PENDING,
                BloodRequestStatus.MATCHING,
                BloodRequestStatus.PARTIALLY_FULFILLED,
            ):
                for d in rng.sample(donors, k=3):
                    responses.append(
                        DonorResponse(
                            request_id=req.id,
                            donor_user_id=d,
                            status=rng.choice(list(DonorResponseStatus)),
                        )
                    )
        db.add_all(responses)
        db.commit()
    _log(f"requests: {n}")


def seed_emergencies(db, rng, patients, donors, cities, n: int) -> None:
    statuses = (
        [EmergencyStatus.ACTIVE] * 5
        + [EmergencyStatus.ACKNOWLEDGED] * 5
        + [EmergencyStatus.MATCHING] * 5
        + [EmergencyStatus.ASSISTANCE_FOUND] * 10
        + [EmergencyStatus.RESOLVED] * 60
        + [EmergencyStatus.CANCELLED] * 15
    )
    batch = []
    for _ in range(n):
        p = rng.choice(patients)
        batch.append(
            Emergency(
                created_by_user_id=p,
                patient_user_id=p,
                blood_type=rng.choice(list(BloodType)),
                urgency=Urgency.CRITICAL,
                status=rng.choice(statuses),
                location_text=rng.choice(cities),
                note="perf seed",
                units_needed=rng.randint(1, 3),
            )
        )
    db.add_all(batch)
    db.flush()
    db.add_all(
        [
            EmergencyResponse(
                emergency_id=e.id,
                responder_user_id=rng.choice(donors),
                responder_role=UserRole.DONOR,
                status=DonorResponseStatus.ACCEPTED,
            )
            for e in batch
            if rng.random() < 0.5
        ]
    )
    db.commit()
    _log(f"emergencies: {n}")


def seed_notifications(db, rng, recipients, n: int) -> None:
    for start in range(0, n, 5000):
        rows = []
        for _ in range(min(5000, n - start)):
            created = _now() - timedelta(minutes=rng.randint(0, 60 * 24 * 90))
            rows.append(
                Notification(
                    user_id=rng.choice(recipients),
                    type=rng.choice(
                        [
                            "blood_request.matching",
                            "emergency.broadcast",
                            "request.update",
                            "shipment.update",
                        ]
                    ),
                    title="Perf seed notification",
                    body="perf seed",
                    data={},
                    read_at=(created + timedelta(minutes=5)) if rng.random() < 0.7 else None,
                    created_at=created,
                )
            )
        db.add_all(rows)
        db.commit()
        if start % 50000 == 0:
            _log(f"  notifications {start + len(rows)}/{n}")
    _log(f"notifications: {n}")


def seed_audit(db, rng, actors, n: int) -> None:
    for start in range(0, n, 2000):
        for _ in range(min(2000, n - start)):
            audit_service.record(
                db,
                rng.choice(
                    [
                        "inventory.adjust",
                        "request.view",
                        "auth.login",
                        "shipment.dispatch",
                        "donation.verified",
                    ]
                ),
                actor_user_id=rng.choice(actors),
                target_type="perf",
                target_id=uuid.uuid4(),
                meta={"source": "perf_seed"},
            )
        db.commit()
    _log(f"audit: {n}")


def main() -> None:
    if settings.is_production:
        print("Refusing to run perf_seed against ENVIRONMENT=production.", file=sys.stderr)
        raise SystemExit(1)
    rng = random.Random(SEED)
    db = SessionLocal()
    try:
        cities = sorted({c for c in db.scalars(select(HospitalProfile.city)).all() if c})
        if not cities:
            print("Run scripts.seed_demo_data first.", file=sys.stderr)
            raise SystemExit(1)
        pw_hash = hash_password(uuid.uuid4().hex)
        patients = seed_people(db, 500, UserRole.PATIENT, pw_hash)
        donors = list(db.scalars(select(User.id).where(User.role == UserRole.DONOR)).all())
        _log(f"patients={len(patients)} donors={len(donors)} cities={len(cities)}")
        seed_units(db, rng, per_bucket=120)
        seed_requests(db, rng, patients, donors, cities, 10_000)
        seed_emergencies(db, rng, patients, donors, cities, 2_000)
        seed_notifications(db, rng, donors + patients, 250_000)
        seed_audit(db, rng, donors[:200], 30_000)
    finally:
        db.close()
    _log("done")


if __name__ == "__main__":
    main()
