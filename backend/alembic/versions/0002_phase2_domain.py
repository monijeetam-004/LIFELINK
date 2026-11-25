"""phase 2 domain: profiles, family links, blood requests, emergencies, notifications, donations

Revision ID: 0002_phase2
Revises: 0001_initial
Create Date: 2026-09-06
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_phase2"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

blood_type = postgresql.ENUM(
    "A_POS", "A_NEG", "B_POS", "B_NEG", "AB_POS", "AB_NEG", "O_POS", "O_NEG",
    name="blood_type", create_type=False,
)
urgency = postgresql.ENUM("ROUTINE", "URGENT", "CRITICAL", name="urgency", create_type=False)
blood_request_status = postgresql.ENUM(
    "PENDING", "MATCHING", "FULFILLED", "CANCELLED", "EXPIRED",
    name="blood_request_status", create_type=False,
)
donor_response_status = postgresql.ENUM(
    "ACCEPTED", "DECLINED", name="donor_response_status", create_type=False
)
emergency_status = postgresql.ENUM(
    "ACTIVE", "ACKNOWLEDGED", "RESOLVED", "CANCELLED", name="emergency_status", create_type=False
)
family_link_status = postgresql.ENUM(
    "PENDING", "ACCEPTED", "REVOKED", name="family_link_status", create_type=False
)

_ALL_ENUMS = (
    blood_type, urgency, blood_request_status, donor_response_status,
    emergency_status, family_link_status,
)


def _ts() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )


def upgrade() -> None:
    bind = op.get_bind()
    for e in _ALL_ENUMS:
        e.create(bind, checkfirst=True)

    op.create_table(
        "patient_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("blood_type", blood_type, nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("phone", sa.String(40), nullable=True),
        sa.Column("address", sa.String(300), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("medical_notes", sa.String(2000), nullable=True),
        sa.Column("emergency_contact_name", sa.String(200), nullable=True),
        sa.Column("emergency_contact_phone", sa.String(40), nullable=True),
        *_ts(),
    )
    op.create_index("ix_patient_profiles_user_id", "patient_profiles", ["user_id"], unique=True)
    op.create_index("ix_patient_profiles_city", "patient_profiles", ["city"])

    op.create_table(
        "donor_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("blood_type", blood_type, nullable=True),
        sa.Column("phone", sa.String(40), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("last_donation_date", sa.Date(), nullable=True),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("availability_note", sa.String(300), nullable=True),
        sa.Column("weight_kg", sa.Integer(), nullable=True),
        sa.Column("medical_conditions", sa.String(2000), nullable=True),
        *_ts(),
    )
    op.create_index("ix_donor_profiles_user_id", "donor_profiles", ["user_id"], unique=True)
    op.create_index("ix_donor_profiles_city", "donor_profiles", ["city"])
    op.create_index("ix_donor_profiles_blood_type", "donor_profiles", ["blood_type"])

    op.create_table(
        "family_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("family_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("patient_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("relationship", sa.String(60), nullable=False),
        sa.Column("status", family_link_status, nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        *_ts(),
        sa.UniqueConstraint("family_user_id", "patient_user_id", name="uq_family_link_pair"),
    )
    op.create_index("ix_family_links_family_user_id", "family_links", ["family_user_id"])
    op.create_index("ix_family_links_patient_user_id", "family_links", ["patient_user_id"])

    op.create_table(
        "blood_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("requester_user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("patient_user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("blood_type", blood_type, nullable=False),
        sa.Column("units_needed", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("urgency", urgency, nullable=False),
        sa.Column("status", blood_request_status, nullable=False),
        sa.Column("hospital_name", sa.String(200), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("note", sa.String(1000), nullable=True),
        sa.Column("needed_by", sa.Date(), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
    )
    op.create_index("ix_blood_requests_requester_user_id", "blood_requests", ["requester_user_id"])
    op.create_index("ix_blood_requests_patient_user_id", "blood_requests", ["patient_user_id"])
    op.create_index("ix_blood_requests_blood_type", "blood_requests", ["blood_type"])
    op.create_index("ix_blood_requests_status", "blood_requests", ["status"])
    op.create_index("ix_blood_requests_city", "blood_requests", ["city"])

    op.create_table(
        "blood_request_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(),
                  sa.ForeignKey("blood_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("note", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_blood_request_events_request_id", "blood_request_events", ["request_id"])
    op.create_index("ix_blood_request_events_created_at", "blood_request_events", ["created_at"])

    op.create_table(
        "donor_responses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(),
                  sa.ForeignKey("blood_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("donor_user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", donor_response_status, nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        *_ts(),
        sa.UniqueConstraint("request_id", "donor_user_id", name="uq_donor_response_pair"),
    )
    op.create_index("ix_donor_responses_request_id", "donor_responses", ["request_id"])
    op.create_index("ix_donor_responses_donor_user_id", "donor_responses", ["donor_user_id"])

    op.create_table(
        "emergencies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_by_user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("patient_user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("blood_type", blood_type, nullable=True),
        sa.Column("urgency", urgency, nullable=False),
        sa.Column("status", emergency_status, nullable=False),
        sa.Column("location_text", sa.String(300), nullable=True),
        sa.Column("note", sa.String(1000), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        *_ts(),
    )
    op.create_index("ix_emergencies_created_by_user_id", "emergencies", ["created_by_user_id"])
    op.create_index("ix_emergencies_status", "emergencies", ["status"])

    op.create_table(
        "emergency_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("emergency_id", sa.Uuid(),
                  sa.ForeignKey("emergencies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("note", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_emergency_events_emergency_id", "emergency_events", ["emergency_id"])
    op.create_index("ix_emergency_events_created_at", "emergency_events", ["created_at"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(60), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.String(1000), nullable=True),
        sa.Column("data", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])

    op.create_table(
        "donations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("donor_user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("blood_request_id", sa.Uuid(),
                  sa.ForeignKey("blood_requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("units", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("donated_on", sa.Date(), nullable=False),
        sa.Column("hospital_name", sa.String(200), nullable=True),
        sa.Column("note", sa.String(1000), nullable=True),
        *_ts(),
    )
    op.create_index("ix_donations_donor_user_id", "donations", ["donor_user_id"])
    op.create_index("ix_donations_blood_request_id", "donations", ["blood_request_id"])


def downgrade() -> None:
    for t in (
        "donations", "notifications", "emergency_events", "emergencies",
        "donor_responses", "blood_request_events", "blood_requests",
        "family_links", "donor_profiles", "patient_profiles",
    ):
        op.drop_table(t)
    for e in reversed(_ALL_ENUMS):
        e.drop(op.get_bind(), checkfirst=True)
