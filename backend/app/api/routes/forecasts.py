"""Demand forecasting API (decision support; see app.services.forecast_service).

Hospitals forecast their own facility's usage; admins may also forecast the
whole network. Every run is stored (method, window, parameters, results)
and labelled with whether the underlying data is synthetic."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_roles, verify_origin
from app.models.demand_forecast import DemandForecastRun
from app.models.user import User, UserRole
from app.services import audit_service, forecast_service
from app.services.domain_service import get_or_create_hospital_profile

router = APIRouter(prefix="/forecasts", tags=["forecasts"])
_actor = require_roles(UserRole.HOSPITAL, UserRole.ADMIN)


class ForecastIn(BaseModel):
    facility_id: uuid.UUID | None = None
    network: bool = False
    history_days: int = Field(default=56, ge=14, le=730)
    horizon_days: int = Field(default=7, ge=1, le=60)


class ForecastRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    facility_id: uuid.UUID | None
    method: str
    history_days: int
    horizon_days: int
    parameters: dict
    results: list
    data_is_synthetic: bool
    created_at: datetime
    disclaimer: str = (
        "Statistical estimate from this deployment's own history (simple exponential "
        "smoothing). Not a clinical prediction and not validated on real hospital data."
    )


def _scope(db: Session, user: User, facility_id: uuid.UUID | None, network: bool):
    if user.role == UserRole.HOSPITAL:
        own = get_or_create_hospital_profile(db, user).id
        if network or (facility_id is not None and facility_id != own):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hospitals can forecast their own facility only",
            )
        return own
    return None if network or facility_id is None else facility_id


@router.post("", response_model=ForecastRunOut, status_code=status.HTTP_201_CREATED)
def create_forecast(
    payload: ForecastIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_actor),
    db: Session = Depends(get_db),
):
    facility_id = _scope(db, user, payload.facility_id, payload.network)
    run = forecast_service.run_forecast(
        db,
        facility_id=facility_id,
        history_days=payload.history_days,
        horizon_days=payload.horizon_days,
        requested_by_user_id=user.id,
    )
    audit_service.record(
        db,
        "forecast.created",
        actor_user_id=user.id,
        target_type="demand_forecast_run",
        target_id=run.id,
        meta={
            "facility_id": str(facility_id) if facility_id else None,
            "series": len(run.results),
            "synthetic": run.data_is_synthetic,
        },
    )
    db.commit()
    db.refresh(run)
    return run


@router.get("", response_model=list[ForecastRunOut])
def list_forecasts(
    limit: int = Query(default=10, ge=1, le=100),
    user: User = Depends(_actor),
    db: Session = Depends(get_db),
):
    stmt = select(DemandForecastRun).order_by(DemandForecastRun.created_at.desc()).limit(limit)
    if user.role == UserRole.HOSPITAL:
        stmt = stmt.where(
            DemandForecastRun.facility_id == get_or_create_hospital_profile(db, user).id
        )
    return list(db.scalars(stmt).all())


@router.get("/{run_id}", response_model=ForecastRunOut)
def get_forecast(run_id: uuid.UUID, user: User = Depends(_actor), db: Session = Depends(get_db)):
    run = db.get(DemandForecastRun, run_id)
    if run is None or (
        user.role == UserRole.HOSPITAL
        and run.facility_id != get_or_create_hospital_profile(db, user).id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return run
