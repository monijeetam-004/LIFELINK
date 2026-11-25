"""Hardening: append-only guard triggers on audit_logs.

Refuses DELETE / TRUNCATE of audit entries, any UPDATE of a sealed entry,
and any change to an unsealed entry's content columns (only the sealer's
one-time fill of the chain columns is allowed). See app.core.audit_guard
for what this does and does not protect against.

Revision ID: c9d0e1f2a3b4
Revises: a6b7c8d9e0f1
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "a6b7c8d9e0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of app.core.audit_guard at the time of this revision (a
# migration must not change if the app module changes later).
_FUNCTION = """
CREATE OR REPLACE FUNCTION lifelink_audit_logs_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'TRUNCATE' THEN
    RAISE EXCEPTION 'audit_logs is append-only: TRUNCATE refused'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'audit_logs is append-only: DELETE of entry % refused',
      coalesce(OLD.chain_seq::text, OLD.id::text)
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.chain_seq IS NOT NULL THEN
    RAISE EXCEPTION 'audit_logs is append-only: sealed entry % cannot be modified',
      OLD.chain_seq USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.id IS DISTINCT FROM OLD.id
     OR NEW.actor_user_id IS DISTINCT FROM OLD.actor_user_id
     OR NEW.action IS DISTINCT FROM OLD.action
     OR NEW.target_type IS DISTINCT FROM OLD.target_type
     OR NEW.target_id IS DISTINCT FROM OLD.target_id
     OR NEW.ip IS DISTINCT FROM OLD.ip
     OR NEW.user_agent IS DISTINCT FROM OLD.user_agent
     OR NEW.meta IS DISTINCT FROM OLD.meta
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'audit_logs is append-only: entry content cannot be modified'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
"""

_CREATE = [
    _FUNCTION,
    "DROP TRIGGER IF EXISTS audit_logs_append_only ON audit_logs",
    "CREATE TRIGGER audit_logs_append_only BEFORE UPDATE OR DELETE ON audit_logs "
    "FOR EACH ROW EXECUTE FUNCTION lifelink_audit_logs_append_only()",
    "DROP TRIGGER IF EXISTS audit_logs_no_truncate ON audit_logs",
    "CREATE TRIGGER audit_logs_no_truncate BEFORE TRUNCATE ON audit_logs "
    "FOR EACH STATEMENT EXECUTE FUNCTION lifelink_audit_logs_append_only()",
]

_DROP = [
    "DROP TRIGGER IF EXISTS audit_logs_no_truncate ON audit_logs",
    "DROP TRIGGER IF EXISTS audit_logs_append_only ON audit_logs",
    "DROP FUNCTION IF EXISTS lifelink_audit_logs_append_only()",
]


def upgrade() -> None:
    for stmt in _CREATE:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _DROP:
        op.execute(stmt)
