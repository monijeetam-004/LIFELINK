from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class CertificateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    donation_id: uuid.UUID
    donor_user_id: uuid.UUID
    serial: str
    verify_code: str
    created_at: datetime
    # Hydrated from the linked donation for display.
    donor_name: str | None = None
    donated_on: date | None = None
    units: int | None = None
    hospital_name: str | None = None
    verify_path: str | None = None
    download_token: str | None = None
    # ---- Phase 31 additions ----
    revoked_at: datetime | None = None
    revocation_reason: str | None = None


class CertificateIssueIn(BaseModel):
    donation_id: uuid.UUID


class CertificateVerifyOut(BaseModel):
    """Public verification payload — safe fields only.

    Phase 31: `valid` now honestly reflects revocation, not merely
    "a row with this code exists". A revoked certificate still returns its
    factual donation details (so a relying party can see what it *was*),
    but `valid=False` plus `revoked_at`/`revocation_reason` make its current
    trust status unambiguous rather than indistinguishable from a genuine
    certificate.
    """

    valid: bool
    serial: str
    donor_name: str
    donated_on: date
    units: int
    hospital_name: str | None
    issued_at: datetime
    revoked_at: datetime | None = None
    revocation_reason: str | None = None


class CertificateRevokeIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
