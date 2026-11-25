from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import BloodType, GraphPathwaySource, TransportRouteStatus


class TransportRouteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    origin_facility_id: uuid.UUID
    destination_facility_id: uuid.UUID
    distance_km: float
    transit_minutes: int
    status: TransportRouteStatus
    disrupted_reason: str | None
    disrupted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TransportRouteCreateIn(BaseModel):
    origin_facility_id: uuid.UUID
    destination_facility_id: uuid.UUID
    # Both optional: when omitted, distance is computed for real from the two
    # facilities' recorded coordinates (haversine_km) rather than faked, and
    # transit_minutes is then estimated from that real distance at an
    # assumed average road speed -- see _estimate_transit_minutes in
    # app/api/routes/admin.py. Both are clearly labelled as estimates when
    # derived rather than supplied.
    distance_km: float | None = Field(default=None, gt=0, le=5000)
    transit_minutes: int | None = Field(default=None, gt=0, le=100_000)


class TransportRouteDisruptIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class DisruptionImpactOut(BaseModel):
    before_best_distance_km: float | None
    after_best_distance_km: float | None
    before_candidate_count: int
    after_candidate_count: int
    distance_increase_km: float | None
    became_unreachable: bool
    became_reachable: bool
    source: GraphPathwaySource


class EmergencyReoptimizationOut(BaseModel):
    """Phase 12: one emergency whose ACTIVE resource pathway was
    automatically re-evaluated because this route just became DISRUPTED
    (see app.services.emergency_pathway_service.handle_route_disrupted).
    `still_reachable=False` is a real, honestly-reported outcome -- it is
    never hidden or turned into a fabricated success."""

    emergency_id: uuid.UUID
    previous_version: int
    new_version: int
    still_reachable: bool
    best_distance_km: float | None
    # Gap elimination: what actually happened -- BACKUP_PROMOTED (a stored
    # backup was re-validated and promoted), REPLANNED (no backup survived;
    # a fresh plan was made) or BACKUPS_INVALIDATED (only backup pathways
    # used the failed route; the ACTIVE plan was untouched).
    kind: str = "REPLANNED"
    invalidated_backups: list[int] = []
    rejected_backups: dict[int, list[str]] = {}


class TransportRouteActionOut(BaseModel):
    route: TransportRouteOut
    # None when the impact could not be honestly computed (e.g. the route's
    # own facilities have no recorded blood-type stock to probe with) --
    # never a fabricated zero-impact result.
    sample_blood_type: BloodType | None
    impact: DisruptionImpactOut | None
    graph_synced: bool
    # Only ever populated by the disrupt action (recover never re-optimizes
    # anything -- a route coming back doesn't force any emergency off its
    # current, still-valid plan). Empty list, not None, when disrupting
    # this route genuinely affected no open emergency's current pathway.
    affected_emergencies: list[EmergencyReoptimizationOut] = []


class PathwayCandidateOut(BaseModel):
    facility_id: uuid.UUID
    facility_name: str | None
    city: str | None
    units: int
    distance_km: float
    transit_minutes: int | None
    hops: int | None
    # Gap elimination: the complete route and how the distance was obtained.
    route_basis: str | None = None  # LOCAL | ROUTED | STRAIGHT_LINE_ESTIMATE
    route_ids: list[uuid.UUID] = []
    node_ids: list[uuid.UUID] = []
    eta_minutes: int | None = None
    slack_minutes: float | None = None
    units_by_type: dict[str, int] = {}


class ResourcePathwayOut(BaseModel):
    blood_type: BloodType
    origin_facility_id: uuid.UUID
    pathway_source: GraphPathwaySource
    graph_available: bool
    disrupted_routes_excluded: int
    degraded_reason: str | None
    candidates: list[PathwayCandidateOut]
    component: str | None = None
    units_needed: int | None = None


class GraphResyncOut(BaseModel):
    facilities: int
    cities: int
    stock_edges: int
    routes: int
    donor_aggregate_edges: int


class GraphPathwayLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    requested_by_user_id: uuid.UUID | None
    emergency_id: uuid.UUID | None
    origin_facility_id: uuid.UUID | None
    blood_type: BloodType
    pathway_source: GraphPathwaySource
    best_distance_km: float | None
    candidate_count: int
    disrupted_routes_excluded: int
    graph_available: bool
    created_at: datetime


class GraphPathwayLogListOut(BaseModel):
    items: list[GraphPathwayLogOut]
    next_cursor: str | None
