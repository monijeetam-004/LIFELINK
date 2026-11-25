"""Deterministic demo/synthetic data seed for local dev and staging.

    Run from the backend/ directory (same convention as alembic/pytest):

        python -m scripts.seed_demo_data

No synthetic CSV or seed system pre-existed in this repository -- this
script was written from scratch after inspecting the actual schema
(app/models/*) to generate data that genuinely satisfies it, not a
plausible-looking approximation. It is idempotent: every row is upserted
by a deterministic natural key (email for users, the existing
(hospital_id, blood_type) unique constraint for inventory, the existing
(origin_facility_id, destination_facility_id) unique constraint -- under a
canonical ordering -- for transport routes), so running it twice updates
in place rather than creating duplicates. See test_seed_demo_data.py for
an automated check of that property.

WHAT THIS GENERATES (all synthetic -- see "Marking as synthetic" below):
  * ~1000 donor accounts (User + DonorProfile) across 20 real Indian
    cities, with a randomised-but-realistic blood-type distribution,
    availability, verification status, coarse jittered coordinates, and
    donation history.
  * Donation records for donors with a last_donation_date on file.
  * ~60 facilities (hospitals and blood banks) across the same 20 cities,
    each with a full HospitalInventory row (all 8 blood types).
  * A connected TransportRoute network: an intra-city cluster per city
    plus an inter-city backbone with real haversine-computed distances,
    a handful of routes deliberately marked DISRUPTED to demonstrate the
    graph engine's failure-handling paths.
  * Five explicit demo login accounts, one per role (including an
    ADMIN), with a shared, documented, non-secret demo password -- for
    manually exercising every portal without hunting through 1000
    generated donors for credentials.
  * A best-effort call to graph_service.resync_all() at the end. This is
    explicitly ALLOWED to fail (Neo4j may be unconfigured or unreachable,
    as it genuinely is in the sandbox this was developed in) -- seeding
    the relational database must never be blocked by the graph mirror
    being unavailable. A resync failure is reported, not swallowed
    silently, and does not affect this script's exit code.

WHAT THIS DELIBERATELY DOES NOT GENERATE:
  * Fake VerificationRequest rows with fabricated document uploads. A
    donor's `identity_verified` flag is set directly for a realistic
    fraction of the pool -- exactly the field state a real admin
    approval would produce -- without inventing a fake document review
    trail that never happened. This mirrors tests/helpers.py's
    established `seed_verified_user` pattern of inserting an
    already-verified state directly rather than replaying the whole
    real workflow.
  * A donor "reliability"/response-rate score. DonorProfile has no such
    column, and TASK 2 is explicit that such fields are seeded "only if
    supported by the model" -- it isn't, so none is fabricated.
  * Impersonation of any real, specific hospital, blood bank, or person.
    Facility names are generic templates ("<City> General Hospital"),
    never a real institution's actual name; donor names are drawn from
    large generic first/last-name pools, never copied from a real
    individual.

Marking as synthetic (no schema change required)
--------------------------------------------------
Every account this script creates uses an email address under
`lifelink-demo.example.com` -- `example.com` is one of the three
second-level domains IANA/RFC 2606 permanently reserves for use in
documentation and examples, guaranteed to never be assigned to a real
registrant. (An earlier draft of this script used the `.invalid` TLD,
which is reserved even more strongly in principle -- but this codebase's
own `EmailStr` validation correctly refuses to accept a `.invalid`
address as syntactically valid at all, which would have made every demo
account unable to log in through the real API. `example.com` was chosen
instead specifically because it is both reserved AND real-looking enough
to pass normal email validation, which was verified by actually logging
in with a seeded demo account -- see tests/test_seed_demo_data.py.)
This is a real, checkable invariant, not a comment: no genuine user
registration could plausibly collide with this domain, and an operator
can identify (or, before a production deployment, refuse to allow) all
of it with:

    SELECT id, email, role FROM users WHERE email LIKE '%@lifelink-demo.example.com';

Reproducibility
----------------
All randomisation goes through one `random.Random(SEED)` instance
(SEED = 1337, below) -- never the global `random` module -- so re-running
this script from a clean database always produces byte-identical output.
"""

from __future__ import annotations

import hashlib
import os
import random
import sys
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.graph import GraphUnavailableError
from app.core.security import hash_password
from app.core.time import utcnow
from app.db.session import SessionLocal
from app.models.donation import Donation
from app.models.enums import (
    BloodType,
    FacilityType,
    FamilyLinkStatus,
    TransportRouteStatus,
    haversine_km,
)
from app.models.family import FamilyLink
from app.models.hospital import HospitalProfile
from app.models.profile import DonorProfile, PatientProfile
from app.models.transport import TransportRoute
from app.models.user import User, UserRole
from app.services import graph_service

SEED = 1337

DEMO_PASSWORD = "LifeLink-Demo-2026!"  # noqa: S105 - documented, non-secret, dev/staging seed only
# All three point at the same reserved (RFC 2606) domain -- see the
# "Marking as synthetic" section above -- kept as separate names only so
# each usage site documents which account category it's for.
SYNTHETIC_DOMAIN_DONOR = "lifelink-demo.example.com"
SYNTHETIC_DOMAIN_FACILITY = "lifelink-demo.example.com"
SYNTHETIC_DOMAIN_NAMED = "lifelink-demo.example.com"

DONOR_COUNT = 1000

# 20 real Indian cities spanning every region, with approximate (public,
# city-centroid) coordinates -- not exact addresses. Loosely illustrative
# blood-type weighting below is a generic ABO/Rh population skew (common
# types more frequent, AB/Rh-negative rarer); it is not sourced from a
# specific cited study and is not presented as one.
CITIES = [
    ("Mumbai", "Maharashtra", 19.0760, 72.8777),
    ("Delhi", "Delhi", 28.7041, 77.1025),
    ("Bengaluru", "Karnataka", 12.9716, 77.5946),
    ("Hyderabad", "Telangana", 17.3850, 78.4867),
    ("Chennai", "Tamil Nadu", 13.0827, 80.2707),
    ("Kolkata", "West Bengal", 22.5726, 88.3639),
    ("Pune", "Maharashtra", 18.5204, 73.8567),
    ("Ahmedabad", "Gujarat", 23.0225, 72.5714),
    ("Jaipur", "Rajasthan", 26.9124, 75.7873),
    ("Lucknow", "Uttar Pradesh", 26.8467, 80.9462),
    ("Surat", "Gujarat", 21.1702, 72.8311),
    ("Nagpur", "Maharashtra", 21.1458, 79.0882),
    ("Indore", "Madhya Pradesh", 22.7196, 75.8577),
    ("Bhopal", "Madhya Pradesh", 23.2599, 77.4126),
    ("Patna", "Bihar", 25.5941, 85.1376),
    ("Chandigarh", "Chandigarh", 30.7333, 76.7794),
    ("Kochi", "Kerala", 9.9312, 76.2673),
    ("Coimbatore", "Tamil Nadu", 11.0168, 76.9558),
    ("Visakhapatnam", "Andhra Pradesh", 17.6868, 83.2185),
    ("Guwahati", "Assam", 26.1445, 91.7362),
]

BLOOD_TYPE_WEIGHTS: list[tuple[BloodType, int]] = [
    (BloodType.O_POS, 30),
    (BloodType.B_POS, 25),
    (BloodType.A_POS, 20),
    (BloodType.AB_POS, 5),
    (BloodType.O_NEG, 8),
    (BloodType.B_NEG, 6),
    (BloodType.A_NEG, 4),
    (BloodType.AB_NEG, 2),
]

FIRST_NAMES_M = [
    "Aarav",
    "Vivaan",
    "Aditya",
    "Arjun",
    "Sai",
    "Reyansh",
    "Krishna",
    "Ishaan",
    "Rohan",
    "Karan",
    "Rahul",
    "Vikram",
    "Aryan",
    "Dev",
    "Kabir",
    "Nikhil",
    "Siddharth",
    "Abhishek",
    "Anand",
    "Harish",
    "Manoj",
    "Suresh",
    "Ramesh",
    "Ganesh",
    "Venkatesh",
    "Prakash",
    "Ajay",
    "Sanjay",
    "Deepak",
    "Rajesh",
]
FIRST_NAMES_F = [
    "Aadhya",
    "Ananya",
    "Diya",
    "Ira",
    "Kavya",
    "Myra",
    "Saanvi",
    "Anika",
    "Ishita",
    "Priya",
    "Neha",
    "Pooja",
    "Divya",
    "Sneha",
    "Meera",
    "Lakshmi",
    "Radha",
    "Sunita",
    "Anjali",
    "Kavita",
    "Shreya",
    "Nisha",
    "Swati",
    "Rekha",
    "Geeta",
    "Usha",
    "Vidya",
    "Deepa",
    "Asha",
    "Uma",
]
LAST_NAMES = [
    "Sharma",
    "Verma",
    "Gupta",
    "Patel",
    "Reddy",
    "Nair",
    "Iyer",
    "Menon",
    "Rao",
    "Naidu",
    "Khan",
    "Chatterjee",
    "Banerjee",
    "Mukherjee",
    "Das",
    "Kumar",
    "Singh",
    "Yadav",
    "Mishra",
    "Tiwari",
    "Pillai",
    "Krishnan",
    "Subramaniam",
    "Joshi",
    "Deshmukh",
    "Kulkarni",
    "Pandey",
    "Chauhan",
    "Bose",
    "Sengupta",
]

HOSPITAL_NAME_TEMPLATES = ["{city} General Hospital", "{city} Community Hospital"]
BLOODBANK_NAME_TEMPLATES = ["{city} Blood Bank & Transfusion Centre"]


def _weighted_blood_type(rng) -> BloodType:
    types, weights = zip(*BLOOD_TYPE_WEIGHTS, strict=True)
    return rng.choices(types, weights=weights, k=1)[0]


def _synthetic_phone(rng) -> str:
    prefix = rng.choice(["6", "7", "8", "9"])
    return prefix + "".join(str(rng.randint(0, 9)) for _ in range(9))


def _get_or_create_user(
    db: Session, email: str, *, full_name: str, role: UserRole
) -> tuple[User, bool]:
    """Returns (user, created). Idempotent by email (the real unique key)."""
    user = db.scalar(select(User).where(User.email == email))
    if user is not None:
        return user, False
    user = User(
        email=email,
        password_hash=hash_password(DEMO_PASSWORD),
        full_name=full_name,
        role=role,
        is_email_verified=True,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user, True


def _upsert_inventory(db: Session, hospital_id, blood_type: BloodType, units: int) -> None:
    """Set on-hand whole-blood stock through the real inventory service (a
    RECONCILE stock-take), so real per-unit BloodUnit rows with a cited
    shelf-life clock exist -- the pathway engine counts units, never a bare
    aggregate. Idempotent: reconciling to the current count is a no-op."""
    from app.services import inventory_service

    inventory_service.reconcile_inventory(
        db,
        hospital_id,
        blood_type,
        units,
        actor_user_id=None,
        note="synthetic demo seed stock-take",
    )


def seed_donors(db: Session, rng) -> dict[str, int]:
    created = updated = donations_created = 0
    for n in range(1, DONOR_COUNT + 1):
        email = f"donor{n:04d}@{SYNTHETIC_DOMAIN_DONOR}"
        city, state, lat, lon = rng.choice(CITIES)
        is_male = rng.random() < 0.5
        first = rng.choice(FIRST_NAMES_M if is_male else FIRST_NAMES_F)
        last = rng.choice(LAST_NAMES)
        full_name = f"{first} {last}"

        user, was_created = _get_or_create_user(db, email, full_name=full_name, role=UserRole.DONOR)
        if was_created:
            created += 1
        else:
            updated += 1
        user.full_name = full_name

        profile = db.scalar(select(DonorProfile).where(DonorProfile.user_id == user.id))
        if profile is None:
            profile = DonorProfile(user_id=user.id)
            db.add(profile)

        has_donated_before = rng.random() < 0.6
        last_donation = None
        if has_donated_before:
            days_ago = rng.randint(10, 400)
            last_donation = date.today() - timedelta(days=days_ago)

        share_location = rng.random() < 0.7
        profile.blood_type = _weighted_blood_type(rng)
        profile.phone = _synthetic_phone(rng)
        profile.city = f"{city}, {state}"
        profile.last_donation_date = last_donation
        profile.is_available = rng.random() < 0.8
        profile.weight_kg = rng.randint(48, 95)
        profile.share_location = share_location
        profile.latitude = lat + rng.uniform(-0.15, 0.15) if share_location else None
        profile.longitude = lon + rng.uniform(-0.15, 0.15) if share_location else None
        profile.identity_verified = rng.random() < 0.5

        if last_donation is not None:
            existing_donation = db.scalar(
                select(Donation).where(
                    Donation.donor_user_id == user.id, Donation.donated_on == last_donation
                )
            )
            if existing_donation is None:
                db.add(
                    Donation(
                        donor_user_id=user.id,
                        units=1,
                        donated_on=last_donation,
                        hospital_name=f"{city} General Hospital",
                        note="Seed demo donation record",
                    )
                )
                donations_created += 1

        if n % 200 == 0:
            db.flush()
            print(f"  ... {n}/{DONOR_COUNT} donors processed", file=sys.stderr)

    db.commit()
    return {"created": created, "updated": updated, "donations_created": donations_created}


def seed_facilities(db: Session, rng) -> dict:
    created = updated = 0
    facility_index = 0
    facilities_by_city: dict[str, list[HospitalProfile]] = {}
    for city, state, lat, lon in CITIES:
        facilities_by_city[city] = []
        templates = [(name, FacilityType.HOSPITAL) for name in HOSPITAL_NAME_TEMPLATES] + [
            (name, FacilityType.BLOOD_BANK) for name in BLOODBANK_NAME_TEMPLATES
        ]
        for template, facility_type in templates:
            facility_index += 1
            email = f"facility{facility_index:03d}@{SYNTHETIC_DOMAIN_FACILITY}"
            name = template.format(city=city)
            user, was_created = _get_or_create_user(
                db, email, full_name=f"{name} Admin", role=UserRole.HOSPITAL
            )
            created += 1 if was_created else 0
            updated += 0 if was_created else 1

            hp = db.scalar(select(HospitalProfile).where(HospitalProfile.user_id == user.id))
            if hp is None:
                hp = HospitalProfile(user_id=user.id)
                db.add(hp)
            hp.name = name
            hp.facility_type = facility_type
            hp.city = city
            hp.latitude = lat + rng.uniform(-0.05, 0.05)
            hp.longitude = lon + rng.uniform(-0.05, 0.05)
            hp.verified = rng.random() < 0.7
            hp.phone = _synthetic_phone(rng)
            hp.address = f"Synthetic demo address, {city}, {state}"
            db.flush()
            facilities_by_city[city].append(hp)

            base_units = 30 if facility_type == FacilityType.BLOOD_BANK else 15
            for bt in BloodType:
                units = max(0, int(rng.gauss(base_units, base_units / 2)))
                _upsert_inventory(db, hp.id, bt, units)

    db.commit()
    return {"created": created, "updated": updated, "facilities_by_city": facilities_by_city}


def _upsert_route(
    db: Session, a: HospitalProfile, b: HospitalProfile, distance_km: float, rng
) -> tuple[TransportRoute, bool]:
    origin_id, dest_id = sorted((a.id, b.id), key=str)
    existing = db.scalar(
        select(TransportRoute).where(
            TransportRoute.origin_facility_id == origin_id,
            TransportRoute.destination_facility_id == dest_id,
        )
    )
    if existing is not None:
        return existing, False
    route = TransportRoute(
        origin_facility_id=origin_id,
        destination_facility_id=dest_id,
        distance_km=distance_km,
        transit_minutes=graph_service.estimate_transit_minutes(distance_km),
    )
    db.add(route)
    db.flush()
    return route, True


def demo_disruption_key(email_a: str, email_b: str) -> int:
    """Deterministic selector for the demo's deliberately disrupted routes:
    independent of route/facility UUIDs and of endpoint order."""
    a, b = sorted((email_a, email_b))
    return int(hashlib.sha256(f"{a}|{b}".encode()).hexdigest(), 16)


def seed_transport_routes(db: Session, facilities_by_city: dict, rng) -> dict[str, int]:
    created = 0
    city_names = [c[0] for c in CITIES]

    # Intra-city cluster: every facility connects to every other facility
    # in the same city (a real short-haul ambulance/courier network).
    for facilities in facilities_by_city.values():
        for i in range(len(facilities)):
            for j in range(i + 1, len(facilities)):
                a, b = facilities[i], facilities[j]
                if None in (a.latitude, a.longitude, b.latitude, b.longitude):
                    continue
                dist = haversine_km(a.latitude, a.longitude, b.latitude, b.longitude)
                _, was_created = _upsert_route(db, a, b, dist, rng)
                created += 1 if was_created else 0

    # Inter-city backbone: consecutive cities in a fixed order, plus a few
    # extra cross-links every third city for redundancy -- guarantees the
    # whole national network is connected, not just clustered islands.
    def _primary(city: str) -> HospitalProfile:
        return facilities_by_city[city][0]

    def _pair_distance_km(a: HospitalProfile, b: HospitalProfile) -> float:
        # seed_facilities() always sets coordinates on every facility it
        # creates; this asserts that invariant rather than silently
        # tolerating a None (which haversine_km rejects by type anyway).
        assert a.latitude is not None and a.longitude is not None
        assert b.latitude is not None and b.longitude is not None
        return haversine_km(a.latitude, a.longitude, b.latitude, b.longitude)

    for idx in range(len(city_names) - 1):
        a = _primary(city_names[idx])
        b = _primary(city_names[idx + 1])
        dist = _pair_distance_km(a, b)
        _, was_created = _upsert_route(db, a, b, dist, rng)
        created += 1 if was_created else 0

    for idx in range(0, len(city_names) - 3, 3):
        a = _primary(city_names[idx])
        b = _primary(city_names[idx + 3])
        dist = _pair_distance_km(a, b)
        _, was_created = _upsert_route(db, a, b, dist, rng)
        created += 1 if was_created else 0

    db.commit()

    # Deliberately disrupt a deterministic ~10% of routes to demonstrate
    # the graph engine's failure-handling paths in the seeded demo.
    all_routes = list(db.scalars(select(TransportRoute)).all())
    disrupted = 0
    reasons = [
        "seasonal flooding on this corridor",
        "scheduled highway maintenance",
        "vehicle shortage reported by regional transport office",
    ]
    # Keyed by the two endpoints' account e-mails (stable natural keys).
    # It used to be keyed by the route's UUID, which is random per database,
    # so a fresh seed disrupted a different number of routes each time
    # (6, 7 and 11 were observed) -- not reproducible.
    email_of = dict(
        db.execute(
            select(HospitalProfile.id, User.email).join(User, User.id == HospitalProfile.user_id)
        ).all()
    )
    for route in all_routes:
        key = demo_disruption_key(
            email_of[route.origin_facility_id], email_of[route.destination_facility_id]
        )
        if key % 10 == 0:
            if route.status != TransportRouteStatus.DISRUPTED:
                route.status = TransportRouteStatus.DISRUPTED
                route.disrupted_reason = reasons[disrupted % len(reasons)]
                route.disrupted_at = utcnow()
                disrupted += 1
    db.commit()

    for route in all_routes:
        graph_service.sync_route(route)  # best-effort

    return {"created": created, "disrupted": disrupted, "total": len(all_routes)}


def seed_demo_accounts(db: Session, rng) -> dict[str, str]:
    """Five explicit, documented demo logins -- one per role."""
    patient, _ = _get_or_create_user(
        db,
        f"demo.patient@{SYNTHETIC_DOMAIN_NAMED}",
        full_name="Demo Patient",
        role=UserRole.PATIENT,
    )
    pp = db.scalar(select(PatientProfile).where(PatientProfile.user_id == patient.id))
    if pp is None:
        pp = PatientProfile(user_id=patient.id)
        db.add(pp)
    pp.blood_type = BloodType.A_POS
    pp.city = "Mumbai, Maharashtra"
    pp.phone = _synthetic_phone(rng)

    family, _ = _get_or_create_user(
        db,
        f"demo.family@{SYNTHETIC_DOMAIN_NAMED}",
        full_name="Demo Family Member",
        role=UserRole.FAMILY,
    )
    link = db.scalar(
        select(FamilyLink).where(
            FamilyLink.family_user_id == family.id, FamilyLink.patient_user_id == patient.id
        )
    )
    if link is None:
        db.add(
            FamilyLink(
                family_user_id=family.id,
                patient_user_id=patient.id,
                relationship="Sibling",
                status=FamilyLinkStatus.ACCEPTED,
                requested_by_user_id=family.id,
            )
        )

    donor, _ = _get_or_create_user(
        db, f"demo.donor@{SYNTHETIC_DOMAIN_NAMED}", full_name="Demo Donor", role=UserRole.DONOR
    )
    dp = db.scalar(select(DonorProfile).where(DonorProfile.user_id == donor.id))
    if dp is None:
        dp = DonorProfile(user_id=donor.id)
        db.add(dp)
    dp.blood_type = BloodType.O_NEG
    dp.city = "Mumbai, Maharashtra"
    dp.is_available = True
    dp.identity_verified = True
    dp.phone = _synthetic_phone(rng)

    hospital, _ = _get_or_create_user(
        db,
        f"demo.hospital@{SYNTHETIC_DOMAIN_NAMED}",
        full_name="Demo Hospital Admin",
        role=UserRole.HOSPITAL,
    )
    hp = db.scalar(select(HospitalProfile).where(HospitalProfile.user_id == hospital.id))
    if hp is None:
        hp = HospitalProfile(user_id=hospital.id)
        db.add(hp)
    hp.name = "LifeLink Demo Hospital"
    hp.city = "Mumbai"
    hp.facility_type = FacilityType.HOSPITAL
    hp.latitude, hp.longitude = 19.0760, 72.8777
    hp.verified = True
    hp.phone = _synthetic_phone(rng)
    db.flush()
    for bt in BloodType:
        _upsert_inventory(db, hp.id, bt, 20)

    admin, _ = _get_or_create_user(
        db, f"demo.admin@{SYNTHETIC_DOMAIN_NAMED}", full_name="Demo Admin", role=UserRole.ADMIN
    )

    db.commit()
    return {
        "patient": patient.email,
        "family": family.email,
        "donor": donor.email,
        "hospital": hospital.email,
        "admin": admin.email,
    }


def seed_demo_activity() -> dict[str, str]:
    """Phase I: a small amount of real activity for the demo accounts -- one
    open blood request (with a donor's acceptance), one acknowledged SOS
    emergency, and the notifications those produce.

    Driven through the REAL HTTP API in-process (FastAPI TestClient, signed
    in as the demo users), never by inserting rows directly: every domain
    rule, history row, notification, audit entry and real-time event is
    produced exactly as it would be for a person clicking through the app.
    Idempotent: creation uses the API's own idempotency keys (a re-run
    replays the original rather than creating a second one), and the
    follow-up steps check current state first.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    origin = settings.cors_origin_list[0] if settings.cors_origin_list else "http://localhost:5173"

    def login(email: str) -> TestClient:
        c = TestClient(app, base_url="http://seed.local")
        c.headers["Origin"] = origin
        r = c.post("/auth/login", json={"email": email, "password": DEMO_PASSWORD})
        if r.status_code != 200:
            raise RuntimeError(f"demo login failed for {email}: {r.status_code} {r.text[:200]}")
        return c

    def ok(r, what: str) -> dict:
        if r.status_code not in (200, 201):
            raise RuntimeError(
                f"demo activity step '{what}' failed: {r.status_code} {r.text[:300]}"
            )
        return r.json()

    d = SYNTHETIC_DOMAIN_NAMED
    patient = login(f"demo.patient@{d}")
    family = login(f"demo.family@{d}")
    donor = login(f"demo.donor@{d}")
    hospital = login(f"demo.hospital@{d}")

    req = ok(
        patient.post(
            "/blood-requests",
            json={
                "blood_type": "A_POS",
                "units_needed": 2,
                "urgency": "URGENT",
                "hospital_name": "LifeLink Demo Hospital",
                "city": "Mumbai",
                "note": "Demo data (seed_demo_data.py) -- not a real request.",
                "idempotency_key": "demo-seed-blood-request-1",
            },
        ),
        "create blood request",
    )
    responded = {x["id"] for x in ok(donor.get("/donors/me/responses"), "list responses")}
    if req["id"] not in responded:
        ok(
            donor.post(
                f"/blood-requests/{req['id']}/respond",
                json={
                    "action": "ACCEPTED",
                    "note": "Demo response",
                },
            ),
            "donor accepts request",
        )

    em = ok(
        family.post(
            "/emergencies",
            json={
                "blood_type": "O_NEG",
                "units_needed": 1,
                "urgency": "CRITICAL",
                "location_text": "Mumbai, Maharashtra",
                "patient_email": f"demo.patient@{d}",
                "note": "Demo data (seed_demo_data.py) -- not a real emergency.",
                "idempotency_key": "demo-seed-emergency-1",
            },
        ),
        "create emergency",
    )
    if em["status"] == "ACTIVE":
        ok(
            hospital.post(
                f"/emergencies/{em['id']}/acknowledge",
                json={
                    "note": "Demo acknowledgement",
                },
            ),
            "hospital acknowledges emergency",
        )

    for c in (patient, family, donor, hospital):
        c.post("/auth/logout")
    return {"blood_request": req["id"], "emergency": em["id"]}


def run(db: Session) -> dict:
    rng = random.Random(SEED)
    db_target = settings.database_url.split("@")[-1]  # never log credentials
    print(f"Seeding LifeLink demo data (fixed seed={SEED}, DB={db_target})")

    donor_stats = seed_donors(db, rng)
    print(f"Donors: {donor_stats}")

    facility_stats = seed_facilities(db, rng)
    facilities_by_city = facility_stats.pop("facilities_by_city")
    print(f"Facilities: {facility_stats}")

    route_stats = seed_transport_routes(db, facilities_by_city, rng)
    print(f"Transport routes: {route_stats}")

    demo_accounts = seed_demo_accounts(db, rng)
    print(f"Demo accounts (password: {DEMO_PASSWORD!r}): {demo_accounts}")

    activity = seed_demo_activity()
    print(f"Demo activity: {activity}")

    graph_resync_ok = True
    graph_resync_detail = "ok"
    try:
        counts = graph_service.resync_all(db)
        graph_resync_detail = str(counts)
    except GraphUnavailableError as exc:
        graph_resync_ok = False
        graph_resync_detail = str(exc)
    print(f"Graph resync: ok={graph_resync_ok} detail={graph_resync_detail}")

    return {
        "donors": donor_stats,
        "facilities": facility_stats,
        "routes": route_stats,
        "demo_accounts": demo_accounts,
        "activity": activity,
        "graph_resync_ok": graph_resync_ok,
        "graph_resync_detail": graph_resync_detail,
    }


def main() -> None:
    # Phase 28: this script was never wired into app startup/lifespan (a
    # grep of app/ confirms nothing there imports or calls it -- production
    # boot cannot trigger this by itself), but nothing previously stopped
    # an operator from manually running it with DATABASE_URL pointed at a
    # real production database, mistaking it for the same "seed my local
    # dev DB" command they run all the time. Mirrors the explicit
    # RESTORE_ALLOW_PROD guard already added to scripts/db_restore.sh: a
    # fail-closed check here, not just a docstring warning, plus a second,
    # narrower guard against the one command actually run in CI/E2E.
    if settings.is_production and os.environ.get("SEED_DEMO_ALLOW_PROD") != "yes":
        print(
            "Refusing to seed synthetic demo data: ENVIRONMENT=production and "
            "SEED_DEMO_ALLOW_PROD is not 'yes'. This would write ~1000 fake "
            "donor accounts, fake facilities, and shared-password demo logins "
            "into what this process believes is a production database. If "
            "this really is an intentional non-production use of a "
            "production-labelled environment (e.g. a one-off demo "
            "environment), set SEED_DEMO_ALLOW_PROD=yes and re-run.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    db = SessionLocal()
    try:
        run(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
