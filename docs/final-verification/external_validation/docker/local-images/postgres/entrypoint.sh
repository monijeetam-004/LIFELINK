#!/bin/bash
# Mirrors the official postgres image: initialise PGDATA once from
# POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB, then run postgres.
set -e
chown -R postgres:postgres "$PGDATA" /var/run/postgresql
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "$POSTGRES_PASSWORD" > /tmp/pw && chown postgres /tmp/pw
  su postgres -c "/usr/lib/postgresql/16/bin/initdb -D $PGDATA -U $POSTGRES_USER --pwfile=/tmp/pw -A scram-sha-256 -E UTF8 --locale=C.UTF-8" >/dev/null
  rm -f /tmp/pw
  echo "listen_addresses='*'" >> "$PGDATA/postgresql.conf"
  echo "host all all 0.0.0.0/0 scram-sha-256" >> "$PGDATA/pg_hba.conf"
  su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D $PGDATA -w start -o '-c listen_addresses=127.0.0.1'" >/dev/null
  if [ -n "$POSTGRES_DB" ] && [ "$POSTGRES_DB" != "postgres" ]; then
    PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U "$POSTGRES_USER" -d postgres -qc "CREATE DATABASE \"$POSTGRES_DB\""
  fi
  su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D $PGDATA -w stop" >/dev/null
  echo "database initialised"
fi
exec su postgres -c "/usr/lib/postgresql/16/bin/postgres -D $PGDATA"
