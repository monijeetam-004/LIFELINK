from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import BloodType, GraphPathwaySource


class GraphPathwayLog(TimestampMixin, Base):
    """Audit trail of every resource-pathway query the graph engine ran.

    This is what makes pathway discovery explainable/auditable rather than
    a black box: it records who asked, what for, which engine actually
    answered (real Neo4j traversal vs. the haversine fallback used when
    Neo4j is unreachable), and the result shape -- satisfying the
    "explanation/audit" and "recovery metrics" requirements for the
    resource-pathway flow without duplicating the request/response bodies
    themselves.
    """

    __tablename__ = "graph_pathway_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # NULL = system-initiated (automatic failover/replanning with no human
    # actor, e.g. the deadline monitor or a facility revocation cascade).
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    emergency_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("emergencies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    origin_facility_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    blood_type: Mapped[BloodType] = mapped_column(
        Enum(BloodType, name="blood_type"), nullable=False
    )
    pathway_source: Mapped[GraphPathwaySource] = mapped_column(
        Enum(GraphPathwaySource, name="graph_pathway_source"), nullable=False, index=True
    )
    best_distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    disrupted_routes_excluded: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    graph_available: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
