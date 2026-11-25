"""Hardening -- database-level append-only guard for `audit_logs`.

What it does (PostgreSQL triggers, installed by migration c9d0e1f2a3b4 and,
for test databases built with `metadata.create_all`, by an `after_create`
hook on the table):

  * DELETE of any audit row is refused.
  * UPDATE of a SEALED row (chain_seq IS NOT NULL) is refused.
  * UPDATE of an unsealed row may only fill in the five chain columns
    (what the sealer does, once); every content column is frozen.
  * TRUNCATE of audit_logs (directly or via TRUNCATE ... CASCADE from
    users) is refused.

What it does NOT do -- stated plainly so nobody mistakes it for
immutability:

  * The table OWNER (and any superuser) can `ALTER TABLE audit_logs
    DISABLE TRIGGER ...` or drop the trigger, then tamper. The guard stops
    application bugs, SQL injection through the application's ordinary
    statements, and accidental admin SQL; it does not stop someone who
    holds the owner role. Run the API with a role that does NOT own the
    table (see docs/LIFELINK_AUDIT_CHAIN.md, "Deployment") so that even a
    compromised application cannot switch it off.
  * Tampering by an owner is still DETECTED by the hash chain, and a full
    recompute by an owner is detected by the HMAC-signed checkpoints kept
    outside the database (app.services.audit_checkpoint_service).

Consequence: a user referenced by a sealed audit row cannot be
hard-deleted (the FK's ON DELETE SET NULL would rewrite a sealed row).
The application never hard-deletes users -- accounts are deactivated.
"""
from __future__ import annotations

FUNCTION = "lifelink_audit_logs_append_only"
ROW_TRIGGER = "audit_logs_append_only"
TRUNCATE_TRIGGER = "audit_logs_no_truncate"

CREATE_SQL = [
    f"""
CREATE OR REPLACE FUNCTION {FUNCTION}() RETURNS trigger
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
""",
    f"DROP TRIGGER IF EXISTS {ROW_TRIGGER} ON audit_logs",
    f"""CREATE TRIGGER {ROW_TRIGGER} BEFORE UPDATE OR DELETE ON audit_logs
FOR EACH ROW EXECUTE FUNCTION {FUNCTION}()""",
    f"DROP TRIGGER IF EXISTS {TRUNCATE_TRIGGER} ON audit_logs",
    f"""CREATE TRIGGER {TRUNCATE_TRIGGER} BEFORE TRUNCATE ON audit_logs
FOR EACH STATEMENT EXECUTE FUNCTION {FUNCTION}()""",
]

DROP_SQL = [
    f"DROP TRIGGER IF EXISTS {TRUNCATE_TRIGGER} ON audit_logs",
    f"DROP TRIGGER IF EXISTS {ROW_TRIGGER} ON audit_logs",
    f"DROP FUNCTION IF EXISTS {FUNCTION}()",
]

#: For owner-level maintenance (tests that simulate an attacker with owner
#: rights; the test-suite reset). Must run in the same transaction as the
#: statement it enables, followed by ENABLE_SQL.
DISABLE_SQL = "ALTER TABLE audit_logs DISABLE TRIGGER USER"
ENABLE_SQL = "ALTER TABLE audit_logs ENABLE TRIGGER USER"


#: Gap elimination #26: detect a guard that was switched off. pg_trigger's
#: tgenabled is 'O' (enabled), 'D' (disabled), 'R'/'A' (replica/always).
STATUS_SQL = (
    "SELECT t.tgname, t.tgenabled FROM pg_trigger t "
    "JOIN pg_class c ON c.oid = t.tgrelid "
    "WHERE c.relname = 'audit_logs' AND NOT t.tgisinternal"
)


def guard_status(conn) -> dict[str, str]:
    """{trigger name: ENABLED | DISABLED | MISSING} for both guard triggers."""
    from sqlalchemy import text

    found = dict(conn.execute(text(STATUS_SQL)).all())
    out = {}
    for name in (ROW_TRIGGER, TRUNCATE_TRIGGER):
        flag = found.get(name)
        if flag is None:
            out[name] = "MISSING"
        else:
            flag = flag.decode() if isinstance(flag, bytes) else str(flag)
            out[name] = "DISABLED" if flag == "D" else "ENABLED"
    return out
