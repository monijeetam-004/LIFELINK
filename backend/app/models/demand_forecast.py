"""Stored demand-forecast runs (decision support only).

A run records exactly what was computed, from which history window, with
which method and parameters, so a forecast shown to a user can always be
traced back. Forecasts are statistical estimates over this deployment's own
request history -- synthetic in development -- and are never presented as
clinical predictions.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DemandForecastRun(Base):
    __tablename__ = "demand_forecast_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # None = every facility; otherwise one facility's own demand.
    facility_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=True, index=True
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    method: Mapped[str] = mapped_column(String(60), nullable=False)
    history_days: Mapped[int] = mapped_column(Integer, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Per (blood_type, component): history, forecast, intervals, backtest.
    results: Mapped[list] = mapped_column(JSON, nullable=False)
    data_is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
