from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import AppointmentStatus


class DonorAppointment(TimestampMixin, Base):
    """A donor's self-requested donation appointment ("pre-schedule").

    hospital_id is nullable: a donor can request an appointment before a
    facility confirms it, and can name a facility that isn't yet a registered
    HospitalProfile (see hospital_name). Confirming/cancelling is a real state
    transition, not a status label with no effect -- see
    app/api/routes/donors.py appointment endpoints.
    """

    __tablename__ = "donor_appointments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    donor_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    hospital_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    hospital_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    preferred_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus, name="appointment_status"),
        nullable=False,
        server_default=AppointmentStatus.REQUESTED.value,
        index=True,
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
