#!/usr/bin/env bash
# Full local stack for the QA evidence runs (disposable database `lifelink_qa`).
#
#   qa_stack.sh up      fresh DB -> migrate -> seed -> API :8000 + RQ worker + frontend :4173
#   qa_stack.sh down    stop the API, worker and preview started by `up` (pid files)
#
# Real services: PostgreSQL, Redis (db 5), clamd, MinIO (bucket lifelink-qa), Mailpit.
# RQ_SYNC=false: e-mail goes API -> Redis queue -> worker -> SMTP (Mailpit), as in
# production. MinIO root credentials are read from a 0600 file outside the repo
# ($MINIO_ENV) and never printed. Nothing outside `lifelink_qa`, Redis db 5 and the
# `lifelink-qa` bucket is touched.
set -u
L=$(cd "$(dirname "$0")/../../.." && pwd); B=$L/backend; F=$L/frontend
RUN=${QA_RUN_DIR:-/tmp/lifelink_qa_run}; mkdir -p "$RUN"
MINIO_ENV=${MINIO_ENV:-/srv/realsvc/minio.env}

down() {
  for p in api worker preview; do
    [ -f "$RUN/$p.pid" ] && kill "$(cat "$RUN/$p.pid")" 2>/dev/null; rm -f "$RUN/$p.pid"
  done
  sleep 2
}

up() {
  down
  set -a; . "$MINIO_ENV"; set +a
  BASEURL=$(grep ^DATABASE_URL "$B/.env" | cut -d= -f2- | sed 's#/lifelink$##')
  export ENVIRONMENT=development DATABASE_URL=$BASEURL/lifelink_qa REDIS_URL=redis://127.0.0.1:6379/5 \
    RQ_SYNC=false SECRET_KEY=qa-round-secret-key-not-for-production-0123456789 \
    S3_ENDPOINT_URL=http://127.0.0.1:9000 S3_ACCESS_KEY=$MINIO_ROOT_USER S3_SECRET_KEY=$MINIO_ROOT_PASSWORD \
    S3_BUCKET=lifelink-qa MAIL_HOST=127.0.0.1 MAIL_PORT=1025 MAIL_STARTTLS=false MAIL_USE_SSL=false \
    DOCUMENT_SCANNER=clamd CLAMD_HOST=127.0.0.1 CLAMD_PORT=3310 NEO4J_URI= NEO4J_USERNAME= NEO4J_PASSWORD= \
    CORS_ORIGINS="http://localhost:5173,http://localhost:4173" FRONTEND_BASE_URL=http://localhost:4173 \
    RL_LOGIN_IP="[1000,300]"
  unset MINIO_ROOT_USER MINIO_ROOT_PASSWORD
  echo "=== $(date -u +%FT%TZ) fresh lifelink_qa"
  su postgres -c "psql -q -c 'DROP DATABASE IF EXISTS lifelink_qa WITH (FORCE)' -c 'CREATE DATABASE lifelink_qa OWNER lifelink'"
  redis-cli -n 5 flushdb >/dev/null
  cd "$B"
  .venv/bin/python -m scripts.migration_doctor | head -1
  .venv/bin/alembic upgrade head 2>&1 | tail -1
  .venv/bin/python -m scripts.e2e_seed
  .venv/bin/python -m scripts.seed_demo_data 2>&1 | grep -E "Demo accounts|Graph resync" | sed 's/password: [^)]*)/password: <documented demo password>)/' | cut -c1-120
  nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 > "$RUN/api.log" 2>&1 & echo $! > "$RUN/api.pid"
  nohup .venv/bin/python -m app.workers.worker > "$RUN/worker.log" 2>&1 & echo $! > "$RUN/worker.pid"
  cd "$F"
  VITE_API_URL=http://localhost:8000 npx vite build --outDir "$RUN/dist" --emptyOutDir 2>&1 | tail -1
  nohup npx vite preview --outDir "$RUN/dist" --port 4173 --strictPort > "$RUN/preview.log" 2>&1 & echo $! > "$RUN/preview.pid"
  for i in $(seq 1 60); do curl -sf http://localhost:8000/health/ready >/dev/null && break; sleep 1; done
  for i in $(seq 1 60); do curl -sf http://localhost:4173/ >/dev/null && break; sleep 1; done
  echo "ready: $(curl -s http://localhost:8000/health/ready | head -c 200)"
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  *) echo "usage: $0 up|down"; exit 2 ;;
esac
