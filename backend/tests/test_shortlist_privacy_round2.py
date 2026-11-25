"""Privacy remediation round 2 (2026-11-03) -- real PostgreSQL, real API.

Every assertion here reads the JSON a real endpoint returns to a real,
logged-in user of the role in question:

* P1 -- a failed confirmation re-check never reaches the request/emergency
  event history (read by patients, family, responding/candidate donors and
  facilities), while facility/admin users keep the reason on the shortlist
  and admins keep it in the audit log;
* P2/P4 -- on an SOS with coordinates, distance is exact to 0.1 km for
  hospital/admin users and whole km for patient/family users, and the
  travel-time estimate, the score and the score at contact carry no more
  precision than that whole-km distance;
* P3 -- the patient-facing reason is chosen by the contact STATUS, for every
  status x every reason the code writes, with and without "[sim-wait]";
* access control on every shortlist endpoint that exposes the data.
"""
from __future__ import annotations

import json
from datetime import date

from app.api.routes import shortlists as routes
from app.models.enums import DonorContactStatus, haversine_km
from app.services import donor_shortlist_service as svc
from tests.helpers import auth_client
from tests.test_donor_shortlist import KM_PER_DEG_LAT, LAT0, LON0, _donor, _hospital

RECHECK_TEXT = "the eligibility re-check did not pass"
# Words that would reveal WHY a re-check failed or WHO it was about.
SENSITIVE = ("interval", "available", "accepted another", "donation", "commitment")


def _family_of(patient_email: str):
    fam, _ = auth_client("FAMILY")
    r = fam.post("/family/links", json={"patient_email": patient_email,
                                        "relationship": "daughter"})
    assert r.status_code == 201, r.text
    return fam, r.json()["id"]


def _accept_link(patient, link_id):
    assert patient.post(f"/family/links/{link_id}/accept").status_code == 200


def _sos(patient, units=1):
    r = patient.post("/emergencies", json={"blood_type": "O_NEG", "units_needed": units,
                                           "city": "Metro", "latitude": LAT0,
                                           "longitude": LON0})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _confirmation_notes(client, url):
    r = client.get(url)
    assert r.status_code == 200, (url, r.status_code, r.text)
    events = r.json()["events"] if isinstance(r.json(), dict) else r.json()
    return [e for e in events if e["event_type"] == "shortlist_confirmation"]


def _assert_generic(notes, *, donor_refs):
    assert notes, "the confirmation event must exist (a missing event would make this vacuous)"
    for e in notes:
        text = (e.get("note") or "").lower()
        for word in SENSITIVE:
            assert word not in text, (word, e["note"])
        for ref in donor_refs:
            assert ref not in text, (ref, e["note"])
        assert "eligibility_recheck_required" not in text


def _admin_audit(admin, subject_id):
    r = admin.get("/admin/audit-log", params={"action": "shortlist.confirmation"})
    assert r.status_code == 200, r.text
    return [e for e in r.json()["items"] if e["target_id"] == subject_id]


# --------------------------------------------------------------------------- #
# P1 -- emergency event history
# --------------------------------------------------------------------------- #
def test_failed_recheck_reason_is_not_in_emergency_events_for_any_role():
    patient, pat_email = auth_client("PATIENT")
    fam, link_id = _family_of(pat_email)
    _accept_link(patient, link_id)
    a, _ = _donor(2, name="Donor A")      # accepts, then becomes ineligible
    b, _ = _donor(3, name="Donor B")      # accepts and is confirmed
    c, _ = _donor(4, name="Donor C")      # contacted, never answers
    hosp = _hospital()
    admin, _ = auth_client("ADMIN")
    eid = _sos(patient, units=2)
    assert patient.post(f"/emergencies/{eid}/shortlist/contact-next").status_code == 200
    for d in (a, b):
        assert d.post(f"/emergencies/{eid}/respond",
                      json={"action": "ACCEPTED"}).status_code == 200
    a.put("/donors/me", json={"last_donation_date": date.today().isoformat()})
    sl = patient.get(f"/emergencies/{eid}/shortlist").json()
    by_name = {ct["donor_name"]: ct for ct in sl["contacts"]}
    assert {"Donor A", "Donor B", "Donor C"} <= set(by_name)
    refs = [by_name[n]["donor_ref"] for n in ("Donor A", "Donor B", "Donor C")]
    fail = patient.post(f"/emergencies/{eid}/shortlist/contacts/{by_name['Donor A']['id']}"
                        "/confirm")
    ok = patient.post(f"/emergencies/{eid}/shortlist/contacts/{by_name['Donor B']['id']}"
                      "/confirm")
    assert fail.status_code == 200 and ok.status_code == 200
    row = next(x for x in fail.json()["contacts"] if x["id"] == by_name["Donor A"]["id"])
    assert row["status"] == "ELIGIBILITY_RECHECK_REQUIRED"
    assert row["status_reason"] == RECHECK_TEXT          # patient's own POST response

    # Everyone who can read the emergency: patient, family, the other donors
    # (a confirmed responder and a mere candidate), the failed donor, a
    # hospital and an admin. Each must get 200 -- a 404 would hide nothing.
    for who, client in (("patient", patient), ("family", fam), ("donor B", b),
                        ("donor C", c), ("donor A", a), ("hospital", hosp), ("admin", admin)):
        notes = _confirmation_notes(client, f"/emergencies/{eid}")
        assert len(notes) == 2, (who, notes)
        _assert_generic(notes, donor_refs=refs)
        assert any("could not be confirmed" in e["note"] for e in notes), who
        assert any("was confirmed" in e["note"] for e in notes), who

    # The detail stays with authorised staff.
    for client in (hosp, admin):
        s = client.get(f"/emergencies/{eid}/shortlist").json()
        assert s["explanation_level"] == "FULL"
        r = next(x for x in s["contacts"] if x["id"] == by_name["Donor A"]["id"])
        assert r["status_reason"] == "donation interval not elapsed"
    for client in (patient, fam):
        s = client.get(f"/emergencies/{eid}/shortlist").json()
        assert s["explanation_level"] == "SUMMARY"
        r = next(x for x in s["contacts"] if x["id"] == by_name["Donor A"]["id"])
        assert r["status_reason"] == RECHECK_TEXT
    audit = _admin_audit(admin, eid)
    failed = [e for e in audit if e["meta"]["result"] == "ELIGIBILITY_RECHECK_REQUIRED"]
    passed = [e for e in audit if e["meta"]["result"] == "CONFIRMED_FOR_COORDINATION"]
    assert len(failed) == 1 and failed[0]["meta"]["reason"] == "donation interval not elapsed"
    assert failed[0]["meta"]["contact_id"] == by_name["Donor A"]["id"]
    assert len(passed) == 1 and "reason" not in passed[0]["meta"]
    # the audit log itself is staff-only
    for client in (patient, fam, a, b, hosp):
        assert client.get("/admin/audit-log").status_code == 403


# --------------------------------------------------------------------------- #
# P1 -- blood-request event history (+ the hospital's own event endpoints)
# --------------------------------------------------------------------------- #
def test_failed_recheck_reason_is_not_in_blood_request_events_for_any_role():
    patient, pat_email = auth_client("PATIENT")
    fam, link_id = _family_of(pat_email)
    _accept_link(patient, link_id)
    a, _ = _donor(3, name="Donor A")
    b, _ = _donor(4, name="Donor B")
    hosp = _hospital()
    admin, _ = auth_client("ADMIN")
    rid = patient.post("/blood-requests", json={"blood_type": "O_NEG", "units_needed": 1,
                                                "urgency": "URGENT", "city": "Metro"}).json()["id"]
    assert patient.post(f"/blood-requests/{rid}/shortlist/contact-next").status_code == 200
    assert a.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"}).status_code == 200
    # Donor B answers too (so B may read the request) but declines.
    assert b.post(f"/blood-requests/{rid}/respond", json={"action": "DECLINED"}).status_code == 200
    assert a.put("/donors/me/availability", json={"state": "UNAVAILABLE"}).status_code == 200
    sl = patient.get(f"/blood-requests/{rid}/shortlist").json()
    acc = next(x for x in sl["contacts"] if x["donor_name"] == "Donor A")
    refs = [x["donor_ref"] for x in sl["contacts"]]
    r = patient.post(f"/blood-requests/{rid}/shortlist/contacts/{acc['id']}/confirm")
    assert r.status_code == 200, r.text
    row = next(x for x in r.json()["contacts"] if x["id"] == acc["id"])
    assert row["status"] == "ELIGIBILITY_RECHECK_REQUIRED"
    assert row["status_reason"] == RECHECK_TEXT

    for who, client in (("patient", patient), ("family", fam), ("donor B", b),
                        ("donor A", a)):
        notes = _confirmation_notes(client, f"/blood-requests/{rid}")
        assert len(notes) == 1, who
        _assert_generic(notes, donor_refs=refs)
    # Hospital-side readers of the same history (a same-city facility can
    # read this request; _confirmation_notes asserts the 200).
    _assert_generic(_confirmation_notes(hosp, f"/blood-requests/{rid}"), donor_refs=refs)
    _assert_generic(_confirmation_notes(hosp, f"/hospitals/me/requests/{rid}/events"),
                    donor_refs=refs)
    act = hosp.get("/hospitals/me/activity")
    assert act.status_code == 200
    mine = [i for i in act.json() if i.get("request_id") == rid and i["kind"] == "request"]
    assert any("could not be confirmed" in i["summary"] for i in mine), mine
    for item in mine:
        for word in SENSITIVE:
            assert word not in item["summary"].lower(), item
    # Staff keep the detail.
    s = admin.get(f"/blood-requests/{rid}/shortlist").json()
    assert s["explanation_level"] == "FULL"
    assert next(x for x in s["contacts"] if x["id"] == acc["id"])["status_reason"] \
        == "no longer marked available"
    audit = _admin_audit(admin, rid)
    assert [e["meta"].get("reason") for e in audit] == ["no longer marked available"]


def test_a_recheck_reason_does_not_survive_a_later_status_change():
    """A donor whose re-check failed and who then withdraws: the reason must
    not reappear under WITHDRAWN (where the status-based rule would not hide
    it). It is cleared on the contact row and kept in the audit log."""
    patient, _ = auth_client("PATIENT")
    a, _ = _donor(3, name="Donor A")
    admin, _ = auth_client("ADMIN")
    eid = _sos(patient)
    patient.post(f"/emergencies/{eid}/shortlist/contact-next")
    assert a.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"}).status_code == 200
    a.put("/donors/me", json={"last_donation_date": date.today().isoformat()})
    cid = next(x for x in patient.get(f"/emergencies/{eid}/shortlist").json()["contacts"]
               if x["status"] == "ACCEPTED")["id"]
    patient.post(f"/emergencies/{eid}/shortlist/contacts/{cid}/confirm")
    assert a.post(f"/emergencies/{eid}/respond", json={"action": "DECLINED"}).status_code == 200
    for client in (patient, admin):
        row = next(x for x in client.get(f"/emergencies/{eid}/shortlist").json()["contacts"]
                   if x["id"] == cid)
        assert row["status"] == "WITHDRAWN"
        assert row["status_reason"] is None
    audit = _admin_audit(admin, eid)
    assert [e["meta"].get("reason") for e in audit] == ["donation interval not elapsed"]


# --------------------------------------------------------------------------- #
# P2 / P4 -- measured distance, travel time and score precision by role
# --------------------------------------------------------------------------- #
# Offsets chosen so that the 0.1-km and whole-km figures, and the two travel
# estimates, differ where they can (Mid: 3.4 km / 5 min for staff, 3 km /
# 4 min for patient and family). haversine_km itself returns 0.1-km values.
OFFSETS_KM = {"Near": 0.3, "Mid": 3.44, "Far": 12.34}


def test_distance_travel_time_and_score_precision_by_role_on_an_sos():
    patient, pat_email = auth_client("PATIENT")
    fam, link_id = _family_of(pat_email)
    _accept_link(patient, link_id)
    expected = {}
    for name, km in OFFSETS_KM.items():
        _donor(km, name=name)
        expected[name] = haversine_km(LAT0, LON0, LAT0 + km / KM_PER_DEG_LAT, LON0)
    hosp = _hospital()
    admin, _ = auth_client("ADMIN")
    eid = _sos(patient, units=2)  # 2 units x multiplier 2: all three donors are listed

    full_by_ref = {}
    for client in (hosp, admin):
        s = client.get(f"/emergencies/{eid}/shortlist").json()
        assert s["explanation_level"] == "FULL"
        assert len(s["next_to_contact"]) >= 3
        for cand in s["next_to_contact"]:
            full_by_ref[cand["donor_ref"]] = cand
    names = {}
    for client in (patient, fam):
        s = client.get(f"/emergencies/{eid}/shortlist").json()
        assert s["explanation_level"] == "SUMMARY"
        for cand in s["next_to_contact"]:
            names[cand["donor_ref"]] = cand["donor_name"]
    assert set(OFFSETS_KM) <= set(names.values())

    for ref, name in names.items():
        if name not in OFFSETS_KM:
            continue
        d = expected[name]
        full = full_by_ref[ref]
        # FULL: 0.1 km, travel from the exact distance
        assert full["distance_basis"] == "MEASURED"
        assert full["distance_km"] is not None, "distance must be present for a located donor"
        assert full["distance_km"] == round(d, 1)
        assert full["travel_minutes_estimate"] == int(round(d / svc.ASSUMED_TRAVEL_KMPH * 60))
        assert full["priority"]["missing_data_baseline"] is not None
        for client in (patient, fam):
            s = client.get(f"/emergencies/{eid}/shortlist").json()
            cand = next(c for c in s["next_to_contact"] if c["donor_ref"] == ref)
            whole = float(max(1, round(d)))
            assert cand["distance_basis"] == "MEASURED"
            assert cand["distance_km"] is not None, "distance must be present"
            assert cand["distance_km"] == whole
            # travel time is derived from the WHOLE-km figure, not the exact one
            assert cand["travel_minutes_estimate"] == int(round(whole / svc.ASSUMED_TRAVEL_KMPH
                                                                * 60))
            # score: a 10-point band; no baseline; no factor numbers
            p = cand["priority"]
            assert p["score"] % 10 == 0
            assert p["score"] == round(full["priority"]["score"] / 10) * 10
            assert cand["score"] == round(p["score"] / 100, 4)
            assert p["missing_data_baseline"] is None
            assert cand["components"] == {}
            for f in p["factors"]:
                assert f["weight"] is None and f["value"] is None and f["contribution"] is None
            assert cand["explanation"] == [f"about {whole:g} km away"]
            # no field anywhere repeats the 0.1-km figure (where it differs)
            if round(d, 1) != whole:
                assert f"{round(d, 1)}" not in json.dumps(cand), (name, cand)
    # the exact travel figures differ from the SUMMARY ones for "Mid" (5 vs 4 min)
    mid = next(r for r, n in names.items() if n == "Mid")
    assert full_by_ref[mid]["travel_minutes_estimate"] == 5
    s = patient.get(f"/emergencies/{eid}/shortlist").json()
    assert next(c for c in s["next_to_contact"]
                if c["donor_ref"] == mid)["travel_minutes_estimate"] == 4

    # score at contact is banded for SUMMARY too
    patient.post(f"/emergencies/{eid}/shortlist/contact-next")
    s_p = patient.get(f"/emergencies/{eid}/shortlist").json()
    s_a = admin.get(f"/emergencies/{eid}/shortlist").json()
    exact = {c["id"]: c["score_at_contact"] for c in s_a["contacts"]}
    assert s_p["contacts"]
    for c in s_p["contacts"]:
        assert round(c["score_at_contact"] * 100, 6) % 10 == 0
        assert c["score_at_contact"] == round(round(exact[c["id"]] * 100 / 10) * 10 / 100, 4)


def test_shortlist_endpoints_refuse_donors_outsiders_and_non_managers():
    patient, _ = auth_client("PATIENT")
    other_patient, _ = auth_client("PATIENT")
    outsider_family, _ = auth_client("FAMILY")  # not linked
    donor, _ = _donor(2)
    hosp = _hospital()
    eid = _sos(patient)
    rid = patient.post("/blood-requests", json={"blood_type": "O_NEG", "units_needed": 1,
                                                "urgency": "URGENT", "city": "Metro"}).json()["id"]
    for prefix, sid in (("emergencies", eid), ("blood-requests", rid)):
        for client in (donor, other_patient, outsider_family):
            assert client.get(f"/{prefix}/{sid}/shortlist").status_code == 404
            assert client.post(f"/{prefix}/{sid}/shortlist/contact-next").status_code == 404
        # a same-city facility may read (without names) but is not a manager
        r = hosp.get(f"/{prefix}/{sid}/shortlist")
        assert r.status_code == 200
        assert all(c["donor_name"] is None for c in r.json()["next_to_contact"])
        assert hosp.post(f"/{prefix}/{sid}/shortlist/contact-next").status_code == 403
    patient.post(f"/emergencies/{eid}/shortlist/contact-next")
    assert donor.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"}).status_code \
        == 200
    cid = next(x for x in patient.get(f"/emergencies/{eid}/shortlist").json()["contacts"]
               if x["status"] == "ACCEPTED")["id"]
    url = f"/emergencies/{eid}/shortlist/contacts/{cid}/confirm"
    for client in (donor, other_patient, outsider_family):
        assert client.post(url).status_code == 404
    assert hosp.post(url).status_code == 403
    # still ACCEPTED: none of the refused calls changed anything
    row = next(x for x in patient.get(f"/emergencies/{eid}/shortlist").json()["contacts"]
               if x["id"] == cid)
    assert row["status"] == "ACCEPTED"
    # policy endpoint: weights for facility/admin only
    assert patient.get("/shortlist/policy").json()["weights"] is None
    assert donor.get("/shortlist/policy").json()["weights"] is None
    assert hosp.get("/shortlist/policy").json()["weights"] is not None


# --------------------------------------------------------------------------- #
# P3 -- status x reason x audience
# --------------------------------------------------------------------------- #
DETAIL = "donation interval not elapsed"
# Every reason text the code writes, and what a patient/family member sees
# when the contact is NOT in ELIGIBILITY_RECHECK_REQUIRED.
REASONS_SUMMARY_ELSEWHERE = {
    None: None,
    svc.REASON_PRIOR_ALERT: svc.REASON_PRIOR_ALERT,
    svc.REASON_TIMEOUT: svc.REASON_TIMEOUT,
    svc.REASON_OPEN_ALERT: svc.REASON_OPEN_ALERT,
    svc.REASON_SIM_TIMEOUT: svc.REASON_SIM_TIMEOUT,
    "no longer marked available": "details are visible to facility staff",
    DETAIL: "details are visible to facility staff",
    "you already accepted another open blood request": "details are visible to facility staff",
    "you already accepted another open emergency": "details are visible to facility staff",
    "no longer marked available; you already accepted another open emergency":
        "details are visible to facility staff",
    f"simulated accept refused by real checks: {DETAIL}":
        "simulated acceptance refused by the eligibility checks",
}


def _check_reason(status, reason, sim_wait):
    stored = reason
    if sim_wait:  # what demo_simulator appends to a slow persona's row
        stored = ((reason or "") + " [sim-wait]").strip()
    full = routes._reason_out(stored, status, True)
    summary = routes._reason_out(stored, status, False)
    case = (status.value, stored)
    # the internal marker is never shown to anyone
    assert full is None or "[sim-wait]" not in full, case
    assert summary is None or "[sim-wait]" not in summary, case
    # facility/admin: the stored text (minus the marker)
    assert full == reason, case
    # patient/family
    if reason is None:
        assert summary is None, case
    elif status is DonorContactStatus.ELIGIBILITY_RECHECK_REQUIRED:
        assert summary == RECHECK_TEXT, case
    else:
        assert summary == REASONS_SUMMARY_ELSEWHERE[reason], case
        assert summary != RECHECK_TEXT, case  # never mislabelled as a re-check
    if summary is not None and summary != reason:
        assert "interval" not in summary and "accepted another" not in summary, case


def test_reason_shown_depends_on_status_for_every_status_and_reason():
    """Pure function, so one test walks the whole grid (every status x every
    reason the code writes x with/without the simulator marker) rather than
    paying the database fixture 220 times."""
    n = 0
    for status in DonorContactStatus:
        for reason in REASONS_SUMMARY_ELSEWHERE:
            for sim_wait in (False, True):
                _check_reason(status, reason, sim_wait)
                n += 1
    assert n == len(DonorContactStatus) * len(REASONS_SUMMARY_ELSEWHERE) * 2


def test_marker_alone_is_not_a_reason():
    for status in DonorContactStatus:
        for full in (True, False):
            assert routes._reason_out("[sim-wait]", status, full) is None
