"""Emergency pathway planner.

Answers one question: *which facilities can get enough compatible, usable
blood of the right component to the patient's facility before the deadline,
by which routes, and what is the backup if that fails?*

What it takes into account (each was a documented gap before this module):

* **Compatibility** -- every blood type whose requested *component* is
  compatible with the patient (``app.core.compatibility``), not only the
  patient's exact type. Units of another component are never considered.
* **Quantity** -- usable units only: ``AVAILABLE`` units (reserved,
  allocated, quarantined, discarded and expired units are excluded by
  state), plus units already reserved for *this* emergency, that will still
  be in date when they arrive (frozen shelf life and, for thawed plasma,
  the post-thaw window). Never ``stock > 0``.
* **Multi-facility fulfilment** -- when no single facility can supply the
  full quantity, legs from several facilities are combined; when even all
  of them together cannot, the plan is PARTIAL and says so.
* **Deadline** -- every leg has an ETA (route transit time + configured
  handling time). A leg that would arrive after the deadline is never part
  of a plan. Plans are classified VIABLE / DEADLINE_RISK / PARTIAL /
  IMPOSSIBLE (``PathwayFeasibility``).
* **Urgency** -- selects a deterministic ranking strategy (``STRATEGIES``);
  urgent and critical requests minimise travel *time*, routine requests
  minimise *distance* and conserve universal-donor stock.
* **Routes** -- each leg keeps its full ordered list of transport routes and
  facilities, so failure handling can match a failed route exactly.
* **Verification** -- only verified facilities are sources, unless an
  audited ADMIN override is active for the emergency.

Path search: Neo4j when configured and reachable (``graph_service``),
otherwise the same bounded shortest-path computation over the
``transport_routes`` table (``app.core.route_graph``). The result always
says which engine produced it. Facilities with stock but no recorded route
get a clearly labelled straight-line estimate and can never be VIABLE when
a deadline is set.

Stock always comes from PostgreSQL (the per-unit ``blood_units`` table is
the source of truth), never from the graph projection.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import compatibility
from app.core.config import settings
from app.core.graph import GraphUnavailableError, graph_enabled
from app.core.route_graph import Edge, Path, shortest_paths_to
from app.models.enums import (
    BloodComponent,
    BloodType,
    GraphPathwaySource,
    PathwayFeasibility,
    RouteBasis,
    TransportRouteStatus,
    Urgency,
    haversine_km,
)
from app.models.hospital import HospitalProfile
from app.models.transport import TransportRoute

ASSUMED_ROAD_SPEED_KMPH = 45.0  # same constant graph_service uses for route estimates


class PlanningError(ValueError):
    """The request cannot be planned at all (e.g. no blood type, unknown
    destination). Distinct from a plan that is simply IMPOSSIBLE."""


# --------------------------------------------------------------------------- #
# Strategies (documented, deterministic)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Strategy:
    name: str
    objective: Literal["minutes", "km"]
    keys: tuple[str, ...]
    description: str


STRATEGIES: dict[Urgency, Strategy] = {
    Urgency.CRITICAL: Strategy(
        name="CRITICAL_FASTEST_FEASIBLE",
        objective="minutes",
        keys=(
            "meets_deadline",
            "full_quantity",
            "eta_minutes",
            "hops",
            "distance_km",
            "facility_id",
        ),
        description=(
            "Deadline-feasible sources first, then those that can supply the full "
            "quantity alone, then the earliest arrival; fewer route hops (fewer "
            "hand-overs that can fail) and shorter distance break ties."
        ),
    ),
    Urgency.URGENT: Strategy(
        name="URGENT_DEADLINE_THEN_TIME",
        objective="minutes",
        keys=(
            "meets_deadline",
            "full_quantity",
            "eta_minutes",
            "distance_km",
            "hops",
            "facility_id",
        ),
        description=(
            "Deadline-feasible and full-quantity sources first, then the earliest "
            "arrival, then the shortest distance, then fewer hops."
        ),
    ),
    Urgency.ROUTINE: Strategy(
        name="ROUTINE_EFFICIENT",
        objective="km",
        keys=(
            "meets_deadline",
            "full_quantity",
            "distance_km",
            "non_identical_units",
            "hops",
            "eta_minutes",
            "facility_id",
        ),
        description=(
            "Deadline-feasible and full-quantity sources first, then the shortest "
            "distance, then sources that can supply the patient's identical type "
            "(conserving universal-donor stock), then fewer hops."
        ),
    ),
}


# --------------------------------------------------------------------------- #
# Inputs / outputs
# --------------------------------------------------------------------------- #
@dataclass
class PlanRequest:
    recipient_type: BloodType
    component: BloodComponent
    units_needed: int
    urgency: Urgency
    deadline: datetime | None
    destination_facility_id: uuid.UUID
    now: datetime
    emergency_id: uuid.UUID | None = None
    excluded_route_ids: frozenset[uuid.UUID] = frozenset()
    excluded_facility_ids: frozenset[uuid.UUID] = frozenset()
    allow_unverified: bool = False
    max_backups: int | None = None
    handling_minutes: int | None = None
    risk_minutes: int | None = None
    max_hops: int | None = None
    use_graph: bool = True

    def resolved(self) -> PlanRequest:
        if self.units_needed < 1:
            raise PlanningError("units_needed must be at least 1")
        self.max_backups = (
            settings.pathway_max_backups if self.max_backups is None else self.max_backups
        )
        self.handling_minutes = (
            settings.pathway_handling_minutes
            if self.handling_minutes is None
            else self.handling_minutes
        )
        self.risk_minutes = (
            settings.pathway_deadline_risk_minutes
            if self.risk_minutes is None
            else self.risk_minutes
        )
        self.max_hops = settings.pathway_max_hops if self.max_hops is None else self.max_hops
        return self


@dataclass
class StepPlan:
    route_id: uuid.UUID
    from_facility_id: uuid.UUID
    to_facility_id: uuid.UUID
    distance_km: float
    transit_minutes: int


@dataclass
class FacilityOption:
    """One candidate source facility, evaluated -- including ones that were
    rejected, so the decision trace shows why."""

    facility_id: uuid.UUID
    facility_name: str | None
    city: str | None
    verified: bool
    route_basis: RouteBasis | None = None
    node_ids: list[uuid.UUID] = field(default_factory=list)
    steps: list[StepPlan] = field(default_factory=list)
    distance_km: float | None = None
    straight_line_km: float | None = None
    transit_minutes: int | None = None
    eta_minutes: int | None = None
    arrival_at: datetime | None = None
    slack_minutes: float | None = None
    usable_by_type: dict[BloodType, int] = field(default_factory=dict)
    rejected_reason: str | None = None

    @property
    def usable_total(self) -> int:
        return sum(self.usable_by_type.values())

    @property
    def hops(self) -> int:
        return len(self.steps)

    @property
    def meets_deadline(self) -> bool:
        return self.slack_minutes is None or self.slack_minutes >= 0

    @property
    def route_ids(self) -> list[uuid.UUID]:
        return [s.route_id for s in self.steps]


@dataclass
class LegPlan:
    option: FacilityOption
    units: int
    units_by_type: dict[BloodType, int]


@dataclass
class CandidatePlan:
    legs: list[LegPlan]
    units_requested: int
    feasibility: PathwayFeasibility
    eta_minutes: int | None
    arrival_at: datetime | None
    slack_minutes: float | None
    explanation: list[str] = field(default_factory=list)

    @property
    def units_planned(self) -> int:
        return sum(leg.units for leg in self.legs)

    @property
    def route_ids(self) -> set[uuid.UUID]:
        return {rid for leg in self.legs for rid in leg.option.route_ids}

    @property
    def facility_ids(self) -> list[uuid.UUID]:
        return [leg.option.facility_id for leg in self.legs]


@dataclass
class PlanResult:
    request: PlanRequest
    source: GraphPathwaySource
    graph_available: bool
    degraded_reason: str | None
    strategy: Strategy
    compatible_types: list[BloodType]
    options: list[FacilityOption]
    primary: CandidatePlan
    backups: list[CandidatePlan]
    disrupted_routes_excluded: int

    @property
    def assumptions(self) -> dict:
        r = self.request
        return {
            "handling_minutes": r.handling_minutes,
            "deadline_risk_minutes": r.risk_minutes,
            "max_hops": r.max_hops,
            "max_backups": r.max_backups,
            "straight_line_speed_kmph": ASSUMED_ROAD_SPEED_KMPH,
            "path_engine": self.source.value,
        }


# --------------------------------------------------------------------------- #
# Data access
# --------------------------------------------------------------------------- #
def modeled_facility_ids(db: Session) -> set[uuid.UUID]:
    """Facilities that appear on at least one recorded transport route (in
    any status) -- i.e. that are part of the modelled network."""
    ids: set[uuid.UUID] = set()
    for a, b in db.execute(
        select(TransportRoute.origin_facility_id, TransportRoute.destination_facility_id)
    ).all():
        ids.add(a)
        ids.add(b)
    return ids


def load_edges(db: Session) -> tuple[list[Edge], int]:
    """Operational routes as graph edges, plus the number of disrupted
    routes that were left out."""
    routes = db.scalars(select(TransportRoute)).all()
    edges = [
        Edge(
            r.id,
            r.origin_facility_id,
            r.destination_facility_id,
            float(r.distance_km),
            int(r.transit_minutes),
        )
        for r in routes
        if r.status == TransportRouteStatus.OPERATIONAL
    ]
    return edges, sum(1 for r in routes if r.status == TransportRouteStatus.DISRUPTED)


_CANDIDATE_SQL = text("""
SELECT bu.hospital_id
FROM blood_units bu
JOIN hospital_profiles hp ON hp.id = bu.hospital_id
JOIN users u ON u.id = hp.user_id
WHERE bu.component = CAST(:component AS blood_component)
  AND bu.blood_type = ANY(CAST(:types AS blood_type[]))
  AND (bu.state = 'AVAILABLE'
       OR (bu.state = 'RESERVED' AND CAST(:em AS uuid) IS NOT NULL
           AND bu.reserved_for_emergency_id = CAST(:em AS uuid)))
  AND (bu.expires_at IS NULL OR bu.expires_at > :now)
  AND (bu.thaw_expires_at IS NULL OR bu.thaw_expires_at > :now)
  AND u.is_active
GROUP BY bu.hospital_id
""")

_USABLE_AT_ARRIVAL_SQL = text("""
WITH cut(hid, cutoff) AS (
    SELECT * FROM unnest(CAST(:hids AS uuid[]), CAST(:cutoffs AS timestamptz[]))
)
SELECT bu.hospital_id, bu.blood_type, count(*)
FROM blood_units bu
JOIN cut ON cut.hid = bu.hospital_id
WHERE bu.component = CAST(:component AS blood_component)
  AND bu.blood_type = ANY(CAST(:types AS blood_type[]))
  AND (bu.state = 'AVAILABLE'
       OR (bu.state = 'RESERVED' AND CAST(:em AS uuid) IS NOT NULL
           AND bu.reserved_for_emergency_id = CAST(:em AS uuid)))
  AND (bu.expires_at IS NULL OR bu.expires_at > cut.cutoff)
  AND (bu.thaw_expires_at IS NULL OR bu.thaw_expires_at > cut.cutoff)
GROUP BY bu.hospital_id, bu.blood_type
""")


def _type_array(types: list[BloodType]) -> list[str]:
    return [t.value for t in types]


# --------------------------------------------------------------------------- #
# Path computation
# --------------------------------------------------------------------------- #
def _paths(
    db: Session,
    req: PlanRequest,
    candidate_ids: list[uuid.UUID],
    edges: list[Edge],
) -> tuple[dict[uuid.UUID, Path], GraphPathwaySource, bool, str | None]:
    objective = STRATEGIES[req.urgency].objective
    degraded: str | None = None
    if req.use_graph and graph_enabled() and not candidate_ids:
        # Nothing to route: Neo4j is not queried, so it is not credited.
        return (
            {},
            GraphPathwaySource.RELATIONAL_ROUTES,
            False,
            "no candidate source facility; no path search was needed",
        )
    if req.use_graph and graph_enabled():
        from app.services import graph_service  # local import: avoids a cycle

        try:
            paths = graph_service.route_paths_to(
                destination_id=req.destination_facility_id,
                source_ids=candidate_ids,
                objective=objective,
                max_hops=req.max_hops,
                excluded_route_ids=req.excluded_route_ids,
            )
            # The graph is a projection; PostgreSQL is authoritative. If the
            # graph returns a route that is not operational in PostgreSQL
            # (projection drift), its answer cannot be trusted.
            operational = {e.route_id for e in edges}
            stale = [rid for p in paths.values() for rid in p.route_ids if rid not in operational]
            if not stale:
                edges_by_id = {e.route_id: e for e in edges}
                for p in paths.values():
                    p.distance_km = round(sum(edges_by_id[r].distance_km for r in p.route_ids), 3)
                    p.transit_minutes = sum(edges_by_id[r].transit_minutes for r in p.route_ids)
                # Audit A8 (2026-10-31): checking only the RETURNED routes
                # cannot notice a stale projection that is missing a newer,
                # better route (or a whole reachable source). The operational
                # edges are already loaded from PostgreSQL, so the authoritative
                # hop-bounded optimum is cheap to compute here; the graph answer
                # is used only if it is not worse for any candidate source.
                missed = _graph_missed_better_paths(paths, edges, req, candidate_ids, objective)
                if not missed:
                    return paths, GraphPathwaySource.GRAPH, True, None
                degraded = (
                    "graph projection is out of date with PostgreSQL "
                    f"({missed} source(s) have a better or missing path in PostgreSQL); "
                    "routes recomputed from PostgreSQL"
                )
            else:
                degraded = (
                    "graph projection is out of date with PostgreSQL "
                    f"({len(stale)} non-operational route(s)); routes recomputed from PostgreSQL"
                )
        except GraphUnavailableError as exc:
            degraded = str(exc)
    elif not graph_enabled():
        degraded = "Neo4j is not configured; routes computed from PostgreSQL"
    paths = shortest_paths_to(
        edges,
        req.destination_facility_id,
        max_hops=req.max_hops,
        objective=objective,
        excluded_route_ids=req.excluded_route_ids,
    )
    return paths, GraphPathwaySource.RELATIONAL_ROUTES, False, degraded


def _graph_missed_better_paths(paths, edges, req, candidate_ids, objective) -> int:
    """How many candidate sources the graph answer serves worse than the
    authoritative PostgreSQL routes (missing, or costlier by > 1e-6)."""
    truth = shortest_paths_to(
        edges, req.destination_facility_id, max_hops=req.max_hops, objective=objective,
        excluded_route_ids=req.excluded_route_ids,
    )

    def cost(p):
        return (p.transit_minutes, p.distance_km) if objective == "minutes" else (
            p.distance_km, p.transit_minutes)

    missed = 0
    for sid in candidate_ids:
        t = truth.get(sid)
        if t is None:
            continue
        g = paths.get(sid)
        if g is None or cost(g)[0] > cost(t)[0] + 1e-6:
            missed += 1
    return missed


def _steps_from_path(path: Path, edges_by_id: dict[uuid.UUID, Edge]) -> list[StepPlan]:
    steps = []
    for i, rid in enumerate(path.route_ids):
        e = edges_by_id[rid]
        steps.append(
            StepPlan(rid, path.node_ids[i], path.node_ids[i + 1], e.distance_km, e.transit_minutes)
        )
    return steps


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #
def _non_identical_units(opt: FacilityOption, req: PlanRequest) -> int:
    """Units this facility would have to supply from a non-identical type
    to cover the request (0 when its identical-type stock suffices)."""
    identical = opt.usable_by_type.get(req.recipient_type, 0)
    return max(0, min(req.units_needed, opt.usable_total) - identical)


def option_sort_key(opt: FacilityOption, req: PlanRequest) -> tuple:
    strategy = STRATEGIES[req.urgency]
    values = {
        "meets_deadline": 0 if opt.meets_deadline else 1,
        "full_quantity": 0 if opt.usable_total >= req.units_needed else 1,
        "eta_minutes": opt.eta_minutes if opt.eta_minutes is not None else 10**9,
        "hops": opt.hops,
        "distance_km": round(opt.distance_km if opt.distance_km is not None else 1e12, 6),
        "non_identical_units": _non_identical_units(opt, req),
        "facility_id": str(opt.facility_id),
    }
    # A straight-line estimate always ranks after a recorded route with the
    # same leading keys -- its time is not something the system knows.
    estimate_penalty = 1 if opt.route_basis == RouteBasis.STRAIGHT_LINE_ESTIMATE else 0
    key = [values[k] for k in strategy.keys]
    key.insert(2, estimate_penalty)
    return tuple(key)


def _take_units(opt: FacilityOption, req: PlanRequest, wanted: int) -> dict[BloodType, int]:
    """Choose which compatible types a leg takes, in preference order
    (identical type first) -- never a type outside the compatible list."""
    taken: dict[BloodType, int] = {}
    remaining = wanted
    for t in compatibility.compatible_source_types(req.recipient_type, req.component):
        have = opt.usable_by_type.get(t, 0)
        if have <= 0 or remaining <= 0:
            continue
        n = min(have, remaining)
        taken[t] = n
        remaining -= n
    return taken


# --------------------------------------------------------------------------- #
# Plan assembly
# --------------------------------------------------------------------------- #
def _fmt(m: float | int | None) -> str:
    return "n/a" if m is None else f"{m:.0f}"


def _classify(
    req: PlanRequest, legs: list[LegPlan]
) -> tuple[PathwayFeasibility, int | None, datetime | None, float | None]:
    if req.deadline is not None and req.deadline <= req.now:
        return PathwayFeasibility.IMPOSSIBLE, None, None, None
    planned = sum(leg.units for leg in legs)
    if planned == 0:
        return PathwayFeasibility.IMPOSSIBLE, None, None, None
    eta = max(leg.option.eta_minutes or 0 for leg in legs)
    arrival = req.now + timedelta(minutes=eta)
    slack = None
    if req.deadline is not None:
        slack = round((req.deadline - arrival).total_seconds() / 60.0, 2)
    if planned < req.units_needed:
        return PathwayFeasibility.PARTIAL, eta, arrival, slack
    estimated = any(leg.option.route_basis == RouteBasis.STRAIGHT_LINE_ESTIMATE for leg in legs)
    if req.deadline is not None and (estimated or (slack is not None and slack < req.risk_minutes)):
        return PathwayFeasibility.DEADLINE_RISK, eta, arrival, slack
    return PathwayFeasibility.VIABLE, eta, arrival, slack


def _explain(
    req: PlanRequest, plan: CandidatePlan, strategy: Strategy, names: dict[uuid.UUID, str]
) -> list[str]:
    lines: list[str] = []
    rt, comp = req.recipient_type.value, req.component.value
    lines.append(
        f"Request: {req.units_needed} unit(s) of {comp} for a {rt} patient, urgency "
        f"{req.urgency.value}"
        + (f", deadline {req.deadline.isoformat()}" if req.deadline else ", no deadline")
        + "."
    )
    lines.append(f"Ranked by {strategy.name}: {strategy.description}")
    for i, leg in enumerate(plan.legs, start=1):
        o = leg.option
        types = ", ".join(f"{n} x {t.value}" for t, n in leg.units_by_type.items())
        why = "; ".join(
            compatibility.explain(t, req.recipient_type, req.component) for t in leg.units_by_type
        )
        lines.append(
            f"Leg {i}: {names.get(o.facility_id, str(o.facility_id))} supplies "
            f"{leg.units} unit(s) ({types}). Compatible because: {why}."
        )
        if o.route_basis == RouteBasis.LOCAL:
            lines.append(
                f"Leg {i}: stock is already at the destination; only "
                f"{req.handling_minutes} min handling."
            )
        elif o.route_basis == RouteBasis.ROUTED:
            path = " -> ".join(names.get(n, str(n)[:8]) for n in o.node_ids)
            lines.append(
                f"Leg {i}: route {path} ({o.hops} route(s), {o.distance_km:.1f} km, "
                f"{o.transit_minutes} min transit + {req.handling_minutes} min "
                f"handling = {o.eta_minutes} min)."
            )
        else:
            lines.append(
                f"Leg {i}: NO recorded transport route -- {o.straight_line_km:.1f} km "
                f"straight-line estimate at {ASSUMED_ROAD_SPEED_KMPH:.0f} km/h "
                f"({o.eta_minutes} min). Treated as a deadline risk."
            )
    if plan.eta_minutes is not None:
        tail = (
            f", {_fmt(plan.slack_minutes)} min before the deadline"
            if plan.slack_minutes is not None
            else ""
        )
        lines.append(f"Expected arrival in {plan.eta_minutes} min{tail}.")
    lines.append(
        f"Feasibility: {plan.feasibility.value} -- "
        f"{plan.units_planned} of {plan.units_requested} unit(s) planned."
    )
    return lines


def _make_plan(
    req: PlanRequest, legs: list[LegPlan], strategy: Strategy, names: dict[uuid.UUID, str]
) -> CandidatePlan:
    feas, eta, arrival, slack = _classify(req, legs)
    plan = CandidatePlan(
        legs=legs,
        units_requested=req.units_needed,
        feasibility=feas,
        eta_minutes=eta,
        arrival_at=arrival,
        slack_minutes=slack,
    )
    plan.explanation = _explain(req, plan, strategy, names)
    return plan


def _greedy(
    req: PlanRequest, ordered: list[FacilityOption], exclude: set[uuid.UUID]
) -> list[LegPlan]:
    legs: list[LegPlan] = []
    remaining = req.units_needed
    for opt in ordered:
        if remaining <= 0:
            break
        if opt.facility_id in exclude or not opt.meets_deadline or opt.usable_total <= 0:
            continue
        take = min(opt.usable_total, remaining)
        legs.append(LegPlan(opt, take, _take_units(opt, req, take)))
        remaining -= take
    return legs


def plan(db: Session, req: PlanRequest) -> PlanResult:
    """Plan pathways for `req`. Read-only: never reserves or writes anything."""
    req = req.resolved()
    strategy = STRATEGIES[req.urgency]
    dest = db.get(HospitalProfile, req.destination_facility_id)
    if dest is None:
        raise PlanningError("Destination facility not found")
    compat = compatibility.compatible_source_types(req.recipient_type, req.component)

    rows = db.execute(
        _CANDIDATE_SQL,
        {
            "component": req.component.value,
            "types": _type_array(compat),
            "em": str(req.emergency_id) if req.emergency_id else None,
            "now": req.now,
        },
    ).all()
    candidate_ids = [r[0] for r in rows if r[0] not in req.excluded_facility_ids]
    facilities = {
        f.id: f
        for f in db.scalars(
            select(HospitalProfile).where(HospitalProfile.id.in_(candidate_ids + [dest.id]))
        ).all()
    }
    names = {fid: (f.name or str(fid)[:8]) for fid, f in facilities.items()}

    options: list[FacilityOption] = []
    eligible_ids: list[uuid.UUID] = []
    for fid in candidate_ids:
        f = facilities[fid]
        opt = FacilityOption(
            facility_id=fid, facility_name=f.name, city=f.city, verified=bool(f.verified)
        )
        if not f.verified and not req.allow_unverified:
            opt.rejected_reason = "facility is not verified"
        else:
            eligible_ids.append(fid)
        options.append(opt)

    edges, disrupted = load_edges(db)
    edges_by_id = {e.route_id: e for e in edges}
    modeled = modeled_facility_ids(db)
    paths, source, graph_ok, degraded = _paths(
        db,
        req,
        [i for i in eligible_ids if i != dest.id],
        edges,
    )

    by_id = {o.facility_id: o for o in options}
    timed: list[FacilityOption] = []
    for fid in eligible_ids:
        opt = by_id[fid]
        f = facilities[fid]
        if fid == dest.id:
            opt.route_basis = RouteBasis.LOCAL
            opt.node_ids = [fid]
            opt.distance_km = 0.0
            opt.transit_minutes = 0
        elif fid in paths and paths[fid].route_ids:
            p = paths[fid]
            opt.route_basis = RouteBasis.ROUTED
            opt.node_ids = list(p.node_ids)
            opt.steps = _steps_from_path(p, edges_by_id)
            opt.distance_km = round(p.distance_km, 2)
            opt.transit_minutes = int(p.transit_minutes)
        elif fid in modeled and dest.id in modeled:
            # Both facilities are on the modelled transport network, so the
            # network is authoritative: no operational path means no
            # delivery, never a straight-line guess that would make every
            # disruption meaningless.
            opt.rejected_reason = (
                "both facilities are on the transport network and no operational "
                f"route within {req.max_hops} hops connects them"
                + (" (some routes are disrupted)" if disrupted else "")
            )
            continue
        elif None not in (f.latitude, f.longitude, dest.latitude, dest.longitude):
            # At least one side has no recorded transport routes at all:
            # estimate from coordinates, clearly labelled as an estimate
            # (with a deadline, never classified better than DEADLINE_RISK).
            km = haversine_km(f.latitude, f.longitude, dest.latitude, dest.longitude)
            opt.route_basis = RouteBasis.STRAIGHT_LINE_ESTIMATE
            opt.node_ids = [fid, dest.id]
            opt.distance_km = km
            opt.transit_minutes = max(1, round(km / ASSUMED_ROAD_SPEED_KMPH * 60))
        else:
            opt.rejected_reason = (
                "no operational transport route within "
                f"{req.max_hops} hops and no coordinates to estimate one"
            )
            continue
        if None not in (f.latitude, f.longitude, dest.latitude, dest.longitude):
            opt.straight_line_km = haversine_km(
                f.latitude, f.longitude, dest.latitude, dest.longitude
            )
        opt.eta_minutes = int(req.handling_minutes) + int(opt.transit_minutes or 0)
        opt.arrival_at = req.now + timedelta(minutes=opt.eta_minutes)
        if req.deadline is not None:
            opt.slack_minutes = round((req.deadline - opt.arrival_at).total_seconds() / 60.0, 2)
        timed.append(opt)

    if timed:
        counts = db.execute(
            _USABLE_AT_ARRIVAL_SQL,
            {
                "hids": [str(o.facility_id) for o in timed],
                "cutoffs": [o.arrival_at for o in timed],
                "component": req.component.value,
                "types": _type_array(compat),
                "em": str(req.emergency_id) if req.emergency_id else None,
            },
        ).all()
        for hid, bt, n in counts:
            bt = BloodType(bt) if not isinstance(bt, BloodType) else bt
            by_id[hid].usable_by_type[bt] = int(n)
    for opt in timed:
        if opt.usable_total == 0:
            opt.rejected_reason = "no compatible unit would still be in date on arrival"
        elif not opt.meets_deadline:
            opt.rejected_reason = (
                f"would arrive {abs(opt.slack_minutes or 0):.0f} min after the deadline"
            )

    usable = sorted(
        [o for o in timed if o.rejected_reason is None], key=lambda o: option_sort_key(o, req)
    )
    deadline_passed = req.deadline is not None and req.deadline <= req.now

    singles = (
        []
        if deadline_passed
        else [
            _make_plan(
                req,
                [LegPlan(o, req.units_needed, _take_units(o, req, req.units_needed))],
                strategy,
                names,
            )
            for o in usable
            if o.usable_total >= req.units_needed
        ]
    )
    if singles:
        primary = singles[0]
    else:
        legs = [] if deadline_passed else _greedy(req, usable, set())
        primary = _make_plan(req, legs, strategy, names)
        if deadline_passed:
            primary.explanation.append(
                "The deadline has already passed; nothing can arrive in time."
            )
        elif not legs:
            primary.explanation.append(
                "No verified facility can deliver a compatible, in-date unit before the "
                "deadline over an operational route."
            )

    backups: list[CandidatePlan] = []
    if primary.legs and req.max_backups:
        primary_routes = primary.route_ids
        primary_facilities = set(primary.facility_ids)
        alt_singles = [
            p for p in singles[1:] if p.legs[0].option.facility_id not in primary_facilities
        ]
        # Route-disjoint backups first: a backup that shares a route with
        # the primary fails together with it.
        alt_singles.sort(
            key=lambda p: (
                1 if p.route_ids & primary_routes else 0,
                option_sort_key(p.legs[0].option, req),
            )
        )
        backups.extend(alt_singles[: req.max_backups])
        if len(backups) < req.max_backups:
            legs = _greedy(req, usable, primary_facilities)
            if legs and (
                sum(leg.units for leg in legs) >= req.units_needed
                or primary.feasibility == PathwayFeasibility.PARTIAL
            ):
                alt = _make_plan(req, legs, strategy, names)
                if not any(set(alt.facility_ids) == set(b.facility_ids) for b in backups):
                    backups.append(alt)

    return PlanResult(
        request=req,
        source=source,
        graph_available=graph_ok,
        degraded_reason=degraded,
        strategy=strategy,
        compatible_types=compat,
        options=options,
        primary=primary,
        backups=backups[: req.max_backups],
        disrupted_routes_excluded=disrupted,
    )


__all__ = [
    "STRATEGIES",
    "CandidatePlan",
    "FacilityOption",
    "LegPlan",
    "PlanRequest",
    "PlanResult",
    "PlanningError",
    "StepPlan",
    "Strategy",
    "plan",
    "option_sort_key",
]
