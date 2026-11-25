"""Phase 31 -- cross-request/cross-emergency donor double-booking check.

A physical donor can only actually donate blood in one place. Before this
phase, `blood_requests.respond_to_request` and `emergencies.respond_to_emergency`
each only checked uniqueness *within their own request/emergency*
(`uq_donor_response_pair` / `uq_emergency_response_pair`) -- nothing stopped
the same donor from simultaneously holding an ACCEPTED `DonorResponse` on
one open blood request AND an ACCEPTED `EmergencyResponse` on an open
emergency (or two open blood requests, or two open emergencies) at the same
time. That is a genuine domain-correctness gap, not a deliberate design
choice: nothing in this codebase's comments or reports discusses it as
intentional, and a donor who has "ACCEPTED" two different urgent asks at
once creates a false sense of supply for whichever one they don't actually
show up for.

This module answers one question -- "does this donor already hold another
active (ACCEPTED, not yet withdrawn) donation commitment?" -- by checking
both tables. It intentionally does NOT apply to HOSPITAL responders on
emergencies: a hospital is an institution coordinating supply/logistics,
not a single physical body that can only be in one place, so a hospital
accepting to help with several concurrent emergencies is normal and
correct.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.request_lifecycle import OPEN_STATUSES as BLOOD_REQUEST_OPEN_STATUSES
from app.models.blood_request import BloodRequest, DonorResponse
from app.models.emergency import Emergency, EmergencyResponse
from app.models.enums import EMERGENCY_OPEN_STATUSES, DonorResponseStatus
from app.models.profile import DonorProfile


def find_conflicting_commitment(
    db: Session,
    donor_user_id: uuid.UUID,
    *,
    exclude_request_id: uuid.UUID | None = None,
    exclude_emergency_id: uuid.UUID | None = None,
) -> str | None:
    """Return a human-readable description of the donor's other active
    ACCEPTED commitment, or None if they have none.

    Checked in the same order a caller would want to know about it:
    another open blood request first, then another open emergency. Only
    ACCEPTED responses on requests/emergencies still in an OPEN status
    count -- a commitment on a request that has since been fulfilled,
    cancelled, or expired is no longer "active" and must not block a new
    acceptance elsewhere.

    QA round (2026-10-20): the check is now serialized per donor. Before,
    two accepts from the same donor arriving at the same moment (two tabs,
    a retry racing the original) each ran this check before either had
    written its ACCEPTED row, so both passed and the donor ended up
    committed to two open requests -- reproduced by
    tests/test_qa_round_races.py. Locking the donor's profile row here makes
    the second caller wait until the first transaction commits; its check
    then sees the first commitment and is refused with 409. The lock is held
    only until the caller's transaction ends, and only one donor's row is
    locked, so unrelated donors are never serialized against each other.
    """
    db.execute(
        select(DonorProfile.id).where(DonorProfile.user_id == donor_user_id).with_for_update()
    )
    request_stmt = (
        select(BloodRequest.id)
        .join(DonorResponse, DonorResponse.request_id == BloodRequest.id)
        .where(
            DonorResponse.donor_user_id == donor_user_id,
            DonorResponse.status == DonorResponseStatus.ACCEPTED,
            BloodRequest.status.in_(BLOOD_REQUEST_OPEN_STATUSES),
        )
        .limit(1)
    )
    if exclude_request_id is not None:
        request_stmt = request_stmt.where(BloodRequest.id != exclude_request_id)
    if db.scalar(request_stmt) is not None:
        return "you already accepted another open blood request"

    emergency_stmt = (
        select(Emergency.id)
        .join(EmergencyResponse, EmergencyResponse.emergency_id == Emergency.id)
        .where(
            EmergencyResponse.responder_user_id == donor_user_id,
            EmergencyResponse.status == DonorResponseStatus.ACCEPTED,
            Emergency.status.in_(EMERGENCY_OPEN_STATUSES),
        )
        .limit(1)
    )
    if exclude_emergency_id is not None:
        emergency_stmt = emergency_stmt.where(Emergency.id != exclude_emergency_id)
    if db.scalar(emergency_stmt) is not None:
        return "you already accepted another open emergency"

    return None
