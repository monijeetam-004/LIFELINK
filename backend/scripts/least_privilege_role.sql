-- Gap elimination #26: run the API with a role that does NOT own the tables.
--
-- The audit_logs append-only guard (app/core/audit_guard.py) is a trigger.
-- A table OWNER can disable it; a non-owner cannot. Migrations run as the
-- owner (lifelink_owner); the API and workers run as lifelink_app.
--
--   psql -v app_password="'<strong secret from your secret store>'" -f least_privilege_role.sql
--
-- Never commit the password; pass it at run time.
-- Run it connected to the application database (psql -d <db>); CONNECT is
-- granted on whichever database that is.
CREATE ROLE lifelink_app LOGIN PASSWORD :app_password NOSUPERUSER NOCREATEDB NOCREATEROLE;
DO $$ BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO lifelink_app', current_database());
END $$;
GRANT USAGE ON SCHEMA public TO lifelink_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO lifelink_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lifelink_app;
-- audit_logs: append + seal only (the trigger enforces which columns).
REVOKE DELETE, TRUNCATE ON audit_logs FROM lifelink_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO lifelink_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO lifelink_app;
-- Verify afterwards (must fail with "must be owner of table audit_logs"):
--   SET ROLE lifelink_app; ALTER TABLE audit_logs DISABLE TRIGGER USER;
