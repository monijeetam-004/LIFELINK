"""Synthetic donors and scenario clusters for the confidence-shortlist demo
(2026-10-31). Deterministic (seed 20261031) and idempotent: every row is keyed
by a fixed e-mail address, so re-running updates instead of duplicating.

    python -m scripts.seed_shortlist_demo [--donors 400]

Run AFTER scripts/seed_demo_data.py (seed 1337: 1,000 donors, 61 facilities,
20 cities, recorded routes). Nothing that seed created is modified, so the
paper's E1-E4 experiments, which use that data, are unaffected.

What is created (all synthetic, all visibly marked):

* ``--donors`` synthetic donors in total (default 400), e-mail
  ``sim-<persona>-<key>@synthetic.lifelink.example`` (RFC 2606 reserved,
  never deliverable), name prefixed ``[SYNTHETIC]``, **no phone number**
  (and app/services/sms_service.py refuses to text this domain anyway).
  The persona -- accept / decline / notnow / silent / slow -- only matters
  to the DEMO_MODE simulator.
* Four scenario clusters, each around its own demo hospital (fictional
  "Demopur A-D", >= 150 km from each other and from the 20 seed cities'
  donors' 50 km radius), built so the demo shows exactly:
    A  three needed, three accept                 -> SATISFIED
    B  fifteen eligible, only two accept          -> PARTIAL, 1 short
    C  nobody eligible (distance / interval / ...) -> NO_ELIGIBLE
    D  the top-ranked donor never answers          -> #1 TIMED_OUT, #2 confirmed
* The remaining donors spread over the 20 seed cities with coherent,
  varied attributes: blood group (generic weighting), availability,
  donation interval state consistent with DONOR_ELIGIBILITY_POLICY,
  shared/unshared and fresh/stale locations, identity verification,
  facility-verified donation records, recent/old/no availability
  confirmations, and a response history (expired historical requests with
  real alert and response rows) so reliability ranges from good to poor to
  unknown.

Refuses to run when ENVIRONMENT=production.
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.eligibility import donation_interval_days
from app.core.time import utcnow
from app.db.session import SessionLocal
from app.models.blood_request import BloodRequest, DonorResponse
from app.models.donation import Donation
from app.models.enums import (
    BloodRequestStatus,
    BloodType,
    DonationVerificationStatus,
    DonorAvailabilityState,
    DonorResponseStatus,
    FacilityType,
    Urgency,
)
from app.models.hospital import HospitalProfile
from app.models.notification import Notification
from app.models.profile import DonorProfile
from app.models.user import User, UserRole
from scripts.seed_demo_data import BLOOD_TYPE_WEIGHTS, CITIES, DEMO_PASSWORD, _get_or_create_user

SEED = 20261031
DOMAIN = "synthetic.lifelink.example"
HOSPITAL_DOMAIN = "lifelink-demo.example.com"
KM_PER_DEG = 111.195

# Fictional demo towns, deliberately away from the 20 seed cities.
CLUSTERS = {
    "A": ("Demopur A", 21.0, 76.6),
    "B": ("Demopur B", 22.3, 80.6),
    "C": ("Demopur C", 24.4, 82.3),
    "D": ("Demopur D", 19.4, 81.4),
}
COMPATIBLE_WITH_A_POS = [BloodType.A_POS, BloodType.A_NEG, BloodType.O_POS, BloodType.O_NEG]


def _offset(lat: float, lon: float, km: float, rng) -> tuple[float, float]:
    """A point `km` north-east-ish of (lat, lon) (great-circle within ~1%)."""
    import math

    bearing = rng.uniform(0, math.pi / 2)
    return (lat + km * math.cos(bearing) / KM_PER_DEG,
            lon + km * math.sin(bearing) / (KM_PER_DEG * math.cos(math.radians(lat))))


def _hospital(db: Session, key: str) -> HospitalProfile:
    town, lat, lon = CLUSTERS[key]
    user, _ = _get_or_create_user(db, f"shortlist.hospital.{key.lower()}@{HOSPITAL_DOMAIN}",
                                  full_name=f"[SYNTHETIC] {town} Hospital", role=UserRole.HOSPITAL)
    hp = db.scalar(select(HospitalProfile).where(HospitalProfile.user_id == user.id))
    if hp is None:
        hp = HospitalProfile(user_id=user.id)
        db.add(hp)
    hp.name, hp.city, hp.latitude, hp.longitude = f"{town} Demo Hospital", town, lat, lon
    hp.verified, hp.facility_type = True, FacilityType.HOSPITAL
    hp.address = f"Synthetic demo address, {town}"
    db.flush()
    return hp


def _donor(db: Session, *, persona: str, key: str, name: str, blood: BloodType,
           city: str, lat: float | None, lon: float | None, available: bool = True,
           last_donation_days: int | None = None, verified: bool = False,
           location_age_days: int | None = 5, active_days_ago: int | None = 3,
           verified_donations: int = 0, verifier: User | None = None) -> User:
    now = utcnow()
    user, _ = _get_or_create_user(db, f"sim-{persona}-{key}@{DOMAIN}",
                                  full_name=f"[SYNTHETIC] {name}", role=UserRole.DONOR)
    dp = db.scalar(select(DonorProfile).where(DonorProfile.user_id == user.id))
    if dp is None:
        dp = DonorProfile(user_id=user.id)
        db.add(dp)
    dp.blood_type, dp.city, dp.phone = blood, city, None
    dp.share_location = lat is not None
    dp.latitude, dp.longitude = lat, lon
    dp.location_updated_at = (now - timedelta(days=location_age_days)
                              if lat is not None and location_age_days is not None else None)
    dp.is_available = available
    dp.availability_state = (DonorAvailabilityState.AVAILABLE if available
                             else DonorAvailabilityState.UNAVAILABLE)
    dp.availability_confirmed_at = (now - timedelta(days=active_days_ago)
                                    if active_days_ago is not None else None)
    dp.last_donation_date = (date.today() - timedelta(days=last_donation_days)
                             if last_donation_days is not None else None)
    dp.identity_verified = verified
    dp.weight_kg = 60
    db.flush()
    # facility-verified donation records, consistent with last_donation_date
    for i in range(verified_donations):
        donated_on = (dp.last_donation_date or date.today() - timedelta(days=200)) - timedelta(
            days=i * (donation_interval_days() + 30))
        existing = db.scalar(select(Donation).where(Donation.donor_user_id == user.id,
                                                    Donation.donated_on == donated_on))
        if existing is not None and existing.verified_by_user_id is None and verifier:
            # A VERIFIED donation must name who verified it and when
            # (invariant #31); repairs rows seeded before this was enforced.
            existing.verified_by_user_id, existing.verified_at = verifier.id, now
        if existing is None:
            db.add(Donation(donor_user_id=user.id, units=1, donated_on=donated_on,
                            hospital_name="Synthetic demo blood centre",
                            note="synthetic demo donation (verified)",
                            verification_status=DonationVerificationStatus.VERIFIED,
                            verified_by_user_id=verifier.id if verifier else None,
                            verified_at=now))
    return user


def seed_clusters(db: Session, rng) -> dict[str, int]:
    counts: dict[str, int] = {}
    interval = donation_interval_days()
    recent = max(5, interval // 4)       # clearly inside the interval
    elapsed = interval + 40              # clearly outside it
    for key, (town, lat0, lon0) in CLUSTERS.items():
        hp = _hospital(db, key)
        verifier = db.get(User, hp.user_id)
        n = 0

        def add(persona, km, blood=BloodType.O_POS, located=True, *, _k=key, _lat=lat0,
                _lon=lon0, _town=town, _ver=verifier, **kw):
            nonlocal n
            n += 1
            lat, lon = _offset(_lat, _lon, km, rng) if located else (None, None)
            return _donor(db, persona=persona, key=f"{_k.lower()}{n:02d}",
                          name=f"Donor {_k}-{n:02d}", blood=blood,
                          city=_town if located else "Elsewhere",
                          lat=lat, lon=lon, verifier=_ver, **kw)

        if key == "A":
            for i in range(3):
                add("accept", 2 + i, blood=COMPATIBLE_WITH_A_POS[i], verified=True,
                    last_donation_days=elapsed, verified_donations=1)
            for i in range(3):
                add("decline", 7 + i)
            add("accept", 3, blood=BloodType.B_POS)                     # incompatible
            add("accept", 4, blood=BloodType.AB_POS)                    # incompatible
            add("accept", 1, last_donation_days=recent)                 # interval not elapsed
            add("accept", 2, last_donation_days=recent // 2)
            add("accept", 70)                                           # beyond 50 km
        elif key == "B":
            add("accept", 12, verified=False, active_days_ago=20)
            add("accept", 14, verified=False, active_days_ago=25)
            for i in range(7):
                add("decline", 2 + i, verified=True, verified_donations=1,
                    last_donation_days=elapsed)
            for i in range(6):
                add("silent", 9 + i, active_days_ago=None)
            for i in range(3):
                add("accept", 5 + i, available=False)                   # unavailable
            add("accept", 60)
            add("accept", 95)                                           # beyond 50 km
        elif key == "C":
            add("accept", 80)
            add("accept", 120)                                          # beyond 50 km
            add("accept", 3, last_donation_days=recent)
            add("accept", 4, last_donation_days=1)                      # interval
            add("accept", 2, available=False)                           # unavailable
            add("accept", 1, blood=BloodType.B_NEG)                     # incompatible
            add("accept", 0, located=False)                             # location unknown
        else:  # D: best-looking donor never answers
            add("silent", 0.8, verified=True, verified_donations=3, active_days_ago=1,
                last_donation_days=elapsed)
            add("accept", 9, verified=False, active_days_ago=40)
        counts[key] = n
    db.commit()
    return counts


def seed_population(db: Session, rng, count: int) -> dict[str, int]:
    """`count` donors over the 20 seed cities, plus a response history."""
    now = utcnow()
    interval = donation_interval_days()
    personas = (["accept"] * 35 + ["decline"] * 25 + ["notnow"] * 10 + ["silent"] * 20
                + ["slow"] * 10)
    types, weights = zip(*BLOOD_TYPE_WEIGHTS, strict=True)
    stats = {"donors": 0, "stale_location": 0, "no_location": 0, "in_interval": 0,
             "unavailable": 0, "verified": 0, "history_requests": 0, "history_responses": 0}
    by_city: dict[str, list[tuple[User, str]]] = {}
    verifiers: dict[str, User | None] = {}
    for i in range(count):
        city, state, clat, clon = CITIES[i % len(CITIES)]
        if city not in verifiers:  # a hospital of the seed network in that city verifies
            hp = db.scalar(select(HospitalProfile).where(HospitalProfile.city == city))
            verifiers[city] = db.get(User, hp.user_id) if hp is not None else None
        persona = rng.choice(personas)
        located = rng.random() < 0.7
        lat = clat + rng.uniform(-0.15, 0.15) if located else None
        lon = clon + rng.uniform(-0.15, 0.15) if located else None
        roll = rng.random()
        last = (None if roll < 0.3 else rng.randint(1, interval - 1) if roll < 0.5
                else rng.randint(interval, interval + 400))
        loc_age = rng.choice([2, 10, 30, 60, 120, 200, 400]) if located else None
        active = rng.choice([1, 3, 7, 14, 30, 60, 180, None])
        verified = rng.random() < 0.5
        available = rng.random() < 0.85
        user = _donor(db, persona=persona, key=f"x{i:04d}", name=f"Donor X-{i:04d}",
                      blood=rng.choices(types, weights=weights, k=1)[0], city=f"{city}, {state}",
                      lat=lat, lon=lon, available=available, last_donation_days=last,
                      verified=verified, location_age_days=loc_age, active_days_ago=active,
                      verified_donations=rng.choice([0, 0, 1, 2]) if last else 0,
                      verifier=verifiers[city])
        by_city.setdefault(city, []).append((user, persona))
        stats["donors"] += 1
        stats["no_location"] += 0 if located else 1
        stale = bool(loc_age and loc_age > settings.donor_location_stale_days)
        stats["stale_location"] += int(stale)
        stats["in_interval"] += int(last is not None and last < interval)
        stats["unavailable"] += 0 if available else 1
        stats["verified"] += int(verified)
        if i % 100 == 99:
            db.flush()
    db.commit()

    # Response history: per city, a few EXPIRED historical requests that
    # alerted a sample of donors; each answered (or not) per persona.
    answer_p = {"accept": 0.9, "slow": 0.6, "decline": 0.85, "notnow": 0.7, "silent": 0.05}
    for city, members in by_city.items():
        owner = db.scalar(select(HospitalProfile).where(HospitalProfile.city == city))
        if owner is None:
            continue
        for h in range(3):
            marker = f"[SYNTHETIC history] {city} #{h + 1}"
            req = db.scalar(select(BloodRequest).where(BloodRequest.note == marker))
            if req is None:
                created = now - timedelta(days=30 + 40 * h)
                req = BloodRequest(requester_user_id=owner.user_id, patient_user_id=owner.user_id,
                                   blood_type=BloodType.O_POS, units_needed=1,
                                   urgency=Urgency.URGENT, status=BloodRequestStatus.EXPIRED,
                                   hospital_name=owner.name, city=city, note=marker,
                                   expired_at=created + timedelta(days=3))
                db.add(req)
                db.flush()
                stats["history_requests"] += 1
            for user, persona in members:
                if rng.random() > 0.6:
                    continue
                if db.scalar(select(Notification.id).where(
                        Notification.user_id == user.id, Notification.type == "blood_request.new",
                        Notification.data["request_id"].astext == str(req.id))) is None:
                    db.add(Notification(user_id=user.id, type="blood_request.new",
                                        title="A patient near you needs blood (synthetic history)",
                                        data={"request_id": str(req.id)}))
                if rng.random() < answer_p[persona] and db.scalar(select(DonorResponse.id).where(
                        DonorResponse.request_id == req.id,
                        DonorResponse.donor_user_id == user.id)) is None:
                    status = (DonorResponseStatus.ACCEPTED if persona in ("accept", "slow")
                              else DonorResponseStatus.NOT_NOW if persona == "notnow"
                              else DonorResponseStatus.DECLINED)
                    db.add(DonorResponse(request_id=req.id, donor_user_id=user.id, status=status,
                                         note="synthetic history"))
                    stats["history_responses"] += 1
        db.flush()
    db.commit()
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--donors", type=int, default=400,
                    help="total synthetic shortlist donors (clusters + population)")
    args = ap.parse_args(argv)
    if settings.is_production:
        print("Refusing to seed synthetic donors: ENVIRONMENT=production.", file=sys.stderr)
        return 1
    rng = random.Random(SEED)
    db = SessionLocal()
    try:
        print(f"Seeding shortlist demo (seed={SEED}, policy={settings.donor_eligibility_policy}, "
              f"DB={settings.database_url.split('@')[-1]})")
        clusters = seed_clusters(db, rng)
        n_cluster = sum(clusters.values())
        population = seed_population(db, rng, max(0, args.donors - n_cluster))
        total = db.scalar(select(func.count()).select_from(User).where(
            User.email.like(f"%@{DOMAIN}")))
        print(f"Scenario clusters: {clusters} (= {n_cluster} donors)")
        print(f"Population: {population}")
        print(f"Synthetic shortlist donors in database: {total}")
        print(f"Demo hospitals: shortlist.hospital.[a-d]@{HOSPITAL_DOMAIN} / password "
              f"{DEMO_PASSWORD!r}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
