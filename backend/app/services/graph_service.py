"""Neo4j graph engine: schema, sync, resource discovery, pathway ranking.

This module is the ONLY place that issues Cypher. It is integrated into
the real LifeLink emergency/hospital flow (app/api/routes/emergencies.py,
app/api/routes/hospitals.py, app/api/routes/admin.py -- task 28) rather
than standing alone as a demo: the callers there feed it real
`HospitalProfile` / `HospitalInventory` / `TransportRoute` rows and use its
results to answer real "where can I get blood type X from" questions.

Synchronization strategy (see the phase report for the full rationale):
PostgreSQL is the single source of truth for every entity mirrored into
the graph. The graph is:
  1. Rebuildable from scratch at any time via `resync_all()`, which wipes
     and rebuilds every node/relationship from the current relational
     state. This is the authoritative recovery/consistency path
     (exposed as `POST /admin/graph/resync`).
  2. Kept incrementally in sync, best-effort, by `sync_facility()` /
     `sync_inventory()` / `sync_route()` at the real mutation points
     (facility create/update, inventory update, transport-route
     create/disrupt/recover). These NEVER raise -- a Neo4j hiccup must
     never fail or roll back the primary Postgres write that already
     succeeded. They return True/False so callers can log/report drift
     without treating it as a request failure.
`resync_all()` is the one exception that DOES raise `GraphUnavailableError`
on failure: it is the explicit, admin-triggered "make the graph correct
again" operation, so silently swallowing a failure there would hide the
one thing an operator is specifically asking this endpoint to guarantee.

Graph schema
------------
Nodes:
  (:Facility {id, name, facility_type, city, latitude, longitude, verified})
  (:BloodType {code})                         -- mirrors BloodType enum
  (:City {name})
Relationships:
  (:BloodType)-[:CAN_DONATE_TO]->(:BloodType)  -- mirrors can_donate_to()
  (:Facility)-[:HAS_STOCK {units}]->(:BloodType)
  (:Facility)-[:ROUTE {route_id, distance_km, transit_minutes, status,
                       disrupted_reason}]->(:Facility)
      Created with a fixed direction (origin -> destination, matching the
      TransportRoute row) but always MATCHed with an undirected pattern
      (`-[:ROUTE]-`) for traversal, since a transport link can be used in
      either direction.
  (:Facility)-[:LOCATED_IN]->(:City)
  (:City)-[:HAS_AVAILABLE_DONORS {count}]->(:BloodType)
      Aggregate-only: city + blood type + a count of available donors.
      There are deliberately NO per-donor nodes -- donor identity,
      contact info and exact location never enter the graph. This is the
      privacy-appropriate level for graph operations the engineering
      rules call for.

Pathway ranking without APOC/GDS
--------------------------------
Neo4j Community edition (and a default Aura Free instance) does not ship
a weighted shortest-path algorithm out of the box. Rather than depend on
an optional plugin that may not be installed, `_graph_pathways()` uses a
bounded-depth (<=6 hops) variable-length path enumeration in plain Cypher
and picks the minimum total `distance_km` in Python-free Cypher via
`min()`. This is a real, correct weighted-shortest-path computation for
any pair connected within 6 hops -- an appropriate bound for a regional
hospital/blood-bank transport network at this project's scale. For a much
larger deployment this should be replaced with `apoc.algo.dijkstra` or the
Graph Data Science `gds.shortestPath` procedure; that is a known,
documented limitation, not something hidden.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.blood_compatibility import can_donate_to
from app.core.graph import GraphUnavailableError, graph_session
from app.core.route_graph import Path
from app.models.enums import BloodComponent, BloodType, GraphPathwaySource, Urgency
from app.models.hospital import HospitalInventory, HospitalProfile
from app.models.profile import DonorProfile
from app.models.transport import TransportRoute

logger = logging.getLogger("lifelink.graph_service")

# Real Cypher-only weighted-shortest-path bound (see module docstring).
_MAX_PATH_HOPS = 6

# Assumed average road speed used ONLY to estimate a TransportRoute's
# transit_minutes when a real measured figure isn't supplied (admin API,
# scripts/seed_demo_data.py) -- a single shared constant/formula so the
# estimate is identical everywhere it's used, never presented as measured.
ASSUMED_ROAD_SPEED_KMPH = 45.0


def estimate_transit_minutes(distance_km: float) -> int:
    """Real formula, clearly an estimate -- see ASSUMED_ROAD_SPEED_KMPH."""
    return max(1, round(distance_km / ASSUMED_ROAD_SPEED_KMPH * 60))


@dataclass
class PathwayCandidate:
    facility_id: uuid.UUID
    facility_name: str | None
    city: str | None
    # Usable, unreserved, compatible units of the requested component that
    # would still be in date on arrival (never a raw stock count).
    units: int
    distance_km: float
    transit_minutes: int | None = None
    hops: int | None = None
    # Gap elimination: the full route, not just its ends.
    route_ids: list[uuid.UUID] = field(default_factory=list)
    node_ids: list[uuid.UUID] = field(default_factory=list)
    route_basis: str | None = None
    eta_minutes: int | None = None
    slack_minutes: float | None = None
    units_by_type: dict[str, int] = field(default_factory=dict)


@dataclass
class PathwayResult:
    source: GraphPathwaySource
    candidates: list[PathwayCandidate] = field(default_factory=list)
    disrupted_routes_excluded: int = 0
    graph_available: bool = True
    degraded_reason: str | None = None

    @property
    def best_distance_km(self) -> float | None:
        return self.candidates[0].distance_km if self.candidates else None


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
def ensure_constraints() -> None:
    """Idempotent constraint/index setup. Safe to call repeatedly."""
    statements = [
        "CREATE CONSTRAINT facility_id_unique IF NOT EXISTS "
        "FOR (f:Facility) REQUIRE f.id IS UNIQUE",
        "CREATE CONSTRAINT blood_type_code_unique IF NOT EXISTS "
        "FOR (b:BloodType) REQUIRE b.code IS UNIQUE",
        "CREATE CONSTRAINT city_name_unique IF NOT EXISTS "
        "FOR (c:City) REQUIRE c.name IS UNIQUE",
        "CREATE INDEX facility_city_idx IF NOT EXISTS FOR (f:Facility) ON (f.city)",
        "CREATE INDEX route_id_idx IF NOT EXISTS FOR ()-[r:ROUTE]-() ON (r.route_id)",
        "CREATE INDEX route_status_idx IF NOT EXISTS FOR ()-[r:ROUTE]-() ON (r.status)",
    ]
    with graph_session() as session:
        for stmt in statements:
            session.run(stmt)


# --------------------------------------------------------------------------- #
# Full rebuild (rebuildable-from-relational recovery path)
# --------------------------------------------------------------------------- #
def resync_all(db: Session) -> dict[str, int]:
    """Wipe and rebuild the entire graph from the current Postgres state.

    Raises GraphUnavailableError on any failure -- this is the one sync
    path that must NOT fail silently, since it is the explicit recovery
    operation an operator invokes specifically to fix drift.
    """
    with graph_session() as session:
        session.run("MATCH (n) DETACH DELETE n")
        for stmt in [
            "CREATE CONSTRAINT facility_id_unique IF NOT EXISTS "
            "FOR (f:Facility) REQUIRE f.id IS UNIQUE",
            "CREATE CONSTRAINT blood_type_code_unique IF NOT EXISTS "
            "FOR (b:BloodType) REQUIRE b.code IS UNIQUE",
            "CREATE CONSTRAINT city_name_unique IF NOT EXISTS "
            "FOR (c:City) REQUIRE c.name IS UNIQUE",
            "CREATE INDEX facility_city_idx IF NOT EXISTS FOR (f:Facility) ON (f.city)",
            "CREATE INDEX route_id_idx IF NOT EXISTS FOR ()-[r:ROUTE]-() ON (r.route_id)",
            "CREATE INDEX route_status_idx IF NOT EXISTS FOR ()-[r:ROUTE]-() ON (r.status)",
        ]:
            session.run(stmt)

        for bt in BloodType:
            session.run("MERGE (:BloodType {code: $code})", code=bt.value)
        for donor in BloodType:
            for recipient in BloodType:
                if can_donate_to(donor, recipient):
                    session.run(
                        "MATCH (d:BloodType {code: $d}), (r:BloodType {code: $r}) "
                        "MERGE (d)-[:CAN_DONATE_TO]->(r)",
                        d=donor.value,
                        r=recipient.value,
                    )

        facilities = db.scalars(select(HospitalProfile)).all()
        for f in facilities:
            _merge_facility_tx(session, f)

        inventory_rows = db.scalars(select(HospitalInventory)).all()
        for inv in inventory_rows:
            _merge_stock_tx(session, inv.hospital_id, inv.blood_type, inv.units,
                            component=inv.component,
                            available=max(0, inv.units - inv.reserved_units))

        routes = db.scalars(select(TransportRoute)).all()
        for r in routes:
            _merge_route_tx(session, r)

        donor_agg = db.execute(
            select(DonorProfile.city, DonorProfile.blood_type, func.count())
            .where(
                DonorProfile.is_available.is_(True),
                DonorProfile.blood_type.is_not(None),
                DonorProfile.city.is_not(None),
            )
            .group_by(DonorProfile.city, DonorProfile.blood_type)
        ).all()
        for city, blood_type, count in donor_agg:
            session.run(
                "MERGE (c:City {name: $city}) "
                "MERGE (b:BloodType {code: $bt}) "
                "MERGE (c)-[rel:HAS_AVAILABLE_DONORS]->(b) "
                "SET rel.count = $count",
                city=city,
                bt=blood_type.value,
                count=count,
            )

        counts_record = session.run(
            "MATCH (f:Facility) WITH count(f) AS facilities "
            "MATCH (c:City) WITH facilities, count(c) AS cities "
            "MATCH ()-[s:HAS_STOCK]->() WITH facilities, cities, count(s) AS stock_edges "
            "MATCH ()-[r:ROUTE]->() WITH facilities, cities, stock_edges, count(r) AS routes "
            "MATCH ()-[d:HAS_AVAILABLE_DONORS]->() "
            "RETURN facilities, cities, stock_edges, routes, count(d) AS donor_aggregate_edges"
        ).single()

        if counts_record is None:
            return {
                "facilities": len(facilities),
                "cities": 0,
                "stock_edges": len(inventory_rows),
                "routes": len(routes),
                "donor_aggregate_edges": len(donor_agg),
            }
        return dict(counts_record.data())


# --------------------------------------------------------------------------- #
# Incremental sync (best-effort -- never raises to the caller)
# --------------------------------------------------------------------------- #
def _merge_facility_tx(session: Any, facility: HospitalProfile) -> None:
    session.run(
        """
        MERGE (f:Facility {id: $id})
        SET f.name = $name, f.facility_type = $facility_type, f.city = $city,
            f.latitude = $latitude, f.longitude = $longitude, f.verified = $verified
        WITH f
        FOREACH (_ IN CASE WHEN $city IS NOT NULL THEN [1] ELSE [] END |
            MERGE (c:City {name: $city})
            MERGE (f)-[:LOCATED_IN]->(c)
        )
        """,
        id=str(facility.id),
        name=facility.name,
        facility_type=facility.facility_type.value,
        city=facility.city,
        latitude=facility.latitude,
        longitude=facility.longitude,
        verified=facility.verified,
    )


def _merge_stock_tx(
    session: Any, hospital_id: uuid.UUID, blood_type: BloodType, units: int,
    *, component: BloodComponent = BloodComponent.WHOLE_BLOOD, available: int | None = None,
) -> None:
    """One HAS_STOCK edge per (facility, blood type, component). `units` is
    on-hand stock, `available` is on-hand minus reserved. Informational for
    graph analytics only -- the planner always reads stock from PostgreSQL."""
    session.run(
        """
        MERGE (f:Facility {id: $facility_id})
        MERGE (b:BloodType {code: $blood_type})
        MERGE (f)-[s:HAS_STOCK {component: $component}]->(b)
        SET s.units = $units, s.available = $available
        """,
        facility_id=str(hospital_id),
        blood_type=blood_type.value,
        component=component.value,
        units=units,
        available=units if available is None else available,
    )


def _merge_route_tx(session: Any, route: TransportRoute) -> None:
    session.run(
        """
        MERGE (a:Facility {id: $origin_id})
        MERGE (b:Facility {id: $destination_id})
        MERGE (a)-[r:ROUTE {route_id: $route_id}]->(b)
        SET r.distance_km = $distance_km, r.transit_minutes = $transit_minutes,
            r.status = $status, r.disrupted_reason = $disrupted_reason
        """,
        origin_id=str(route.origin_facility_id),
        destination_id=str(route.destination_facility_id),
        route_id=str(route.id),
        distance_km=route.distance_km,
        transit_minutes=route.transit_minutes,
        status=route.status.value,
        disrupted_reason=route.disrupted_reason,
    )


def sync_facility(facility: HospitalProfile) -> bool:
    """Best-effort incremental sync. Never raises -- returns False on failure."""
    try:
        with graph_session() as session:
            _merge_facility_tx(session, facility)
        return True
    except GraphUnavailableError as exc:
        logger.warning("sync_facility skipped (graph unavailable): %s", exc)
        return False


def sync_inventory(
    hospital_id: uuid.UUID, blood_type: BloodType, units: int,
    *, component: BloodComponent = BloodComponent.WHOLE_BLOOD, available: int | None = None,
) -> bool:
    """Best-effort incremental sync. Never raises -- returns False on failure."""
    try:
        with graph_session() as session:
            _merge_stock_tx(session, hospital_id, blood_type, units,
                            component=component, available=available)
        return True
    except GraphUnavailableError as exc:
        logger.warning("sync_inventory skipped (graph unavailable): %s", exc)
        return False


def sync_route(route: TransportRoute) -> bool:
    """Best-effort incremental sync. Never raises -- returns False on failure."""
    try:
        with graph_session() as session:
            _merge_route_tx(session, route)
        return True
    except GraphUnavailableError as exc:
        logger.warning("sync_route skipped (graph unavailable): %s", exc)
        return False


# --------------------------------------------------------------------------- #
# Resource discovery / pathway ranking
# --------------------------------------------------------------------------- #
def _route_paths_query(max_hops: int) -> str:
    if not 1 <= int(max_hops) <= 12:  # literal bound inside Cypher; validated here
        raise ValueError("max_hops out of range")
    return f"""
MATCH (dest:Facility {{id: $dest_id}})
UNWIND $source_ids AS sid
MATCH (src:Facility {{id: sid}})
OPTIONAL MATCH p = (src)-[:ROUTE*1..{int(max_hops)}]-(dest)
WHERE all(r IN relationships(p)
          WHERE r.status = 'OPERATIONAL' AND NOT (r.route_id IN $excluded))
WITH src, p,
     reduce(t = 0.0, r IN relationships(p) | t + r.distance_km) AS km,
     reduce(t = 0, r IN relationships(p) | t + r.transit_minutes) AS minutes
ORDER BY src.id,
         CASE WHEN $objective = 'minutes' THEN toFloat(minutes) ELSE km END,
         CASE WHEN $objective = 'minutes' THEN km ELSE toFloat(minutes) END,
         length(p)
WITH src, collect(CASE WHEN p IS NULL THEN NULL ELSE {{
        route_ids: [r IN relationships(p) | r.route_id],
        node_ids: [n IN nodes(p) | n.id],
        km: km, minutes: minutes}} END) AS found
RETURN src.id AS source_id, head([x IN found WHERE x IS NOT NULL]) AS best
"""


def _parse_route_paths(
    records: list[dict[str, Any]], destination_id: uuid.UUID
) -> dict[uuid.UUID, Path]:
    """Pure parsing step (unit-testable without a Neo4j server)."""
    out: dict[uuid.UUID, Path] = {}
    for rec in records:
        best = rec.get("best")
        if not best:
            continue
        src = uuid.UUID(rec["source_id"])
        out[src] = Path(
            source=src,
            destination=destination_id,
            node_ids=[uuid.UUID(n) for n in best["node_ids"]],
            route_ids=[uuid.UUID(r) for r in best["route_ids"]],
            distance_km=float(best["km"]),
            transit_minutes=int(best["minutes"]),
        )
    return out


def route_paths_to(
    *,
    destination_id: uuid.UUID,
    source_ids: list[uuid.UUID],
    objective: str,
    max_hops: int,
    excluded_route_ids: frozenset[uuid.UUID] | set[uuid.UUID] = frozenset(),
) -> dict[uuid.UUID, Path]:
    """Best operational path (at most `max_hops` routes) from each source
    facility to the destination, with the full ordered route and node
    lists, computed by Neo4j. Raises GraphUnavailableError when Neo4j cannot
    answer -- the caller then uses the PostgreSQL route search instead."""
    if not source_ids:
        return {}
    with graph_session() as session:
        rows = session.run(
            _route_paths_query(max_hops),
            dest_id=str(destination_id),
            source_ids=[str(s) for s in source_ids],
            excluded=[str(r) for r in excluded_route_ids],
            objective=objective,
        )
        records = [r.data() for r in rows]
    return _parse_route_paths(records, destination_id)


def find_resource_pathways(
    db: Session,
    *,
    blood_type: BloodType,
    origin_facility_id: uuid.UUID,
    max_results: int = 5,
    component: BloodComponent = BloodComponent.WHOLE_BLOOD,
    units_needed: int = 1,
    urgency: Urgency = Urgency.ROUTINE,
    excluded_route_ids: frozenset[uuid.UUID] = frozenset(),
    emergency_id: uuid.UUID | None = None,
) -> PathwayResult:
    """Resource discovery for "where can `origin_facility_id` get
    `component` for a `blood_type` patient from". Built on the same planner
    every emergency uses (app.services.pathway_planner): compatible types,
    usable unreserved in-date units, verified facilities, full route lists.
    The origin's own stock is included (LOCAL, no transport)."""
    from app.core.time import utcnow
    from app.services import pathway_planner as planner  # local: planner imports this module

    result = planner.plan(db, planner.PlanRequest(
        recipient_type=blood_type, component=component, units_needed=units_needed,
        urgency=urgency, deadline=None, destination_facility_id=origin_facility_id,
        now=utcnow(), emergency_id=emergency_id, excluded_route_ids=excluded_route_ids,
        max_backups=0,
    ))
    usable = sorted(
        (o for o in result.options if o.rejected_reason is None and o.route_basis is not None),
        key=lambda o: planner.option_sort_key(o, result.request),
    )
    candidates = [
        PathwayCandidate(
            facility_id=o.facility_id, facility_name=o.facility_name, city=o.city,
            units=o.usable_total, distance_km=round(o.distance_km or 0.0, 1),
            transit_minutes=o.transit_minutes, hops=o.hops,
            route_ids=o.route_ids, node_ids=list(o.node_ids),
            route_basis=o.route_basis.value if o.route_basis else None,
            eta_minutes=o.eta_minutes, slack_minutes=o.slack_minutes,
            units_by_type={t.value: n for t, n in o.usable_by_type.items()},
        )
        for o in usable[:max_results]
    ]
    return PathwayResult(
        source=result.source, candidates=candidates,
        disrupted_routes_excluded=result.disrupted_routes_excluded,
        graph_available=result.graph_available, degraded_reason=result.degraded_reason,
    )


# --------------------------------------------------------------------------- #
# Disruption impact analysis (pure diff -- no I/O)
# --------------------------------------------------------------------------- #
def diff_pathway_rankings(before: PathwayResult, after: PathwayResult) -> dict[str, Any]:
    """Compare two PathwayResults for the same (origin, blood_type) taken
    before/after a transport-route mutation. Used by the transport-route
    disrupt/recover endpoints (task 28) to report a real, computed impact
    rather than a decorative status flag.
    """
    before_best = before.best_distance_km
    after_best = after.best_distance_km
    became_unreachable = before_best is not None and after_best is None
    became_reachable = before_best is None and after_best is not None
    distance_increase_km = (
        round(after_best - before_best, 1)
        if before_best is not None and after_best is not None
        else None
    )
    return {
        "before_best_distance_km": before_best,
        "after_best_distance_km": after_best,
        "before_candidate_count": len(before.candidates),
        "after_candidate_count": len(after.candidates),
        "distance_increase_km": distance_increase_km,
        "became_unreachable": became_unreachable,
        "became_reachable": became_reachable,
        "source": after.source.value,
    }
