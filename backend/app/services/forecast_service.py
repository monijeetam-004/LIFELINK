"""Blood demand forecasting (decision support, honest about its limits).

Signal
  * facility scope  -- units ALLOCATED (dispatched/used) at that facility per
    day, from the audited inventory ledger (HospitalInventoryLog);
  * network scope   -- units requested per day across all blood requests and
    emergencies.
  Days with no activity count as zero demand.

Method: simple exponential smoothing (SES), the smoothing constant chosen by
minimising one-step-ahead squared error on the history (grid 0.05..0.95).
The forecast is the final smoothed level, flat over the horizon (SES has no
trend or weekly term -- stated, not hidden). An approximate 80% interval
uses the in-sample one-step residual standard deviation, widened with the
horizon as for SES: sigma * sqrt(1 + (h-1) * alpha^2), clipped at 0.

Backtest: the last `holdout` days are hidden, SES is fitted on the rest,
and its mean absolute error is compared with two baselines on the same
days -- naive (yesterday's value) and seasonal-naive (same weekday last
week). If SES does not beat the naive baseline, the result says so.

Data honesty: in development every number comes from seeded synthetic
data (`DATA_IS_SYNTHETIC`), and the response carries that flag. Nothing
here is a clinical prediction or has been validated on real hospital data.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utcnow
from app.models.blood_request import BloodRequest
from app.models.demand_forecast import DemandForecastRun
from app.models.emergency import Emergency
from app.models.enums import BloodComponent, HospitalInventoryAction
from app.models.hospital import HospitalInventoryLog

METHOD = "simple_exponential_smoothing"
Z80 = 1.2816
MIN_HISTORY_DAYS = 14
MIN_ACTIVE_DAYS = 7


@dataclass
class SesFit:
    alpha: float
    level: float
    sigma: float
    fitted: list[float]
    sse: float = 0.0


def ses_fit(series: list[float]) -> SesFit:
    if not series:
        raise ValueError("empty series")
    best: SesFit | None = None
    for i in range(1, 20):
        alpha = round(i * 0.05, 2)
        level = series[0]
        fitted, sse = [series[0]], 0.0
        for y in series[1:]:
            fitted.append(level)
            sse += (y - level) ** 2
            level = alpha * y + (1 - alpha) * level
        n = max(1, len(series) - 1)
        sigma = math.sqrt(sse / n)
        cand = SesFit(alpha, level, sigma, fitted, sse)
        if best is None or sse < best.sse - 1e-12:
            best = cand
    return best


def ses_forecast(fit: SesFit, horizon: int) -> list[dict]:
    out = []
    for h in range(1, horizon + 1):
        half = Z80 * fit.sigma * math.sqrt(1 + (h - 1) * fit.alpha**2)
        out.append(
            {
                "h": h,
                "mean": round(max(0.0, fit.level), 3),
                "lo80": round(max(0.0, fit.level - half), 3),
                "hi80": round(fit.level + half, 3),
            }
        )
    return out


def _mae(pred: list[float], actual: list[float]) -> float:
    return round(sum(abs(p - a) for p, a in zip(pred, actual, strict=True)) / len(actual), 4)


def backtest(series: list[float], holdout: int) -> dict | None:
    if holdout < 1 or len(series) < holdout + 8:
        return None
    train, test = series[:-holdout], series[-holdout:]
    fit = ses_fit(train)
    ses_pred = [fit.level] * holdout
    naive_pred = [train[-1]] * holdout
    # multi-step seasonal naive: repeat the last observed week
    seasonal = [train[len(train) - 7 + (i % 7)] for i in range(holdout)]
    ses_mae, naive_mae = _mae(ses_pred, test), _mae(naive_pred, test)
    seasonal_mae = _mae(seasonal, test)
    return {
        "holdout_days": holdout,
        "ses_mae": ses_mae,
        "naive_mae": naive_mae,
        "seasonal_naive_mae": seasonal_mae,
        "beats_naive": ses_mae < naive_mae,
        "beats_seasonal_naive": ses_mae < seasonal_mae,
    }


def _daily(
    db: Session, facility_id: uuid.UUID | None, start: date, end: date
) -> dict[tuple[str, str], dict[date, float]]:
    """{(blood_type, component): {day: units}} over [start, end]."""
    lo = datetime.combine(start, time.min, tzinfo=utcnow().tzinfo)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=utcnow().tzinfo)
    out: dict[tuple[str, str], dict[date, float]] = defaultdict(lambda: defaultdict(float))
    if facility_id is not None:
        day = func.date(HospitalInventoryLog.created_at)
        rows = db.execute(
            select(
                HospitalInventoryLog.blood_type,
                HospitalInventoryLog.component,
                day,
                func.sum(-HospitalInventoryLog.delta_units),
            )
            .where(
                HospitalInventoryLog.hospital_id == facility_id,
                HospitalInventoryLog.action == HospitalInventoryAction.ALLOCATE,
                HospitalInventoryLog.created_at >= lo,
                HospitalInventoryLog.created_at < hi,
            )
            .group_by(HospitalInventoryLog.blood_type, HospitalInventoryLog.component, day)
        ).all()
        for bt, comp, d, units in rows:
            out[(bt.value, comp.value)][d] += float(units or 0)
        return out
    for model, comp_col in (
        (BloodRequest, getattr(BloodRequest, "component", None)),
        (Emergency, Emergency.component),
    ):
        day = func.date(model.created_at)
        cols = [
            model.blood_type,
            comp_col if comp_col is not None else None,
            day,
            func.sum(model.units_needed),
        ]
        stmt = select(*[c for c in cols if c is not None]).where(
            model.blood_type.is_not(None), model.created_at >= lo, model.created_at < hi
        )
        group = [model.blood_type, day] + ([comp_col] if comp_col is not None else [])
        for row in db.execute(stmt.group_by(*group)).all():
            if comp_col is not None:
                bt, comp, d, units = row
                comp_v = comp.value if comp else BloodComponent.WHOLE_BLOOD.value
            else:
                bt, d, units = row
                comp_v = BloodComponent.WHOLE_BLOOD.value
            out[(bt.value, comp_v)][d] += float(units or 0)
    return out


def run_forecast(
    db: Session,
    *,
    facility_id: uuid.UUID | None,
    history_days: int,
    horizon_days: int,
    requested_by_user_id: uuid.UUID | None,
    today: date | None = None,
) -> DemandForecastRun:
    today = today or utcnow().date()
    start = today - timedelta(days=history_days)
    end = today - timedelta(days=1)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    data = _daily(db, facility_id, start, end)
    results = []
    for (bt, comp), by_day in sorted(data.items()):
        series = [by_day.get(d, 0.0) for d in days]
        active = sum(1 for v in series if v > 0)
        entry = {
            "blood_type": bt,
            "component": comp,
            "history": [
                {"day": d.isoformat(), "units": v} for d, v in zip(days, series, strict=True)
            ],
            "total_units": sum(series),
            "active_days": active,
        }
        if len(series) < MIN_HISTORY_DAYS or active < MIN_ACTIVE_DAYS:
            entry.update(
                status="INSUFFICIENT_HISTORY",
                forecast=[],
                backtest=None,
                note=f"Needs at least {MIN_HISTORY_DAYS} days of history with "
                f"{MIN_ACTIVE_DAYS}+ days of activity; no forecast is shown.",
            )
        else:
            fit = ses_fit(series)
            entry.update(
                status="OK",
                alpha=fit.alpha,
                level=round(fit.level, 3),
                residual_sigma=round(fit.sigma, 3),
                forecast=[
                    {**f, "day": (today + timedelta(days=f["h"] - 1)).isoformat()}
                    for f in ses_forecast(fit, horizon_days)
                ],
                backtest=backtest(series, min(14, max(1, len(series) // 4))),
            )
        results.append(entry)
    run = DemandForecastRun(
        facility_id=facility_id,
        requested_by_user_id=requested_by_user_id,
        method=METHOD,
        history_days=history_days,
        horizon_days=horizon_days,
        parameters={
            "interval": "approximate 80% (z=1.2816)",
            "alpha_grid": "0.05..0.95",
            "signal": "units allocated at facility"
            if facility_id
            else "units requested network-wide (requests + emergencies)",
            "min_history_days": MIN_HISTORY_DAYS,
            "min_active_days": MIN_ACTIVE_DAYS,
            "as_of": today.isoformat(),
        },
        results=results,
        data_is_synthetic=settings.data_is_synthetic,
    )
    db.add(run)
    db.flush()
    return run
