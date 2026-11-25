#!/usr/bin/env bash
# Clean-start verification harness (docs/final-verification/CLEAN_START_VERIFICATION.md).
#
# Builds EVERYTHING from nothing in $CS and proves the application is usable:
#   fresh copy of the source tree -> new Python venv from the hashed lock ->
#   brand-new PostgreSQL cluster (initdb) -> fresh Redis -> fresh clamd with a
#   freshly built signature DB -> S3 + SMTP servers -> .env from .env.example ->
#   migration doctor -> alembic upgrade head -> API + RQ worker + deadline
#   monitor -> health -> seed -> frontend `npm ci` + build + preview ->
#   scripts/clean_start_check.py (12 functional checks) -> browser check.
#
# Nothing from the development environment is reused: no database, Redis
# data, volume, venv, node_modules, .env or build output.
#
# Sandbox substitutions, each reported as such by the checker (never as PASS
# for the real product): MinIO -> moto_server (S3 protocol), Mailpit ->
# MailDev (SMTP + HTTP API), Neo4j -> none (BLOCKED). Docker images could not
# be pulled (registries blocked), so services run as native processes; the
# compose file wires the same services the same way.
#
# Usage: run_clean_start.sh <SOURCE_REPO_ROOT> <WORK_DIR> <MOTO_SERVER_BIN> <MAILDEV_BIN>
set -uo pipefail
SRC=$1; CS=$2; MOTO=$3; MAILDEV=$4
PG_PORT=55432 REDIS_PORT=6390 CLAMD_PORT=3320 S3_PORT=9300 SMTP_PORT=1036 MAILWEB_PORT=1091
API_PORT=8200 FE_PORT=4300
LOG=$CS/clean_start.log
ts() { date -u +%FT%TZ; }
step() { echo; echo "=== [$(ts)] $*"; }
run() { echo "\$ $*"; "$@"; local rc=$?; echo "[exit $rc]"; return $rc; }

"$(dirname "$0")/stop_clean_start.sh" "$CS" >/dev/null 2>&1; rm -rf "$CS"; mkdir -p "$CS"
exec > >(tee "$LOG") 2>&1
step "0. clean work dir $CS (host: $(uname -srm); python3.12 $(python3.12 -V 2>&1 | cut -d' ' -f2); node $(node -v))"

step "1. fresh copy of the source tree (no .venv, node_modules, .env, build output, caches, .git)"
mkdir -p "$CS/LIFELINK"
(cd "$SRC" && tar --exclude=.venv --exclude=node_modules --exclude=.env --exclude=dist \
   --exclude=__pycache__ --exclude=.ruff_cache --exclude=.pytest_cache --exclude=storage \
   --exclude=.git --exclude=.local-storage --exclude=test-results --exclude=playwright-report --exclude='*.egg-info' -cf - .) | tar -xf - -C "$CS/LIFELINK"
B=$CS/LIFELINK/backend; F=$CS/LIFELINK/frontend
ls -a "$B" | tr '\n' ' '; echo

step "2. backend venv exactly as README 'Run locally' documents"
cd "$B"
run python3.12 -m venv .venv
. .venv/bin/activate
run pip install -q --require-hashes --no-deps -r requirements-dev.lock || exit 1
run pip install -q --no-deps -e . || exit 1

step "3. brand-new PostgreSQL 16 cluster (initdb) on :$PG_PORT"
PGBIN=/usr/lib/postgresql/16/bin
mkdir -p "$CS/pg" "$CS/pgsock"; echo "pgadmin-$$" > "$CS/pg/pw"; chown -R postgres "$CS/pg" "$CS/pgsock"
chmod 755 "$CS"
run su postgres -c "$PGBIN/initdb -D $CS/pg/data -A scram-sha-256 --pwfile=$CS/pg/pw -U postgres >/dev/null"
rm -f "$CS/pg/pw"
su postgres -c "$PGBIN/pg_ctl -D $CS/pg/data -o '-p $PG_PORT -k $CS/pgsock -c listen_addresses=127.0.0.1' -l $CS/pg/log -w start" >/dev/null
DBPW=$(python3 -c "import secrets;print(secrets.token_urlsafe(16))")
PGPASSWORD=pgadmin-$$ psql -h 127.0.0.1 -p $PG_PORT -U postgres -qc "CREATE ROLE lifelink LOGIN PASSWORD '$DBPW'" -c "CREATE DATABASE lifelink OWNER lifelink"
PGPASSWORD=pgadmin-$$ psql -h 127.0.0.1 -p $PG_PORT -U postgres -Atc "select 'databases: '||string_agg(datname, ',') from pg_database"

step "4. fresh Redis on :$REDIS_PORT (no persistence file)"
redis-server --port $REDIS_PORT --save '' --appendonly no --daemonize yes --dir "$CS" >/dev/null
redis-cli -p $REDIS_PORT ping

step "5. ClamAV: signature DB built from the repository, new clamd on :$CLAMD_PORT"
mkdir -p "$CS/clamdb"
run python clamav/build_local_signatures.py "$CS/clamdb" /usr/share/clamav-testfiles
cat > "$CS/clamd.conf" <<EOF
Foreground no
LogFile $CS/clamd.log
PidFile $CS/clamd.pid
DatabaseDirectory $CS/clamdb
TCPSocket $CLAMD_PORT
TCPAddr 127.0.0.1
StreamMaxLength 25M
User root
EOF
/usr/sbin/clamd -c "$CS/clamd.conf"
for i in $(seq 1 30); do (echo PING | timeout 2 nc 127.0.0.1 $CLAMD_PORT 2>/dev/null | grep -q PONG) && break; sleep 1; done
echo "clamd: $(echo PING | timeout 2 nc 127.0.0.1 $CLAMD_PORT)"

step "6. object storage (MinIO STAND-IN: moto_server) on :$S3_PORT and mail catcher (Mailpit STAND-IN: MailDev) smtp :$SMTP_PORT web :$MAILWEB_PORT"
"$MOTO" -H 127.0.0.1 -p $S3_PORT > "$CS/moto.log" 2>&1 & echo $! >> "$CS/pids"
"$MAILDEV" --smtp $SMTP_PORT --web $MAILWEB_PORT --ip 127.0.0.1 > "$CS/maildev.log" 2>&1 & echo $! >> "$CS/pids"
sleep 5
curl -s -o /dev/null -w "s3 %{http_code}\n" http://127.0.0.1:$S3_PORT/
curl -s -o /dev/null -w "mail api %{http_code}\n" http://127.0.0.1:$MAILWEB_PORT/email

step "7. Neo4j: no server obtainable here (registries and dist.neo4j.org blocked) -> NEO4J_URI left empty"

step "8. backend .env from .env.example (documented overrides only; secrets generated, masked)"
cp .env.example .env
set_env() { if grep -q "^$1=" .env; then sed -i "s#^$1=.*#$1=$2#" .env; else echo "$1=$2" >> .env; fi; }
set_env SECRET_KEY "$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
set_env DATABASE_URL "postgresql+psycopg://lifelink:$DBPW@127.0.0.1:$PG_PORT/lifelink"
set_env TEST_DATABASE_URL "postgresql+psycopg://lifelink:$DBPW@127.0.0.1:$PG_PORT/lifelink_test"
set_env REDIS_URL "redis://127.0.0.1:$REDIS_PORT/0"
set_env RQ_SYNC false
set_env CORS_ORIGINS "http://127.0.0.1:$FE_PORT"
set_env FRONTEND_BASE_URL "http://127.0.0.1:$FE_PORT"
set_env MAIL_HOST 127.0.0.1
set_env MAIL_PORT $SMTP_PORT
set_env S3_ENDPOINT_URL "http://127.0.0.1:$S3_PORT"
set_env DOCUMENT_SCANNER clamd
set_env CLAMD_HOST 127.0.0.1
set_env CLAMD_PORT $CLAMD_PORT
grep -vE '^\s*#|^\s*$' .env | sed -E 's#^(SECRET_KEY)=.*#\1=***generated***#; s#(postgresql\+psycopg://lifelink:)[^@]+@#\1***@#'
set -a; . ./.env; set +a

step "9. migrations: doctor (read-only) -> upgrade head -> current -> check"
run python -m scripts.migration_doctor
run alembic heads
run alembic upgrade head 2>&1 | tail -3
run alembic current
run alembic check
run python -m scripts.migration_doctor

step "10. start API (:$API_PORT), RQ worker, deadline monitor"
uvicorn app.main:app --host 127.0.0.1 --port $API_PORT > "$CS/api.log" 2>&1 & echo $! >> "$CS/pids"
python -m app.workers.worker > "$CS/worker.log" 2>&1 & echo $! >> "$CS/pids"
python -m app.workers.deadline_monitor > "$CS/monitor.log" 2>&1 & echo $! >> "$CS/pids"
for i in $(seq 1 60); do curl -sf http://127.0.0.1:$API_PORT/health/ready >/dev/null && break; sleep 1; done
echo "\$ curl /health/live";  curl -s http://127.0.0.1:$API_PORT/health/live; echo
echo "\$ curl /health/ready"; curl -s http://127.0.0.1:$API_PORT/health/ready; echo
sleep 3; echo "worker log:"; tail -3 "$CS/worker.log"; echo "monitor log:"; tail -2 "$CS/monitor.log"

step "11. seed the documented synthetic dataset"
run python -m scripts.seed_demo_data 2>&1 | grep -vE "httpx|lifelink.access" | tail -8

step "12. frontend: .env, npm ci, build, preview (:$FE_PORT)"
cd "$F"
cp .env.example .env && sed -i "s#^VITE_API_URL=.*#VITE_API_URL=http://127.0.0.1:$API_PORT#" .env
cat .env | grep -v '^#'
run npm ci --no-audit --no-fund --loglevel=error || exit 1
run npm run build 2>&1 | tail -4
npx vite preview --host 127.0.0.1 --port $FE_PORT --strictPort > "$CS/preview.log" 2>&1 & echo $! >> "$CS/pids"
for i in $(seq 1 30); do curl -sf http://127.0.0.1:$FE_PORT/ >/dev/null && break; sleep 1; done

step "13. functional verification (scripts/clean_start_check.py)"
cd "$B"
run python -m scripts.clean_start_check --api http://127.0.0.1:$API_PORT \
  --frontend http://127.0.0.1:$FE_PORT --mail-api http://127.0.0.1:$MAILWEB_PORT/email \
  --out "$CS/clean_start_result.json"

step "14. browser: fresh SPA -> fresh API (Playwright, e2e/clean_start.spec.ts)"
cd "$F"
CLEAN_FRONTEND_URL=http://127.0.0.1:$FE_PORT CLEAN_API_URL=http://127.0.0.1:$API_PORT \
  run npx playwright test e2e/clean_start.spec.ts --reporter=list 2>&1 | tail -40

step "15. done at $(ts); API log errors: $(grep -ciE ' ERROR |Traceback' "$CS/api.log")"
