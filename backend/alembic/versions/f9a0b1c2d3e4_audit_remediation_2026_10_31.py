"""Audit remediation (2026-10-31): shortlist contacts, plan-to-shipment
hand-off, failover recovery state, donor location/availability timestamps.

Revision ID: f9a0b1c2d3e4
Revises: e1f2a3b4c5d6
Create Date: 2026-10-31

All additive; no existing column changes meaning.

* donor_contact_attempts (new): one row per donor actually contacted from a
  confidence shortlist, with its response lifecycle;
* donor_profiles.location_updated_at / availability_confirmed_at: NULL for
  existing rows (age unknown -- never back-filled with a guess);
* emergency_pathways.recovery_required_at / _reason (audit C15);
* shipments.emergency_id / pathway_id and
  pathway_reservations.handed_off_shipment_id (audit C21/C20);
* hospital_inventory_action gains TRANSFER (ALTER TYPE ... ADD VALUE; the
  label stays on downgrade, which is harmless -- no row of the older schema
  uses it).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'f9a0b1c2d3e4'
down_revision: str | None = 'e1f2a3b4c5d6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TYPE hospital_inventory_action ADD VALUE IF NOT EXISTS 'TRANSFER'"))
    op.create_table('donor_contact_attempts',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('subject', sa.Enum('BLOOD_REQUEST', 'EMERGENCY', name='shortlist_subject'), nullable=False),
    sa.Column('blood_request_id', sa.Uuid(), nullable=True),
    sa.Column('emergency_id', sa.Uuid(), nullable=True),
    sa.Column('donor_user_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.Enum('QUEUED', 'AWAITING_RESPONSE', 'NOTIFICATION_FAILED', 'ACCEPTED', 'DECLINED', 'UNAVAILABLE', 'TIMED_OUT', 'ELIGIBILITY_RECHECK_REQUIRED', 'CONFIRMED_FOR_COORDINATION', 'WITHDRAWN', name='donor_contact_status'), nullable=False),
    sa.Column('batch', sa.Integer(), nullable=False),
    sa.Column('rank_at_contact', sa.Integer(), nullable=False),
    sa.Column('score_at_contact', sa.Float(), nullable=False),
    sa.Column('notification_id', sa.Uuid(), nullable=True),
    sa.Column('contacted_by_user_id', sa.Uuid(), nullable=True),
    sa.Column('contacted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('timeout_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('eligibility_rechecked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('confirmed_by_user_id', sa.Uuid(), nullable=True),
    sa.Column('status_reason', sa.String(length=300), nullable=True),
    sa.Column('simulated', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(blood_request_id IS NOT NULL AND emergency_id IS NULL AND subject = 'BLOOD_REQUEST') OR (emergency_id IS NOT NULL AND blood_request_id IS NULL AND subject = 'EMERGENCY')", name='ck_contact_exactly_one_subject'),
    sa.ForeignKeyConstraint(['blood_request_id'], ['blood_requests.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['confirmed_by_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['contacted_by_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['donor_user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['emergency_id'], ['emergencies.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['notification_id'], ['notifications.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('blood_request_id', 'donor_user_id', name='uq_contact_request_donor'),
    sa.UniqueConstraint('emergency_id', 'donor_user_id', name='uq_contact_emergency_donor')
    )
    op.create_index('ix_contact_status_timeout', 'donor_contact_attempts', ['status', 'timeout_at'], unique=False)
    op.create_index(op.f('ix_donor_contact_attempts_blood_request_id'), 'donor_contact_attempts', ['blood_request_id'], unique=False)
    op.create_index(op.f('ix_donor_contact_attempts_donor_user_id'), 'donor_contact_attempts', ['donor_user_id'], unique=False)
    op.create_index(op.f('ix_donor_contact_attempts_emergency_id'), 'donor_contact_attempts', ['emergency_id'], unique=False)
    op.add_column('donor_profiles', sa.Column('location_updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('donor_profiles', sa.Column('availability_confirmed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('emergency_pathways', sa.Column('recovery_required_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('emergency_pathways', sa.Column('recovery_required_reason', sa.String(length=500), nullable=True))
    op.add_column('pathway_reservations', sa.Column('handed_off_shipment_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_pathway_reservations_handed_off_shipment_id'), 'pathway_reservations', ['handed_off_shipment_id'], unique=False)
    op.create_foreign_key('fk_pathway_reservations_handed_off_shipment', 'pathway_reservations', 'shipments', ['handed_off_shipment_id'], ['id'], ondelete='SET NULL')
    op.add_column('shipments', sa.Column('emergency_id', sa.Uuid(), nullable=True))
    op.add_column('shipments', sa.Column('pathway_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_shipments_emergency_id'), 'shipments', ['emergency_id'], unique=False)
    op.create_index(op.f('ix_shipments_pathway_id'), 'shipments', ['pathway_id'], unique=False)
    op.create_foreign_key('fk_shipments_pathway', 'shipments', 'emergency_pathways', ['pathway_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_shipments_emergency', 'shipments', 'emergencies', ['emergency_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_shipments_emergency', 'shipments', type_='foreignkey')
    op.drop_constraint('fk_shipments_pathway', 'shipments', type_='foreignkey')
    op.drop_index(op.f('ix_shipments_pathway_id'), table_name='shipments')
    op.drop_index(op.f('ix_shipments_emergency_id'), table_name='shipments')
    op.drop_column('shipments', 'pathway_id')
    op.drop_column('shipments', 'emergency_id')
    op.drop_constraint('fk_pathway_reservations_handed_off_shipment', 'pathway_reservations', type_='foreignkey')
    op.drop_index(op.f('ix_pathway_reservations_handed_off_shipment_id'), table_name='pathway_reservations')
    op.drop_column('pathway_reservations', 'handed_off_shipment_id')
    op.drop_column('emergency_pathways', 'recovery_required_reason')
    op.drop_column('emergency_pathways', 'recovery_required_at')
    op.drop_column('donor_profiles', 'availability_confirmed_at')
    op.drop_column('donor_profiles', 'location_updated_at')
    op.drop_index(op.f('ix_donor_contact_attempts_emergency_id'), table_name='donor_contact_attempts')
    op.drop_index(op.f('ix_donor_contact_attempts_donor_user_id'), table_name='donor_contact_attempts')
    op.drop_index(op.f('ix_donor_contact_attempts_blood_request_id'), table_name='donor_contact_attempts')
    op.drop_index('ix_contact_status_timeout', table_name='donor_contact_attempts')
    op.drop_table('donor_contact_attempts')
    sa.Enum(name='donor_contact_status').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='shortlist_subject').drop(op.get_bind(), checkfirst=True)
