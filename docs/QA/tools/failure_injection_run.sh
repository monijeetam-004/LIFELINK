#!/usr/bin/env bash
# Start a dedicated API (:8400) on the disposable QA database and run
# failure_injection.py against it. Run after `qa_stack.sh up` has created and
# seeded `lifelink_qa`, and after `qa_stack.sh down` (so no other worker
# consumes the e-mail queue). Usage: failure_injection_run.sh OUT_FILE
set -u
OUT=$1
L=$(cd "$(dirname "$0")/../../.." && pwd); B=$L/backend; T=$L/docs/QA/tools
RUN=${QA_RUN_DIR:-/tmp/lifelink_qa_run}; mkdir -p "$RUN"
set -a; . "${MINIO_ENV:-/srv/realsvc/minio.env}"; set +a
BASEURL=$(grep ^DATABASE_URL "$B/.env" | cut -d= -f2- | sed 's#/lifelink$##')
export ENVIRONMENT=development DATABASE_URL=$BASEURL/lifelink_qa REDIS_URL=redis://127.0.0.1:6379/5 \
  RQ_SYNC=false SECRET_KEY=qa-round-secret-key-not-for-production-0123456789 \
  S3_ENDPOINT_URL=http://127.0.0.1:9000 S3_ACCESS_KEY=$MINIO_ROOT_USER S3_SECRET_KEY=$MINIO_ROOT_PASSWORD \
  S3_BUCKET=lifelink-qa MAIL_HOST=127.0.0.1 MAIL_PORT=1025 MAIL_STARTTLS=false MAIL_USE_SSL=false \
  DOCUMENT_SCANNER=clamd CLAMD_HOST=127.0.0.1 CLAMD_PORT=3310 NEO4J_URI= \
  CORS_ORIGINS="http://localhost:5173" FRONTEND_BASE_URL=http://localhost:4173 RL_LOGIN_IP="[50,300]"
unset MINIO_ROOT_USER MINIO_ROOT_PASSWORD
cd "$B"
# The documented production entrypoint (bounded graceful shutdown, proxy
# headers); it runs `python3`, so the venv goes first on PATH.
PATH="$B/.venv/bin:$PATH" HOST=127.0.0.1 PORT=8400 nohup scripts/run_production.sh > "$RUN/fi_api.log" 2>&1 &
API_PID=$!
for i in $(seq 1 60); do curl -sf http://127.0.0.1:8400/health/ready >/dev/null && break; sleep 1; done
.venv/bin/python "$T/failure_injection.py" --api http://127.0.0.1:8400 --api-pid $API_PID \
  --mailpit http://127.0.0.1:8025 --worker-cmd "$B/.venv/bin/python -m app.workers.worker" \
  --worker-pid "$RUN/fi_worker.pid" --out "$OUT"
rc=$?
kill $API_PID 2>/dev/null
exit $rc
