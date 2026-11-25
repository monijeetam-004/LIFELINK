"""Resolve an emergency's location to coordinates -- honestly.

Order of precedence (the first that applies wins, and the result records
which one it was in ``Emergency.geocode_source`` / ``geocode_detail``):

1. USER_INPUT -- the caller sent latitude/longitude (validated ranges).
2. FACILITY   -- the emergency names a target facility that has coordinates.
3. GAZETTEER  -- the city (or a city named in the address text) matches the
   city of one or more registered facilities; the centroid of those
   facilities' coordinates is used and the detail says so. This is a coarse,
   city-level position, labelled as such -- never presented as a street
   address fix.
4. GEOCODER   -- only when ``GEOCODER_URL`` is configured: a Nominatim-
   compatible ``/search`` call with a short timeout. A failure or empty
   answer is logged and treated as "no coordinates", never invented.

If nothing applies the emergency simply has no coordinates; distance-based
features then say "distance unknown" rather than guessing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import GeocodeSource
from app.models.hospital import HospitalProfile

logger = logging.getLogger("lifelink.geocoding")


@dataclass(frozen=True)
class GeoResult:
    latitude: float
    longitude: float
    source: GeocodeSource
    detail: str


def valid_coordinates(lat: float | None, lon: float | None) -> bool:
    return lat is not None and lon is not None and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def _gazetteer(db: Session, city: str | None, text: str | None) -> GeoResult | None:
    rows = db.execute(
        select(
            func.lower(HospitalProfile.city),
            func.avg(HospitalProfile.latitude),
            func.avg(HospitalProfile.longitude),
            func.count(),
        )
        .where(
            HospitalProfile.city.is_not(None),
            HospitalProfile.latitude.is_not(None),
            HospitalProfile.longitude.is_not(None),
        )
        .group_by(func.lower(HospitalProfile.city))
    ).all()
    known = {c.strip(): (float(lat), float(lon), int(n)) for c, lat, lon, n in rows if c}
    wanted: list[str] = []
    if city and city.strip():
        wanted.append(city.strip().lower())
    if text:
        lowered = text.lower()
        # whole-word match only, longest city names first ("new delhi" before "delhi")
        for name in sorted(known, key=len, reverse=True):
            if re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", lowered):
                wanted.append(name)
    for name in wanted:
        if name in known:
            lat, lon, n = known[name]
            return GeoResult(
                lat,
                lon,
                GeocodeSource.GAZETTEER,
                f"city-level estimate: centroid of {n} registered facilit"
                f"{'y' if n == 1 else 'ies'} in {name.title()}",
            )
    return None


def _external(query: str, *, client: httpx.Client | None = None) -> GeoResult | None:
    if not settings.geocoder_url or not query.strip():
        return None
    own = client is None
    client = client or httpx.Client(timeout=settings.geocoder_timeout_seconds)
    try:
        resp = client.get(
            settings.geocoder_url,
            params={"q": query[:200], "format": "jsonv2", "limit": 1},
            headers={"User-Agent": settings.geocoder_user_agent},
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list) or not data:
            return None
        lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
        if not valid_coordinates(lat, lon):
            return None
        label = str(data[0].get("display_name", ""))[:200]
        return GeoResult(lat, lon, GeocodeSource.GEOCODER, f"geocoder match: {label}")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("geocoder lookup failed: %s", type(exc).__name__)
        return None
    finally:
        if own:
            client.close()


def resolve(
    db: Session,
    *,
    latitude: float | None,
    longitude: float | None,
    city: str | None,
    location_text: str | None,
    target_facility: HospitalProfile | None,
    client: httpx.Client | None = None,
) -> GeoResult | None:
    if latitude is not None or longitude is not None:
        if not valid_coordinates(latitude, longitude):
            raise ValueError("latitude/longitude must both be given and within range")
        return GeoResult(
            latitude, longitude, GeocodeSource.USER_INPUT, "coordinates supplied by the requester"
        )
    if target_facility is not None and valid_coordinates(
        target_facility.latitude, target_facility.longitude
    ):
        return GeoResult(
            target_facility.latitude,
            target_facility.longitude,
            GeocodeSource.FACILITY,
            f"location of target facility {target_facility.name or ''}".strip(),
        )
    found = _gazetteer(db, city, location_text)
    if found is not None:
        return found
    query = ", ".join(x for x in (location_text, city) if x)
    return _external(query, client=client)
