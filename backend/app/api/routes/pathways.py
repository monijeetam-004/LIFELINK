"""Pathway engine: configuration and dry-run simulation.

`POST /pathways/simulate` runs the real planner (the same code every
emergency uses) against the live database with optional *simulated* route
failures, and returns every evaluated facility -- including the rejected
ones and why -- without storing or reserving anything. It backs the Graph
Decision Engine, Decision Transparency and Simulation screens, so those
show real computations instead of canned numbers.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_roles, verify_origin
from app.core.config import settings
from app.core.time import utcnow
from app.models.hospital import HospitalProfile
from app.models.transport import TransportRoute
from app.models.user import User, UserRole
from app.schemas.pathway import PathwaySimulateIn, PlanResultOut, plan_result_out
from app.services import audit_service, emergency_pathway_service
from app.services import pathway_planner as planner

router = APIRouter(prefix="/pathways", tags=["pathways"])


@router.get("/config")
def pathway_config(user: User = Depends(get_current_user)) -> dict:
    """The planning assumptions in force (all configurable; none measured)."""
    return {
        **emergency_pathway_service.config_snapshot(),
        "data_is_synthetic": settings.data_is_synthetic,
    }


@router.post("/simulate", response_model=PlanResultOut)
def simulate_pathway(
    payload: PathwaySimulateIn,
    _: None = Depends(verify_origin),
    user: User = Depends(require_roles(UserRole.HOSPITAL, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> PlanResultOut:
    dest = db.get(HospitalProfile, payload.destination_facility_id)
    if dest is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Facility not found")
    if user.role == UserRole.HOSPITAL and dest.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hospitals can simulate deliveries to their own facility only",
        )
    failed = list(dict.fromkeys(payload.failed_route_ids))
    if failed:
        known = set(
            db.scalars(select(TransportRoute.id).where(TransportRoute.id.in_(failed))).all()
        )
        unknown = [str(r) for r in failed if r not in known]
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown route id(s): {', '.join(unknown)}",
            )
    now = utcnow()
    try:
        result = planner.plan(
            db,
            planner.PlanRequest(
                recipient_type=payload.recipient_blood_type,
                component=payload.component,
                units_needed=payload.units_needed,
                urgency=payload.urgency,
                deadline=(
                    now + timedelta(minutes=payload.deadline_minutes)
                    if payload.deadline_minutes is not None
                    else None
                ),
                destination_facility_id=dest.id,
                now=now,
                excluded_route_ids=frozenset(failed),
                max_backups=payload.max_backups,
            ),
        )
    except planner.PlanningError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    audit_service.record(
        db,
        "pathway.simulated",
        actor_user_id=user.id,
        target_type="hospital",
        target_id=dest.id,
        meta={"failed_routes": len(failed), "feasibility": result.primary.feasibility.value},
    )
    db.commit()
    return plan_result_out(result, simulated_failed=failed)
