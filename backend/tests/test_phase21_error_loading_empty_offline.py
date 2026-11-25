"""Phase 21 -- Comprehensive Error, Loading, Empty & Offline UX Hardening.

The bulk of Phase 21's work was on the frontend (frontend/src/lib/api.ts's
toApiError()/session-expiry plumbing, the global ErrorBoundary and 403/404/
500/offline/session-expired pages, and per-page loading/empty/error/retry
coverage -- see LIFELINK_PHASE_21_AUDIT.md and LIFELINK_PHASE_21_REPORT.md).
No backend behavior changed in this phase. Per the phase brief's "add
backend tests only where backend behavior changes," this file does not
re-test unchanged backend logic -- it instead locks in the exact backend
error-contract properties that two Phase 21 frontend bug fixes now depend
on, against the real HTTP API and the real Postgres test database, nothing
mocked:

1. `NewRequestGuidedFlow.tsx` (patient guided blood-request flow) was fixed
   to mint an idempotency_key like `BloodRequestModal.tsx` already did, and
   to mint a *new* key only when the server responds 409 to a same-key/
   different-payload replay -- see blood_requests.py's `_idempotency_payload_hash`/
   `_existing_by_idempotency_key`. If the server ever stopped returning 409
   for that case, or changed its detail contract, that frontend key-rotation
   logic would silently stop working and duplicate requests could result.
2. `VerifyCertificate.tsx` was fixed to only show the honest "Not verified /
   double-check the code" message for a genuine 404, and a distinct
   "couldn't check this certificate" message with retry for every other
   failure -- so the exact 404 status/detail contract for an unknown
   verification code matters just as much as the fix itself.

A regression in either of these two contracts would silently break the
frontend fix without any frontend test catching it (the frontend tests
mock/observe the *shape* of a 404/409, not whether the real backend still
produces one for these specific real-world cases) -- these tests close
that gap.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests.helpers import auth_client


# --------------------------------------------------------------------------- #
# 1. Blood-request idempotency-key conflict: still a clean 409, still with a
#    detail message safe to show directly in the UI (no leaked internals).
#    NewRequestGuidedFlow.tsx / BloodRequestModal.tsx both branch on
#    `status === 409` to decide whether to mint a fresh key for the next
#    attempt -- this is the exact contract that decision relies on.
# --------------------------------------------------------------------------- #
def test_blood_request_same_key_different_payload_is_a_clean_409() -> None:
    patient, _ = auth_client("PATIENT")
    key = str(uuid.uuid4())

    first = patient.post(
        "/blood-requests",
        json={"blood_type": "O_NEG", "city": "Metro", "idempotency_key": key},
    )
    assert first.status_code == 201, first.text

    # Same key, different payload (different blood type) -- a real replay of
    # a *changed* form, exactly the case NewRequestGuidedFlow's onError
    # handles by minting a new key so the next submit isn't rejected again.
    conflict = patient.post(
        "/blood-requests",
        json={"blood_type": "A_POS", "city": "Metro", "idempotency_key": key},
    )
    assert conflict.status_code == 409, conflict.text
    body = conflict.json()
    assert "detail" in body
    assert isinstance(body["detail"], str) and body["detail"]
    # No leaked internals (SQL, stack trace, file paths, secret material) --
    # the whole point of a UI-safe detail string.
    lowered = body["detail"].lower()
    for leak_marker in ("traceback", "sqlalchemy", "/home/", "password", "secret"):
        assert leak_marker not in lowered


def test_blood_request_same_key_same_payload_replays_cleanly_not_a_duplicate() -> None:
    """The companion behavior a correct idempotency-key implementation must
    have: a genuine retry (same key, unchanged form) must NOT create a
    second request -- this is what NewRequestGuidedFlow's fix is actually
    protecting against (a failed submit retried by the user creating a
    duplicate blood request), so it's worth locking in explicitly here."""
    patient, _ = auth_client("PATIENT")
    key = str(uuid.uuid4())
    payload = {"blood_type": "B_POS", "units_needed": 2, "city": "Metro", "idempotency_key": key}

    first = patient.post("/blood-requests", json=payload)
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]

    replay = patient.post("/blood-requests", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == first_id

    listing = patient.get("/blood-requests").json()
    matching = [r for r in listing if r["id"] == first_id]
    assert len(matching) == 1


# --------------------------------------------------------------------------- #
# 2. Certificate verification: a genuinely-unknown code is a clean 404 with
#    an honest, UI-safe detail -- VerifyCertificate.tsx's `genuinelyNotFound
#    = apiError?.status === 404` branch depends on exactly this status code
#    to decide whether to show "Not verified" (this branch) vs. "couldn't
#    check this certificate, try again" (every other status).
# --------------------------------------------------------------------------- #
def test_verify_unknown_certificate_code_is_a_clean_404_not_a_500_or_a_false_200() -> None:
    resp = auth_client("DONOR")[0].get(f"/certificates/verify/{uuid.uuid4().hex}")
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body == {"detail": "Certificate not found"}


def test_verify_unknown_certificate_code_requires_no_auth() -> None:
    """VerifyCertificate.tsx is a public, unauthenticated page (`/verify/:code`)
    -- this locks in that the endpoint it calls stays public, since an
    unexpected 401 there would (correctly, per the Phase 21 fix) render as
    "couldn't check this certificate" instead of "not verified," which would
    be a confusing regression for every anonymous visitor."""
    anon = TestClient(app, base_url="http://testserver")
    resp = anon.get(f"/certificates/verify/{uuid.uuid4().hex}")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# 3. Hospital request-detail 404: RequestManagementDetail.tsx renders
#    `req.isError` with `toApiError(req.error).detail` directly -- confirms
#    that detail is clean, human-readable text safe to show as-is, for the
#    common "bad/foreign id" case a hospital operator can genuinely hit.
# --------------------------------------------------------------------------- #
def test_blood_request_detail_nonexistent_id_is_a_clean_404() -> None:
    hospital, _ = auth_client("HOSPITAL")
    resp = hospital.get(f"/blood-requests/{uuid.uuid4()}")
    assert resp.status_code == 404, resp.text
    assert resp.json() == {"detail": "Request not found"}
