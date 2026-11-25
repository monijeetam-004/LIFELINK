from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    BloodComponent,
    BloodRequestStatus,
    BloodType,
    DonorAvailabilityState,
    EmergencyStatus,
    FacilityType,
    Urgency,
)
from app.models.user import UserRole


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    is_email_verified: bool
    suspended_reason: str | None
    created_at: datetime


class AdminUserListOut(BaseModel):
    items: list[AdminUserOut]
    total: int
    page: int
    page_size: int


class AdminUserStatsOut(BaseModel):
    total_users: int
    by_role: dict[str, int]
    active_count: int
    suspended_count: int
    unverified_email_count: int


class AdminRoleChangeIn(BaseModel):
    role: UserRole


class AdminSuspendIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class AdminBloodRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    requester_user_id: uuid.UUID
    patient_user_id: uuid.UUID
    blood_type: BloodType
    units_needed: int
    urgency: Urgency
    status: BloodRequestStatus
    hospital_name: str | None
    city: str | None
    fulfilled_at: datetime | None = None
    cancelled_at: datetime | None = None
    expired_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    allowed_transitions: list[BloodRequestStatus] = []


class AdminBloodRequestTransitionIn(BaseModel):
    """Phase 10 admin intervention. Deliberately narrower than the full state
    machine -- an admin can force a request to a stop (CANCELLED) or mark it
    EXPIRED early, but cannot force MATCHING/FULFILLED on someone else's
    behalf (those require an actual donor match / actual fulfilment)."""

    target_status: BloodRequestStatus
    reason: str = Field(min_length=3, max_length=500)


class AdminEmergencyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_by_user_id: uuid.UUID
    patient_user_id: uuid.UUID | None
    blood_type: BloodType | None
    urgency: Urgency
    status: EmergencyStatus
    location_text: str | None
    created_at: datetime
    updated_at: datetime


class AdminAuditLogEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: uuid.UUID | None
    ip: str | None
    meta: dict[str, Any]
    created_at: datetime
    # Hydrated by the route (a join on the actor), not stored on AuditLog --
    # `severity` is derived from `action` at read time (see
    # app/api/routes/admin.py:_classify_severity), never a stored fact that
    # could drift from the action name.
    actor_email: str | None = None
    actor_role: UserRole | None = None
    severity: str = "INFO"
    # Phase E: hash-chain position. NULL only in the brief window between
    # the entry's commit and its sealing (or if sealing failed and has not
    # yet been retried) -- shown as "unsealed", never as verified.
    chain_seq: int | None = None
    entry_hash: str | None = None


class AdminAuditLogListOut(BaseModel):
    items: list[AdminAuditLogEntryOut]
    next_cursor: str | None


class AuditChainBreakOut(BaseModel):
    seq: int | None
    audit_id: str | None
    reason: str


class AuditChainVerifyOut(BaseModel):
    """Result of recomputing the audit hash chain. `ok` means "no evidence
    of tampering among sealed rows" (and, if an anchor was supplied, that
    the anchored head is still present and unchanged) -- it is NOT a claim
    that the log is immutable or complete beyond what the chain can show.
    See docs/LIFELINK_AUDIT_CHAIN.md."""

    ok: bool
    checked: int
    head_seq: int | None
    head_hash: str | None
    unsealed: int
    first_break: AuditChainBreakOut | None = None
    # Gap elimination #26: state of the database append-only guard
    # ({trigger: ENABLED|DISABLED|MISSING}). A disabled or missing guard makes
    # `ok` false even when the chain itself still verifies.
    guard: dict[str, str] = {}
    anchored: bool = False
    #: Hardening: "full" re-hashes every sealed entry from seq 1;
    #: "incremental" only entries after the latest signed checkpoint.
    mode: str = "full"
    checkpoints: AuditCheckpointStatusOut | None = None
    # Audit E4 (2026-10-31): what `ok` can actually vouch for.
    #   EXTERNALLY_ANCHORED       a head recorded outside the database matched
    #   CHECKPOINTED              signed checkpoints outside the database matched
    #   INTERNAL_CONSISTENCY_ONLY neither: a full recomputation or a cut-off
    #                             tail by a database owner would NOT be detected
    tamper_evidence: str = "INTERNAL_CONSISTENCY_ONLY"
    warning: str | None = None


class AuditCheckpointStatusOut(BaseModel):
    """HMAC-signed checkpoints kept outside the database (hardening). When
    `enabled` is false nothing below is meaningful."""

    enabled: bool
    ok: bool | None = None
    checked: int = 0
    latest_seq: int | None = None
    reason: str | None = None


AuditChainVerifyOut.model_rebuild()


class AdminHealthComponentOut(BaseModel):
    ok: bool
    detail: str | None = None


class AdminGraphHealthOut(BaseModel):
    """Neo4j graph-engine health. Deliberately its own shape (not
    AdminHealthComponentOut) -- `configured=False` (no Neo4j credentials
    set) is a normal, expected state, not a degraded one, so it must be
    distinguishable from `configured=True, connected=False` (genuinely
    unreachable). Never carries credentials -- see app/core/graph.py.
    """

    configured: bool
    connected: bool
    status: str
    reason: str | None = None


class AdminSmsHealthOut(BaseModel):
    """SMS provider status. `configured=False` (the default in every
    LifeLink deployment -- see LIFELINK_SMS_STATUS.md) is a normal, fully
    supported "feature off" state, never a fault, exactly like the graph
    engine's `configured=False`. This is NOT a live reachability probe --
    MSG91 has no ping endpoint, and this deliberately never sends a real
    SMS just to populate an admin dashboard -- so `status="configured"`
    means "credentials are set", not "we verified delivery works right
    now". `recent_failures` counts FAILED rows among the last 20 SMS send
    attempts (0 whenever SMS has never actually been used, including the
    default disabled state). Never carries provider credentials.
    """

    configured: bool
    status: str
    provider: str
    recent_failures: int
    reason: str | None = None


class AdminRqHealthOut(BaseModel):
    """Phase 29: RQ email-queue depth/backlog visibility. Added directly in
    response to Phase 28's own finding (148,965 stale queued email jobs
    accumulated with no bound) -- this is the "queue monitoring" half of
    that finding's remediation; the other half (bounding job TTLs so new
    jobs can't accumulate forever) is in app/services/email_service.py's
    `_enqueue`. Deliberately informational/non-blocking: a deep queue is an
    operational signal an operator should look at, not by itself proof the
    system is unhealthy (see AdminHealthOut.status below), so this never
    flips overall status on its own -- same treatment as `graph`/`sms`
    above. Never touches job payloads (no email addresses/content), only
    counts and ages.
    """

    queued: int
    failed: int
    started: int
    oldest_queued_job_age_seconds: float | None = None
    workers: int = 0
    status: str = "healthy"
    reason: str | None = None


class AdminDbPoolOut(BaseModel):
    """Phase 30: real-time SQLAlchemy connection-pool utilization, read
    directly from the live `Engine.pool` object (no separate tracking, no
    chance of drifting from reality). NOT a pool-size change and NOT a
    tuning knob -- see app/db/session.py's own extensive comment on why
    pool_size/max_overflow are deliberately left at library defaults.
    This is pure visibility: "how much of the existing pool is in use
    right now", which is exactly what an operator investigating a
    connection-pool-exhaustion-shaped incident (Phase 24/25/27's own
    documented near-total-outage finding) would otherwise have to SSH in
    and query `pg_stat_activity` to see."""

    size: int
    checked_out: int
    overflow: int


class AdminVersionOut(BaseModel):
    """Phase 30: safe, non-secret build/version identification -- admin-only
    (this endpoint is already Depends(_admin)-gated). Never anything beyond
    a version string and a commit SHA, both of which are meant to be
    visible in a deployed artifact's own metadata anyway (e.g. a Docker
    image label), not a secret."""

    app_version: str
    git_commit_sha: str


class AdminHealthOut(BaseModel):
    status: str
    db: AdminHealthComponentOut
    redis: AdminHealthComponentOut
    storage: AdminHealthComponentOut
    graph: AdminGraphHealthOut
    sms: AdminSmsHealthOut
    rq: AdminRqHealthOut
    db_pool: AdminDbPoolOut
    version: AdminVersionOut


# --------------------------------------------------------------------------- #
# Facilities (hospitals / blood banks)
# --------------------------------------------------------------------------- #


class AdminFacilityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str | None
    license_no: str | None
    phone: str | None
    address: str | None
    city: str | None
    latitude: float | None
    longitude: float | None
    verified: bool
    facility_type: FacilityType
    is_active: bool
    total_units: int = 0
    total_reserved_units: int = 0
    created_at: datetime


class AdminFacilityListOut(BaseModel):
    items: list[AdminFacilityOut]
    total: int
    page: int
    page_size: int


class AdminFacilityInventoryLineOut(BaseModel):
    blood_type: BloodType
    units: int
    reserved_units: int
    available_units: int
    # Phase 35: without this, a facility holding both a WHOLE_BLOOD and a
    # RED_BLOOD_CELLS row for the same blood_type would render as two
    # identically-labeled "O_NEG" lines with different numbers -- an
    # honesty regression this schema's own addition of `component` to
    # HospitalInventory would otherwise silently cause for this admin
    # view. Defaults to WHOLE_BLOOD only for response construction
    # safety; the route below always sets it explicitly from the real row.
    component: BloodComponent = BloodComponent.WHOLE_BLOOD


class AdminFacilityDetailOut(BaseModel):
    facility: AdminFacilityOut
    inventory: list[AdminFacilityInventoryLineOut]


# --------------------------------------------------------------------------- #
# Donor governance
#
# Deliberately excludes DonorProfile.medical_conditions and .weight_kg (health
# data) and raw latitude/longitude (precise location) -- per the Phase 6 spec's
# "do not expose unnecessary private medical data". Admin oversight only needs
# identity/verification/eligibility-adjacent fields, not the medical detail
# donors give hospitals.
# --------------------------------------------------------------------------- #


class AdminDonorOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    blood_type: BloodType | None
    city: str | None
    is_available: bool
    availability_state: DonorAvailabilityState
    identity_verified: bool
    last_donation_date: date | None
    created_at: datetime


class AdminDonorListOut(BaseModel):
    items: list[AdminDonorOut]
    total: int
    page: int
    page_size: int


# --------------------------------------------------------------------------- #
# Certificate registry
# --------------------------------------------------------------------------- #


class AdminCertificateOut(BaseModel):
    id: uuid.UUID
    serial: str
    verify_code: str
    donor_user_id: uuid.UUID
    donor_name: str | None
    donor_email: str | None
    donated_on: date | None
    units: int | None
    hospital_name: str | None
    created_at: datetime
    # ---- Phase 31 additions ----
    revoked_at: datetime | None = None
    revocation_reason: str | None = None


class AdminCertificateListOut(BaseModel):
    items: list[AdminCertificateOut]
    total: int
    page: int
    page_size: int


# --------------------------------------------------------------------------- #
# Analytics -- every field below is a real aggregate computed from existing
# tables at request time. Nothing here is a hardcoded chart or a fabricated
# trend; a breakdown with zero rows in every bucket is a legitimate empty
# platform, not padded to look active.
# --------------------------------------------------------------------------- #


class AdminAnalyticsDayCountOut(BaseModel):
    day: date
    count: int


class AdminAnalyticsOverviewOut(BaseModel):
    request_status_breakdown: dict[str, int]
    request_blood_type_breakdown: dict[str, int]
    emergency_status_breakdown: dict[str, int]
    donor_response_breakdown: dict[str, int]
    hospital_response_breakdown: dict[str, int]
    verification_breakdown: dict[str, int]
    requests_per_day: list[AdminAnalyticsDayCountOut]
