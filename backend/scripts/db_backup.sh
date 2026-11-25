#!/usr/bin/env bash
# Phase 28 Part 8 -- explicit, credential-free database backup.
#
# Takes a full logical backup of the database named by DATABASE_URL (or an
# explicit override -- see below) using pg_dump's custom format (-Fc):
# compressed, and restorable selectively or in full by scripts/db_restore.sh
# (or plain pg_restore). Nothing here hardcodes a host, user, password, or
# database name -- every value comes from the environment, exactly the way
# the application itself is configured (see app/core/config.py), and the
# backup file itself is written under backend/backups/, which is
# git-ignored (see .gitignore) so a backup can never be accidentally
# committed.
#
# Usage:
#   ./scripts/db_backup.sh
#       Backs up the database named by $DATABASE_URL (falls back to
#       backend/.env's DATABASE_URL if that file is present and readable,
#       exactly like the application does -- see app/core/config.py's
#       env_file=".env").
#
#   DATABASE_URL=postgresql://user:pass@host:port/dbname ./scripts/db_backup.sh
#       Backs up an explicitly named database instead (e.g. to back up a
#       database other than the one this shell's .env points at).
#
#   BACKUP_DIR=/some/other/path ./scripts/db_backup.sh
#       Writes the backup file somewhere other than backend/backups/
#       (default). Whatever directory is used, back it up somewhere this
#       script does not manage (offsite/object storage) -- this script only
#       produces the file, it does not ship it anywhere.
#
# Output: backend/backups/lifelink_<dbname>_<UTC timestamp>.dump
# (pg_dump custom format -- restore with scripts/db_restore.sh or
# `pg_restore -d <target-url> <file>`).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Reuse the exact same DATABASE_URL resolution the application uses: an
# explicit env var wins, otherwise fall back to backend/.env if present
# (never required to exist -- a real deployment sets DATABASE_URL directly
# in its process environment, per app/core/config.py's own precedence).
if [ -z "${DATABASE_URL:-}" ] && [ -f .env ]; then
    DATABASE_URL="$(grep -E '^DATABASE_URL=' .env | tail -n1 | cut -d= -f2-)"
fi
if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL is not set (and no backend/.env DATABASE_URL= line was found)." >&2
    echo "Set it explicitly, e.g.: DATABASE_URL=postgresql://user:pass@host:5432/dbname $0" >&2
    exit 1
fi

# pg_dump understands the plain postgresql:// scheme; SQLAlchemy's
# postgresql+psycopg:// driver suffix is app-side only and must be stripped.
PG_URL="${DATABASE_URL/postgresql+psycopg:\/\//postgresql://}"

DB_NAME="$(python3 -c "
import sys
from urllib.parse import urlparse
print((urlparse(sys.argv[1]).path or '/unknown').lstrip('/') or 'unknown')
" "$PG_URL")"

BACKUP_DIR="${BACKUP_DIR:-$(pwd)/backups}"
mkdir -p "$BACKUP_DIR"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_FILE="$BACKUP_DIR/lifelink_${DB_NAME}_${TIMESTAMP}.dump"

echo "Backing up database '$DB_NAME' -> $OUT_FILE"
pg_dump --format=custom --no-owner --no-privileges --file="$OUT_FILE" "$PG_URL"

echo "Backup complete: $OUT_FILE ($(du -h "$OUT_FILE" | cut -f1))"
echo "This file is NOT committed to source control (backend/.gitignore excludes backups/)."
echo "Copy it somewhere durable (offsite/object storage) -- this script does not do that for you."
