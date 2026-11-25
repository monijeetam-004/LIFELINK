from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import TransportRouteStatus


class TransportRoute(TimestampMixin, Base):
    """An undirected transport link between two facilities.

    This is the relational source of truth the graph engine mirrors as a
    Neo4j `ROUTE` relationship (app/services/graph_service.py). Distance
    and transit time are computed once at creation (haversine, or a
    provided real-world figure) and stored here rather than recomputed
    ad hoc, so relational queries, the graph mirror, and the haversine
    fallback path all agree on the same numbers.
    """

    __tablename__ = "transport_routes"
    __table_args__ = (
        UniqueConstraint(
            "origin_facility_id", "destination_facility_id", name="uq_transport_route_pair"
        ),
        CheckConstraint(
            "origin_facility_id <> destination_facility_id", name="ck_transport_route_distinct"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    origin_facility_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    destination_facility_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    distance_km: Mapped[float] = mapped_column(Float, nullable=False)
    transit_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[TransportRouteStatus] = mapped_column(
        Enum(TransportRouteStatus, name="transport_route_status"),
        nullable=False,
        server_default=TransportRouteStatus.OPERATIONAL.value,
        index=True,
    )
    disrupted_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    disrupted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
