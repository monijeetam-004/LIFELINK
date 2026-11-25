"""Phase 27 -- notify_matching_donors batching fix (scalability hardening).

Real, measured regression test for the fix documented in
app/services/notification_service.py::notify_matching_donors's own
docstring: before this phase, notifying N compatible/available donors cost
N * (INSERT+flush, SELECT preference, SELECT user) DB round trips, all held
open on the SAME connection/transaction for the entire loop -- reproduced
live in this phase with a real 129-donor O_NEG match (5-17s+ per call,
enough to exhaust the connection pool and take down unrelated endpoints
under concurrent load -- see LIFELINK_PHASE_27_ACCESSIBILITY_SCALABILITY_
REPORT.md's Scalability Investigation section for the full py-spy /
pg_stat_activity evidence).

This test proves two things with the real Postgres test database (no
mocking, matching every other file in this suite, and reusing Phase 24's
own `count_queries` real-SQL-statement-counter pattern):

  1. The number of DB statements notify_matching_donors triggers does NOT
     scale linearly with the number of matching donors (the actual bug),
     using the same "seed N, assert bounded not linear" methodology Phase
     24 established for the hospitals.py N+1 fix.
  2. The batching change is 100% behavior-preserving: every matching donor
     still gets their in-app Notification row AND their real-time email
     (RQ_SYNC=true in tests, so _enqueue delivers inline -- see conftest.py
     and email_service.sent_messages), and the existing Alert Watcher
     filters (alert_min_urgency, alert_radius_km) -- covered in depth by
     test_alert_watcher.py, untouched by this phase -- still apply; this
     file only adds the *scaling* angle those tests don't cover.
"""
from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator

from sqlalchemy import event

from app.db.session import engine
from app.services import email_service
from tests.helpers import auth_client


@contextlib.contextmanager
def count_queries() -> Iterator[list[str]]:
    """Same real SQL-statement counter as test_phase24_performance_hardening.py."""
    statements: list[str] = []

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        # Hardening: real-time publication runs on its own background thread
        # (off the request path); its statements are not the request's.
        if threading.current_thread().name == "lifelink-realtime-publisher":
            return
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)


def _available_donor(blood_type: str = "O_NEG"):
    c, email = auth_client("DONOR")
    # Audit D4 (2026-10-31): donors and hospital share a city so the shared
    # distance policy admits them (this test measures batching, not distance).
    r = c.put("/donors/me", json={"blood_type": blood_type, "is_available": True,
                                  "city": "Metro"})
    assert r.status_code == 200, r.text
    return c, email


def _hospital(lat: float = 12.9, lng: float = 77.5):
    c, email = auth_client("HOSPITAL")
    r = c.put("/hospitals/me", json={"name": "Metro General", "latitude": lat, "longitude": lng,
                                      "city": "Metro"})
    assert r.status_code == 200, r.text
    return c, email


def test_matching_donor_notification_query_count_is_bounded_not_linear() -> None:
    """Seed enough distinct compatible/available donors that the old
    per-donor loop's query count would clearly grow with N, then prove the
    real, measured count today does not."""
    email_service.sent_messages.clear()
    N = 12
    donors = [_available_donor("O_NEG") for _ in range(N)]
    hospital, _ = _hospital()

    with count_queries() as statements:
        r = hospital.post(
            "/blood-requests",
            json={"blood_type": "O_NEG", "units_needed": 1, "urgency": "URGENT"},
        )
    assert r.status_code == 201, r.text

    # Every donor's in-app notification and real-time email must still land.
    for client, email in donors:
        types = {n["type"] for n in client.get("/notifications").json()}
        assert "blood_request.new" in types, f"donor {email} was not notified"
    sent_to = {m["to"] for m in email_service.sent_messages}
    for _, email in donors:
        assert email in sent_to, f"donor {email} did not receive the real-time email"

    all_statements = statements
    # A linear (pre-fix) implementation needed >= N INSERTs (one Notification
    # row each) + >= N preference SELECTs + >= N user SELECTs on top of its
    # fixed per-request overhead -- i.e. it would grow by 3*N = 36 statements
    # for these 12 donors. The batched fix issues one INSERT (or a small,
    # fixed number of insertmanyvalues batches) and one SELECT each for
    # preferences and users, regardless of N.
    assert len(all_statements) <= 40, (
        f"expected a fixed, small statement count independent of the {N} "
        f"matching donors (old code needed ~{3 * N} extra statements just for "
        f"this fan-out), got {len(all_statements)} total"
    )


def test_matching_donor_notification_respects_existing_filters_after_batching() -> None:
    """Correctness spot-check specifically for the batched rewrite: an
    incompatible blood type and an unavailable donor are still correctly
    excluded (the batched fix must filter identically to the original
    per-donor loop, not just faster) -- alert_min_urgency/alert_radius_km's
    own filtering logic is covered in full by test_alert_watcher.py."""
    email_service.sent_messages.clear()
    match, match_email = _available_donor("O_NEG")
    wrong_type, wrong_email = _available_donor("A_POS")
    unavailable, unavailable_email = _available_donor("O_NEG")
    # `is_available` isn't a settable field on plain PUT /donors/me -- it's
    # derived from availability_state via the dedicated endpoint below (see
    # app/api/routes/donors.py::update_availability).
    r = unavailable.put("/donors/me/availability", json={"state": "UNAVAILABLE"})
    assert r.status_code == 200, r.text
    hospital, _ = _hospital()

    r = hospital.post(
        "/blood-requests",
        json={"blood_type": "O_NEG", "units_needed": 1, "urgency": "URGENT"},
    )
    assert r.status_code == 201, r.text

    assert "blood_request.new" in {n["type"] for n in match.get("/notifications").json()}
    assert "blood_request.new" not in {n["type"] for n in wrong_type.get("/notifications").json()}
    assert "blood_request.new" not in {
        n["type"] for n in unavailable.get("/notifications").json()
    }
    sent_to = {m["to"] for m in email_service.sent_messages}
    assert match_email in sent_to
    assert wrong_email not in sent_to
    assert unavailable_email not in sent_to
