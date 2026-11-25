#!/usr/bin/env bash
# Application-level validation against the REAL MinIO and REAL Mailpit
# (official binaries), real clamd, real PostgreSQL and Redis. Native processes.
set -u
R=/srv/realsvc; L=/home/claude/lifelink/LIFELINK; B=$L/backend; OUT=$1
set -a; . $R/minio.env; set +a
BASEURL=$(grep ^DATABASE_URL $B/.env | cut -d= -f2- | sed 's#/lifelink$##')
export DATABASE_URL=$BASEURL/lifelink_ext REDIS_URL=redis://127.0.0.1:6379/3 RQ_SYNC=false \
  S3_ENDPOINT_URL=http://127.0.0.1:9000 S3_ACCESS_KEY=$MINIO_ROOT_USER S3_SECRET_KEY=$MINIO_ROOT_PASSWORD S3_BUCKET=lifelink-dev \
  MAIL_HOST=127.0.0.1 MAIL_PORT=1025 MAIL_STARTTLS=false DOCUMENT_SCANNER=clamd CLAMD_HOST=127.0.0.1 CLAMD_PORT=3310 \
  CORS_ORIGINS=http://127.0.0.1:4400 FRONTEND_BASE_URL=http://127.0.0.1:4400 NEO4J_URI=
echo "=== $(date -u +%FT%TZ) fresh database lifelink_ext"
su postgres -c "psql -q -c 'DROP DATABASE IF EXISTS lifelink_ext WITH (FORCE)' -c 'CREATE DATABASE lifelink_ext OWNER lifelink'"
redis-cli -n 3 flushdb >/dev/null
cd $B
.venv/bin/python -m scripts.migration_doctor | head -1
.venv/bin/alembic upgrade head 2>&1 | tail -1
.venv/bin/python -m scripts.migration_doctor | head -1
echo "=== $(date -u +%FT%TZ) start API :8300 + worker"
nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8300 > $OUT/api.log 2>&1 & echo $! > $OUT/api.pid
nohup .venv/bin/python -m app.workers.worker > $OUT/worker.log 2>&1 & echo $! > $OUT/worker.pid
for i in $(seq 1 60); do curl -sf http://127.0.0.1:8300/health/ready >/dev/null && break; sleep 1; done
curl -s http://127.0.0.1:8300/health/ready; echo
echo "=== $(date -u +%FT%TZ) seed"
.venv/bin/python -m scripts.seed_demo_data 2>&1 | grep -E "Demo accounts|Graph resync" | sed 's/password: .*)/password: <documented demo password>)/' | cut -c1-120
echo "=== $(date -u +%FT%TZ) frontend build against :8300"
cd $L/frontend && VITE_API_URL=http://127.0.0.1:8300 npx vite build --outDir $R/fe-dist --emptyOutDir 2>&1 | tail -1
nohup npx vite preview --outDir $R/fe-dist --host 127.0.0.1 --port 4400 --strictPort > $OUT/preview.log 2>&1 & echo $! > $OUT/preview.pid
for i in $(seq 1 30); do curl -sf http://127.0.0.1:4400/ >/dev/null && break; sleep 1; done
echo "=== $(date -u +%FT%TZ) functional checks"
cd $B && .venv/bin/python -m scripts.clean_start_check --api http://127.0.0.1:8300 --frontend http://127.0.0.1:4400 \
  --mail-api http://127.0.0.1:8025/api/v1/messages --out $OUT/native_check_result.json
echo "=== $(date -u +%FT%TZ) done"
