"""Gap elimination: pathway engine, cold chain, reconciliation, thaw, forecasting.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-10-10

What this adds (all additive; no existing column changes meaning):

* emergency_pathways: plan rounds (one ACTIVE primary + up to N BACKUP rows),
  feasibility, quantity/component/urgency context, ETA/deadline/slack,
  strategy and a plain-language explanation;
* emergency_pathway_legs / emergency_pathway_steps: the COMPLETE stored
  route (every facility node and every transport route id, in order) so
  re-planning can be scoped to pathways that actually use a failed route;
* pathway_reservations: which units a pathway holds, per leg;
* emergency_overrides: audited admin overrides (donor distance limit,
  unverified facility);
* emergencies: component, coordinates (+ CHECK ranges), geocode metadata,
  deadline-monitor flags;
* blood_units: thaw lifecycle (thawed_at, thaw_expires_at), status reason;
* shipments / shipment_items: cold-chain status and per-unit outcomes;
* shipment_temperature_readings: temperature telemetry;
* demand_forecast_runs: stored forecast runs (labelled synthetic);
* new enum VALUES on existing types (ALTER TYPE ... ADD VALUE):
  emergency_pathway_status BACKUP/INVALIDATED, graph_pathway_source
  RELATIONAL_ROUTES, blood_unit_state QUARANTINED/DISCARDED,
  hospital_inventory_action THAW/QUARANTINE/RELEASE_QUARANTINE/DISCARD.

Downgrade drops everything added here. PostgreSQL cannot drop enum VALUES,
so the ADD VALUE labels remain on downgrade (harmless: no row uses them
once the new tables/columns are gone, and the ORM of the older revision
never writes them).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing(name: str) -> postgresql.ENUM:
    """Reference an enum type that already exists (never re-create it)."""
    return postgresql.ENUM(name=name, create_type=False)


_new_pathway_feasibility = postgresql.ENUM(
    "VIABLE", "DEADLINE_RISK", "PARTIAL", "IMPOSSIBLE", name="pathway_feasibility")
_new_geocode_source = postgresql.ENUM(
    "USER_INPUT", "FACILITY", "GAZETTEER", "GEOCODER", name="geocode_source")
_new_shipment_item_outcome = postgresql.ENUM(
    "PENDING", "RECEIVED_OK", "QUARANTINED", "DAMAGED", "SPOILED", "LOST",
    "RETURNED", "REDIRECTED", "EXPIRED_ON_ARRIVAL", name="shipment_item_outcome")
_new_cold_chain_status = postgresql.ENUM(
    "NO_DATA", "WITHIN_RANGE", "EXCURSION", "REPEATED_EXCURSIONS", "DATA_GAP",
    "SENSOR_FAILURE", name="cold_chain_status")
_ADD_COLUMN_ENUMS = (_new_pathway_feasibility, _new_geocode_source,
                     _new_shipment_item_outcome, _new_cold_chain_status)
_CREATE_TABLE_ENUMS = ("emergency_override_kind", "route_basis", "telemetry_source")


def upgrade() -> None:
    bind = op.get_bind()
    for value in ("BACKUP", "INVALIDATED"):
        op.execute(sa.text(
            f"ALTER TYPE emergency_pathway_status ADD VALUE IF NOT EXISTS '{value}'"))
    op.execute(sa.text(
        "ALTER TYPE graph_pathway_source ADD VALUE IF NOT EXISTS 'RELATIONAL_ROUTES'"))
    for value in ("QUARANTINED", "DISCARDED"):
        op.execute(sa.text(f"ALTER TYPE blood_unit_state ADD VALUE IF NOT EXISTS '{value}'"))
    for value in ("THAW", "QUARANTINE", "RELEASE_QUARANTINE", "DISCARD"):
        op.execute(sa.text(
            f"ALTER TYPE hospital_inventory_action ADD VALUE IF NOT EXISTS '{value}'"))
    op.execute(sa.text("ALTER TYPE sms_status ADD VALUE IF NOT EXISTS 'LOGGED_ONLY'"))
    for enum in _ADD_COLUMN_ENUMS:
        enum.create(bind, checkfirst=True)

    op.create_table('demand_forecast_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('facility_id', sa.Uuid(), nullable=True),
    sa.Column('requested_by_user_id', sa.Uuid(), nullable=True),
    sa.Column('method', sa.String(length=60), nullable=False),
    sa.Column('history_days', sa.Integer(), nullable=False),
    sa.Column('horizon_days', sa.Integer(), nullable=False),
    sa.Column('parameters', sa.JSON(), nullable=False),
    sa.Column('results', sa.JSON(), nullable=False),
    sa.Column('data_is_synthetic', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['facility_id'], ['hospital_profiles.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['requested_by_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_demand_forecast_runs_created_at'), 'demand_forecast_runs', ['created_at'], unique=False)
    op.create_index(op.f('ix_demand_forecast_runs_facility_id'), 'demand_forecast_runs', ['facility_id'], unique=False)
    op.create_table('emergency_overrides',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('emergency_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.Enum('DONOR_DISTANCE', 'UNVERIFIED_FACILITY', name='emergency_override_kind'), nullable=False),
    sa.Column('reason', sa.String(length=500), nullable=False),
    sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_by_user_id', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['emergency_id'], ['emergencies.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['revoked_by_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_emergency_overrides_emergency_id'), 'emergency_overrides', ['emergency_id'], unique=False)
    op.create_table('emergency_pathway_legs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('pathway_id', sa.Uuid(), nullable=False),
    sa.Column('leg_index', sa.Integer(), nullable=False),
    sa.Column('source_facility_id', sa.Uuid(), nullable=False),
    sa.Column('destination_facility_id', sa.Uuid(), nullable=False),
    sa.Column('units', sa.Integer(), nullable=False),
    sa.Column('units_by_type', sa.JSON(), nullable=False),
    sa.Column('route_basis', sa.Enum('LOCAL', 'ROUTED', 'STRAIGHT_LINE_ESTIMATE', name='route_basis'), nullable=False),
    sa.Column('node_ids', sa.JSON(), nullable=False),
    sa.Column('distance_km', sa.Float(), nullable=False),
    sa.Column('straight_line_km', sa.Float(), nullable=True),
    sa.Column('transit_minutes', sa.Integer(), nullable=False),
    sa.Column('eta_minutes', sa.Integer(), nullable=False),
    sa.Column('hops', sa.Integer(), nullable=False),
    sa.CheckConstraint('units > 0', name='ck_pathway_leg_units_positive'),
    sa.ForeignKeyConstraint(['destination_facility_id'], ['hospital_profiles.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['pathway_id'], ['emergency_pathways.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_facility_id'], ['hospital_profiles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('pathway_id', 'leg_index', name='uq_pathway_leg_index')
    )
    op.create_index(op.f('ix_emergency_pathway_legs_pathway_id'), 'emergency_pathway_legs', ['pathway_id'], unique=False)
    op.create_index(op.f('ix_emergency_pathway_legs_source_facility_id'), 'emergency_pathway_legs', ['source_facility_id'], unique=False)
    op.create_table('shipment_temperature_readings',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('shipment_id', sa.Uuid(), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('temperature_c', sa.Float(), nullable=True),
    sa.Column('sensor_id', sa.String(length=80), nullable=False),
    sa.Column('source', sa.Enum('SENSOR', 'MANUAL', 'SIMULATED', name='telemetry_source'), nullable=False),
    sa.Column('error_code', sa.String(length=80), nullable=True),
    sa.Column('submitted_by_user_id', sa.Uuid(), nullable=True),
    sa.Column('ingested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['shipment_id'], ['shipments.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['submitted_by_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('shipment_id', 'sensor_id', 'recorded_at', name='uq_temperature_reading')
    )
    op.create_index(op.f('ix_shipment_temperature_readings_shipment_id'), 'shipment_temperature_readings', ['shipment_id'], unique=False)
    op.create_index('ix_temperature_shipment_time', 'shipment_temperature_readings', ['shipment_id', 'recorded_at'], unique=False)
    op.create_table('emergency_pathway_steps',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('pathway_id', sa.Uuid(), nullable=False),
    sa.Column('leg_id', sa.Uuid(), nullable=False),
    sa.Column('step_index', sa.Integer(), nullable=False),
    sa.Column('route_id', sa.Uuid(), nullable=False),
    sa.Column('from_facility_id', sa.Uuid(), nullable=False),
    sa.Column('to_facility_id', sa.Uuid(), nullable=False),
    sa.Column('distance_km', sa.Float(), nullable=False),
    sa.Column('transit_minutes', sa.Integer(), nullable=False),
    sa.Column('route_status_at_plan', _existing('transport_route_status'), nullable=False),
    sa.ForeignKeyConstraint(['from_facility_id'], ['hospital_profiles.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['leg_id'], ['emergency_pathway_legs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['pathway_id'], ['emergency_pathways.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['route_id'], ['transport_routes.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['to_facility_id'], ['hospital_profiles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('leg_id', 'step_index', name='uq_pathway_step_index')
    )
    op.create_index(op.f('ix_emergency_pathway_steps_pathway_id'), 'emergency_pathway_steps', ['pathway_id'], unique=False)
    op.create_index('ix_pathway_steps_route', 'emergency_pathway_steps', ['route_id'], unique=False)
    op.create_table('pathway_reservations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('pathway_id', sa.Uuid(), nullable=False),
    sa.Column('leg_id', sa.Uuid(), nullable=False),
    sa.Column('emergency_id', sa.Uuid(), nullable=False),
    sa.Column('facility_id', sa.Uuid(), nullable=False),
    sa.Column('blood_type', _existing('blood_type'), nullable=False),
    sa.Column('component', _existing('blood_component'), nullable=False),
    sa.Column('units', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('release_reason', sa.String(length=300), nullable=True),
    sa.CheckConstraint('units > 0', name='ck_pathway_reservation_units_positive'),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['emergency_id'], ['emergencies.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['facility_id'], ['hospital_profiles.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['leg_id'], ['emergency_pathway_legs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['pathway_id'], ['emergency_pathways.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_pathway_reservations_emergency_id'), 'pathway_reservations', ['emergency_id'], unique=False)
    op.create_index(op.f('ix_pathway_reservations_pathway_id'), 'pathway_reservations', ['pathway_id'], unique=False)
    op.add_column('blood_units', sa.Column('thawed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('blood_units', sa.Column('thaw_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('blood_units', sa.Column('thaw_rule_ref', sa.String(length=80), nullable=True))
    op.add_column('blood_units', sa.Column('status_reason', sa.String(length=300), nullable=True))
    op.create_index(op.f('ix_blood_units_thaw_expires_at'), 'blood_units', ['thaw_expires_at'], unique=False)
    op.add_column('emergencies', sa.Column('component', _existing('blood_component'), server_default='WHOLE_BLOOD', nullable=False))
    op.add_column('emergencies', sa.Column('latitude', sa.Float(), nullable=True))
    op.add_column('emergencies', sa.Column('longitude', sa.Float(), nullable=True))
    op.add_column('emergencies', sa.Column('geocode_source', _new_geocode_source, nullable=True))
    op.add_column('emergencies', sa.Column('geocode_detail', sa.String(length=300), nullable=True))
    op.add_column('emergencies', sa.Column('city', sa.String(length=120), nullable=True))
    op.add_column('emergencies', sa.Column('deadline_warning_sent_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('emergencies', sa.Column('deadline_escalated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('emergency_pathways', sa.Column('plan_round', sa.Integer(), server_default='1', nullable=False))
    op.add_column('emergency_pathways', sa.Column('backup_rank', sa.Integer(), server_default='0', nullable=False))
    op.add_column('emergency_pathways', sa.Column('feasibility', _new_pathway_feasibility, nullable=True))
    op.add_column('emergency_pathways', sa.Column('component', _existing('blood_component'), nullable=True))
    op.add_column('emergency_pathways', sa.Column('recipient_blood_type', _existing('blood_type'), nullable=True))
    op.add_column('emergency_pathways', sa.Column('urgency', _existing('urgency'), nullable=True))
    op.add_column('emergency_pathways', sa.Column('units_requested', sa.Integer(), nullable=True))
    op.add_column('emergency_pathways', sa.Column('units_planned', sa.Integer(), nullable=True))
    op.add_column('emergency_pathways', sa.Column('eta_minutes', sa.Integer(), nullable=True))
    op.add_column('emergency_pathways', sa.Column('arrival_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('emergency_pathways', sa.Column('deadline_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('emergency_pathways', sa.Column('slack_minutes', sa.Float(), nullable=True))
    op.add_column('emergency_pathways', sa.Column('strategy', sa.String(length=40), nullable=True))
    op.add_column('emergency_pathways', sa.Column('explanation', sa.JSON(), nullable=True))
    op.add_column('emergency_pathways', sa.Column('assumptions', sa.JSON(), nullable=True))
    op.add_column('emergency_pathways', sa.Column('promoted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('emergency_pathways', sa.Column('invalidated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('emergency_pathways', sa.Column('invalidated_reason', sa.String(length=500), nullable=True))
    op.add_column('shipment_items', sa.Column('outcome', _new_shipment_item_outcome, server_default='PENDING', nullable=False))
    op.add_column('shipment_items', sa.Column('outcome_reason', sa.String(length=500), nullable=True))
    op.add_column('shipment_items', sa.Column('outcome_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('shipment_items', sa.Column('outcome_by_user_id', sa.Uuid(), nullable=True))
    op.add_column('shipment_items', sa.Column('resulting_unit_id', sa.Uuid(), nullable=True))
    op.create_foreign_key('fk_shipment_items_outcome_by_user', 'shipment_items', 'users', ['outcome_by_user_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_shipment_items_resulting_unit', 'shipment_items', 'blood_units', ['resulting_unit_id'], ['id'], ondelete='SET NULL')
    op.add_column('shipments', sa.Column('temp_min_c', sa.Float(), nullable=True))
    op.add_column('shipments', sa.Column('temp_max_c', sa.Float(), nullable=True))
    op.add_column('shipments', sa.Column('cold_chain_status', _new_cold_chain_status, nullable=True))
    op.add_column('shipments', sa.Column('cold_chain_evaluated_at', sa.DateTime(timezone=True), nullable=True))

    # System-initiated replanning has no human requester.
    op.alter_column("graph_pathway_logs", "requested_by_user_id", nullable=True)
    op.create_check_constraint(
        "ck_emergency_coords_pair", "emergencies",
        "(latitude IS NULL) = (longitude IS NULL)")
    op.create_check_constraint(
        "ck_emergency_latitude_range", "emergencies",
        "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)")
    op.create_check_constraint(
        "ck_emergency_longitude_range", "emergencies",
        "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)")


def downgrade() -> None:
    bind = op.get_bind()
    op.execute(sa.text("DELETE FROM graph_pathway_logs WHERE requested_by_user_id IS NULL"))
    op.alter_column("graph_pathway_logs", "requested_by_user_id", nullable=False)
    op.drop_constraint("ck_emergency_longitude_range", "emergencies", type_="check")
    op.drop_constraint("ck_emergency_latitude_range", "emergencies", type_="check")
    op.drop_constraint("ck_emergency_coords_pair", "emergencies", type_="check")

    op.drop_column('shipments', 'cold_chain_evaluated_at')
    op.drop_column('shipments', 'cold_chain_status')
    op.drop_column('shipments', 'temp_max_c')
    op.drop_column('shipments', 'temp_min_c')
    op.drop_constraint('fk_shipment_items_resulting_unit', 'shipment_items', type_='foreignkey')
    op.drop_constraint('fk_shipment_items_outcome_by_user', 'shipment_items', type_='foreignkey')
    op.drop_column('shipment_items', 'resulting_unit_id')
    op.drop_column('shipment_items', 'outcome_by_user_id')
    op.drop_column('shipment_items', 'outcome_at')
    op.drop_column('shipment_items', 'outcome_reason')
    op.drop_column('shipment_items', 'outcome')
    op.drop_column('emergency_pathways', 'invalidated_reason')
    op.drop_column('emergency_pathways', 'invalidated_at')
    op.drop_column('emergency_pathways', 'promoted_at')
    op.drop_column('emergency_pathways', 'assumptions')
    op.drop_column('emergency_pathways', 'explanation')
    op.drop_column('emergency_pathways', 'strategy')
    op.drop_column('emergency_pathways', 'slack_minutes')
    op.drop_column('emergency_pathways', 'deadline_at')
    op.drop_column('emergency_pathways', 'arrival_at')
    op.drop_column('emergency_pathways', 'eta_minutes')
    op.drop_column('emergency_pathways', 'units_planned')
    op.drop_column('emergency_pathways', 'units_requested')
    op.drop_column('emergency_pathways', 'urgency')
    op.drop_column('emergency_pathways', 'recipient_blood_type')
    op.drop_column('emergency_pathways', 'component')
    op.drop_column('emergency_pathways', 'feasibility')
    op.drop_column('emergency_pathways', 'backup_rank')
    op.drop_column('emergency_pathways', 'plan_round')
    op.drop_column('emergencies', 'deadline_escalated_at')
    op.drop_column('emergencies', 'deadline_warning_sent_at')
    op.drop_column('emergencies', 'city')
    op.drop_column('emergencies', 'geocode_detail')
    op.drop_column('emergencies', 'geocode_source')
    op.drop_column('emergencies', 'longitude')
    op.drop_column('emergencies', 'latitude')
    op.drop_column('emergencies', 'component')
    op.drop_index(op.f('ix_blood_units_thaw_expires_at'), table_name='blood_units')
    op.drop_column('blood_units', 'status_reason')
    op.drop_column('blood_units', 'thaw_rule_ref')
    op.drop_column('blood_units', 'thaw_expires_at')
    op.drop_column('blood_units', 'thawed_at')
    op.drop_index(op.f('ix_pathway_reservations_pathway_id'), table_name='pathway_reservations')
    op.drop_index(op.f('ix_pathway_reservations_emergency_id'), table_name='pathway_reservations')
    op.drop_table('pathway_reservations')
    op.drop_index('ix_pathway_steps_route', table_name='emergency_pathway_steps')
    op.drop_index(op.f('ix_emergency_pathway_steps_pathway_id'), table_name='emergency_pathway_steps')
    op.drop_table('emergency_pathway_steps')
    op.drop_index('ix_temperature_shipment_time', table_name='shipment_temperature_readings')
    op.drop_index(op.f('ix_shipment_temperature_readings_shipment_id'), table_name='shipment_temperature_readings')
    op.drop_table('shipment_temperature_readings')
    op.drop_index(op.f('ix_emergency_pathway_legs_source_facility_id'), table_name='emergency_pathway_legs')
    op.drop_index(op.f('ix_emergency_pathway_legs_pathway_id'), table_name='emergency_pathway_legs')
    op.drop_table('emergency_pathway_legs')
    op.drop_index(op.f('ix_emergency_overrides_emergency_id'), table_name='emergency_overrides')
    op.drop_table('emergency_overrides')
    op.drop_index(op.f('ix_demand_forecast_runs_facility_id'), table_name='demand_forecast_runs')
    op.drop_index(op.f('ix_demand_forecast_runs_created_at'), table_name='demand_forecast_runs')
    op.drop_table('demand_forecast_runs')

    for enum in _ADD_COLUMN_ENUMS:
        enum.drop(bind, checkfirst=True)
    for name in _CREATE_TABLE_ENUMS:
        op.execute(sa.text(f"DROP TYPE IF EXISTS {name}"))
