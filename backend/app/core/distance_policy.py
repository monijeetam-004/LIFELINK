"""One donor-distance policy for every place that alerts, ranks or
shortlists donors (audit finding D4, 2026-10-31).

Before this module, three call sites applied the 50 km donor limit in
slightly different ways: SOS alerts and the ranking views applied it, but
blood-request alerts did not, and a donor with no shared location was
alerted everywhere (so a request without a city reached every compatible
donor in the country). Every call site now asks this module.

How a donor's distance from the request is classified
------------------------------------------------------
``MEASURED``   both the request and the donor have coordinates and the donor
               has opted into sharing their location. The great-circle
               distance is real. The location may be *stale* (older than
               ``DONOR_LOCATION_STALE_DAYS``); that is reported, never hidden.
``SAME_CITY``  the donor's coordinates are not usable, but the donor's
               recorded city equals the request's city (case- and
               space-insensitive). This is a labelled, city-level proximity
               assumption -- it is shown as "same city, distance unknown",
               never as a number of kilometres.
``UNKNOWN``    the donor's distance cannot be established at all.
``NO_REQUEST_LOCATION``  the request itself has neither coordinates nor a
               city, so no geographic scope exists.

Alert rule
----------
* MEASURED: alerted only when ``distance <= limit`` (the limit is
  inclusive) and, if the donor set a personal alert radius, within it.
* SAME_CITY: alerted (city-level scope).
* UNKNOWN: **not** alerted. Missing location is never read as "nearby".
* NO_REQUEST_LOCATION: **not** alerted, unless the deployment explicitly sets
  ``DONOR_ALERT_ALLOW_UNSCOPED=true`` -- a documented, off-by-default
  fallback. A request with no location is never broadcast nationwide
  silently.
* An audited administrator override (``EmergencyOverrideKind.DONOR_DISTANCE``
  with a mandatory reason) lifts the kilometre limit and admits UNKNOWN /
  NO_REQUEST_LOCATION donors for that one emergency. A donor's own alert
  radius is a personal preference and is respected even under an override.

Pure functions only: no database access, so the rule is unit-testable.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, datetime

from app.models.enums import haversine_km


class DistanceBasis(str, enum.Enum):
    MEASURED = "MEASURED"
    SAME_CITY = "SAME_CITY"
    UNKNOWN = "UNKNOWN"
    NO_REQUEST_LOCATION = "NO_REQUEST_LOCATION"


@dataclass(frozen=True)
class DistanceAssessment:
    basis: DistanceBasis
    distance_km: float | None
    within_limit: bool | None  # None when no distance could be measured
    within_donor_radius: bool | None
    location_age_days: int | None
    location_stale: bool | None

    def describe(self) -> str:
        if self.basis is DistanceBasis.MEASURED:
            text = f"{self.distance_km:.1f} km away"
            if self.location_stale:
                text += f" (location last confirmed {self.location_age_days} days ago)"
            return text
        if self.basis is DistanceBasis.SAME_CITY:
            return "same city as the request (exact distance unknown)"
        if self.basis is DistanceBasis.NO_REQUEST_LOCATION:
            return "the request has no location, so distance cannot be checked"
        return "location unknown"


def normalise_city(city: str | None) -> str | None:
    """Case/space-insensitive city name; "Mumbai, Maharashtra" and "Mumbai"
    are the same city (profiles store either form)."""
    if city is None:
        return None
    c = " ".join(city.split(",")[0].split()).casefold()
    return c or None


def within_limit(distance_km: float, limit_km: float) -> bool:
    """The limit is inclusive: a donor exactly at the limit is inside it."""
    return distance_km <= limit_km


def assess(
    *,
    origin_lat: float | None,
    origin_lng: float | None,
    origin_city: str | None,
    donor_lat: float | None,
    donor_lng: float | None,
    donor_shares_location: bool,
    donor_city: str | None,
    donor_alert_radius_km: float | None,
    limit_km: float,
    location_updated_at: datetime | None = None,
    stale_after_days: int = 90,
    now: datetime | None = None,
) -> DistanceAssessment:
    have_origin = origin_lat is not None and origin_lng is not None
    have_donor = donor_shares_location and donor_lat is not None and donor_lng is not None
    if have_origin and have_donor:
        d = haversine_km(origin_lat, origin_lng, donor_lat, donor_lng)  # type: ignore[arg-type]
        age = None
        stale = None
        if location_updated_at is not None:
            now = now or datetime.now(UTC)
            ts = location_updated_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            age = max(0, (now - ts).days)
            stale = age > stale_after_days
        return DistanceAssessment(
            basis=DistanceBasis.MEASURED,
            distance_km=d,
            within_limit=within_limit(d, limit_km),
            within_donor_radius=(
                None if donor_alert_radius_km is None else d <= donor_alert_radius_km
            ),
            location_age_days=age,
            location_stale=stale,
        )
    o_city, d_city = normalise_city(origin_city), normalise_city(donor_city)
    if not have_origin and o_city is None:
        return DistanceAssessment(DistanceBasis.NO_REQUEST_LOCATION, None, None, None, None, None)
    if o_city is not None and d_city is not None and o_city == d_city:
        return DistanceAssessment(DistanceBasis.SAME_CITY, None, None, None, None, None)
    return DistanceAssessment(DistanceBasis.UNKNOWN, None, None, None, None, None)


def alert_skip_reason(
    a: DistanceAssessment, *, override: bool = False, allow_unscoped: bool = False,
) -> str | None:
    """None when the donor may be alerted; otherwise the skip counter name."""
    if a.basis is DistanceBasis.MEASURED:
        if a.within_donor_radius is False:
            return "outside_alert_radius"
        if a.within_limit is False and not override:
            return "beyond_distance_limit"
        return None
    if a.basis is DistanceBasis.SAME_CITY:
        return None
    if a.basis is DistanceBasis.NO_REQUEST_LOCATION:
        return None if (override or allow_unscoped) else "no_request_location"
    return None if override else "location_unknown"
