"""SMS provider integration: sms_notifications audit table

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-09-11
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: str | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SMS_STATUS = ("PENDING", "SENT", "FAILED")
_SMS_FAILURE_CATEGORY = (
    "TIMEOUT",
    "PROVIDER_5XX",
    "RATE_LIMITED",
    "PROVIDER_4XX",
    "AUTH_FAILURE",
    "INVALID_RECIPIENT",
    "INVALID_TEMPLATE",
    "PROVIDER_NOT_CONFIGURED",
    "NO_PHONE_ON_FILE",
    "UNKNOWN",
)
_SMS_TEMPLATE_KEY = (
    "EMERGENCY_CREATED",
    "EMERGENCY_STATUS_CHANGED",
    "DONOR_MATCH_ALERT",
    "HOSPITAL_REQUEST_UPDATE",
    "FAMILY_FILED_REQUEST",
    "BLOOD_REQUEST_FULFILLED",
    "BLOOD_REQUEST_CANCELLED",
)


def upgrade() -> None:
    # Brand-new enum types on a brand-new table: declared inline (no
    # explicit pre-create step) so op.create_table's own DDL compiler
    # creates each CREATE TYPE exactly once, alongside CREATE TABLE -- the
    # same pattern already used for e.g. `pathway_source` and transport
    # route `status` in the phase-8 graph-engine migration. Explicit
    # pre-create + create_type=False (used elsewhere in this codebase) is
    # only needed when a type is *reused* across an ALTER on an existing
    # table, which isn't the case here.
    op.create_table(
        "sms_notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "notification_id",
            sa.Uuid(),
            sa.ForeignKey("notifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recipient_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("template_key", sa.Enum(*_SMS_TEMPLATE_KEY, name="sms_template_key"), nullable=False),
        sa.Column("recipient_phone_masked", sa.String(20), nullable=True),
        sa.Column("channel", sa.String(10), nullable=False, server_default="SMS"),
        sa.Column("provider", sa.String(20), nullable=False, server_default="MSG91"),
        sa.Column(
            "status", sa.Enum(*_SMS_STATUS, name="sms_status"), nullable=False,
            server_default="PENDING",
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_message_id", sa.String(120), nullable=True),
        sa.Column(
            "failure_category",
            sa.Enum(*_SMS_FAILURE_CATEGORY, name="sms_failure_category"),
            nullable=True,
        ),
        sa.Column("failure_detail", sa.String(300), nullable=True),
        sa.Column("related_type", sa.String(30), nullable=True),
        sa.Column("related_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("notification_id", name="uq_sms_notification_per_notification"),
    )
    op.create_index(
        "ix_sms_notifications_notification_id", "sms_notifications", ["notification_id"]
    )
    op.create_index(
        "ix_sms_notifications_recipient_user_id", "sms_notifications", ["recipient_user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_sms_notifications_recipient_user_id", table_name="sms_notifications")
    op.drop_index("ix_sms_notifications_notification_id", table_name="sms_notifications")
    op.drop_table("sms_notifications")
    sa.Enum(name="sms_template_key").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="sms_failure_category").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="sms_status").drop(op.get_bind(), checkfirst=True)
