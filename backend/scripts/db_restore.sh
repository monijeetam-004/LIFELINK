#!/usr/bin/env bash
# Phase 28 Part 8 -- explicit, confirmation-gated database restore.
#
# Restores a pg_dump custom-format backup (as produced by
# scripts/db_backup.sh) into a TARGET database. Nothing here hardcodes a
# credential -- the target is always passed explicitly, never inferred from
# backend/.env, precisely so a restore can never accidentally land on
# whatever database happens to be configured for local dev at the time.
#
# SAFETY: this script refuses to run unless RESTORE_CONFIRM=yes is set, and
# separately refuses to restore into a database whose name contains "prod"
# unless RESTORE_ALLOW_PROD=yes is ALSO set -- two independent, explicit
# opt-ins, so a single copy-pasted command can't silently overwrite a
# production database. There is no way to bypass either check with a flag
# alone; both must be set as real environment variables.
#
# Usage:
#   RESTORE_CONFIRM=yes ./scripts/db_restore.sh <backup-file> <target-database-url>
#
# Example (restoring into a disposable/test database -- the normal case):
#   RESTORE_CONFIRM=yes ./scripts/db_restore.sh \
#       backups/lifelink_lifelink_20260101T000000Z.dump \
#       postgresql://lifelink:lifelink@localhost:5432/lifelink_restore_drill
#
# The target database must already exist and be empty (or at least not
# hold anything you need) -- this restores WITH --clean --if-exists, which
# drops each object immediately before recreating it, so it is written
# assuming the target is disposable, not merged into.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ "${RESTORE_CONFIRM:-}" != "yes" ]; then
    echo "ERROR: refusing to restore without RESTORE_CONFIRM=yes." >&2
    echo "This is a destructive operation (--clean drops existing objects in the target" >&2
    echo "database first) -- set RESTORE_CONFIRM=yes explicitly to proceed." >&2
    exit 1
fi

BACKUP_FILE="${1:-}"
TARGET_URL="${2:-}"
if [ -z "$BACKUP_FILE" ] || [ -z "$TARGET_URL" ]; then
    echo "Usage: RESTORE_CONFIRM=yes $0 <backup-file> <target-database-url>" >&2
    exit 1
fi
if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERROR: backup file not found: $BACKUP_FILE" >&2
    exit 1
fi

PG_URL="${TARGET_URL/postgresql+psycopg:\/\//postgresql://}"
DB_NAME="$(python3 -c "
import sys
from urllib.parse import urlparse
print((urlparse(sys.argv[1]).path or '/unknown').lstrip('/') or 'unknown')
" "$PG_URL")"

case "$DB_NAME" in
    *prod*)
        if [ "${RESTORE_ALLOW_PROD:-}" != "yes" ]; then
            echo "ERROR: target database name '$DB_NAME' looks like a production database." >&2
            echo "Refusing to restore onto it without RESTORE_ALLOW_PROD=yes set explicitly," >&2
            echo "in addition to RESTORE_CONFIRM=yes. This double opt-in exists specifically" >&2
            echo "so a restore drill against a disposable database can never be accidentally" >&2
            echo "pointed at production by a copy-pasted command." >&2
            exit 1
        fi
        echo "WARNING: RESTORE_ALLOW_PROD=yes set -- proceeding against '$DB_NAME'." >&2
        ;;
esac

echo "Restoring $BACKUP_FILE -> database '$DB_NAME'"
pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$PG_URL" "$BACKUP_FILE"
echo "Restore complete."
