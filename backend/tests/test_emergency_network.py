from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from tests.helpers import auth_client, verify_facility_of


def _ready_donor(blood_type: str = "O_NEG", city: str | None = None):
    c, email = auth_client("DONOR")
    c.put("/donors/me", json={"blood_type": blood_type, "city": city})
    return c, email


def _hospital(city: str = "Metro", name: str = "Metro General", verified: bool = True):
    """An operating (verified) facility by default: since gap elimination #7
    only verified facilities can respond or be offered as responders."""
    c, email = auth_client("HOSPITAL")
    c.put("/hospitals/me", json={"name": name, "city": city})
    if verified:
        verify_facility_of(email)
    return c, email


def _sos(client, blood_type: str = "A_POS", location_text: str = "x") -> str:
    r = client.post(
        "/emergencies", json={"blood_type": blood_type, "location_text": location_text}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _has_notif(client, ntype: str) -> bool:
    return any(n["type"] == ntype for n in client.get("/notifications").json())


def _respond(client, eid: str, action: str):
    return client.post(f"/emergencies/{eid}/respond", json={"action": action})


def test_full_sos_lifecycle_to_resolved() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor("O_NEG", city="Metro")

    # Audit D4 (2026-10-31): an SOS with no location alerts nobody, so the
    # request shares the donor's city.
    r = patient.post("/emergencies", json={"blood_type": "A_POS", "location_text": "Metro ER",
                                           "city": "Metro"})
    assert r.status_code == 201
    eid = r.json()["id"]
    assert r.json()["status"] == "ACTIVE"
    assert any(n["type"] == "emergency.new" for n in donor.get("/notifications").json())

    # donor acknowledges -> ACKNOWLEDGED + creator notified
    r = donor.post(f"/emergencies/{eid}/acknowledge", json={"note": "en route"})
    assert r.status_code == 200 and r.json()["status"] == "ACKNOWLEDGED"

    # donor commits -> MATCHING, both parties notified
    r = donor.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"})
    assert r.status_code == 200
    assert patient.get(f"/emergencies/{eid}").json()["status"] == "MATCHING"
    ptypes = {n["type"] for n in patient.get("/notifications").json()}
    dtypes = {n["type"] for n in donor.get("/notifications").json()}
    assert "emergency.responder_accepted" in ptypes
    assert "emergency.you_responded" in dtypes

    # creator marks assistance found -> ASSISTANCE_FOUND, responder notified
    r = patient.post(f"/emergencies/{eid}/assistance-found", json={})
    assert r.status_code == 200 and r.json()["status"] == "ASSISTANCE_FOUND"
    assert _has_notif(donor, "emergency.assistance_found")

    # creator resolves -> RESOLVED
    r = patient.post(f"/emergencies/{eid}/resolve", json={})
    assert r.status_code == 200 and r.json()["status"] == "RESOLVED"
    assert r.json()["resolved_at"] is not None
    assert _has_notif(donor, "emergency.resolved")

    kinds = [e["event_type"] for e in patient.get(f"/emergencies/{eid}").json()["events"]]
    # Phase 12: the ACTIVE/ACKNOWLEDGED -> MATCHING transition itself now
    # gets its own explicit timeline entry ("matching_started") via the new
    # app.services.emergency_lifecycle -- formalizing the same "every real
    # transition is its own auditable event" rule Phase 10 already applies
    # to BloodRequest. The actual status sequence and every notification
    # below are unchanged; only the audit trail got more granular.
    assert kinds == [
        "created", "acknowledged", "responder_accepted", "matching_started",
        "assistance_found", "resolved",
    ]


def test_withdrawal_reopens_and_notifies() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor("O_NEG")
    eid = _sos(patient)

    donor.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"})
    assert patient.get(f"/emergencies/{eid}").json()["status"] == "MATCHING"

    r = donor.post(f"/emergencies/{eid}/respond", json={"action": "DECLINED"})
    assert r.status_code == 200
    det = patient.get(f"/emergencies/{eid}").json()
    assert det["status"] == "ACKNOWLEDGED"  # reopened, no responders remain
    assert det["accepted_count"] == 0
    kinds = [e["event_type"] for e in det["events"]]
    assert "responder_withdrew" in kinds and "reopened" in kinds
    assert any(
        n["type"] == "emergency.responder_withdrew" for n in patient.get("/notifications").json()
    )


def test_hospital_can_respond_and_mark_assistance_found() -> None:
    patient, _ = auth_client("PATIENT")
    hosp, _ = _hospital(city="Metro")
    eid = _sos(patient, "O_NEG", "Metro General ER")

    assert any(x["id"] == eid for x in hosp.get("/emergencies/active").json())
    r = _respond(hosp, eid, "ACCEPTED")
    assert r.status_code == 200 and r.json()["responder_role"] == "HOSPITAL"
    assert patient.get(f"/emergencies/{eid}").json()["status"] == "MATCHING"

    # a hospital that has accepted may confirm assistance
    r = hosp.post(f"/emergencies/{eid}/assistance-found", json={"note": "2 units reserved"})
    assert r.status_code == 200 and r.json()["status"] == "ASSISTANCE_FOUND"


def test_incompatible_or_unavailable_donor_cannot_accept() -> None:
    patient, _ = auth_client("PATIENT")
    eid = _sos(patient, "O_NEG")

    incompatible, _ = _ready_donor("A_POS")  # A_POS cannot donate to O_NEG
    assert _respond(incompatible, eid, "ACCEPTED").status_code == 400

    unavailable, _ = _ready_donor("O_NEG")
    unavailable.put("/donors/me/availability", json={"state": "UNAVAILABLE"})
    assert _respond(unavailable, eid, "ACCEPTED").status_code == 400


def test_emergency_not_now_response_logged_distinctly_from_declined() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor("O_NEG")
    eid = _sos(patient, "O_NEG")

    _respond(donor, eid, "NOT_NOW")
    events = [e["event_type"] for e in patient.get(f"/emergencies/{eid}").json()["events"]]
    assert "responder_not_now" in events
    assert "responder_declined" not in events

    _respond(donor, eid, "DECLINED")
    events = [e["event_type"] for e in patient.get(f"/emergencies/{eid}").json()["events"]]
    assert "responder_declined" in events


def test_role_boundaries_on_emergencies() -> None:
    patient, _ = auth_client("PATIENT")
    other_patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor("O_NEG")
    eid = _sos(patient)

    # unrelated patient cannot view or act
    assert other_patient.get(f"/emergencies/{eid}").status_code == 404
    assert other_patient.post(f"/emergencies/{eid}/cancel", json={}).status_code == 404
    assert other_patient.post(f"/emergencies/{eid}/assistance-found", json={}).status_code == 404

    # donor is a responder, not a manager: cannot mark assistance found or resolve
    assert donor.post(f"/emergencies/{eid}/assistance-found", json={}).status_code == 404
    assert donor.post(f"/emergencies/{eid}/resolve", json={}).status_code == 404
    assert donor.post(f"/emergencies/{eid}/cancel", json={}).status_code == 404

    # donor cannot create; anonymous cannot list active
    assert donor.post("/emergencies", json={"note": "x"}).status_code == 403
    assert TestClient(app).get("/emergencies/active").status_code == 401
    assert patient.get("/emergencies/active").status_code == 403


def test_match_candidates_factual_and_anonymised() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor("O_NEG", city="Metro")
    hosp, _ = _hospital(city="Metro")
    eid = patient.post(
        "/emergencies", json={"blood_type": "O_NEG", "location_text": "Metro central"}
    ).json()["id"]
    donor.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"})

    mc = patient.get(f"/emergencies/{eid}/match-candidates").json()
    assert mc["recipient_blood_type"] == "O_NEG"
    assert mc["compatible_donor_types"] == ["O_NEG"]
    donors = [c for c in mc["candidates"] if c["role"] == "DONOR"]
    hospitals = [c for c in mc["candidates"] if c["role"] == "HOSPITAL"]
    assert donors and donors[0]["has_accepted"] is True
    assert donors[0]["responder_name"] is not None  # creator sees names
    assert "score" not in donors[0]
    assert len(hospitals) == 1 and hospitals[0]["facility_verified"] is True
    # an unverified facility in the same area is not offered as a responder
    _hospital(city="Metro", name="Metro Unverified", verified=False)
    mc2 = patient.get(f"/emergencies/{eid}/match-candidates").json()
    assert len([c for c in mc2["candidates"] if c["role"] == "HOSPITAL"]) == 1

    # a responding hospital gets an anonymised view
    hc = hosp.get(f"/emergencies/{eid}/match-candidates").json()
    assert all(c["responder_name"] is None for c in hc["candidates"])
    assert all(len(c["responder_ref"]) == 8 for c in hc["candidates"])


def test_no_regression_acknowledge_only_from_active() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor("O_NEG")
    eid = _sos(patient)
    assert donor.post(f"/emergencies/{eid}/acknowledge", json={}).status_code == 200
    # second acknowledge now conflicts (already ACKNOWLEDGED)
    assert donor.post(f"/emergencies/{eid}/acknowledge", json={}).status_code == 409
