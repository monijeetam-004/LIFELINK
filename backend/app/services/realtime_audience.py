"""Phase F -- who may receive a real-time event about a resource.

Every rule here MIRRORS the authorization the REST API already enforces
for reading that resource, so an event channel can never tell anyone
something the API would not:

  blood_request -> app.api.routes.blood_requests._can_view
                   (requester, patient, family with an ACCEPTED link to an
                   active patient, donors who responded, hospitals whose
                   city/name matches) + ADMIN (the /admin/blood-requests view)
  emergency     -> app.api.routes.emergencies._can_view (creator, patient,
                   linked family, responders, every hospital while open /
                   area-matched hospitals once closed) + ADMIN
  shipment      -> shipments._assert_is_party (origin/destination hospital) + ADMIN
  inventory     -> the facility's own hospital account + ADMIN (facility detail)
  donation      -> the donor (/donors/me/donations), the linked facility
                   (donation_review queue) + ADMIN
  verification  -> the submitter (/verifications/me) + ADMIN
  notification  -> the owner only (never ADMIN: notifications are private)
  family_link   -> the two parties (family.list_links: family OR patient);
                   no ADMIN (there is no admin family-link API)
  certificate   -> the donor (/certificates) + ADMIN (/admin/certificates)

The audience may be SMALLER than the full API-visible set -- deliberately,
for one case: donors who have not responded to an open request/emergency
are not individually enumerated (potentially every compatible donor in a
city); they already receive a `notification.created` event for the
matching-donor notification itself. It is never LARGER: that is the
security property, asserted by tests/test_phase_f_realtime.py against
the real `_can_view` functions.

Payloads never contain anything but (type, resource_type, resource_id,
seq): even an audience member learns only "re-fetch this", and the
re-fetch goes through the normal authorized endpoint.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.blood_request import BloodRequest, DonorResponse
from app.models.certificate import Certificate
from app.models.donation import Donation
from app.models.emergency import Emergency, EmergencyResponse
from app.models.enums import EMERGENCY_OPEN_STATUSES, FamilyLinkStatus
from app.models.family import FamilyLink
from app.models.hospital import HospitalProfile
from app.models.notification import Notification
from app.models.shipment import Shipment
from app.models.user import User, UserRole
from app.models.verification import VerificationRequest

ADMIN = UserRole.ADMIN.value


def _linked_family(db: Session, patient_user_id: uuid.UUID | None) -> set[uuid.UUID]:
    """Family members with an ACCEPTED link to an ACTIVE patient -- exactly
    app.services.domain_service.has_accepted_link's rule."""
    if patient_user_id is None:
        return set()
    patient = db.get(User, patient_user_id)
    if patient is None or not patient.is_active:
        return set()
    return set(
        db.scalars(
            select(FamilyLink.family_user_id).where(
                FamilyLink.patient_user_id == patient_user_id,
                FamilyLink.status == FamilyLinkStatus.ACCEPTED,
            )
        ).all()
    )


def _hospital_users(db: Session, facility_ids) -> set[uuid.UUID]:
    ids = [f for f in facility_ids if f is not None]
    if not ids:
        return set()
    return set(db.scalars(select(HospitalProfile.user_id).where(HospitalProfile.id.in_(ids))).all())


def _blood_request(db: Session, rid: uuid.UUID) -> tuple[set[uuid.UUID], list[str]]:
    req = db.get(BloodRequest, rid)
    if req is None:
        return set(), [ADMIN]
    users = {req.requester_user_id, req.patient_user_id}
    users |= _linked_family(db, req.patient_user_id)
    users |= set(
        db.scalars(select(DonorResponse.donor_user_id).where(DonorResponse.request_id == rid)).all()
    )
    clauses = []
    if req.city:
        clauses.append(HospitalProfile.city == req.city)
    if req.hospital_name:
        clauses.append(HospitalProfile.name == req.hospital_name)
    if clauses:
        users |= set(db.scalars(select(HospitalProfile.user_id).where(or_(*clauses))).all())
    return users, [ADMIN]


def _emergency(db: Session, eid: uuid.UUID) -> tuple[set[uuid.UUID], list[str]]:
    em = db.get(Emergency, eid)
    if em is None:
        return set(), [ADMIN]
    users = {em.created_by_user_id}
    if em.patient_user_id:
        users.add(em.patient_user_id)
        users |= _linked_family(db, em.patient_user_id)
    users |= set(
        db.scalars(
            select(EmergencyResponse.responder_user_id).where(EmergencyResponse.emergency_id == eid)
        ).all()
    )
    if em.status in EMERGENCY_OPEN_STATUSES:
        users |= set(db.scalars(select(HospitalProfile.user_id)).all())
    elif em.location_text:
        loc = em.location_text.lower()
        users |= set(
            db.scalars(
                select(HospitalProfile.user_id).where(
                    HospitalProfile.city.is_not(None),
                    HospitalProfile.city != "",
                    func.strpos(loc, func.lower(HospitalProfile.city)) > 0,
                )
            ).all()
        )
    return users, [ADMIN]


def _shipment(db: Session, sid: uuid.UUID) -> tuple[set[uuid.UUID], list[str]]:
    sh = db.get(Shipment, sid)
    if sh is None:
        return set(), [ADMIN]
    return _hospital_users(db, [sh.origin_facility_id, sh.destination_facility_id]), [ADMIN]


def _inventory(db: Session, facility_id: uuid.UUID) -> tuple[set[uuid.UUID], list[str]]:
    return _hospital_users(db, [facility_id]), [ADMIN]


def _donation(db: Session, did: uuid.UUID) -> tuple[set[uuid.UUID], list[str]]:
    d = db.get(Donation, did)
    if d is None:
        return set(), [ADMIN]
    return {d.donor_user_id} | _hospital_users(db, [d.hospital_id]), [ADMIN]


def _verification(db: Session, vid: uuid.UUID) -> tuple[set[uuid.UUID], list[str]]:
    v = db.get(VerificationRequest, vid)
    if v is None:
        return set(), [ADMIN]
    return {v.user_id}, [ADMIN]


def _notification(db: Session, nid: uuid.UUID) -> tuple[set[uuid.UUID], list[str]]:
    n = db.get(Notification, nid)
    return ({n.user_id} if n is not None else set()), []


def _family_link(db: Session, lid: uuid.UUID) -> tuple[set[uuid.UUID], list[str]]:
    link = db.get(FamilyLink, lid)
    if link is None:
        return set(), []
    return {link.family_user_id, link.patient_user_id}, []


def _certificate(db: Session, cid: uuid.UUID) -> tuple[set[uuid.UUID], list[str]]:
    cert = db.get(Certificate, cid)
    if cert is None:
        return set(), [ADMIN]
    return {cert.donor_user_id}, [ADMIN]


RESOLVERS = {
    "blood_request": _blood_request,
    "emergency": _emergency,
    "shipment": _shipment,
    "inventory": _inventory,
    "donation": _donation,
    "verification": _verification,
    "notification": _notification,
    "family_link": _family_link,
    "certificate": _certificate,
}


def audience_for(
    db: Session, resource_type: str, resource_id: uuid.UUID
) -> tuple[list[uuid.UUID], list[str]]:
    users, roles = RESOLVERS[resource_type](db, resource_id)
    users.discard(None)
    return sorted(users, key=str), roles


def audience_batch(
    db: Session,
    resource_type: str,
    resource_ids,
) -> dict[uuid.UUID, tuple[list[uuid.UUID], list[str]]]:
    """Audience for many resources of one type. Notifications -- the only
    type that fans out per recipient (one per matching donor) -- resolve
    in ONE query; other types resolve per distinct id (a transaction
    touches few of those). Keeps the publisher's cost independent of how
    many donors a request notified (Phase 27's bounded-fan-out property)."""
    ids = list(dict.fromkeys(resource_ids))
    if resource_type == "notification":
        owners = dict(
            db.execute(
                select(Notification.id, Notification.user_id).where(Notification.id.in_(ids))
            ).all()
        )
        return {i: (([owners[i]] if i in owners else []), []) for i in ids}
    return {i: audience_for(db, resource_type, i) for i in ids}
