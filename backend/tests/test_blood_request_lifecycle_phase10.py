"""Phase 10 -- the formalized blood request state machine.

Covers the spec's five sections end to end: every valid transition, every
invalid one, concurrency (two hospitals fulfilling the same request, two
managers cancelling simultaneously, repeated allocation), duplicate
operations, expiry, and the audit trail every transition must leave behind.

`app.core.request_lifecycle` (the pure transition table) and
`app.services.blood_request_lifecycle` (locking + audit) are exercised
indirectly here, through the real HTTP endpoints -- these tests are about
the state machine as the API actually enforces it, not the internals in
isolation.
"""
from __future__ import annotations

import threading
import uuid
from datetime import timedelta

from app.core.time import utcnow
from app.db.session import SessionLocal
from app.models.blood_request import BloodRequest
from tests.helpers import auth_client


def _hospital(city="Metro", name="St Jude"):
    c, _ = auth_client("HOSPITAL")
    c.put("/hospitals/me", json={"name": name, "city": city})
    return c


def _ready_donor(blood_type="O_NEG"):
    c, email = auth_client("DONOR")
    c.put("/donors/me", json={"blood_type": blood_type})
    return c, email


def _request(patient, **overrides):
    payload = {"blood_type": "O_NEG"}
    payload.update(overrides)
    r = patient.post("/blood-requests", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _set_needed_by_in_past(rid: str, days_ago: int = 1) -> None:
    db = SessionLocal()
    try:
        req = db.get(BloodRequest, uuid.UUID(rid))
        assert req is not None
        req.needed_by = (utcnow() - timedelta(days=days_ago)).date()
        db.commit()
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Every valid transition
# --------------------------------------------------------------------------- #


def test_pending_to_matching_via_donor_accept() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    rid = _request(patient)

    detail = patient.get(f"/blood-requests/{rid}").json()
    assert detail["status"] == "PENDING"
    # Phase E: PARTIALLY_FULFILLED is now a real, reachable state from PENDING
    # (a hospital can grant a partial quantity before the request is ever
    # MATCHING) -- see app/core/request_lifecycle.py's TRANSITIONS table.
    assert set(detail["allowed_transitions"]) == {
        "MATCHING", "FULFILLED", "PARTIALLY_FULFILLED", "CANCELLED", "EXPIRED",
    }

    donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    detail = patient.get(f"/blood-requests/{rid}").json()
    assert detail["status"] == "MATCHING"
    assert set(detail["allowed_transitions"]) == {
        "PENDING", "FULFILLED", "PARTIALLY_FULFILLED", "CANCELLED", "EXPIRED",
    }


def test_pending_to_fulfilled_direct_hospital_path() -> None:
    """A hospital can fulfill straight from PENDING -- no donor ever accepted.
    Real, pre-existing behavior; formalized as a first-class table entry."""
    hosp = _hospital()
    patient, _ = auth_client("PATIENT")
    rid = _request(patient, city="Metro", hospital_name="St Jude")

    r = hosp.post(f"/hospitals/me/requests/{rid}/fulfill")
    assert r.status_code == 200
    detail = patient.get(f"/blood-requests/{rid}").json()
    assert detail["status"] == "FULFILLED"
    assert detail["fulfilled_at"] is not None
    assert detail["allowed_transitions"] == []


def test_pending_to_cancelled() -> None:
    patient, _ = auth_client("PATIENT")
    rid = _request(patient)
    r = patient.post(f"/blood-requests/{rid}/cancel", json={"reason": "resolved elsewhere"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "CANCELLED"
    assert body["cancelled_at"] is not None
    assert body["allowed_transitions"] == []


def test_matching_to_fulfilled() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    rid = _request(patient)
    donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})

    r = patient.post(f"/blood-requests/{rid}/fulfill")
    assert r.status_code == 200
    assert r.json()["status"] == "FULFILLED"


def test_matching_to_cancelled() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    rid = _request(patient)
    donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})

    r = patient.post(f"/blood-requests/{rid}/cancel", json={"reason": "no longer needed"})
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"


def test_matching_to_pending_reopen_on_withdrawal() -> None:
    """Every accepted donor withdrawing reopens the request rather than
    stranding it MATCHING with zero donors -- preserved, existing behavior,
    now a real table entry (and audited as a transition, not just an
    event)."""
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    rid = _request(patient)
    donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    assert patient.get(f"/blood-requests/{rid}").json()["status"] == "MATCHING"

    donor.post(f"/blood-requests/{rid}/respond", json={"action": "DECLINED"})
    assert patient.get(f"/blood-requests/{rid}").json()["status"] == "PENDING"


def test_pending_to_expired() -> None:
    patient, _ = auth_client("PATIENT")
    rid = _request(patient, needed_by=(utcnow() + timedelta(days=1)).date().isoformat())
    assert patient.get(f"/blood-requests/{rid}").json()["status"] == "PENDING"

    _set_needed_by_in_past(rid)
    detail = patient.get(f"/blood-requests/{rid}").json()
    assert detail["status"] == "EXPIRED"
    assert detail["expired_at"] is not None
    assert detail["allowed_transitions"] == []
    kinds = [e["event_type"] for e in detail["events"]]
    assert "expired" in kinds


def test_matching_to_expired() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    rid = _request(patient, needed_by=(utcnow() + timedelta(days=1)).date().isoformat())
    donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    assert patient.get(f"/blood-requests/{rid}").json()["status"] == "MATCHING"

    _set_needed_by_in_past(rid)
    assert patient.get(f"/blood-requests/{rid}").json()["status"] == "EXPIRED"


def test_no_needed_by_never_auto_expires() -> None:
    """A request with no `needed_by` has nothing to compare against -- an
    honest scope limit, not an oversight."""
    patient, _ = auth_client("PATIENT")
    rid = _request(patient)
    assert patient.get(f"/blood-requests/{rid}").json()["needed_by"] is None
    assert patient.get(f"/blood-requests/{rid}").json()["status"] == "PENDING"


# --------------------------------------------------------------------------- #
# Every invalid transition
# --------------------------------------------------------------------------- #


def test_cannot_cancel_twice() -> None:
    patient, _ = auth_client("PATIENT")
    rid = _request(patient)
    assert patient.post(f"/blood-requests/{rid}/cancel", json={}).status_code == 200
    r = patient.post(f"/blood-requests/{rid}/cancel", json={})
    assert r.status_code == 409


def test_cannot_cancel_after_fulfilment() -> None:
    hosp = _hospital()
    patient, _ = auth_client("PATIENT")
    rid = _request(patient, city="Metro", hospital_name="St Jude")
    hosp.post(f"/hospitals/me/requests/{rid}/fulfill")

    r = patient.post(f"/blood-requests/{rid}/cancel", json={"reason": "too late"})
    assert r.status_code == 409


def test_cannot_fulfill_a_cancelled_request() -> None:
    hosp = _hospital()
    patient, _ = auth_client("PATIENT")
    rid = _request(patient, city="Metro", hospital_name="St Jude")
    patient.post(f"/blood-requests/{rid}/cancel", json={})

    assert patient.post(f"/blood-requests/{rid}/fulfill").status_code == 409
    assert hosp.post(f"/hospitals/me/requests/{rid}/fulfill").status_code == 409


def test_cannot_fulfill_twice() -> None:
    patient, _ = auth_client("PATIENT")
    rid = _request(patient)
    assert patient.post(f"/blood-requests/{rid}/fulfill").status_code == 200
    assert patient.post(f"/blood-requests/{rid}/fulfill").status_code == 409


def test_cannot_act_on_an_expired_request() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    rid = _request(patient, needed_by=(utcnow() + timedelta(days=1)).date().isoformat())
    _set_needed_by_in_past(rid)
    assert patient.get(f"/blood-requests/{rid}").json()["status"] == "EXPIRED"

    assert patient.post(f"/blood-requests/{rid}/cancel", json={}).status_code == 409
    assert patient.post(f"/blood-requests/{rid}/fulfill").status_code == 409
    # /respond 404s (an expired request isn't "open" -- same code path a
    # closed request already used pre-Phase-10).
    r = donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    assert r.status_code == 404


def test_admin_cannot_force_matching_or_fulfilled() -> None:
    admin, _ = auth_client("ADMIN")
    patient, _ = auth_client("PATIENT")
    rid = _request(patient)

    for target in ("MATCHING", "FULFILLED"):
        r = admin.post(
            f"/admin/blood-requests/{rid}/transition",
            json={"target_status": target, "reason": "test"},
        )
        assert r.status_code == 400, r.text


def test_admin_transition_still_respects_the_table() -> None:
    """The admin endpoint restricts *which targets* are policy-allowed
    (CANCELLED/EXPIRED), but a target that's policy-allowed in general can
    still be unreachable from the request's actual current state -- that's
    still a 409, not a 400."""
    admin, _ = auth_client("ADMIN")
    patient, _ = auth_client("PATIENT")
    rid = _request(patient)
    admin.post(
        f"/admin/blood-requests/{rid}/transition",
        json={"target_status": "CANCELLED", "reason": "duplicate submission"},
    )
    r = admin.post(
        f"/admin/blood-requests/{rid}/transition",
        json={"target_status": "EXPIRED", "reason": "test"},
    )
    assert r.status_code == 409


# --------------------------------------------------------------------------- #
# Authorization
# --------------------------------------------------------------------------- #


def test_admin_force_cancel_and_force_expire() -> None:
    admin, _ = auth_client("ADMIN")
    patient, _ = auth_client("PATIENT")

    rid = _request(patient)
    r = admin.post(
        f"/admin/blood-requests/{rid}/transition",
        json={"target_status": "CANCELLED", "reason": "policy violation"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CANCELLED"
    assert patient.get(f"/blood-requests/{rid}").json()["status"] == "CANCELLED"

    rid2 = _request(patient)
    r = admin.post(
        f"/admin/blood-requests/{rid2}/transition",
        json={"target_status": "EXPIRED", "reason": "stale duplicate"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "EXPIRED"


def test_non_admin_cannot_force_transition() -> None:
    patient, _ = auth_client("PATIENT")
    rid = _request(patient)
    r = patient.post(
        f"/admin/blood-requests/{rid}/transition",
        json={"target_status": "CANCELLED", "reason": "nice try"},
    )
    assert r.status_code == 403


def test_stranger_cannot_cancel_someone_elses_request() -> None:
    patient, _ = auth_client("PATIENT")
    other, _ = auth_client("PATIENT")
    rid = _request(patient)
    assert other.post(f"/blood-requests/{rid}/cancel", json={}).status_code == 404


def test_donor_cannot_fulfill() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    rid = _request(patient)
    assert donor.post(f"/blood-requests/{rid}/fulfill").status_code == 404


# --------------------------------------------------------------------------- #
# Concurrency
# --------------------------------------------------------------------------- #


def test_two_hospitals_fulfilling_the_same_request_only_one_wins() -> None:
    hosp_a = _hospital(city="Metro", name="St Jude")
    hosp_b = _hospital(city="Metro", name="St Jude")  # same visibility, different account
    patient, _ = auth_client("PATIENT")
    rid = _request(patient, city="Metro", hospital_name="St Jude")

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _fulfill(client) -> None:
        barrier.wait()
        r = client.post(f"/hospitals/me/requests/{rid}/fulfill")
        results.append(r.status_code)

    threads = [
        threading.Thread(target=_fulfill, args=(hosp_a,)),
        threading.Thread(target=_fulfill, args=(hosp_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [200, 409], results
    assert patient.get(f"/blood-requests/{rid}").json()["status"] == "FULFILLED"


def test_two_managers_cancelling_simultaneously_only_one_wins() -> None:
    """A patient and their accepted family manager both cancel at once --
    two real, distinct authorized actors racing the same row lock."""
    family, fam_email = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")

    link_id = family.post(
        "/family/links", json={"patient_email": pat_email, "relationship": "son"}
    ).json()["id"]
    patient.post(f"/family/links/{link_id}/accept", json={})

    rid = _request(patient)

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _cancel(client) -> None:
        barrier.wait()
        r = client.post(f"/blood-requests/{rid}/cancel", json={"reason": "race"})
        results.append(r.status_code)

    threads = [
        threading.Thread(target=_cancel, args=(patient,)),
        threading.Thread(target=_cancel, args=(family,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [200, 409], results
    assert patient.get(f"/blood-requests/{rid}").json()["status"] == "CANCELLED"


def test_repeated_allocation_never_oversells_inventory() -> None:
    """Two concurrent /allocate calls against the same hospital stock must
    never both succeed if together they'd exceed what's on hand -- the
    inventory-row lock (app.services.inventory_service._get_or_create_row)
    closes this, distinct from (and in addition to) the BloodRequest-row
    lock."""
    hosp = _hospital(city="Metro", name="St Jude")
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 3})
    patient, _ = auth_client("PATIENT")
    rid = _request(patient, city="Metro", hospital_name="St Jude", units_needed=3)

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _allocate() -> None:
        barrier.wait()
        r = hosp.post(
            f"/hospitals/me/requests/{rid}/allocate",
            json={"blood_type": "O_NEG", "units": 2},
        )
        results.append(r.status_code)

    threads = [threading.Thread(target=_allocate) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Only 3 units exist; two concurrent requests for 2 each cannot both
    # succeed (that would need 4). Exactly one succeeds, the other is
    # rejected as insufficient stock -- never both, and never negative.
    assert sorted(results) == [200, 409], results
    inv = next(i for i in hosp.get("/hospitals/me/inventory").json() if i["blood_type"] == "O_NEG")
    assert inv["units"] == 1
    assert inv["units"] >= 0


# --------------------------------------------------------------------------- #
# Duplicate operations
# --------------------------------------------------------------------------- #


def test_duplicate_cancel_is_rejected_not_silently_accepted() -> None:
    patient, _ = auth_client("PATIENT")
    rid = _request(patient)
    first = patient.post(f"/blood-requests/{rid}/cancel", json={})
    second = patient.post(f"/blood-requests/{rid}/cancel", json={})
    assert first.status_code == 200
    assert second.status_code == 409
    # A same-state "transition" is never treated as an idempotent no-op --
    # it's rejected outright (see validate_transition's docstring on why).


def test_duplicate_fulfill_is_rejected() -> None:
    hosp = _hospital()
    patient, _ = auth_client("PATIENT")
    rid = _request(patient, city="Metro", hospital_name="St Jude")
    assert hosp.post(f"/hospitals/me/requests/{rid}/fulfill").status_code == 200
    assert hosp.post(f"/hospitals/me/requests/{rid}/fulfill").status_code == 409
    # only one Donation-adjacent side effect: fulfilling twice never records
    # donation history twice, since the second call never reaches that code.


# --------------------------------------------------------------------------- #
# Audit trail
# --------------------------------------------------------------------------- #


def test_transition_writes_both_event_and_auditlog_entries() -> None:
    admin, _ = auth_client("ADMIN")
    patient, _ = auth_client("PATIENT")
    rid = _request(patient)

    patient.post(f"/blood-requests/{rid}/cancel", json={"reason": "found a donor elsewhere"})

    # user-facing timeline
    detail = patient.get(f"/blood-requests/{rid}").json()
    assert "cancelled" in [e["event_type"] for e in detail["events"]]

    # admin-facing compliance trail: old state, new state, actor, timestamp,
    # reason, request id -- all present on one unified action.
    log = admin.get(
        "/admin/audit-log",
        params={"action": "blood_request.transition", "target_type": "blood_request"},
    ).json()
    entry = next(e for e in log["items"] if e["target_id"] == rid)
    assert entry["meta"]["old_status"] == "PENDING"
    assert entry["meta"]["new_status"] == "CANCELLED"
    assert entry["meta"]["reason"] == "found a donor elsewhere"
    assert entry["actor_user_id"] is not None
    assert entry["created_at"] is not None


def test_system_expiry_is_audited_with_no_actor() -> None:
    admin, _ = auth_client("ADMIN")
    patient, _ = auth_client("PATIENT")
    rid = _request(patient, needed_by=(utcnow() + timedelta(days=1)).date().isoformat())
    _set_needed_by_in_past(rid)
    patient.get(f"/blood-requests/{rid}")  # trigger the lazy expiry

    log = admin.get(
        "/admin/audit-log",
        params={"action": "blood_request.transition", "target_type": "blood_request"},
    ).json()
    entry = next(e for e in log["items"] if e["target_id"] == rid)
    assert entry["meta"]["new_status"] == "EXPIRED"
    assert entry["actor_user_id"] is None


def test_fulfillment_audit_still_carries_donations_recorded() -> None:
    """The old, separate `"blood_request.fulfilled"` AuditLog action was
    folded into the unified `"blood_request.transition"` one -- confirm the
    donations-recorded detail it used to carry wasn't lost in the merge."""
    admin, _ = auth_client("ADMIN")
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    rid = _request(patient)
    donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    patient.post(f"/blood-requests/{rid}/fulfill")

    log = admin.get(
        "/admin/audit-log",
        params={"action": "blood_request.transition", "target_type": "blood_request"},
    ).json()
    entry = next(
        e for e in log["items"] if e["target_id"] == rid and e["meta"]["new_status"] == "FULFILLED"
    )
    assert entry["meta"]["donations_recorded"] == 1


# --------------------------------------------------------------------------- #
# API shape
# --------------------------------------------------------------------------- #


def test_response_shape_carries_status_transitions_and_timestamps() -> None:
    patient, _ = auth_client("PATIENT")
    rid = _request(patient)
    body = patient.get(f"/blood-requests/{rid}").json()

    assert body["status"] == "PENDING"
    assert isinstance(body["allowed_transitions"], list) and body["allowed_transitions"]
    assert body["fulfilled_at"] is None
    assert body["cancelled_at"] is None
    assert body["expired_at"] is None
    assert body["created_at"] is not None
    assert body["updated_at"] is not None


def test_hospital_view_also_carries_allowed_transitions() -> None:
    hosp = _hospital()
    patient, _ = auth_client("PATIENT")
    rid = _request(patient, city="Metro", hospital_name="St Jude")
    item = next(x for x in hosp.get("/hospitals/me/requests").json() if x["id"] == rid)
    assert set(item["allowed_transitions"]) == {
        "MATCHING", "FULFILLED", "PARTIALLY_FULFILLED", "CANCELLED", "EXPIRED",
    }
