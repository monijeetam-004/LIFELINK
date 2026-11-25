"""Production hardening -- regression tests for the performance findings
measured in docs/LIFELINK_HARDENING_REPORT.md (volume database + load test).

1. GET /blood-requests (patient/family list) issued one COUNT per request
   (552 statements / 460 ms for a patient with 550 requests). Now one
   grouped count for the whole list; counts stay correct.
2. The admin request list and analytics per-day counts scanned + sorted the
   whole requests table; `ix_blood_requests_created_at` serves both.
"""
from __future__ import annotations

from app.models.blood_request import BloodRequest
from tests.helpers import auth_client
from tests.test_phase27_notification_scaling import count_queries


def _patient_with_requests(n: int):
    patient, _ = auth_client("PATIENT")
    ids = []
    for _ in range(n):
        r = patient.post("/blood-requests", json={"blood_type": "O_NEG", "units_needed": 2})
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    return patient, ids


def _list_statements(patient) -> tuple[int, list[dict]]:
    with count_queries() as stmts:
        resp = patient.get("/blood-requests")
        assert resp.status_code == 200
    return len(stmts), resp.json()


def test_request_list_statement_count_does_not_grow_with_requests() -> None:
    small_p, _ = _patient_with_requests(2)
    large_p, _ = _patient_with_requests(9)
    small, small_body = _list_statements(small_p)
    large, large_body = _list_statements(large_p)
    assert len(small_body) == 2 and len(large_body) == 9
    assert large == small, (small, large)


def test_request_list_accepted_counts_are_still_per_request() -> None:
    patient, ids = _patient_with_requests(3)
    for _ in range(2):
        donor, _ = auth_client("DONOR")
        assert donor.put("/donors/me", json={"blood_type": "O_NEG", "is_available": True}
                         ).status_code == 200
        r = donor.post(f"/blood-requests/{ids[1]}/respond", json={"action": "ACCEPTED"})
        assert r.status_code == 200, r.text
    _, body = _list_statements(patient)
    by_id = {row["id"]: row["accepted_count"] for row in body}
    assert by_id == {ids[0]: 0, ids[1]: 2, ids[2]: 0}
    # the single-request read agrees with the list
    assert patient.get(f"/blood-requests/{ids[1]}").json()["accepted_count"] == 2


def test_created_at_index_is_declared() -> None:
    names = {ix.name: [c.name for c in ix.columns] for ix in BloodRequest.__table__.indexes}
    assert names.get("ix_blood_requests_created_at") == ["created_at"]
