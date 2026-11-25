"""Gap elimination section 42: one continuous 35-step end-to-end scenario
through the public API (plus the deadline-monitor worker), ending with the
cross-domain invariant checks and the audit-chain verification."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.session import SessionLocal
from app.models.graph_pathway_log import GraphPathwayLog
from app.services import invariant_check
from app.workers import deadline_monitor
from tests.helpers import auth_client, stock_units, verify_facility_of

KM_PER_DEG_LAT = 6371.0 * 3.141592653589793 / 180


def _hospital(name, lat, lon):
    c, email = auth_client("HOSPITAL", name=name)
    assert (
        c.put(
            "/hospitals/me",
            json={"name": name, "city": "Chennai", "latitude": lat, "longitude": lon},
        ).status_code
        == 200
    )
    verify_facility_of(email)
    return c, c.get("/hospitals/me").json()


def _types(c):
    return [n["type"] for n in c.get("/notifications").json()]


def test_thirty_five_step_end_to_end_scenario(monkeypatch):
    # 1. administrator
    admin, _ = auth_client("ADMIN")
    # 2. three verified facilities: receiving ER and two blood banks
    er_c, er = _hospital("E2E Receiving ER", 13.0827, 80.2707)
    a_c, bank_a = _hospital("E2E Bank A", 13.10, 80.29)
    b_c, bank_b = _hospital("E2E Bank B", 13.00, 80.20)
    # 3. transport routes (A is faster than B)
    ra = admin.post(
        "/admin/transport-routes",
        json={
            "origin_facility_id": er["id"],
            "destination_facility_id": bank_a["id"],
            "distance_km": 3,
            "transit_minutes": 5,
        },
    ).json()
    rb = admin.post(
        "/admin/transport-routes",
        json={
            "origin_facility_id": er["id"],
            "destination_facility_id": bank_b["id"],
            "distance_km": 9,
            "transit_minutes": 8,
        },
    ).json()
    # 4. real per-unit stock
    stock_units(bank_a["id"], "O_NEG", 3)
    stock_units(bank_b["id"], "O_NEG", 4)
    # 5. donors: one 5 km away, one 80 km away
    near, _ = auth_client("DONOR")
    near.put(
        "/donors/me",
        json={
            "blood_type": "O_NEG",
            "is_available": True,
            "latitude": 13.0827 + 5 / KM_PER_DEG_LAT,
            "longitude": 80.2707,
            "share_location": True,
        },
    )
    far, _ = auth_client("DONOR")
    far.put(
        "/donors/me",
        json={
            "blood_type": "O_NEG",
            "is_available": True,
            "latitude": 13.0827 + 80 / KM_PER_DEG_LAT,
            "longitude": 80.2707,
            "share_location": True,
        },
    )
    # 6. the SOS: coordinates, 2 units, deadline 40 min, delivered to the ER
    patient, _ = auth_client("PATIENT")
    created = patient.post(
        "/emergencies",
        json={
            "blood_type": "O_NEG",
            "units_needed": 2,
            "latitude": 13.0827,
            "longitude": 80.2707,
            "deadline": (utcnow() + timedelta(minutes=40)).isoformat(),
            "target_facility_id": er["id"],
            "location_text": "Marina beach road",
        },
    )
    assert created.status_code == 201, created.text
    em = created.json()
    eid = em["id"]
    # 7. structured location recorded with its source
    assert em["geocode_source"] == "USER_INPUT"
    # 8. both banks (verified, in range, compatible stock) and the ER were alerted
    assert "emergency.new" in _types(a_c) and "emergency.new" in _types(b_c)
    assert "emergency.new" in _types(er_c)
    # 9. donor alerts respect the distance limit
    assert "emergency.new" in _types(near) and "emergency.new" not in _types(far)
    # 10. ranking is by true distance, beyond-limit donor excluded
    m = patient.get(f"/emergencies/{eid}/match-candidates").json()
    donors = [c for c in m["candidates"] if c["role"] == "DONOR"]
    assert [d["distance_km"] for d in donors] == [5.0] and m["excluded_beyond_limit"] == 1
    # 11. the ER plans: primary from A (fastest), backup from B
    plan = er_c.post(f"/emergencies/{eid}/pathway", json={})
    assert plan.status_code == 200, plan.text
    assert plan.json()["selected_facility_id"] == bank_a["id"]
    assert plan.json()["feasibility"] == "DEADLINE_RISK"  # ETA 35 min vs 40 min deadline
    # 12. the full route is stored, backup included
    stored = er_c.get(f"/emergencies/{eid}/pathways").json()
    assert stored[0]["legs"][0]["steps"][0]["route_id"] == ra["id"]
    assert (
        stored[1]["status"] == "BACKUP" and stored[1]["legs"][0]["steps"][0]["route_id"] == rb["id"]
    )
    # 13. reserve the plan's units
    res = er_c.post(f"/emergencies/{eid}/pathway/reserve")
    assert res.status_code == 200, res.text
    assert res.json()["reserved_units"] == 2
    # 14. bank A's own inventory shows the hold
    inv_a = next(i for i in a_c.get("/hospitals/me/inventory").json() if i["blood_type"] == "O_NEG")
    assert inv_a["reserved_units"] == 2
    # 15. the ER-A route fails: backup B is validated and promoted
    d = admin.post(f"/admin/transport-routes/{ra['id']}/disrupt", json={"reason": "flooding"})
    outcome = d.json()["affected_emergencies"][0]
    assert outcome["kind"] == "BACKUP_PROMOTED" and outcome["still_reachable"] is True
    # 16. reservations followed the plan: A released, B now holds 2
    assert (
        next(i for i in a_c.get("/hospitals/me/inventory").json() if i["blood_type"] == "O_NEG")[
            "reserved_units"
        ]
        == 0
    )
    assert (
        next(i for i in b_c.get("/hospitals/me/inventory").json() if i["blood_type"] == "O_NEG")[
            "reserved_units"
        ]
        == 2
    )
    # 17. the timeline explains it
    events = [e["event_type"] for e in patient.get(f"/emergencies/{eid}").json()["events"]]
    for expected in (
        "transport_disrupted",
        "backup_promoted",
        "pathway_reoptimized",
        "pathway_reserved",
    ):
        assert expected in events
    # 18. the requester was told
    assert "emergency.pathway_reoptimized" in _types(patient)
    # 19. hand the hold over to a physical shipment: release it first
    assert (
        er_c.post(f"/emergencies/{eid}/pathway/release", json={"note": "shipping now"}).status_code
        == 200
    )
    # 20. bank B ships 2 units to the ER
    ship = b_c.post(
        "/shipments",
        json={
            "origin_facility_id": bank_b["id"],
            "destination_facility_id": er["id"],
            "items": [{"blood_type": "O_NEG", "units": 2}],
        },
    ).json()
    assert ship["temp_min_c"] == 1 and ship["temp_max_c"] == 6
    sid = ship["id"]
    # 21. dispatch
    assert b_c.post(f"/shipments/{sid}/dispatch", json={}).status_code == 200
    # 22. logger readings within range
    now = utcnow()
    tele = b_c.post(
        f"/shipments/{sid}/telemetry",
        json={
            "readings": [
                {
                    "recorded_at": (now - timedelta(minutes=m)).isoformat(),
                    "temperature_c": 4.0,
                    "sensor_id": "LOG-7",
                }
                for m in (20, 10, 0)
            ]
        },
    )
    assert tele.json()["report"]["status"] == "WITHIN_RANGE"
    # 23. in transit
    assert b_c.post(f"/shipments/{sid}/in-transit", json={}).status_code == 200
    # 24. arrived
    assert er_c.post(f"/shipments/{sid}/arrive", json={}).status_code == 200
    # 25. received; one bag damaged
    codes = [i["unit_code"] for i in ship["items"]]
    rec = er_c.post(
        f"/shipments/{sid}/receive",
        json={"items": [{"unit_code": codes[1], "outcome": "DAMAGED", "reason": "cracked port"}]},
    )
    assert rec.status_code == 200, rec.text
    # 26. only the good unit is stock at the ER
    inv_er = next(
        i for i in er_c.get("/hospitals/me/inventory").json() if i["blood_type"] == "O_NEG"
    )
    assert inv_er["units"] == 1
    # 27. per-unit outcomes are recorded on the shipment
    assert sorted(i["outcome"] for i in rec.json()["items"]) == ["DAMAGED", "RECEIVED_OK"]
    # 28. the deadline monitor (configured 60-min warning window) warns once
    monkeypatch.setattr(settings, "deadline_warning_minutes", 60)
    with SessionLocal() as db:
        run = deadline_monitor.run_once(db)
        again = deadline_monitor.run_once(db)
    assert eid in run.warned and again.warned == []
    # 29. the ER runs a demand forecast (labelled synthetic)
    fc = er_c.post("/forecasts", json={"history_days": 28})
    assert fc.status_code == 201 and fc.json()["data_is_synthetic"] is True
    # 30. resolve
    assert patient.post(f"/emergencies/{eid}/resolve", json={}).status_code == 200
    # 31. nothing remains reserved for the emergency anywhere
    for c in (a_c, b_c, er_c):
        assert all(i["reserved_units"] == 0 for i in c.get("/hospitals/me/inventory").json())
    # 32. the audit chain verifies
    verify = admin.post("/admin/audit-log/verify").json()
    assert verify["ok"] is True and verify["first_break"] is None
    # 33. every cross-domain invariant holds
    with SessionLocal() as db:
        assert invariant_check.violations(db) == {}
    # 34. pathway history: invalidated primary, promoted backup, nothing deleted
    statuses = {
        p["version"]: p["status"]
        for p in patient.get(f"/emergencies/{eid}").json()["pathway_history"]
    }
    assert statuses[1] == "INVALIDATED" and statuses[2] == "ACTIVE"
    # 35. planning was logged with its engine
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(GraphPathwayLog)
                .where(GraphPathwayLog.emergency_id == uuid.UUID(eid))
            )
            >= 1
        )
