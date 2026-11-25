from __future__ import annotations

from tests.helpers import auth_client


def _donor(bt="O_NEG", *, lat=None, lng=None, share=False, city="Metro"):
    c, _ = auth_client("DONOR")
    body = {"blood_type": bt, "city": city}
    if lat is not None:
        body |= {"latitude": lat, "longitude": lng, "share_location": share}
    c.put("/donors/me", json=body)
    return c


def test_match_candidates_manager_vs_hospital_view() -> None:
    hosp, _ = auth_client("HOSPITAL")
    hosp.put("/hospitals/me", json={"name": "St Jude", "city": "Metro",
                                    "latitude": 40.0, "longitude": -73.0})
    patient, _ = auth_client("PATIENT")
    # An O_NEG recipient can only receive O_NEG red cells.
    d_a = _donor("O_NEG", lat=40.01, lng=-73.0, share=True)
    _donor("O_NEG", lat=41.0, lng=-73.0, share=True)
    _donor("A_POS")  # incompatible with O_NEG recipient -> excluded

    rid = patient.post("/blood-requests", json={
        "blood_type": "O_NEG", "city": "Metro", "hospital_name": "St Jude",
    }).json()["id"]
    d_a.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})

    # manager (patient) sees names + accepted-first ordering
    mc = patient.get(f"/blood-requests/{rid}/match-candidates").json()
    assert mc["recipient_blood_type"] == "O_NEG"
    assert set(mc["compatible_donor_types"]) == {"O_NEG"}
    assert len(mc["candidates"]) == 2  # two O_NEG donors; A_POS excluded
    assert mc["candidates"][0]["has_accepted"] is True
    assert mc["candidates"][0]["donor_name"] is not None

    # hospital sees the same candidates but anonymised
    hc = hosp.get(f"/blood-requests/{rid}/match-candidates").json()
    assert len(hc["candidates"]) == 2
    assert all(c["donor_name"] is None for c in hc["candidates"])
    assert all(len(c["donor_ref"]) == 8 for c in hc["candidates"])


def test_match_candidates_distance_when_request_has_coords() -> None:
    hosp, _ = auth_client("HOSPITAL")
    hosp.put("/hospitals/me", json={"name": "H", "city": "Metro",
                                    "latitude": 40.0, "longitude": -73.0})
    _donor("O_NEG", lat=40.01, lng=-73.0, share=True)
    # hospital files the request -> request inherits hospital coords
    rid = hosp.post("/blood-requests", json={"blood_type": "O_NEG"}).json()["id"]
    mc = hosp.get(f"/blood-requests/{rid}/match-candidates").json()
    assert mc["candidates"][0]["distance_km"] is not None
    assert 0.5 < mc["candidates"][0]["distance_km"] < 3


def test_match_candidates_requires_relationship() -> None:
    patient, _ = auth_client("PATIENT")
    other, _ = auth_client("PATIENT")
    rid = patient.post("/blood-requests", json={"blood_type": "A_POS"}).json()["id"]
    assert other.get(f"/blood-requests/{rid}/match-candidates").status_code == 404


def test_map_data_privacy() -> None:
    hosp, _ = auth_client("HOSPITAL")
    hosp.put("/hospitals/me", json={"name": "H", "city": "Metro",
                                    "latitude": 40.123456, "longitude": -73.654321})
    _donor("O_NEG", lat=40.117777, lng=-73.622222, share=True)
    _donor("A_POS", lat=40.2, lng=-73.6, share=False)  # not sharing -> excluded

    data = hosp.get("/map/data").json()
    assert data["center"]["lat"] == 40.123456
    assert len(data["donors"]) == 1
    pin = data["donors"][0]
    assert pin["blood_type"] == "O_NEG"
    assert "id" not in pin and "user_id" not in pin and "name" not in pin
    # coarsened to 2 decimals
    assert pin["latitude"] == round(40.117777, 2)
    assert pin["longitude"] == round(-73.622222, 2)

    # filter by recipient compatibility
    filtered = hosp.get("/map/data?blood_type=AB_POS").json()  # everyone compatible
    assert len(filtered["donors"]) == 1
    filtered = hosp.get("/map/data?blood_type=O_NEG").json()  # only O_NEG donors
    assert len(filtered["donors"]) == 1


def test_map_data_role_guard() -> None:
    donor = _donor()
    assert donor.get("/map/data").status_code == 403
    patient, _ = auth_client("PATIENT")
    assert patient.get("/map/data").status_code == 200
