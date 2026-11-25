from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import Date, String, and_, cast, func, or_, select, text
from sqlalchemy.orm import Session, aliased

from app.api.deps import get_db, require_roles, verify_origin
from app.core.config import settings
from app.core.graph import GraphUnavailableError
from app.core.redis import redis_client
from app.core.request_lifecycle import InvalidTransitionError, allowed_transitions
from app.core.time import utcnow
from app.models.audit_log import AuditLog
from app.models.blood_request import BloodRequest, DonorResponse
from app.models.blood_unit import BloodUnit
from app.models.certificate import Certificate
from app.models.donation import Donation
from app.models.emergency import Emergency
from app.models.enums import (
    BloodRequestStatus,
    BloodType,
    BloodUnitState,
    EmergencyStatus,
    FacilityType,
    TransportRouteStatus,
    VerificationKind,
    VerificationStatus,
    haversine_km,
)
from app.models.graph_pathway_log import GraphPathwayLog
from app.models.hospital import (
    HospitalInventory,
    HospitalProfile,
    HospitalRequestResponse,
)
from app.models.profile import DonorProfile
from app.models.transport import TransportRoute
from app.models.user import User, UserRole
from app.models.verification import VerificationRequest
from app.schemas.admin import (
    AdminAnalyticsDayCountOut,
    AdminAnalyticsOverviewOut,
    AdminAuditLogEntryOut,
    AdminAuditLogListOut,
    AdminBloodRequestOut,
    AdminBloodRequestTransitionIn,
    AdminCertificateListOut,
    AdminCertificateOut,
    AdminDbPoolOut,
    AdminDonorListOut,
    AdminDonorOut,
    AdminEmergencyOut,
    AdminFacilityDetailOut,
    AdminFacilityInventoryLineOut,
    AdminFacilityListOut,
    AdminFacilityOut,
    AdminGraphHealthOut,
    AdminHealthComponentOut,
    AdminHealthOut,
    AdminRoleChangeIn,
    AdminRqHealthOut,
    AdminSmsHealthOut,
    AdminSuspendIn,
    AdminUserListOut,
    AdminUserOut,
    AdminUserStatsOut,
    AdminVersionOut,
    AuditChainBreakOut,
    AuditChainVerifyOut,
    AuditCheckpointStatusOut,
)
from app.schemas.certificate import CertificateRevokeIn
from app.schemas.graph import (
    DisruptionImpactOut,
    EmergencyReoptimizationOut,
    GraphPathwayLogListOut,
    GraphPathwayLogOut,
    GraphResyncOut,
    TransportRouteActionOut,
    TransportRouteCreateIn,
    TransportRouteDisruptIn,
    TransportRouteOut,
)
from app.services import (
    audit_chain_service,
    audit_checkpoint_service,
    audit_service,
    blood_request_lifecycle,
    emergency_pathway_service,
    graph_service,
    inventory_service,
    notification_service,
    session_service,
)

router = APIRouter(prefix="/admin", tags=["admin"])

_admin = require_roles(UserRole.ADMIN)


def _get_target_user(db: Session, user_id: uuid.UUID) -> User:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return target


@router.get("/users", response_model=AdminUserListOut)
def list_users(
    query: str | None = Query(default=None, max_length=200),
    role: UserRole | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> AdminUserListOut:
    conditions = []
    if query:
        like = f"%{query.strip()}%"
        conditions.append(
            or_(
                User.full_name.ilike(like),
                User.email.ilike(like),
                cast(User.id, String).ilike(like),
            )
        )
    if role is not None:
        conditions.append(User.role == role)
    where = and_(*conditions) if conditions else None

    count_stmt = select(func.count()).select_from(User)
    list_stmt = select(User)
    if where is not None:
        count_stmt = count_stmt.where(where)
        list_stmt = list_stmt.where(where)
    total = int(db.scalar(count_stmt) or 0)
    rows = db.scalars(
        list_stmt.order_by(User.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return AdminUserListOut(
        items=[AdminUserOut.model_validate(u) for u in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/users/stats", response_model=AdminUserStatsOut)
def user_stats(
    admin: User = Depends(_admin), db: Session = Depends(get_db)
) -> AdminUserStatsOut:
    total_users = int(db.scalar(select(func.count()).select_from(User)) or 0)
    by_role = {
        role_value.value: 0 for role_value in UserRole
    }
    for role_value, count in db.execute(
        select(User.role, func.count()).group_by(User.role)
    ).all():
        by_role[role_value.value] = int(count)
    active_count = int(
        db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))) or 0
    )
    unverified_count = int(
        db.scalar(
            select(func.count()).select_from(User).where(User.is_email_verified.is_(False))
        )
        or 0
    )
    return AdminUserStatsOut(
        total_users=total_users,
        by_role=by_role,
        active_count=active_count,
        suspended_count=total_users - active_count,
        unverified_email_count=unverified_count,
    )


@router.patch("/users/{user_id}/role", response_model=AdminUserOut)
def change_user_role(
    user_id: uuid.UUID,
    payload: AdminRoleChangeIn,
    _: None = Depends(verify_origin),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> User:
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot change your own role"
        )
    target = _get_target_user(db, user_id)
    old_role = target.role
    # Role-specific profile rows (donor_profiles/patient_profiles/hospital_profiles)
    # are intentionally left in place, not deleted, when a role changes. They just
    # become inactive/unused for the new role, which is safe and fully reversible
    # if the role is changed back later.
    target.role = payload.role
    audit_service.record(
        db,
        "admin.user_role_changed",
        actor_user_id=admin.id,
        target_type="user",
        target_id=target.id,
        meta={"old_role": old_role.value, "new_role": payload.role.value},
    )
    db.commit()
    db.refresh(target)
    return target


@router.post("/users/{user_id}/suspend", response_model=AdminUserOut)
def suspend_user(
    user_id: uuid.UUID,
    payload: AdminSuspendIn,
    _: None = Depends(verify_origin),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> User:
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot suspend yourself"
        )
    target = _get_target_user(db, user_id)
    target.is_active = False
    target.suspended_reason = payload.reason
    target.suspended_at = utcnow()
    target.suspended_by_user_id = admin.id
    # No separate session-revocation step is needed: get_current_user (app/api/deps.py)
    # already rejects any user whose is_active is False, so this takes effect on the
    # suspended user's very next authenticated request.
    audit_service.record(
        db,
        "admin.user_suspended",
        actor_user_id=admin.id,
        target_type="user",
        target_id=target.id,
        meta={"reason": payload.reason},
    )
    db.commit()
    db.refresh(target)
    return target


@router.post("/users/{user_id}/reactivate", response_model=AdminUserOut)
def reactivate_user(
    user_id: uuid.UUID,
    _: None = Depends(verify_origin),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> User:
    target = _get_target_user(db, user_id)
    target.is_active = True
    target.suspended_reason = None
    target.suspended_at = None
    target.suspended_by_user_id = None
    # A session issued before the suspension was never deleted -- it was
    # just lazily rejected by the is_active check on every request while
    # suspended (see get_current_user). Flipping is_active back to True
    # would otherwise silently let that pre-suspension session resume
    # working with no fresh authentication at all, which defeats the point
    # of having suspended the account in the first place (e.g. a suspected
    # compromise). Revoke it here so reactivation always requires a new
    # login, exactly like a password reset does.
    revoked = session_service.delete_user_sessions(target.id)
    audit_service.record(
        db,
        "admin.user_reactivated",
        actor_user_id=admin.id,
        target_type="user",
        target_id=target.id,
        meta={"revoked_sessions": revoked},
    )
    db.commit()
    db.refresh(target)
    return target


@router.get("/blood-requests", response_model=list[AdminBloodRequestOut])
def list_blood_requests(
    status_filter: BloodRequestStatus | None = Query(default=None, alias="status"),
    query: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> list[AdminBloodRequestOut]:
    stmt = select(BloodRequest)
    if status_filter is not None:
        stmt = stmt.where(BloodRequest.status == status_filter)
    if query:
        # Phase 19: matches the same ilike-search convention already used
        # by /admin/users and /admin/certificates -- searches the patient's
        # and requester's name/email plus the request's own free-text
        # location fields, since an admin moderating requests typically
        # knows one of those, not the request's UUID.
        like = f"%{query.strip()}%"
        PatientUser = User
        RequesterUser = aliased(User)
        stmt = (
            stmt.join(PatientUser, PatientUser.id == BloodRequest.patient_user_id)
            .join(RequesterUser, RequesterUser.id == BloodRequest.requester_user_id)
            .where(
                or_(
                    PatientUser.full_name.ilike(like),
                    PatientUser.email.ilike(like),
                    RequesterUser.full_name.ilike(like),
                    RequesterUser.email.ilike(like),
                    BloodRequest.hospital_name.ilike(like),
                    BloodRequest.city.ilike(like),
                )
            )
        )
    stmt = stmt.order_by(BloodRequest.created_at.desc()).limit(limit)
    rows = list(db.scalars(stmt).all())
    for req in rows:
        blood_request_lifecycle.expire_if_stale(db, req)
    out = []
    for req in rows:
        item = AdminBloodRequestOut.model_validate(req)
        item.allowed_transitions = allowed_transitions(req.status)
        out.append(item)
    return out


@router.post(
    "/blood-requests/{request_id}/transition",
    response_model=AdminBloodRequestOut,
)
def admin_transition_blood_request(
    request_id: uuid.UUID,
    payload: AdminBloodRequestTransitionIn,
    _origin: None = Depends(verify_origin),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> AdminBloodRequestOut:
    """Administrative intervention on a request's lifecycle.

    Deliberately restricted to CANCELLED / EXPIRED: an admin can shut down a
    request (moderation, a duplicate, a bad-faith submission, an
    organizational policy call) or mark one expired early, but forcing
    MATCHING or FULFILLED on someone else's behalf would fabricate a donor
    match or a fulfilment that didn't actually happen, so those targets are
    rejected here with a 400 before the state machine is even consulted --
    a 409 is reserved for "not reachable from the current state at all."
    """
    if payload.target_status not in (BloodRequestStatus.CANCELLED, BloodRequestStatus.EXPIRED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin intervention may only force CANCELLED or EXPIRED",
        )
    req = blood_request_lifecycle.get_for_update(db, request_id)
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    blood_request_lifecycle.expire_if_stale(db, req)
    try:
        blood_request_lifecycle.transition(
            db, req, payload.target_status,
            actor_user_id=admin.id, reason=payload.reason,
        )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    db.refresh(req)
    item = AdminBloodRequestOut.model_validate(req)
    item.allowed_transitions = allowed_transitions(req.status)
    return item


@router.get("/emergencies", response_model=list[AdminEmergencyOut])
def list_emergencies(
    status_filter: EmergencyStatus | None = Query(default=None, alias="status"),
    query: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> list[Emergency]:
    stmt = select(Emergency)
    if status_filter is not None:
        stmt = stmt.where(Emergency.status == status_filter)
    if query:
        like = f"%{query.strip()}%"
        PatientUser = User
        CreatorUser = aliased(User)
        stmt = (
            stmt.outerjoin(PatientUser, PatientUser.id == Emergency.patient_user_id)
            .join(CreatorUser, CreatorUser.id == Emergency.created_by_user_id)
            .where(
                or_(
                    PatientUser.full_name.ilike(like),
                    PatientUser.email.ilike(like),
                    CreatorUser.full_name.ilike(like),
                    CreatorUser.email.ilike(like),
                    Emergency.location_text.ilike(like),
                )
            )
        )
    stmt = stmt.order_by(Emergency.created_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())


# Severity is derived from the action string at read time -- it is never a
# column on AuditLog, so it can never drift out of sync with a separately
# stored value. Keep `_classify_severity` (Python, used to hydrate each
# response row) and `_SEVERITY_SQL_MATCH` (SQL, used to filter server-side)
# expressing exactly the same three buckets.
_CRITICAL_SUFFIXES = ("_failed",)
_WARNING_KEYWORDS = ("suspended", "rejected", "disrupted", "role_changed")


def _classify_severity(action: str) -> str:
    if action.endswith(_CRITICAL_SUFFIXES):
        return "CRITICAL"
    if any(k in action for k in _WARNING_KEYWORDS):
        return "WARNING"
    return "INFO"


def _severity_sql_match(column: Any, severity: str) -> Any:
    # `_` is a LIKE wildcard, so it must be escaped -- otherwise "%_failed"
    # would also match e.g. "xxfailed", not just a real "..._failed" action.
    is_critical = column.like("%\\_failed", escape="\\")
    is_warning = or_(*[column.contains(k) for k in _WARNING_KEYWORDS])
    if severity == "CRITICAL":
        return is_critical
    if severity == "WARNING":
        return and_(~is_critical, is_warning)
    # INFO: neither critical nor warning.
    return and_(~is_critical, ~is_warning)


@router.get("/audit-log", response_model=AdminAuditLogListOut)
def list_audit_log(
    action: str | None = Query(default=None, max_length=100),
    actor_user_id: uuid.UUID | None = None,
    actor_role: UserRole | None = None,
    target_type: str | None = Query(default=None, max_length=50),
    severity: str | None = Query(default=None, pattern="^(INFO|WARNING|CRITICAL)$"),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> AdminAuditLogListOut:
    # Keyset cursor "<created_at ISO>|<id>": newest first (created_at desc,
    # id desc as the tie-breaker). Keyset rather than offset because reading
    # the log appends an `admin.audit_log_viewed` row at the top -- with an
    # offset cursor every page would shift by one and repeat an entry.
    after: tuple[datetime, uuid.UUID] | None = None
    if cursor:
        try:
            ts_raw, id_raw = cursor.split("|", 1)
            after = (datetime.fromisoformat(ts_raw), uuid.UUID(id_raw))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid cursor"
            ) from None

    conditions = []
    if action:
        conditions.append(AuditLog.action == action)
    if actor_user_id is not None:
        conditions.append(AuditLog.actor_user_id == actor_user_id)
    if target_type:
        conditions.append(AuditLog.target_type == target_type)
    if since is not None:
        conditions.append(AuditLog.created_at >= since)
    if until is not None:
        conditions.append(AuditLog.created_at <= until)
    if severity is not None:
        conditions.append(_severity_sql_match(AuditLog.action, severity))

    stmt = select(AuditLog, User).outerjoin(User, User.id == AuditLog.actor_user_id)
    if actor_role is not None:
        # An actor_role filter necessarily excludes system-generated rows
        # (actor_user_id is NULL) -- there's no role to match against those.
        conditions.append(User.role == actor_role)
    if after is not None:
        conditions.append(or_(AuditLog.created_at < after[0],
                              and_(AuditLog.created_at == after[0], AuditLog.id < after[1])))
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    rows = db.execute(stmt.limit(limit + 1)).all()

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (f"{rows[-1][0].created_at.isoformat()}|{rows[-1][0].id}"
                   if has_more and rows else None)
    items = []
    for entry, actor in rows:
        out = AdminAuditLogEntryOut.model_validate(entry)
        out.severity = _classify_severity(entry.action)
        if actor is not None:
            out.actor_email = actor.email
            out.actor_role = actor.role
        items.append(out)
    # Gap elimination #26: who read the audit log is itself audited.
    audit_service.record(db, "admin.audit_log_viewed", actor_user_id=admin.id,
                         target_type="audit_log", meta={"returned": len(items)})
    db.commit()
    return AdminAuditLogListOut(items=items, next_cursor=next_cursor)


@router.post("/audit-log/verify", response_model=AuditChainVerifyOut)
def verify_audit_chain(
    anchor_seq: int | None = Query(default=None, ge=1),
    anchor_hash: str | None = Query(default=None, pattern="^[0-9a-f]{64}$"),
    mode: str = Query(default="full", pattern="^(full|incremental)$"),
    _: None = Depends(verify_origin),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> AuditChainVerifyOut:
    """Phase E: recompute the tamper-evident audit chain. Seals any
    committed-but-unsealed rows first (so a straggler is chained, not
    reported as a break), then verifies. Supplying a head previously
    recorded OUTSIDE this database (`anchor_seq` + `anchor_hash`, e.g. from
    GET /admin/audit-log/head) additionally detects tail truncation or a
    fully recomputed chain. POST, not GET: it writes (sealing + its own
    audit record of the result).

    Hardening: when signed checkpoints are configured they are always
    verified (MACs, the checkpoint log's own chain, and that every
    checkpointed entry is still present and unchanged). `mode=incremental`
    re-hashes only entries after the latest verified checkpoint -- fast,
    but an un-recomputed edit to an older entry is only found by `full`."""
    if (anchor_seq is None) != (anchor_hash is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="anchor_seq and anchor_hash must be supplied together",
        )
    if mode == "incremental" and anchor_seq is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="an external anchor is checked by full verification only",
        )
    if mode == "incremental" and not settings.audit_checkpoints_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="incremental verification needs signed checkpoints "
                   "(AUDIT_CHECKPOINT_KEY / AUDIT_CHECKPOINT_PATH)",
        )
    # End this session's transaction first so it returns its pooled
    # connection before the sealer takes one (never hold two at once --
    # see audit_service._mark_ready_after_commit).
    db.commit()
    audit_chain_service.seal_pending_now(db.get_bind())

    cp_status: AuditCheckpointStatusOut
    if settings.audit_checkpoints_enabled:
        cp = audit_checkpoint_service.verify_against_db(db)
        cp_status = AuditCheckpointStatusOut(
            enabled=True, ok=cp.ok, checked=cp.checked, latest_seq=cp.latest_seq,
            reason=cp.reason,
        )
    else:
        cp, cp_status = None, AuditCheckpointStatusOut(enabled=False)

    after_seq, after_hash = 0, audit_chain_service.ac.GENESIS_HASH
    if mode == "incremental" and cp is not None and cp.ok and cp.latest_seq is not None:
        after_seq, after_hash = cp.latest_seq, cp.latest_hash
    result = audit_chain_service.verify_chain(
        db, expected_head_seq=anchor_seq, expected_head_hash=anchor_hash,
        after_seq=after_seq, after_hash=after_hash,
    )
    brk = result.first_break
    from app.core import audit_guard

    guard = audit_guard.guard_status(db.connection())
    if brk is None and any(v != "ENABLED" for v in guard.values()):
        result.ok = False
        brk = audit_chain_service.ac.ChainBreak(
            seq=None, audit_id=None,
            reason="append-only guard is not active: "
                   + ", ".join(f"{k}={v}" for k, v in sorted(guard.items())),
        )
        result.first_break = brk
    if brk is None and cp is not None and not cp.ok:
        result.ok = False
        brk = audit_chain_service.ac.ChainBreak(
            seq=None, audit_id=None, reason=f"signed checkpoint check failed: {cp.reason}"
        )
        result.first_break = brk
    evidence = ("EXTERNALLY_ANCHORED" if anchor_seq is not None else
                "CHECKPOINTED" if (cp is not None and cp.ok and cp_status.checked) else
                "INTERNAL_CONSISTENCY_ONLY")
    audit_service.record(
        db,
        "admin.audit_chain_verified" if result.ok else "admin.audit_chain_verify_failed",
        actor_user_id=admin.id,
        target_type="audit_chain",
        meta={
            "checked": result.checked, "head_seq": result.head_seq,
            "unsealed": result.unsealed, "anchored": anchor_seq is not None,
            "mode": mode, "checkpoints_checked": cp_status.checked,
            "break_seq": brk.seq if brk else None,
            "break_reason": brk.reason if brk else None,
        },
    )
    db.commit()
    return AuditChainVerifyOut(
        ok=result.ok, checked=result.checked, head_seq=result.head_seq,
        head_hash=result.head_hash, unsealed=result.unsealed,
        first_break=AuditChainBreakOut(**vars(brk)) if brk else None,
        anchored=anchor_seq is not None, mode=mode, checkpoints=cp_status, guard=guard,
        tamper_evidence=evidence,
        warning=(None if evidence != "INTERNAL_CONSISTENCY_ONLY" else
                 "Only internal consistency was checked. Without signed checkpoints "
                 "(AUDIT_CHECKPOINT_KEY/PATH) or an externally recorded head, a database "
                 "owner who recomputes the whole chain or truncates its newest entries is "
                 "not detected."),
    )


@router.post("/audit-log/checkpoint", response_model=AuditCheckpointStatusOut)
def write_audit_checkpoint(
    _: None = Depends(verify_origin),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> AuditCheckpointStatusOut:
    """Hardening: append an HMAC-signed checkpoint of the current sealed head
    to the external checkpoint log. Refuses to extend a log that does not
    verify."""
    if not settings.audit_checkpoints_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="signed audit checkpoints are not configured",
        )
    db.commit()
    audit_chain_service.seal_pending_now(db.get_bind())
    try:
        cp = audit_checkpoint_service.write_checkpoint(db)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    audit_service.record(
        db, "admin.audit_checkpoint_written", actor_user_id=admin.id,
        target_type="audit_chain", meta={"seq": cp.seq if cp else None},
    )
    db.commit()
    state = audit_checkpoint_service.verify_log()
    return AuditCheckpointStatusOut(
        enabled=True, ok=state.ok, checked=state.checked, latest_seq=state.latest_seq,
        reason=state.reason,
    )


@router.get("/audit-log/head", response_model=AuditChainVerifyOut)
def audit_chain_head(
    admin: User = Depends(_admin), db: Session = Depends(get_db)
) -> AuditChainVerifyOut:
    """Phase E: the current sealed head (seq + hash), WITHOUT a full
    verification -- for recording externally as an anchor. `ok` here only
    means a head exists; use POST /admin/audit-log/verify to verify."""
    head = db.execute(
        select(AuditLog.chain_seq, AuditLog.entry_hash)
        .where(AuditLog.chain_seq.is_not(None))
        .order_by(AuditLog.chain_seq.desc()).limit(1)
    ).first()
    unsealed = db.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.chain_seq.is_(None))
    ) or 0
    return AuditChainVerifyOut(
        ok=head is not None, checked=0,
        head_seq=head.chain_seq if head else None,
        head_hash=head.entry_hash if head else None,
        unsealed=int(unsealed),
    )


@router.get("/system/health", response_model=AdminHealthOut)
def system_health(
    admin: User = Depends(_admin), db: Session = Depends(get_db)
) -> AdminHealthOut:
    from app.core import storage

    try:
        db.execute(text("SELECT 1"))
        db_component = AdminHealthComponentOut(ok=True)
    except Exception as exc:  # noqa: BLE001 - report, don't crash the probe
        db_component = AdminHealthComponentOut(ok=False, detail=str(exc))

    try:
        redis_client.ping()
        redis_component = AdminHealthComponentOut(ok=True)
    except Exception as exc:  # noqa: BLE001
        redis_component = AdminHealthComponentOut(ok=False, detail=str(exc))

    backend = storage.storage_backend()
    try:
        storage.ensure_bucket()
        storage_component = AdminHealthComponentOut(ok=True, detail=backend)
    except Exception as exc:  # noqa: BLE001
        storage_component = AdminHealthComponentOut(ok=False, detail=f"{backend}: {exc}")

    from app.core import graph as graph_core

    graph_status = graph_core.graph_health()
    graph_component = AdminGraphHealthOut(**graph_status)
    # graph_component.configured is False is a normal, supported "feature
    # off" state -- it must NOT flip overall status to degraded. Only a
    # real problem (db/redis/storage down, or graph configured-but-
    # unreachable) does.
    graph_problem = graph_component.configured and not graph_component.connected

    from app.services import sms_service

    sms_status = sms_service.sms_health()
    sms_component = AdminSmsHealthOut(
        **sms_status, recent_failures=sms_service.recent_failure_count(db)
    )
    # Same treatment as the graph engine: SMS being unconfigured (the
    # default in every LifeLink deployment -- see LIFELINK_SMS_STATUS.md)
    # is normal and must never flip overall status to degraded.

    from app.workers.queue import email_queue_health

    # Phase 29: RQ email-queue backlog visibility -- see
    # AdminRqHealthOut's own docstring. Deliberately read-only and never
    # flips `overall_ok` (a deep backlog is a signal for an operator to
    # look at with scripts/rq_queue_maintenance.py, not itself proof the
    # system is broken -- the queue TTL fix in email_service._enqueue
    # already bounds how large it can silently grow). A human-readable
    # `reason` is still set past a conservative threshold so it's visible
    # at a glance on this endpoint, matching the graph/sms components'
    # pattern of an informational `reason` string.
    try:
        rq_status = email_queue_health()
        rq_reason = None
        if rq_status["failed"]:
            rq_reason = f"{rq_status['failed']} failed job(s) in FailedJobRegistry"
        elif rq_status["queued"] > settings.rq_queue_depth_warning_threshold:
            rq_reason = f"{rq_status['queued']} jobs queued -- backlog is unusually deep"
        elif rq_status["status"] == "critical":
            rq_reason = "queue has jobs but no workers are currently registered"
        rq_component = AdminRqHealthOut(**rq_status, reason=rq_reason)
    except Exception as exc:  # noqa: BLE001 - report, don't crash the probe
        rq_component = AdminRqHealthOut(
            queued=0,
            failed=0,
            started=0,
            oldest_queued_job_age_seconds=None,
            workers=0,
            status="critical",
            reason=str(exc),
        )

    version_component = AdminVersionOut(
        app_version=settings.app_version, git_commit_sha=settings.git_commit_sha
    )

    from app.db.session import engine as _engine

    # Phase 30: `Engine.pool`'s declared type is the abstract `Pool` base
    # class, but the real pool this app constructs (app/db/session.py's
    # `create_engine(...)`, no `poolclass=` override) is always a
    # `QueuePool`, which does implement size()/checkedout()/overflow() --
    # getattr-with-a-safe-default keeps this endpoint from ever raising if
    # a future change swaps in a pool implementation that doesn't (e.g.
    # NullPool), rather than asserting a type mypy can't verify here.
    pool = _engine.pool
    db_pool_component = AdminDbPoolOut(
        size=getattr(pool, "size", lambda: 0)(),
        checked_out=getattr(pool, "checkedout", lambda: 0)(),
        overflow=getattr(pool, "overflow", lambda: 0)(),
    )

    overall_ok = (
        db_component.ok and redis_component.ok and storage_component.ok and not graph_problem
    )
    return AdminHealthOut(
        status="ok" if overall_ok else "degraded",
        db=db_component,
        redis=redis_component,
        storage=storage_component,
        graph=graph_component,
        sms=sms_component,
        rq=rq_component,
        db_pool=db_pool_component,
        version=version_component,
    )


@router.get("/system/metrics")
def system_metrics(admin: User = Depends(_admin)) -> dict[str, object]:
    """Phase 30 -- admin-only, read-only snapshot of this process's
    in-memory operational metrics (app/core/metrics.py). Deliberately
    JSON, not a Prometheus text-format endpoint -- this app has no
    Prometheus client dependency and adding one purely to change a
    response's Content-Type, with no scraper actually deployed in this
    sandbox, would be exactly the "monitoring platform for appearance"
    the Phase 30 master prompt warns against. A real deployment that
    wants Prometheus-format scraping can translate this JSON (the
    counters/labels are already bounded and stable) -- see
    docs/OPERATIONS_RUNBOOK.md and the Phase 30 report's own
    classification of this as IMPLEMENTED LOCALLY, NOT-LIVE-VERIFIED
    against any real metrics backend. Protected the same way every other
    operational endpoint on this router is: Depends(_admin), so this is
    never reachable without a real authenticated admin session.
    """
    from app.core import metrics

    return metrics.snapshot()


# --------------------------------------------------------------------------- #
# Graph engine: transport routes, resync, pathway audit log
#
# This is the relational + graph administration surface for the resource-
# pathway engine (app/services/graph_service.py). It is NOT a disconnected
# demo module: transport routes created/disrupted/recovered here are the
# same TransportRoute rows that GET /hospitals/me/resource-pathways and
# GET /emergencies/{id}/resource-pathways traverse for real.
# --------------------------------------------------------------------------- #
def _get_facility_or_404(db: Session, facility_id: uuid.UUID) -> HospitalProfile:
    facility = db.get(HospitalProfile, facility_id)
    if facility is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Facility not found")
    return facility


def _get_route_or_404(db: Session, route_id: uuid.UUID) -> TransportRoute:
    """Locked (`SELECT ... FOR UPDATE`), not a bare `db.get()`.

    Phase 17: this helper's only two callers -- `disrupt_transport_route`
    and `recover_transport_route` -- are both check-then-act ("is it
    already DISRUPTED/OPERATIONAL?" then flip it) mutations. Before this
    fix, two near-simultaneous admin calls for the *same* route (two
    disrupts, two recovers, or one of each racing) could both read the
    pre-mutation status, both pass their "not already in that state" check,
    and both proceed -- not just a last-write-wins on `disrupted_reason`,
    but a duplicate real-world side effect: `handle_route_disrupted` (and
    its re-optimization/audit/notification chain) would run twice for what
    should have been a single disruption event. Exactly the same class of
    check-then-act race Phase 10/12/13/14/15/16 already found and fixed for
    BloodRequest/Emergency/FamilyLink/VerificationRequest/Certificate/
    EmergencyPathway -- this closes the last remaining instance of it.
    Locking here serializes concurrent callers: the second blocks until the
    first commits, then correctly sees the now-current status and gets a
    real 409 instead of a duplicate side effect.
    """
    route = db.get(TransportRoute, route_id, with_for_update=True)
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route not found")
    return route


def _pick_disruption_probe(
    db: Session, route: TransportRoute
) -> tuple[BloodType, uuid.UUID] | None:
    """Pick a real (blood_type, origin_facility_id) scenario to measure this
    route's impact with -- grounded in actual HospitalInventory rows, never
    fabricated. Tries "can the origin still reach stock held at the
    destination", then the reverse; returns None (never a fake scenario)
    if neither endpoint has any recorded stock to probe with.
    """
    for probe_origin_id, probe_dest_id in (
        (route.origin_facility_id, route.destination_facility_id),
        (route.destination_facility_id, route.origin_facility_id),
    ):
        for bt in BloodType:
            has_stock = db.scalar(
                select(HospitalInventory.id).where(
                    HospitalInventory.hospital_id == probe_dest_id,
                    HospitalInventory.blood_type == bt,
                    HospitalInventory.units > 0,
                )
            )
            if has_stock is not None:
                return bt, probe_origin_id
    return None


@router.get("/transport-routes", response_model=list[TransportRouteOut])
def list_transport_routes(
    facility_id: uuid.UUID | None = None,
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> list[TransportRoute]:
    stmt = select(TransportRoute)
    if facility_id is not None:
        stmt = stmt.where(
            or_(
                TransportRoute.origin_facility_id == facility_id,
                TransportRoute.destination_facility_id == facility_id,
            )
        )
    stmt = stmt.order_by(TransportRoute.created_at.desc())
    return list(db.scalars(stmt).all())


@router.post(
    "/transport-routes", response_model=TransportRouteOut, status_code=status.HTTP_201_CREATED
)
def create_transport_route(
    payload: TransportRouteCreateIn,
    _: None = Depends(verify_origin),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> TransportRoute:
    if payload.origin_facility_id == payload.destination_facility_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A route must connect two distinct facilities",
        )
    origin = _get_facility_or_404(db, payload.origin_facility_id)
    dest = _get_facility_or_404(db, payload.destination_facility_id)

    # A transport link is physically undirected; store it under one
    # canonical ordering of the two facility ids so the same real-world
    # route can't be double-created as both (A, B) and (B, A).
    origin_id, dest_id = sorted((origin.id, dest.id), key=str)

    existing = db.scalar(
        select(TransportRoute).where(
            TransportRoute.origin_facility_id == origin_id,
            TransportRoute.destination_facility_id == dest_id,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A route between these two facilities already exists",
        )

    distance_km = payload.distance_km
    if distance_km is None:
        o_lat, o_lon, d_lat, d_lon = (
            origin.latitude, origin.longitude, dest.latitude, dest.longitude,
        )
        if o_lat is None or o_lon is None or d_lat is None or d_lon is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "distance_km was not supplied and one or both facilities have no "
                    "recorded coordinates to compute it from"
                ),
            )
        distance_km = haversine_km(o_lat, o_lon, d_lat, d_lon)

    transit_minutes = (
        payload.transit_minutes or graph_service.estimate_transit_minutes(distance_km)
    )

    route = TransportRoute(
        origin_facility_id=origin_id,
        destination_facility_id=dest_id,
        distance_km=distance_km,
        transit_minutes=transit_minutes,
    )
    db.add(route)
    db.flush()
    audit_service.record(
        db, "admin.transport_route_created", actor_user_id=admin.id,
        target_type="transport_route", target_id=route.id,
        meta={"distance_km": distance_km, "transit_minutes": transit_minutes},
    )
    db.commit()
    db.refresh(route)
    graph_service.sync_route(route)  # best-effort; never blocks this write
    return route


@router.post("/transport-routes/{route_id}/disrupt", response_model=TransportRouteActionOut)
def disrupt_transport_route(
    route_id: uuid.UUID,
    payload: TransportRouteDisruptIn,
    _: None = Depends(verify_origin),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> TransportRouteActionOut:
    route = _get_route_or_404(db, route_id)
    if route.status == TransportRouteStatus.DISRUPTED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Route already disrupted")

    probe = _pick_disruption_probe(db, route)
    before: graph_service.PathwayResult | None = None
    if probe:
        before = graph_service.find_resource_pathways(
            db, blood_type=probe[0], origin_facility_id=probe[1]
        )

    route.status = TransportRouteStatus.DISRUPTED
    route.disrupted_reason = payload.reason
    route.disrupted_at = utcnow()
    audit_service.record(
        db, "admin.transport_route_disrupted", actor_user_id=admin.id,
        target_type="transport_route", target_id=route.id, meta={"reason": payload.reason},
    )
    db.flush()

    # Gap elimination: route-exact re-planning, in the SAME transaction as
    # the status change (no window where the route is disrupted but plans
    # still depend on it). Only live pathways whose stored steps include
    # THIS route id are affected -- first, middle or last step alike; a
    # route that merely touches the same facility changes nothing. A
    # still-valid backup is promoted first; only if none survives
    # re-validation is a fresh plan made.
    outcomes = emergency_pathway_service.handle_route_disrupted(
        db, route, admin_user_id=admin.id
    )
    db.commit()
    db.refresh(route)
    synced = graph_service.sync_route(route)

    impact_out = None
    sample_bt = None
    if probe and before is not None:
        sample_bt = probe[0]
        after = graph_service.find_resource_pathways(
            db, blood_type=probe[0], origin_facility_id=probe[1]
        )
        impact_out = DisruptionImpactOut(**graph_service.diff_pathway_rankings(before, after))

    return TransportRouteActionOut(
        route=TransportRouteOut.model_validate(route),
        sample_blood_type=sample_bt,
        impact=impact_out,
        graph_synced=synced,
        affected_emergencies=[
            EmergencyReoptimizationOut(
                emergency_id=o.emergency_id, previous_version=o.previous_version,
                new_version=o.new_version, still_reachable=o.still_reachable,
                best_distance_km=o.best_distance_km, kind=o.kind,
                invalidated_backups=o.invalidated_backups,
                rejected_backups=o.rejected_backups,
            )
            for o in outcomes
        ],
    )


@router.post("/transport-routes/{route_id}/recover", response_model=TransportRouteActionOut)
def recover_transport_route(
    route_id: uuid.UUID,
    _: None = Depends(verify_origin),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> TransportRouteActionOut:
    route = _get_route_or_404(db, route_id)
    if route.status == TransportRouteStatus.OPERATIONAL:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Route is not disrupted")

    probe = _pick_disruption_probe(db, route)
    before: graph_service.PathwayResult | None = None
    if probe:
        before = graph_service.find_resource_pathways(
            db, blood_type=probe[0], origin_facility_id=probe[1]
        )

    route.status = TransportRouteStatus.OPERATIONAL
    route.disrupted_reason = None
    route.disrupted_at = None
    audit_service.record(
        db, "admin.transport_route_recovered", actor_user_id=admin.id,
        target_type="transport_route", target_id=route.id,
    )
    db.commit()
    db.refresh(route)
    synced = graph_service.sync_route(route)

    impact_out = None
    sample_bt = None
    if probe and before is not None:
        sample_bt = probe[0]
        after = graph_service.find_resource_pathways(
            db, blood_type=probe[0], origin_facility_id=probe[1]
        )
        impact_out = DisruptionImpactOut(**graph_service.diff_pathway_rankings(before, after))

    return TransportRouteActionOut(
        route=TransportRouteOut.model_validate(route),
        sample_blood_type=sample_bt,
        impact=impact_out,
        graph_synced=synced,
    )


@router.post("/graph/resync", response_model=GraphResyncOut)
def resync_graph(
    _: None = Depends(verify_origin),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> GraphResyncOut:
    """Authoritative recovery path: wipes and rebuilds the entire graph from
    the current PostgreSQL state. Raises (503) rather than claiming success
    when Neo4j is unreachable -- this is the one operation that must be
    honest about failure, since it exists specifically to fix drift.
    """
    try:
        counts = graph_service.resync_all(db)
    except GraphUnavailableError as exc:
        audit_service.record(
            db, "admin.graph_resync_failed", actor_user_id=admin.id,
            meta={"reason": type(exc).__name__},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Neo4j is unavailable -- the graph was not resynced",
        ) from exc
    audit_service.record(db, "admin.graph_resync_succeeded", actor_user_id=admin.id, meta=counts)
    db.commit()
    return GraphResyncOut(**counts)


@router.get("/graph/pathway-log", response_model=GraphPathwayLogListOut)
def list_pathway_log(
    blood_type: BloodType | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> GraphPathwayLogListOut:
    offset = 0
    if cursor:
        try:
            offset = max(0, int(cursor))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid cursor"
            ) from None

    stmt = select(GraphPathwayLog)
    if blood_type is not None:
        stmt = stmt.where(GraphPathwayLog.blood_type == blood_type)
    stmt = stmt.order_by(GraphPathwayLog.created_at.desc(), GraphPathwayLog.id.desc())
    rows = db.scalars(stmt.offset(offset).limit(limit + 1)).all()

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = str(offset + limit) if has_more else None
    return GraphPathwayLogListOut(
        items=[GraphPathwayLogOut.model_validate(r) for r in rows],
        next_cursor=next_cursor,
    )


# --------------------------------------------------------------------------- #
# Facilities (hospitals / blood banks)
# --------------------------------------------------------------------------- #

# Per-facility inventory totals, keyed by hospital_id -- one grouped query
# shared by both the list and detail views below.
_INVENTORY_TOTALS = (
    select(
        HospitalInventory.hospital_id.label("hospital_id"),
        func.coalesce(func.sum(HospitalInventory.units), 0).label("total_units"),
        func.coalesce(func.sum(HospitalInventory.reserved_units), 0).label("total_reserved"),
    )
    .group_by(HospitalInventory.hospital_id)
    .subquery()
)

# Phase G: units that have passed their shelf life but have not yet been
# lazily swept (the sweep runs when a facility's inventory is next
# touched). The list view subtracts these so an admin never sees expired
# blood counted as on hand -- the same numbers the sweep would produce,
# without performing writes for every facility on a page. The detail view
# performs the real sweep (one facility, cheap) -- see get_facility.
_EXPIRED_UNSWEPT = (
    select(
        BloodUnit.hospital_id.label("hospital_id"),
        func.count().filter(
            BloodUnit.state.in_([BloodUnitState.AVAILABLE, BloodUnitState.RESERVED])
        ).label("expired_on_hand"),
        func.count().filter(BloodUnit.state == BloodUnitState.RESERVED).label("expired_reserved"),
    )
    .where(
        BloodUnit.state.in_([BloodUnitState.AVAILABLE, BloodUnitState.RESERVED]),
        # effective expiry: shelf life OR the post-thaw window
        or_(
            and_(BloodUnit.expires_at.is_not(None), BloodUnit.expires_at <= func.now()),
            and_(BloodUnit.thaw_expires_at.is_not(None),
                 BloodUnit.thaw_expires_at <= func.now()),
        ),
    )
    .group_by(BloodUnit.hospital_id)
    .subquery()
)


def _facility_to_out(
    facility: HospitalProfile, user: User, total_units: int = 0, total_reserved: int = 0
) -> AdminFacilityOut:
    return AdminFacilityOut(
        id=facility.id,
        user_id=facility.user_id,
        name=facility.name,
        license_no=facility.license_no,
        phone=facility.phone,
        address=facility.address,
        city=facility.city,
        latitude=facility.latitude,
        longitude=facility.longitude,
        verified=facility.verified,
        facility_type=facility.facility_type,
        is_active=user.is_active,
        total_units=int(total_units),
        total_reserved_units=int(total_reserved),
        created_at=facility.created_at,
    )


@router.get("/facilities", response_model=AdminFacilityListOut)
def list_facilities(
    query: str | None = Query(default=None, max_length=200),
    facility_type: FacilityType | None = None,
    verified: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> AdminFacilityListOut:
    conditions = []
    if query:
        like = f"%{query.strip()}%"
        conditions.append(
            or_(HospitalProfile.name.ilike(like), HospitalProfile.city.ilike(like))
        )
    if facility_type is not None:
        conditions.append(HospitalProfile.facility_type == facility_type)
    if verified is not None:
        conditions.append(HospitalProfile.verified.is_(verified))
    where = and_(*conditions) if conditions else None

    count_stmt = select(func.count()).select_from(HospitalProfile)
    list_stmt = (
        select(
            HospitalProfile,
            User,
            (
                func.coalesce(_INVENTORY_TOTALS.c.total_units, 0)
                - func.coalesce(_EXPIRED_UNSWEPT.c.expired_on_hand, 0)
            ).label("total_units"),
            (
                func.coalesce(_INVENTORY_TOTALS.c.total_reserved, 0)
                - func.coalesce(_EXPIRED_UNSWEPT.c.expired_reserved, 0)
            ).label("total_reserved"),
        )
        .join(User, User.id == HospitalProfile.user_id)
        .outerjoin(_INVENTORY_TOTALS, _INVENTORY_TOTALS.c.hospital_id == HospitalProfile.id)
        .outerjoin(_EXPIRED_UNSWEPT, _EXPIRED_UNSWEPT.c.hospital_id == HospitalProfile.id)
    )
    if where is not None:
        count_stmt = count_stmt.where(where)
        list_stmt = list_stmt.where(where)
    total = int(db.scalar(count_stmt) or 0)
    rows = db.execute(
        list_stmt.order_by(HospitalProfile.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items = [
        _facility_to_out(facility, user, total_units or 0, total_reserved or 0)
        for facility, user, total_units, total_reserved in rows
    ]
    return AdminFacilityListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/facilities/{facility_id}", response_model=AdminFacilityDetailOut)
def get_facility(
    facility_id: uuid.UUID, admin: User = Depends(_admin), db: Session = Depends(get_db)
) -> AdminFacilityDetailOut:
    facility = _get_facility_or_404(db, facility_id)
    user = db.get(User, facility.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Facility user not found")

    # Phase G: the same lazy expiry sweep + self-healing reconciliation the
    # hospital's own GET /hospitals/me/inventory performs (it durably
    # commits any real change), so the admin view is never more stale than
    # the facility's own -- previously this read the aggregates raw and
    # could count expired units as available.
    inventory_service.sweep_all_expired(db, facility.id)
    inventory_rows = list(
        db.scalars(
            select(HospitalInventory)
            .where(HospitalInventory.hospital_id == facility.id)
            # Phase 35: component joins the sort key -- a facility can now
            # have up to four rows per blood_type (one per component), and
            # grouping them together (rather than ordering by blood_type
            # alone, which would interleave components arbitrarily) is what
            # makes AdminFacilityInventoryLineOut.component actually legible
            # to an admin scanning the list.
            .order_by(HospitalInventory.blood_type, HospitalInventory.component)
        ).all()
    )
    total_units = sum(i.units for i in inventory_rows)
    total_reserved = sum(i.reserved_units for i in inventory_rows)

    out_facility = _facility_to_out(facility, user, total_units, total_reserved)
    return AdminFacilityDetailOut(
        facility=out_facility,
        inventory=[
            AdminFacilityInventoryLineOut(
                blood_type=i.blood_type,
                component=i.component,
                units=i.units,
                reserved_units=i.reserved_units,
                available_units=max(0, i.units - i.reserved_units),
            )
            for i in inventory_rows
        ],
    )


class FacilityVerificationRevokeIn(BaseModel):
    reason: str = Field(min_length=10, max_length=500)


class FacilityVerificationRevokeOut(BaseModel):
    facility: AdminFacilityOut
    affected_emergencies: list[EmergencyReoptimizationOut] = []
    withdrawn_emergency_responses: int = 0


@router.post("/facilities/{facility_id}/revoke-verification",
             response_model=FacilityVerificationRevokeOut)
def revoke_facility_verification(
    facility_id: uuid.UUID,
    payload: FacilityVerificationRevokeIn,
    _: None = Depends(verify_origin),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> FacilityVerificationRevokeOut:
    """Gap elimination #7: revoke a facility's verification.

    In one transaction: the facility is marked unverified; every live
    emergency pathway that sources from it fails over (backup promotion or
    a fresh plan, unless that emergency carries an UNVERIFIED_FACILITY
    override); its accepted responses to open emergencies are withdrawn
    and their reservations released; the facility is notified; everything
    is audited. Historical records are untouched."""
    from app.models.emergency import Emergency, EmergencyEvent, EmergencyResponse
    from app.models.enums import EMERGENCY_OPEN_STATUSES, DonorResponseStatus

    facility = db.get(HospitalProfile, facility_id, with_for_update=True)
    if facility is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Facility not found")
    if not facility.verified:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Facility is not verified")
    facility.verified = False
    db.flush()
    outcomes = emergency_pathway_service.handle_facility_unverified(
        db, facility.id, admin_user_id=admin.id)

    withdrawn = 0
    responses = db.scalars(
        select(EmergencyResponse).join(Emergency, Emergency.id == EmergencyResponse.emergency_id)
        .where(EmergencyResponse.responder_user_id == facility.user_id,
               EmergencyResponse.status == DonorResponseStatus.ACCEPTED,
               Emergency.status.in_(EMERGENCY_OPEN_STATUSES))
    ).all()
    for r in responses:
        r.status = DonorResponseStatus.DECLINED
        r.note = "Withdrawn automatically: facility verification revoked"
        db.add(EmergencyEvent(emergency_id=r.emergency_id, actor_user_id=admin.id,
                              event_type="responder_withdrew",
                              note="Facility verification revoked"))
        for hid, bt, comp, units in _facility_reservations(db, r.emergency_id, facility.id):
            inventory_service.release_reservation(
                db, hid, bt, units, actor_user_id=admin.id, emergency_id=r.emergency_id,
                component=comp, note="Released: facility verification revoked")
        withdrawn += 1
    audit_service.record(
        db, "admin.facility_verification_revoked", actor_user_id=admin.id,
        target_type="hospital", target_id=facility.id,
        meta={"reason": payload.reason, "pathways_failed_over": len(outcomes),
              "responses_withdrawn": withdrawn},
    )
    notification_service.notify(
        db, facility.user_id, type="verification.revoked",
        title="Your facility verification was revoked", body=payload.reason,
        data={"facility_id": str(facility.id)},
    )
    db.commit()
    user = db.get(User, facility.user_id)
    return FacilityVerificationRevokeOut(
        facility=_facility_to_out(facility, user),
        affected_emergencies=[
            EmergencyReoptimizationOut(
                emergency_id=o.emergency_id, previous_version=o.previous_version,
                new_version=o.new_version, still_reachable=o.still_reachable,
                best_distance_km=o.best_distance_km, kind=o.kind,
                invalidated_backups=o.invalidated_backups, rejected_backups=o.rejected_backups,
            ) for o in outcomes
        ],
        withdrawn_emergency_responses=withdrawn,
    )


def _facility_reservations(db: Session, emergency_id: uuid.UUID, facility_id: uuid.UUID):
    from app.models.hospital import HospitalInventoryLog

    rows = db.execute(
        select(HospitalInventoryLog.hospital_id, HospitalInventoryLog.blood_type,
               HospitalInventoryLog.component, func.sum(HospitalInventoryLog.delta_reserved))
        .where(HospitalInventoryLog.emergency_id == emergency_id,
               HospitalInventoryLog.hospital_id == facility_id)
        .group_by(HospitalInventoryLog.hospital_id, HospitalInventoryLog.blood_type,
                  HospitalInventoryLog.component)
        .having(func.sum(HospitalInventoryLog.delta_reserved) > 0)
    ).all()
    return [(h, bt, c, int(n)) for h, bt, c, n in rows]


# --------------------------------------------------------------------------- #
# Donor governance
# --------------------------------------------------------------------------- #


@router.get("/donors", response_model=AdminDonorListOut)
def list_donors(
    query: str | None = Query(default=None, max_length=200),
    blood_type: BloodType | None = None,
    identity_verified: bool | None = None,
    is_available: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> AdminDonorListOut:
    conditions = []
    if query:
        like = f"%{query.strip()}%"
        conditions.append(or_(User.full_name.ilike(like), User.email.ilike(like)))
    if blood_type is not None:
        conditions.append(DonorProfile.blood_type == blood_type)
    if identity_verified is not None:
        conditions.append(DonorProfile.identity_verified.is_(identity_verified))
    if is_available is not None:
        conditions.append(DonorProfile.is_available.is_(is_available))
    where = and_(*conditions) if conditions else None

    base = select(DonorProfile, User).join(User, User.id == DonorProfile.user_id)
    count_stmt = select(func.count()).select_from(DonorProfile).join(
        User, User.id == DonorProfile.user_id
    )
    if where is not None:
        base = base.where(where)
        count_stmt = count_stmt.where(where)
    total = int(db.scalar(count_stmt) or 0)
    rows = db.execute(
        base.order_by(DonorProfile.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items = [
        AdminDonorOut(
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            blood_type=profile.blood_type,
            city=profile.city,
            is_available=profile.is_available,
            availability_state=profile.availability_state,
            identity_verified=profile.identity_verified,
            last_donation_date=profile.last_donation_date,
            created_at=profile.created_at,
        )
        for profile, user in rows
    ]
    return AdminDonorListOut(items=items, total=total, page=page, page_size=page_size)


# --------------------------------------------------------------------------- #
# Certificate registry
# --------------------------------------------------------------------------- #


@router.get("/certificates", response_model=AdminCertificateListOut)
def list_certificates(
    query: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> AdminCertificateListOut:
    base = (
        select(Certificate, User, Donation)
        .join(User, User.id == Certificate.donor_user_id)
        .join(Donation, Donation.id == Certificate.donation_id)
    )
    count_stmt = (
        select(func.count())
        .select_from(Certificate)
        .join(User, User.id == Certificate.donor_user_id)
        .join(Donation, Donation.id == Certificate.donation_id)
    )
    if query:
        like = f"%{query.strip()}%"
        cond = or_(
            Certificate.serial.ilike(like),
            Certificate.verify_code.ilike(like),
            User.full_name.ilike(like),
            User.email.ilike(like),
            Donation.hospital_name.ilike(like),
        )
        base = base.where(cond)
        count_stmt = count_stmt.where(cond)

    total = int(db.scalar(count_stmt) or 0)
    rows = db.execute(
        base.order_by(Certificate.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items = [
        AdminCertificateOut(
            id=cert.id,
            serial=cert.serial,
            verify_code=cert.verify_code,
            donor_user_id=cert.donor_user_id,
            donor_name=donor.full_name,
            donor_email=donor.email,
            donated_on=donation.donated_on,
            units=donation.units,
            hospital_name=donation.hospital_name,
            created_at=cert.created_at,
            revoked_at=cert.revoked_at,
            revocation_reason=cert.revocation_reason,
        )
        for cert, donor, donation in rows
    ]
    return AdminCertificateListOut(items=items, total=total, page=page, page_size=page_size)


@router.post("/certificates/{certificate_id}/revoke", response_model=AdminCertificateOut)
def revoke_certificate(
    certificate_id: uuid.UUID,
    payload: CertificateRevokeIn,
    _: None = Depends(verify_origin),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> AdminCertificateOut:
    """Mark a previously-issued certificate as no longer trustworthy.

    Phase 31 domain-correctness fix (see Certificate.revoked_at's docstring):
    before this, a Certificate could never be invalidated once issued -- an
    admin who discovered one was issued for a donation later found
    fraudulent or erroneous had no mechanism to say so. This never deletes
    the row (the certificate genuinely was issued and did exist -- deleting
    it would erase that history) and never touches the underlying Donation
    -- revocation is a statement about the certificate's current trust
    status, not a claim that the donation itself did or didn't happen.
    Idempotent: revoking an already-revoked certificate just updates the
    reason/actor/timestamp rather than erroring, since there's no unsafe
    double-effect to guard against here (unlike a state-machine transition).
    """
    cert = db.get(Certificate, certificate_id)
    if cert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Certificate not found")
    cert.revoked_at = utcnow()
    cert.revoked_by_user_id = admin.id
    cert.revocation_reason = payload.reason
    audit_service.record(
        db, "admin.certificate_revoked", actor_user_id=admin.id,
        target_type="certificate", target_id=cert.id,
        meta={"reason": payload.reason, "serial": cert.serial},
    )
    db.commit()
    db.refresh(cert)
    donor = db.get(User, cert.donor_user_id)
    donation = db.get(Donation, cert.donation_id)
    return AdminCertificateOut(
        id=cert.id,
        serial=cert.serial,
        verify_code=cert.verify_code,
        donor_user_id=cert.donor_user_id,
        donor_name=donor.full_name if donor else None,
        donor_email=donor.email if donor else None,
        donated_on=donation.donated_on if donation else None,
        units=donation.units if donation else None,
        hospital_name=donation.hospital_name if donation else None,
        created_at=cert.created_at,
        revoked_at=cert.revoked_at,
        revocation_reason=cert.revocation_reason,
    )


# --------------------------------------------------------------------------- #
# Analytics -- every number below is a real aggregate query against existing
# tables, computed fresh on each request. No funnel step or chart series is
# invented: a status/kind this platform has never had a row for legitimately
# reads as 0, it is never padded to look busier than it is.
# --------------------------------------------------------------------------- #


def _count_by(db: Session, column: Any) -> dict[str, int]:
    return {value.value if hasattr(value, "value") else str(value): int(count)
            for value, count in db.execute(select(column, func.count()).group_by(column)).all()}


@router.get("/analytics/overview", response_model=AdminAnalyticsOverviewOut)
def analytics_overview(
    days: int = Query(default=14, ge=1, le=90),
    admin: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> AdminAnalyticsOverviewOut:
    request_status = _count_by(db, BloodRequest.status)
    request_blood_type = _count_by(db, BloodRequest.blood_type)
    emergency_status = _count_by(db, Emergency.status)
    donor_response = _count_by(db, DonorResponse.status)
    hospital_response = _count_by(db, HospitalRequestResponse.status)

    verification_breakdown: dict[str, int] = {}
    for kind, vstatus, count in db.execute(
        select(VerificationRequest.kind, VerificationRequest.status, func.count()).group_by(
            VerificationRequest.kind, VerificationRequest.status
        )
    ).all():
        kind_v = kind.value if isinstance(kind, VerificationKind) else str(kind)
        status_v = vstatus.value if isinstance(vstatus, VerificationStatus) else str(vstatus)
        verification_breakdown[f"{kind_v}:{status_v}"] = int(count)

    since = utcnow() - timedelta(days=days)
    day_col = cast(BloodRequest.created_at, Date)
    per_day_rows = db.execute(
        select(day_col.label("day"), func.count())
        .where(BloodRequest.created_at >= since)
        .group_by(day_col)
        .order_by(day_col)
    ).all()
    requests_per_day = [
        AdminAnalyticsDayCountOut(day=day, count=int(count)) for day, count in per_day_rows
    ]

    return AdminAnalyticsOverviewOut(
        request_status_breakdown=request_status,
        request_blood_type_breakdown=request_blood_type,
        emergency_status_breakdown=emergency_status,
        donor_response_breakdown=donor_response,
        hospital_response_breakdown=hospital_response,
        verification_breakdown=verification_breakdown,
        requests_per_day=requests_per_day,
    )
