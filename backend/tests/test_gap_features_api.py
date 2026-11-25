"""Gap elimination sections 4-7 and 14-21, through the real HTTP API and
real PostgreSQL: emergency location, distance-aware ranking, the donor
distance limit, hospital verification, SMS provider behaviour, cold-chain
telemetry, per-unit receipt outcomes, the plasma thaw lifecycle, demand
forecasting, the deadline monitor, hospital alerts and donor alert
preferences."""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.session import SessionLocal
from app.models.audit_log import AuditLog
from app.models.blood_unit import BloodUnit
from app.models.emergency import Emergency
from app.models.enums import (
    BloodComponent,
    BloodType,
    BloodUnitState,
    ColdChainStatus,
    GeocodeSource,
    HospitalInventoryAction,
    SmsStatus,
)
from app.models.hospital import HospitalInventory, HospitalInventoryLog, HospitalProfile
from app.services import geocoding
from tests.helpers import auth_client, stock_units, verify_facility_of

KM_PER_DEG_LAT = 6371.0 * 3.141592653589793 / 180


def _hospital(name: str, lat: float | None, lon: float | None, *, city="Metro", verified=True):
    c, email = auth_client("HOSPITAL", name=name)
    body = {"name": name, "city": city}
    if lat is not None:
        body.update(latitude=lat, longitude=lon)
    r = c.put("/hospitals/me", json=body)
    assert r.status_code == 200, r.text
    if verified:
        verify_facility_of(email)
    return c, c.get("/hospitals/me").json()


def _donor(blood_type: str, lat: float | None = None, lon: float | None = None, **prefs):
    c, _ = auth_client("DONOR")
    body = {"blood_type": blood_type, "is_available": True, **prefs}
    if lat is not None:
        body.update(latitude=lat, longitude=lon, share_location=True)
    r = c.put("/donors/me", json=body)
    assert r.status_code == 200, r.text
    return c


def _notif_types(client) -> list[str]:
    return [n["type"] for n in client.get("/notifications").json()]


# --------------------------------------------------------------------------- #
# 4. Emergency location
# --------------------------------------------------------------------------- #
def test_emergency_with_coordinates_records_user_input():
    p, _ = auth_client("PATIENT")
    r = p.post(
        "/emergencies",
        json={"blood_type": "O_NEG", "latitude": 12.97, "longitude": 77.59, "city": "Bengaluru"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["latitude"], body["longitude"]) == (12.97, 77.59)
    assert body["geocode_source"] == "USER_INPUT" and body["city"] == "Bengaluru"


@pytest.mark.parametrize(
    "payload",
    [
        {"latitude": 12.9},  # half a pair
        {"longitude": 77.5},
        {"latitude": 91, "longitude": 77.5},  # out of range
        {"latitude": 12.9, "longitude": -181},
    ],
)
def test_invalid_coordinates_are_rejected(payload):
    p, _ = auth_client("PATIENT")
    r = p.post("/emergencies", json={"blood_type": "O_NEG", **payload})
    assert r.status_code == 422


def test_target_facility_supplies_coordinates():
    _, fac = _hospital("Target General", 13.08, 80.27)
    p, _ = auth_client("PATIENT")
    body = p.post(
        "/emergencies", json={"blood_type": "O_NEG", "target_facility_id": fac["id"]}
    ).json()
    assert body["geocode_source"] == "FACILITY"
    assert (body["latitude"], body["longitude"]) == (13.08, 80.27)


def test_city_named_in_address_resolves_via_gazetteer_as_a_labelled_estimate():
    _hospital("Vellore A", 12.90, 79.10, city="Vellore")
    _hospital("Vellore B", 12.94, 79.16, city="Vellore")
    p, _ = auth_client("PATIENT")
    body = p.post(
        "/emergencies", json={"blood_type": "O_NEG", "location_text": "Bus stand, Vellore"}
    ).json()
    assert body["geocode_source"] == "GAZETTEER"
    assert body["latitude"] == pytest.approx(12.92) and body["longitude"] == pytest.approx(79.13)
    assert "city-level estimate" in body["geocode_detail"]


def test_unknown_place_without_geocoder_gets_no_invented_coordinates():
    p, _ = auth_client("PATIENT")
    body = p.post(
        "/emergencies", json={"blood_type": "O_NEG", "location_text": "Nowhere-in-particular"}
    ).json()
    assert body["latitude"] is None and body["geocode_source"] is None


def test_external_geocoder_success_failure_and_garbage(monkeypatch, db_session):
    monkeypatch.setattr(settings, "geocoder_url", "https://geocoder.invalid/search")

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params["q"]
        assert request.headers["User-Agent"] == settings.geocoder_user_agent
        if "ok" in q:
            return httpx.Response(
                200, json=[{"lat": "12.5", "lon": "77.5", "display_name": "Somewhere"}]
            )
        if "bad" in q:
            return httpx.Response(200, json=[{"lat": "999", "lon": "0"}])
        return httpx.Response(503)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    hit = geocoding.resolve(
        db_session,
        latitude=None,
        longitude=None,
        city=None,
        location_text="ok street",
        target_facility=None,
        client=client,
    )
    assert hit.source == GeocodeSource.GEOCODER and (hit.latitude, hit.longitude) == (12.5, 77.5)
    assert (
        geocoding.resolve(
            db_session,
            latitude=None,
            longitude=None,
            city=None,
            location_text="bad",
            target_facility=None,
            client=client,
        )
        is None
    )
    assert (
        geocoding.resolve(
            db_session,
            latitude=None,
            longitude=None,
            city=None,
            location_text="down",
            target_facility=None,
            client=client,
        )
        is None
    )


# --------------------------------------------------------------------------- #
# 5 + 6. Distance-aware ranking and the donor distance limit
# --------------------------------------------------------------------------- #
def test_donor_distance_limit_below_at_and_above_and_admin_override():
    lat0, lon0 = 12.0, 77.0
    near = _donor("O_NEG", lat0 + 10 / KM_PER_DEG_LAT, lon0)
    at = _donor("O_NEG", lat0 + 50.0 / KM_PER_DEG_LAT, lon0)
    above = _donor("O_NEG", lat0 + 50.2 / KM_PER_DEG_LAT, lon0)
    unknown = _donor("O_NEG")  # no shared location
    assert all(c for c in (near, at, above, unknown))
    p, _ = auth_client("PATIENT")
    eid = p.post(
        "/emergencies", json={"blood_type": "O_NEG", "latitude": lat0, "longitude": lon0}
    ).json()["id"]
    m = p.get(f"/emergencies/{eid}/match-candidates").json()
    donors = [c for c in m["candidates"] if c["role"] == "DONOR"]
    dists = [c["distance_km"] for c in donors]
    assert dists == [10.0, 50.0, None]  # nearest first, unknown last
    assert m["distance_limit_km"] == settings.max_donor_distance_km
    assert m["excluded_beyond_limit"] == 1 and m["unknown_distance"] == 1
    assert m["emergency_has_coordinates"] is True

    # only an admin can lift the limit, and it is audited
    other, _ = auth_client("HOSPITAL")
    r = other.post(
        f"/emergencies/{eid}/overrides",
        json={"kind": "DONOR_DISTANCE", "reason": "rural area, few donors"},
    )
    assert r.status_code == 403
    admin, _ = auth_client("ADMIN")
    r = admin.post(
        f"/emergencies/{eid}/overrides",
        json={"kind": "DONOR_DISTANCE", "reason": "rural area, few donors"},
    )
    assert r.status_code == 201, r.text
    m2 = p.get(f"/emergencies/{eid}/match-candidates").json()
    assert m2["distance_override_active"] is True and m2["excluded_beyond_limit"] == 0
    assert [c["distance_km"] for c in m2["candidates"] if c["role"] == "DONOR"] == [
        10.0,
        50.0,
        50.2,
        None,
    ]
    detail = p.get(f"/emergencies/{eid}").json()
    assert detail["overrides"][0]["kind"] == "DONOR_DISTANCE"
    assert "override_granted" in [e["event_type"] for e in detail["events"]]
    with SessionLocal() as db:
        assert (
            db.scalar(select(AuditLog).where(AuditLog.action == "emergency.override_granted"))
            is not None
        )
    # revoke restores the limit
    oid = r.json()["id"]
    assert admin.post(f"/emergencies/{eid}/overrides/{oid}/revoke").status_code == 200
    assert p.get(f"/emergencies/{eid}/match-candidates").json()["excluded_beyond_limit"] == 1


def test_resource_pathways_report_route_basis_eta_and_component():
    hosp, me = _hospital("Requester", 12.0, 77.0)
    _c, src = _hospital("Supplier", 12.2, 77.2)
    admin, _ = auth_client("ADMIN")
    route = admin.post(
        "/admin/transport-routes",
        json={
            "origin_facility_id": me["id"],
            "destination_facility_id": src["id"],
            "distance_km": 40,
            "transit_minutes": 50,
        },
    ).json()
    stock_units(src["id"], "AB_POS", 3, component="PLASMA")
    r = hosp.get(
        "/hospitals/me/resource-pathways",
        params={"blood_type": "AB_POS", "component": "PLASMA", "units_needed": 2},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    cand = body["candidates"][0]
    assert cand["route_basis"] == "ROUTED" and cand["route_ids"] == [route["id"]]
    assert (
        cand["distance_km"] == 40 and cand["eta_minutes"] == settings.pathway_handling_minutes + 50
    )
    assert cand["units_by_type"] == {"AB_POS": 3}
    # whole blood is a different product: nothing found
    r2 = hosp.get("/hospitals/me/resource-pathways", params={"blood_type": "AB_POS"})
    assert r2.json()["candidates"] == []


# --------------------------------------------------------------------------- #
# 7. Hospital verification in responder selection
# --------------------------------------------------------------------------- #
def test_unverified_hospital_cannot_accept_and_is_not_offered_as_responder():
    unv, _ = _hospital("Unverified Clinic", 12.0, 77.0, verified=False)
    ver, _ = _hospital("Verified Clinic", 12.01, 77.01)
    p, _ = auth_client("PATIENT")
    eid = p.post(
        "/emergencies", json={"blood_type": "O_NEG", "latitude": 12.0, "longitude": 77.0}
    ).json()["id"]
    r = unv.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"})
    assert r.status_code == 403
    assert ver.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"}).status_code == 200
    hospitals = [
        c
        for c in p.get(f"/emergencies/{eid}/match-candidates").json()["candidates"]
        if c["role"] == "HOSPITAL"
    ]
    assert [h["facility_verified"] for h in hospitals] == [True]


def test_hospital_accept_reserves_compatible_component_units():
    hosp, me = _hospital("Plasma Bank", 12.0, 77.0)
    stock_units(me["id"], "AB_NEG", 2, component="PLASMA")
    stock_units(me["id"], "O_NEG", 5, component="PLASMA")  # incompatible plasma for AB
    p, _ = auth_client("PATIENT")
    eid = p.post(
        "/emergencies", json={"blood_type": "AB_POS", "component": "PLASMA", "units_needed": 2}
    ).json()["id"]
    r = hosp.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"})
    assert "Reserved 2 AB_NEG plasma" in r.json()["inventory_note"]
    p.post(f"/emergencies/{eid}/cancel", json={})
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(HospitalInventory.reserved_units).where(
                    HospitalInventory.hospital_id == uuid.UUID(me["id"]),
                    HospitalInventory.blood_type == BloodType.AB_NEG,
                    HospitalInventory.component == BloodComponent.PLASMA,
                )
            )
            == 0
        )


def test_admin_revocation_withdraws_responses_and_releases_reservations():
    hosp, me = _hospital("Soon Revoked", 12.0, 77.0)
    stock_units(me["id"], "O_NEG", 3)
    p, _ = auth_client("PATIENT")
    eid = p.post("/emergencies", json={"blood_type": "O_NEG", "units_needed": 2}).json()["id"]
    assert (
        "Reserved 2"
        in hosp.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"}).json()[
            "inventory_note"
        ]
    )
    admin, _ = auth_client("ADMIN")
    r = admin.post(
        f"/admin/facilities/{me['id']}/revoke-verification",
        json={"reason": "licence suspended by regulator"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["withdrawn_emergency_responses"] == 1
    assert r.json()["facility"]["verified"] is False
    inv = next(i for i in hosp.get("/hospitals/me/inventory").json() if i["blood_type"] == "O_NEG")
    assert inv["reserved_units"] == 0
    assert "verification.revoked" in _notif_types(hosp)
    assert (
        admin.post(
            f"/admin/facilities/{me['id']}/revoke-verification",
            json={"reason": "licence suspended by regulator"},
        ).status_code
        == 409
    )


# --------------------------------------------------------------------------- #
# 20 + 21. Hospital alerts and donor alert preferences
# --------------------------------------------------------------------------- #
def test_hospital_alerts_use_proximity_capability_verification_and_target():
    near_stock, ns = _hospital("Near With Stock", 12.01, 77.01)
    near_empty, _ = _hospital("Near Empty", 12.02, 77.02)
    near_unv, nu = _hospital("Near Unverified", 12.03, 77.03, verified=False)
    far_stock, fs = _hospital("Far With Stock", 15.0, 80.0)
    target, tg = _hospital("Target Far", 16.0, 81.0)
    for f in (ns, nu, fs):
        stock_units(f["id"], "O_NEG", 2)
    p, _ = auth_client("PATIENT")
    p.post(
        "/emergencies",
        json={
            "blood_type": "A_POS",
            "latitude": 12.0,
            "longitude": 77.0,
            "target_facility_id": tg["id"],
        },
    )
    assert "emergency.new" in _notif_types(near_stock)
    assert "emergency.new" in _notif_types(target)
    for c in (near_empty, near_unv, far_stock):
        assert "emergency.new" not in _notif_types(c)


def test_donor_urgency_preference_opt_out_and_hourly_cap(monkeypatch):
    # Audit D4 (2026-10-31): alerts need a shared location or the same city
    # (app/core/distance_policy.py); this test is about something else, so the
    # donors and the request/emergency share one city.
    critical_only = _donor("O_NEG", alert_min_urgency="CRITICAL", city="Hub")
    everything = _donor("O_NEG", city="Hub")
    opted_out = _donor("O_NEG", city="Hub")
    opted_out.put("/notifications/preferences", json={"emergency_alerts_enabled": False})
    p, _ = auth_client("PATIENT")
    p.post("/emergencies", json={"blood_type": "O_NEG", "urgency": "URGENT", "city": "Hub"})
    assert _notif_types(critical_only).count("emergency.new") == 0
    assert _notif_types(everything).count("emergency.new") == 1
    p.post("/emergencies", json={"blood_type": "O_NEG", "urgency": "CRITICAL", "city": "Hub"})
    assert _notif_types(critical_only).count("emergency.new") == 1
    assert _notif_types(opted_out).count("emergency.new") == 0
    monkeypatch.setattr(settings, "donor_emergency_alerts_per_hour", 2)
    p2, _ = auth_client("PATIENT")
    p2.post("/emergencies", json={"blood_type": "O_NEG", "urgency": "CRITICAL", "city": "Hub"})
    assert _notif_types(everything).count("emergency.new") == 2  # capped at 2 per hour


# --------------------------------------------------------------------------- #
# 14. SMS: console provider never claims delivery; retries are scheduled
# --------------------------------------------------------------------------- #
def test_console_sms_provider_records_logged_only_never_sent(monkeypatch, db_session):
    from app.models.notification import Notification
    from app.models.user import User
    from app.services import sms_service

    monkeypatch.setattr(settings, "sms_provider", "console")
    c, email = auth_client("DONOR")
    c.put("/donors/me", json={"phone": "+919876543210"})
    user = db_session.scalar(select(User).where(User.email == email))
    n = Notification(user_id=user.id, type="emergency.new", title="t", body="b", data={})
    db_session.add(n)
    db_session.flush()
    row = sms_service.send_for_event(
        db_session,
        user,
        event_type="emergency.new",
        notification_id=n.id,
        sms_variables={"blood_type": "O-", "location": "x"},
    )
    assert row.status == SmsStatus.LOGGED_ONLY  # never SENT
    assert row.sent_at is None and row.provider == "CONSOLE"
    assert row.recipient_phone_masked and "9876543210" not in row.recipient_phone_masked


def test_console_sms_provider_is_refused_in_production(monkeypatch):
    monkeypatch.setattr(settings, "sms_provider", "console")
    monkeypatch.setattr(settings, "environment", "production")
    assert settings.sms_provider_configured is False


def test_retryable_sms_failure_is_scheduled_with_backoff(monkeypatch):
    from app.models.enums import SmsFailureCategory
    from app.models.sms_notification import SmsNotification
    from app.services import sms_service

    scheduled = []

    class FakeQueue:
        def enqueue_in(self, delay, fn, *args):
            scheduled.append((delay.total_seconds(), fn.__name__, args))

    import app.workers.queue as q

    monkeypatch.setattr(q, "sms_queue", FakeQueue())
    monkeypatch.setattr(settings, "rq_sync", False)
    row = SmsNotification(
        notification_id=uuid.uuid4(),
        recipient_user_id=uuid.uuid4(),
        event_type="x",
        status=SmsStatus.FAILED,
        retry_count=2,
        failure_category=SmsFailureCategory.TIMEOUT,
    )
    assert sms_service._schedule_retry_if_retryable(row) is True
    assert row.status == SmsStatus.PENDING
    assert scheduled[0][0] == settings.sms_retry_base_seconds * 4
    assert scheduled[0][1] == "retry_sms"
    row2 = SmsNotification(
        notification_id=uuid.uuid4(),
        recipient_user_id=uuid.uuid4(),
        event_type="x",
        status=SmsStatus.FAILED,
        retry_count=settings.sms_max_retries,
        failure_category=SmsFailureCategory.TIMEOUT,
    )
    assert sms_service._schedule_retry_if_retryable(row2) is False  # exhausted
    row3 = SmsNotification(
        notification_id=uuid.uuid4(),
        recipient_user_id=uuid.uuid4(),
        event_type="x",
        status=SmsStatus.FAILED,
        retry_count=0,
        failure_category=SmsFailureCategory.INVALID_RECIPIENT,
    )
    assert sms_service._schedule_retry_if_retryable(row3) is False  # permanent


# --------------------------------------------------------------------------- #
# 15 + 16. Cold chain telemetry and per-unit receipt outcomes
# --------------------------------------------------------------------------- #
def _shipment(units=3, component="WHOLE_BLOOD", blood_type="O_POS"):
    origin_c, origin = _hospital("Origin Bank", 12.0, 77.0)
    dest_c, dest = _hospital("Dest Hosp", 12.1, 77.1)
    stock_units(origin["id"], blood_type, units, component=component)
    r = origin_c.post(
        "/shipments",
        json={
            "origin_facility_id": origin["id"],
            "destination_facility_id": dest["id"],
            "items": [{"blood_type": blood_type, "units": units, "component": component}],
        },
    )
    assert r.status_code == 201, r.text
    s = r.json()
    assert origin_c.post(f"/shipments/{s['id']}/dispatch", json={}).status_code == 200
    return origin_c, dest_c, s


def _readings(temps, *, start_minutes_ago=50, step=10, sensor="LOG-1"):
    now = utcnow()
    out = []
    for i, t in enumerate(temps):
        r = {
            "recorded_at": (now - timedelta(minutes=start_minutes_ago - i * step)).isoformat(),
            "sensor_id": sensor,
        }
        if t is None:
            r["error_code"] = "PROBE_FAULT"
        else:
            r["temperature_c"] = t
        out.append(r)
    return out


def test_shipment_takes_its_range_from_cited_storage_conditions_and_refuses_mixes():
    origin_c, origin = _hospital("Mixer", 12.0, 77.0)
    _d, dest = _hospital("MixDest", 12.1, 77.1)
    stock_units(origin["id"], "O_POS", 1)
    stock_units(origin["id"], "O_POS", 1, component="PLATELETS")
    r = origin_c.post(
        "/shipments",
        json={
            "origin_facility_id": origin["id"],
            "destination_facility_id": dest["id"],
            "items": [
                {"blood_type": "O_POS", "units": 1},
                {"blood_type": "O_POS", "units": 1, "component": "PLATELETS"},
            ],
        },
    )
    assert r.status_code == 409 and "different storage temperatures" in r.json()["detail"]
    ok = origin_c.post(
        "/shipments",
        json={
            "origin_facility_id": origin["id"],
            "destination_facility_id": dest["id"],
            "items": [{"blood_type": "O_POS", "units": 1}],
        },
    ).json()
    assert (ok["temp_min_c"], ok["temp_max_c"]) == (1, 6)
    assert ok["cold_chain_status"] == "NO_DATA"


@pytest.mark.parametrize(
    "temps, expected",
    [
        ([4.0, 4.2, 3.9, 4.1, 4.0, 4.3], "WITHIN_RANGE"),
        ([4.0, 8.5, 4.1, 4.0, 4.0, 4.1], "EXCURSION"),
        ([4.0, 8.5, 4.1, 0.2, 4.0, 4.1], "REPEATED_EXCURSIONS"),
        ([4.0, None, 4.1, 4.0, 4.0, 4.1], "SENSOR_FAILURE"),
        ([4.0, 4.1, 150.0, 4.0, 4.0, 4.1], "SENSOR_FAILURE"),  # implausible value
    ],
)
def test_cold_chain_evaluation(temps, expected):
    origin_c, _dest_c, s = _shipment()
    r = origin_c.post(
        f"/shipments/{s['id']}/telemetry", json={"source": "SENSOR", "readings": _readings(temps)}
    )
    assert r.status_code == 200, r.text
    assert r.json()["report"]["status"] == expected
    assert r.json()["inserted"] == len(temps)


def test_missing_data_is_a_data_gap_and_duplicates_are_idempotent(monkeypatch):
    monkeypatch.setattr(settings, "telemetry_max_gap_minutes", 15)
    origin_c, _dest_c, s = _shipment()
    body = {"readings": _readings([4.0, 4.1], start_minutes_ago=50, step=40)}
    r = origin_c.post(f"/shipments/{s['id']}/telemetry", json=body)
    assert r.json()["report"]["status"] == "DATA_GAP"
    again = origin_c.post(f"/shipments/{s['id']}/telemetry", json=body).json()
    assert again["inserted"] == 0 and again["duplicates"] == 2


def test_telemetry_validation_rejects_future_and_valueless_readings():
    origin_c, _d, s = _shipment()
    future = [
        {
            "recorded_at": (utcnow() + timedelta(hours=1)).isoformat(),
            "temperature_c": 4.0,
            "sensor_id": "L",
        }
    ]
    assert (
        origin_c.post(f"/shipments/{s['id']}/telemetry", json={"readings": future}).status_code
        == 422
    )
    blank = [{"recorded_at": utcnow().isoformat(), "sensor_id": "L"}]
    assert (
        origin_c.post(f"/shipments/{s['id']}/telemetry", json={"readings": blank}).status_code
        == 422
    )


def test_receipt_records_per_unit_outcomes():
    origin_c, dest_c, s = _shipment(units=4)
    origin_c.post(
        f"/shipments/{s['id']}/telemetry",
        json={"readings": _readings([4.0, 4.1, 4.0, 4.2, 4.1, 4.0])},
    )
    origin_c.post(f"/shipments/{s['id']}/in-transit", json={})
    dest_c.post(f"/shipments/{s['id']}/arrive", json={})
    codes = [i["unit_code"] for i in s["items"]]
    r = dest_c.post(
        f"/shipments/{s['id']}/receive",
        json={
            "items": [
                {"unit_code": codes[1], "outcome": "DAMAGED", "reason": "bag punctured"},
                {"unit_code": codes[2], "outcome": "LOST", "reason": "not in the cooler"},
                {"unit_code": codes[3], "outcome": "QUARANTINED", "reason": "label unreadable"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    outcomes = {i["unit_code"]: i["outcome"] for i in r.json()["items"]}
    assert outcomes == {
        codes[0]: "RECEIVED_OK",
        codes[1]: "DAMAGED",
        codes[2]: "LOST",
        codes[3]: "QUARANTINED",
    }
    by_code = {i["unit_code"]: i for i in r.json()["items"]}
    assert by_code[codes[2]]["resulting_unit_id"] is None
    with SessionLocal() as db:
        states = {
            u.source_unit_id: u.state
            for u in db.scalars(
                select(BloodUnit).where(BloodUnit.source_shipment_id == uuid.UUID(s["id"]))
            ).all()
        }
    assert sorted(st.value for st in states.values()) == ["AVAILABLE", "DISCARDED", "QUARANTINED"]
    inv = next(
        i for i in dest_c.get("/hospitals/me/inventory").json() if i["blood_type"] == "O_POS"
    )
    assert inv["units"] == 1  # only the RECEIVED_OK unit counts as stock


def test_excursion_forces_quarantine_at_receipt():
    origin_c, dest_c, s = _shipment(units=2)
    origin_c.post(
        f"/shipments/{s['id']}/telemetry",
        json={"readings": _readings([4.0, 9.0, 4.0, 4.0, 4.1, 4.0])},
    )
    origin_c.post(f"/shipments/{s['id']}/in-transit", json={})
    assert dest_c.post(f"/shipments/{s['id']}/arrive", json={}).status_code == 200
    codes = [i["unit_code"] for i in s["items"]]
    refused = dest_c.post(
        f"/shipments/{s['id']}/receive",
        json={"items": [{"unit_code": codes[0], "outcome": "RECEIVED_OK"}]},
    )
    assert refused.status_code == 409 and "EXCURSION" in refused.json()["detail"]
    r = dest_c.post(f"/shipments/{s['id']}/receive", json={})
    assert {i["outcome"] for i in r.json()["items"]} == {"QUARANTINED"}
    assert r.json()["cold_chain_status"] == "EXCURSION"
    assert dest_c.get("/hospitals/me/inventory").json() == [] or all(
        i["units"] == 0 for i in dest_c.get("/hospitals/me/inventory").json()
    )


def test_failed_shipment_cannot_be_restocked_against_bad_telemetry():
    origin_c, _dest_c, s = _shipment(units=1)
    origin_c.post(
        f"/shipments/{s['id']}/telemetry",
        json={"readings": _readings([4.0, 12.0, 4.0, 4.0, 4.1, 4.0])},
    )
    origin_c.post(f"/shipments/{s['id']}/fail", json={"reason": "vehicle breakdown"})
    r = origin_c.post(
        f"/shipments/{s['id']}/reconcile",
        json={
            "outcome": "RETURNED_TO_ORIGIN",
            "note": "driver brought it back",
            "unit_codes": [i["unit_code"] for i in s["items"]],
            "cold_chain_maintained": True,
        },
    )
    assert r.status_code == 422 and "EXCURSION" in r.json()["detail"]
    q = origin_c.post(
        f"/shipments/{s['id']}/reconcile",
        json={"outcome": "QUARANTINED", "note": "held for review after excursion"},
    )
    assert q.status_code == 200
    assert {i["outcome"] for i in q.json()["items"]} == {"QUARANTINED"}


def test_simulator_labels_its_readings_and_is_admin_only():
    origin_c, _d, s = _shipment()
    assert origin_c.post(f"/shipments/{s['id']}/telemetry/simulate", json={}).status_code == 403
    admin, _ = auth_client("ADMIN")
    r = admin.post(
        f"/shipments/{s['id']}/telemetry/simulate", json={"minutes": 60, "interval_minutes": 10}
    )
    assert r.status_code == 200, r.text
    assert all(x["source"] == "SIMULATED" for x in r.json()["readings"])
    assert any("SIMULATED" in f for f in r.json()["report"]["findings"])


# --------------------------------------------------------------------------- #
# 17. Plasma thaw lifecycle + quarantine/discard
# --------------------------------------------------------------------------- #
def test_plasma_thaw_lifecycle():
    hosp, me = _hospital("Thaw Lab", 12.0, 77.0)
    stock_units(me["id"], "A_POS", 2, component="PLASMA")
    stock_units(me["id"], "A_POS", 1)
    units = hosp.get("/hospitals/me/units", params={"component": "PLASMA"}).json()
    wb = hosp.get("/hospitals/me/units", params={"component": "WHOLE_BLOOD"}).json()[0]
    assert hosp.post(f"/hospitals/me/units/{wb['id']}/thaw", json={}).status_code == 409
    r = hosp.post(f"/hospitals/me/units/{units[0]['id']}/thaw", json={"note": "for theatre 2"})
    assert r.status_code == 200, r.text
    thawed = r.json()
    assert thawed["thawed_at"] is not None
    assert thawed["effective_expires_at"] == thawed["thaw_expires_at"]
    assert hosp.post(f"/hospitals/me/units/{units[0]['id']}/thaw", json={}).status_code == 409
    # post-thaw window passes -> the sweep expires it
    with SessionLocal() as db:
        u = db.get(BloodUnit, uuid.UUID(units[0]["id"]))
        u.thaw_expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    after = {
        u["id"]: u for u in hosp.get("/hospitals/me/units", params={"component": "PLASMA"}).json()
    }
    assert after[units[0]["id"]]["state"] == "EXPIRED"
    assert after[units[1]["id"]]["state"] == "AVAILABLE"


def test_quarantine_release_and_discard_keep_counts_honest():
    hosp, me = _hospital("QA Lab", 12.0, 77.0)
    stock_units(me["id"], "B_POS", 2)
    u1, u2 = hosp.get("/hospitals/me/units").json()
    q = hosp.post(f"/hospitals/me/units/{u1['id']}/quarantine", json={"reason": "seal doubt"})
    assert q.json()["state"] == "QUARANTINED"
    inv = next(i for i in hosp.get("/hospitals/me/inventory").json() if i["blood_type"] == "B_POS")
    assert inv["units"] == 1
    rel = hosp.post(
        f"/hospitals/me/units/{u1['id']}/release-quarantine",
        json={"reason": "seal checked, intact"},
    )
    assert rel.json()["state"] == "AVAILABLE"
    d = hosp.post(f"/hospitals/me/units/{u2['id']}/discard", json={"reason": "haemolysed"})
    assert d.json()["state"] == "DISCARDED"
    inv = next(i for i in hosp.get("/hospitals/me/inventory").json() if i["blood_type"] == "B_POS")
    assert inv["units"] == 1
    assert (
        hosp.post(f"/hospitals/me/units/{u2['id']}/discard", json={"reason": "again"}).status_code
        == 409
    )
    other, _ = _hospital("Other Lab", 12.0, 77.0)
    assert (
        other.post(
            f"/hospitals/me/units/{u1['id']}/quarantine", json={"reason": "not mine"}
        ).status_code
        == 404
    )


# --------------------------------------------------------------------------- #
# 18. Demand forecasting
# --------------------------------------------------------------------------- #
def _allocation_history(facility_id: str, days: int, pattern):
    with SessionLocal() as db:
        hid = uuid.UUID(facility_id)
        for d in range(days, 0, -1):
            units = pattern(d)
            if units:
                db.add(
                    HospitalInventoryLog(
                        hospital_id=hid,
                        blood_type=BloodType.O_POS,
                        component=BloodComponent.WHOLE_BLOOD,
                        action=HospitalInventoryAction.ALLOCATE,
                        delta_units=-units,
                        delta_reserved=0,
                        resulting_units=0,
                        resulting_reserved=0,
                        created_at=utcnow() - timedelta(days=d),
                        note="history",
                    )
                )
        db.commit()


def test_forecast_runs_backtests_and_is_labelled_synthetic():
    hosp, me = _hospital("Forecast Hosp", 12.0, 77.0)
    _allocation_history(me["id"], 56, lambda d: 3 + (d % 3))
    r = hosp.post("/forecasts", json={"history_days": 56, "horizon_days": 7})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["data_is_synthetic"] is True and "Not a clinical prediction" in body["disclaimer"]
    series = body["results"][0]
    assert series["status"] == "OK" and len(series["forecast"]) == 7
    f1 = series["forecast"][0]
    assert f1["lo80"] <= f1["mean"] <= f1["hi80"]
    bt = series["backtest"]
    assert {"ses_mae", "naive_mae", "seasonal_naive_mae", "beats_naive"} <= set(bt)
    assert hosp.get("/forecasts").json()[0]["id"] == body["id"]


def test_forecast_refuses_with_insufficient_history_and_scopes_hospitals():
    hosp, me = _hospital("Thin History", 12.0, 77.0)
    _allocation_history(me["id"], 3, lambda d: 1)
    series = hosp.post("/forecasts", json={"history_days": 30}).json()["results"][0]
    assert series["status"] == "INSUFFICIENT_HISTORY" and series["forecast"] == []
    other, them = _hospital("Nosy", 12.0, 77.0)
    assert other.post("/forecasts", json={"facility_id": me["id"]}).status_code == 403
    assert other.post("/forecasts", json={"network": True}).status_code == 403


# --------------------------------------------------------------------------- #
# 19. Deadline monitor
# --------------------------------------------------------------------------- #
def test_deadline_monitor_warns_flags_escalates_once():
    from app.workers import deadline_monitor

    admin, _ = auth_client("ADMIN")
    p, _ = auth_client("PATIENT")
    soon = p.post(
        "/emergencies",
        json={"blood_type": "O_NEG", "deadline": (utcnow() + timedelta(minutes=10)).isoformat()},
    ).json()
    late = p.post(
        "/emergencies",
        json={"blood_type": "O_NEG", "deadline": (utcnow() + timedelta(hours=5)).isoformat()},
    ).json()
    with SessionLocal() as db:
        em = db.get(Emergency, uuid.UUID(late["id"]))
        em.deadline = utcnow() - timedelta(minutes=1)
        db.commit()
        run1 = deadline_monitor.run_once(db)
    assert run1.warned == [soon["id"]]
    assert run1.exceeded == [late["id"]] and run1.escalated == [late["id"]]
    with SessionLocal() as db:
        run2 = deadline_monitor.run_once(db)
    assert (run2.warned, run2.exceeded, run2.escalated) == ([], [], [])  # idempotent
    events = [e["event_type"] for e in p.get(f"/emergencies/{late['id']}").json()["events"]]
    assert "deadline_exceeded" in events and "deadline_escalated" in events
    assert "emergency.deadline_escalated" in _notif_types(admin)
    assert "emergency.deadline_warning" in _notif_types(p)


def test_deadline_monitor_sweeps_effective_expiry():
    from app.workers import deadline_monitor

    hosp, me = _hospital("Sweep Hosp", 12.0, 77.0)
    stock_units(me["id"], "O_POS", 2)
    with SessionLocal() as db:
        u = db.scalars(
            select(BloodUnit).where(BloodUnit.hospital_id == uuid.UUID(me["id"]))
        ).first()
        u.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
        run = deadline_monitor.run_once(db)
        assert run.swept_facilities == 1
        row = db.scalar(
            select(HospitalInventory).where(HospitalInventory.hospital_id == uuid.UUID(me["id"]))
        )
        assert row.units == 1
        assert (
            db.scalar(select(BloodUnit.state).where(BloodUnit.id == u.id)) == BloodUnitState.EXPIRED
        )


def test_cold_chain_status_enum_is_complete():
    assert {s.value for s in ColdChainStatus} == {
        "NO_DATA",
        "WITHIN_RANGE",
        "EXCURSION",
        "REPEATED_EXCURSIONS",
        "DATA_GAP",
        "SENSOR_FAILURE",
    }
    assert HospitalProfile.__tablename__ == "hospital_profiles"


# --------------------------------------------------------------------------- #
# Success paths of the read endpoints added in this round (found missing by
# the API-coverage pass of the final regression: they were only ever hit
# with 401/403).
# --------------------------------------------------------------------------- #
def test_telemetry_read_returns_readings_and_evaluation_to_both_parties_only():
    origin_c, dest_c, s = _shipment(units=2)
    posted = origin_c.post(
        f"/shipments/{s['id']}/telemetry", json={"readings": _readings([4.0, 4.2, 9.5, 4.1])}
    )
    assert posted.status_code in (200, 201), posted.text
    for c in (origin_c, dest_c):
        r = c.get(f"/shipments/{s['id']}/telemetry")
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["readings"]) == 4
        assert [x["temperature_c"] for x in body["readings"]] == [4.0, 4.2, 9.5, 4.1]
        assert body["report"]["status"] == "EXCURSION"  # 9.5 C is outside 1-6 C
    outsider, _ = _hospital("Outsider", 12.0, 77.0)
    assert outsider.get(f"/shipments/{s['id']}/telemetry").status_code == 403


def test_forecast_run_can_be_read_back_by_its_owner_only():
    hosp, me = _hospital("Forecast Reader", 12.0, 77.0)
    _allocation_history(me["id"], 28, lambda d: 2 + (d % 2))
    run = hosp.post("/forecasts", json={"history_days": 28}).json()
    r = hosp.get(f"/forecasts/{run['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == run["id"] and r.json()["results"] == run["results"]
    other, _ = _hospital("Other Reader", 12.0, 77.0)
    assert other.get(f"/forecasts/{run['id']}").status_code == 404
    admin, _ = auth_client("ADMIN")
    assert admin.get(f"/forecasts/{run['id']}").status_code == 200


def test_pathway_config_reports_the_assumptions_in_force():
    c, _ = auth_client("PATIENT")
    r = c.get("/pathways/config")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["handling_minutes"] == settings.pathway_handling_minutes
    assert body["max_hops"] == settings.pathway_max_hops
    assert body["max_backups"] == settings.pathway_max_backups
    assert body["data_is_synthetic"] is settings.data_is_synthetic


def test_pathway_simulation_is_a_dry_run_and_honours_simulated_failures():
    from app.models.emergency_pathway import EmergencyPathway

    admin, _ = auth_client("ADMIN")
    er_c, er = _hospital("Sim ER", 13.0, 80.0)
    _, bank_a = _hospital("Sim Bank A", 13.01, 80.01)
    _, bank_b = _hospital("Sim Bank B", 13.05, 80.05)
    ra = admin.post(
        "/admin/transport-routes",
        json={
            "origin_facility_id": er["id"],
            "destination_facility_id": bank_a["id"],
            "distance_km": 2,
            "transit_minutes": 5,
        },
    ).json()
    admin.post(
        "/admin/transport-routes",
        json={
            "origin_facility_id": er["id"],
            "destination_facility_id": bank_b["id"],
            "distance_km": 9,
            "transit_minutes": 15,
        },
    )
    stock_units(bank_a["id"], "B_NEG", 3)
    stock_units(bank_b["id"], "B_NEG", 3)
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(EmergencyPathway))
    req = {"recipient_blood_type": "B_NEG", "units_needed": 2, "destination_facility_id": er["id"]}
    r = er_c.post("/pathways/simulate", json=req)
    assert r.status_code == 200, r.text
    assert r.json()["primary"]["legs"][0]["facility_id"] == bank_a["id"]
    assert r.json()["primary"]["feasibility"] == "VIABLE"
    r2 = er_c.post("/pathways/simulate", json={**req, "failed_route_ids": [ra["id"]]})
    assert r2.status_code == 200, r2.text
    assert r2.json()["primary"]["legs"][0]["facility_id"] == bank_b["id"]
    assert r2.json()["simulated_failed_route_ids"] == [ra["id"]]
    # nothing stored, nothing reserved, the route itself untouched
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(EmergencyPathway)) == before
        assert all(
            u.state == BloodUnitState.AVAILABLE
            for u in db.scalars(
                select(BloodUnit).where(
                    BloodUnit.hospital_id.in_([uuid.UUID(bank_a["id"]), uuid.UUID(bank_b["id"])])
                )
            )
        )
    routes = admin.get("/admin/transport-routes").json()
    routes = routes.get("items", routes) if isinstance(routes, dict) else routes
    assert next(x for x in routes if x["id"] == ra["id"])["status"] == "OPERATIONAL"
    # a hospital may only simulate deliveries to itself; unknown routes are 404
    other_c, _ = _hospital("Sim Other", 13.0, 80.0)
    assert other_c.post("/pathways/simulate", json=req).status_code == 403
    bogus = {**req, "failed_route_ids": [str(uuid.uuid4())]}
    assert er_c.post("/pathways/simulate", json=bogus).status_code == 404


def test_deadline_monitor_writes_a_heartbeat_after_each_pass(tmp_path, monkeypatch):
    """docker-compose's deadline-monitor health check reads this heartbeat
    (the image's HTTP health check does not apply to a non-HTTP worker)."""
    import time as _time

    from app.workers import deadline_monitor

    hb = tmp_path / "hb"
    monkeypatch.setenv(deadline_monitor.HEARTBEAT_ENV, str(hb))
    deadline_monitor.main(["--once"])
    assert hb.exists() and _time.time() - hb.stat().st_mtime < 60


def test_compose_gives_non_http_services_their_own_health_checks():
    import yaml

    compose = yaml.safe_load(
        open(
            __import__("os").path.join(
                __import__("os").path.dirname(__file__), "..", "docker-compose.yml"
            )
        )
    )
    for name in ("worker", "deadline-monitor"):
        test = " ".join(compose["services"][name]["healthcheck"]["test"])
        assert "health/live" not in test  # not the API's HTTP probe
    assert "Worker.all" in " ".join(compose["services"]["worker"]["healthcheck"]["test"])
    assert "lifelink-deadline-monitor.heartbeat" in " ".join(
        compose["services"]["deadline-monitor"]["healthcheck"]["test"]
    )
