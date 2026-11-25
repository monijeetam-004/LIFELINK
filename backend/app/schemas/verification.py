from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import VerificationKind, VerificationStatus


class VerificationDocumentOut(BaseModel):
    """A submitted document's metadata. Deliberately omits the storage key --
    that's an internal detail, not something to hand back to any client."""

    filename: str
    content_type: str
    size: int
    # Phase E: SHA-256 of the exact bytes stored at submission time. None
    # only for documents submitted before Phase E (no checksum was ever
    # recorded for them, and none is invented after the fact).
    sha256: str | None = None
    # Hardening: malware-scan state (see app/services/document_quarantine.py).
    # Documents stored before scanning existed have no recorded status and
    # are reported as PENDING_SCAN (never served until a scan passes).
    scan_status: str = "PENDING_SCAN"
    # False when scanning was disabled in this environment (the document was
    # never inspected) -- shown to admins so "CLEAN" is never misread.
    malware_scanned: bool = False

    @model_validator(mode="before")
    @classmethod
    def _derive_scan_fields(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            value.setdefault("scan_status", "PENDING_SCAN")
            scanner = value.get("scanned_by")
            value["malware_scanned"] = bool(scanner) and scanner != "noop"
        return value


class VerificationRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    kind: VerificationKind
    status: VerificationStatus
    documents: list[VerificationDocumentOut]
    note: str | None
    reviewer_user_id: uuid.UUID | None
    review_note: str | None
    version: int = 1
    supersedes_request_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class VerificationApproveIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class VerificationRejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)
