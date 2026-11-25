"""Cold-chain telemetry for shipments: ingest readings, evaluate them.

What this is: a data model, an ingestion API and an evaluator for
temperature readings attached to a shipment. What it is NOT: a sensor
integration -- LifeLink ships no device driver and no gateway. Readings
arrive through the API (from a logger export, a gateway, or a person),
and each one records its ``source`` (SENSOR / MANUAL / SIMULATED). The
development-only simulator labels everything it writes SIMULATED, and such
readings are never presented as real sensor data.

Acceptable ranges come from ``app.core.clinical_sources`` (cited storage
conditions per component) -- never invented here.

Evaluation (worst finding wins, all findings reported):

* SENSOR_FAILURE      -- a reading reports a device error (no value), or a
                         value outside the physically plausible range
                         (PLAUSIBLE_MIN_C..PLAUSIBLE_MAX_C) that can only be a
                         faulty probe;
* REPEATED_EXCURSIONS -- two or more separate out-of-range episodes;
* EXCURSION           -- one out-of-range episode;
* DATA_GAP            -- consecutive valid readings (or the start/end of the
                         monitored window) further apart than
                         TELEMETRY_MAX_GAP_MINUTES: the temperature in the gap
                         is unknown, never assumed fine;
* WITHIN_RANGE        -- every reading in range and no gap;
* NO_DATA             -- no readings at all (the normal state today for
                         shipments without a logger).

Precedence: REPEATED_EXCURSIONS > EXCURSION > SENSOR_FAILURE > DATA_GAP >
WITHIN_RANGE. An excursion is a measured fact about the product, so it
outranks a device fault.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core import clinical_sources
from app.core.config import settings
from app.core.time import utcnow
from app.models.blood_unit import BloodUnit
from app.models.enums import (
    BloodComponent,
    ColdChainStatus,
    ShipmentStatus,
    TelemetrySource,
)
from app.models.shipment import Shipment, ShipmentItem, ShipmentTemperatureReading

PLAUSIBLE_MIN_C = -90.0
PLAUSIBLE_MAX_C = 60.0
MAX_CLOCK_SKEW = timedelta(minutes=5)
INGEST_STATUSES = (
    ShipmentStatus.CREATED,
    ShipmentStatus.DISPATCHED,
    ShipmentStatus.IN_TRANSIT,
    ShipmentStatus.ARRIVED,
)


class TelemetryError(ValueError):
    """Invalid telemetry input (maps to HTTP 422/409)."""


def range_for_components(components: set[BloodComponent]) -> tuple[float | None, float | None]:
    """The storage range shared by every component in a shipment. Components
    with different storage classes cannot travel in one container, so a
    mixed shipment is refused at creation."""
    ranges = {clinical_sources.temperature_range_c(c) for c in components}
    if len(ranges) > 1:
        raise TelemetryError(
            "These components need different storage temperatures and cannot share one "
            "shipment: " + ", ".join(sorted(c.value for c in components))
        )
    return next(iter(ranges)) if ranges else (None, None)


def in_range(t: float, lo: float | None, hi: float | None) -> bool:
    return (lo is None or t >= lo) and (hi is None or t <= hi)


@dataclass
class ReadingIn:
    recorded_at: datetime
    temperature_c: float | None
    sensor_id: str
    error_code: str | None = None


@dataclass
class Episode:
    start: datetime
    end: datetime
    readings: int
    min_c: float
    max_c: float


@dataclass
class ColdChainReport:
    status: ColdChainStatus
    readings: int
    valid_readings: int
    failed_readings: int
    min_c: float | None
    max_c: float | None
    range_min_c: float | None
    range_max_c: float | None
    excursions: list[Episode] = field(default_factory=list)
    gaps: list[tuple[datetime, datetime, float]] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    sources: dict[str, int] = field(default_factory=dict)


def ingest(
    db: Session,
    shipment: Shipment,
    readings: list[ReadingIn],
    *,
    source: TelemetrySource,
    submitted_by_user_id: uuid.UUID | None,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Store readings (idempotent per shipment+sensor+timestamp). Returns
    (inserted, duplicates). Validates every reading before storing any."""
    now = now or utcnow()
    if shipment.status not in INGEST_STATUSES:
        raise TelemetryError(f"Telemetry cannot be added to a {shipment.status.value} shipment")
    if source == TelemetrySource.SIMULATED and settings.is_production:
        raise TelemetryError("Simulated telemetry is refused in production")
    if not readings:
        raise TelemetryError("No readings supplied")
    earliest = shipment.created_at - timedelta(hours=1)
    rows = []
    for r in readings:
        when = r.recorded_at
        if when.tzinfo is None:
            raise TelemetryError("recorded_at must include a timezone")
        if when > now + MAX_CLOCK_SKEW:
            raise TelemetryError(f"Reading at {when.isoformat()} is in the future")
        if when < earliest:
            raise TelemetryError(f"Reading at {when.isoformat()} predates the shipment")
        if r.temperature_c is None and not r.error_code:
            raise TelemetryError("A reading without a temperature must carry an error_code")
        rows.append(
            {
                "id": uuid.uuid4(),
                "shipment_id": shipment.id,
                "recorded_at": when,
                "temperature_c": r.temperature_c,
                "sensor_id": r.sensor_id[:80],
                "source": source,
                "error_code": (r.error_code or None),
                "submitted_by_user_id": submitted_by_user_id,
            }
        )
    stmt = (
        pg_insert(ShipmentTemperatureReading)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_temperature_reading")
        .returning(ShipmentTemperatureReading.id)
    )
    inserted = len(db.execute(stmt).all())
    db.flush()
    return inserted, len(rows) - inserted


def evaluate(db: Session, shipment: Shipment, *, now: datetime | None = None) -> ColdChainReport:
    now = now or utcnow()
    lo, hi = shipment.temp_min_c, shipment.temp_max_c
    rows = list(
        db.scalars(
            select(ShipmentTemperatureReading)
            .where(ShipmentTemperatureReading.shipment_id == shipment.id)
            .order_by(ShipmentTemperatureReading.recorded_at, ShipmentTemperatureReading.sensor_id)
        ).all()
    )
    sources: dict[str, int] = {}
    for r in rows:
        sources[r.source.value] = sources.get(r.source.value, 0) + 1
    if not rows:
        return ColdChainReport(
            ColdChainStatus.NO_DATA,
            0,
            0,
            0,
            None,
            None,
            lo,
            hi,
            findings=[
                "No temperature readings were recorded for this "
                "shipment -- its cold chain is unverified."
            ],
        )
    failed = [
        r
        for r in rows
        if r.temperature_c is None
        or r.error_code
        or not (PLAUSIBLE_MIN_C <= r.temperature_c <= PLAUSIBLE_MAX_C)
    ]
    valid = [r for r in rows if r not in failed]
    report = ColdChainReport(
        status=ColdChainStatus.WITHIN_RANGE,
        readings=len(rows),
        valid_readings=len(valid),
        failed_readings=len(failed),
        min_c=min((r.temperature_c for r in valid), default=None),
        max_c=max((r.temperature_c for r in valid), default=None),
        range_min_c=lo,
        range_max_c=hi,
        sources=sources,
    )
    # Excursion episodes: maximal runs of consecutive out-of-range valid readings.
    current: Episode | None = None
    for r in valid:
        if not in_range(r.temperature_c, lo, hi):
            if current is None:
                current = Episode(r.recorded_at, r.recorded_at, 0, r.temperature_c, r.temperature_c)
            current.end = r.recorded_at
            current.readings += 1
            current.min_c = min(current.min_c, r.temperature_c)
            current.max_c = max(current.max_c, r.temperature_c)
        elif current is not None:
            report.excursions.append(current)
            current = None
    if current is not None:
        report.excursions.append(current)
    # Gaps between valid readings, and at the edges of the monitored window.
    max_gap = timedelta(minutes=settings.telemetry_max_gap_minutes)
    window_start = shipment.dispatched_at
    window_end = (
        shipment.arrived_at
        or shipment.received_at
        or (
            now
            if shipment.status in (ShipmentStatus.DISPATCHED, ShipmentStatus.IN_TRANSIT)
            else None
        )
    )
    points = [r.recorded_at for r in valid]
    edges = (
        ([window_start] if window_start and points and window_start < points[0] else [])
        + points
        + ([window_end] if window_end and points and window_end > points[-1] else [])
    )
    for a, b in zip(edges, edges[1:], strict=False):
        if b - a > max_gap:
            report.gaps.append((a, b, round((b - a).total_seconds() / 60.0, 1)))

    def fmt(x: float | None) -> str:
        return "-" if x is None else f"{x:g} C"

    if len(report.excursions) >= 2:
        report.status = ColdChainStatus.REPEATED_EXCURSIONS
    elif report.excursions:
        report.status = ColdChainStatus.EXCURSION
    elif failed:
        report.status = ColdChainStatus.SENSOR_FAILURE
    elif report.gaps or not valid:
        report.status = ColdChainStatus.DATA_GAP
    for e in report.excursions:
        report.findings.append(
            f"Out of range ({fmt(lo)} to {fmt(hi)}) from {e.start.isoformat()} to "
            f"{e.end.isoformat()}: {e.readings} reading(s), {fmt(e.min_c)} to {fmt(e.max_c)}"
        )
    if failed:
        report.findings.append(
            f"{len(failed)} reading(s) reported a sensor error or an implausible value"
        )
    for a, b, minutes in report.gaps:
        report.findings.append(
            f"No valid reading for {minutes} min ({a.isoformat()} to "
            f"{b.isoformat()}); limit is {settings.telemetry_max_gap_minutes} min"
        )
    if report.status == ColdChainStatus.WITHIN_RANGE:
        report.findings.append(f"All {len(valid)} reading(s) within {fmt(lo)} to {fmt(hi)}")
    if sources.get(TelemetrySource.SIMULATED.value):
        report.findings.append(
            "Includes SIMULATED readings (development only) -- not evidence about any real product"
        )
    return report


def evaluate_and_store(db: Session, shipment: Shipment) -> ColdChainReport:
    report = evaluate(db, shipment)
    shipment.cold_chain_status = report.status
    shipment.cold_chain_evaluated_at = utcnow()
    db.flush()
    return report


BAD_STATUSES = frozenset(
    {
        ColdChainStatus.EXCURSION,
        ColdChainStatus.REPEATED_EXCURSIONS,
        ColdChainStatus.SENSOR_FAILURE,
        ColdChainStatus.DATA_GAP,
    }
)


def components_of(db: Session, shipment_id: uuid.UUID) -> set[BloodComponent]:
    return set(
        db.scalars(
            select(BloodUnit.component)
            .join(ShipmentItem, ShipmentItem.blood_unit_id == BloodUnit.id)
            .where(ShipmentItem.shipment_id == shipment_id)
        ).all()
    )


def simulate(
    db: Session,
    shipment: Shipment,
    *,
    minutes: int,
    interval_minutes: int,
    excursion_at_minute: int | None,
    excursion_c: float | None,
    seed: int,
    submitted_by_user_id: uuid.UUID | None,
    sensor_failure_at_minute: int | None = None,
) -> int:
    """DEVELOPMENT ONLY: generate SIMULATED readings for a demo. Values are a
    deterministic random walk inside the shipment's range (plus an optional
    injected excursion / sensor failure). Never used outside dev/test."""
    if settings.is_production:
        raise TelemetryError("The telemetry simulator is disabled in production")
    lo, hi = shipment.temp_min_c, shipment.temp_max_c
    if lo is None and hi is None:
        raise TelemetryError("This shipment has no temperature range to simulate against")
    centre = ((lo if lo is not None else hi - 10) + (hi if hi is not None else lo + 10)) / 2
    rng = random.Random(seed)
    start = shipment.dispatched_at or utcnow() - timedelta(minutes=minutes)
    readings: list[ReadingIn] = []
    for m in range(0, minutes + 1, interval_minutes):
        when = start + timedelta(minutes=m)
        if when > utcnow():
            break
        if sensor_failure_at_minute is not None and m == sensor_failure_at_minute:
            readings.append(ReadingIn(when, None, "SIM-1", "SIMULATED_PROBE_FAILURE"))
            continue
        t = round(centre + rng.uniform(-0.4, 0.4), 2)
        if excursion_at_minute is not None and excursion_c is not None and m == excursion_at_minute:
            t = excursion_c
        readings.append(ReadingIn(when, t, "SIM-1"))
    inserted, _dup = ingest(
        db,
        shipment,
        readings,
        source=TelemetrySource.SIMULATED,
        submitted_by_user_id=submitted_by_user_id,
    )
    return inserted
