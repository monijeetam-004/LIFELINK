#!/usr/bin/env bash
# Run the Playwright suite the way the CI `e2e` job does (.github/workflows/ci.yml):
# fresh lifelink_e2e database, RQ_SYNC=true, real clamd, local-disk storage,
# no Neo4j, no SMTP server reachable, RL_LOGIN_IP relaxed for the suite only.
#
#   docs/QA/tools/e2e_ci_harness.sh OUT_DIR [SPEC ...]
#
# With SPEC arguments, only those spec files run (same fresh setup).
#
# Needs PostgreSQL (role `lifelink`), Redis and clamd on their default local
# ports, backend/.venv and frontend/node_modules. Prints the list reporter to
# stdout; writes OUT_DIR/playwright_junit.xml and OUT_DIR/invariants_after_e2e.txt. It DROPS
# and re-creates only the throwaway database `lifelink_e2e`.
set -u
OUT=$(realpath "$1"); mkdir -p "$OUT"; shift
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
B=$ROOT/backend; F=$ROOT/frontend
export ENVIRONMENT=development SECRET_KEY=ci-e2e-secret-key-not-for-production-use \
  DATABASE_URL=postgresql+psycopg://lifelink:lifelink@localhost:5432/lifelink_e2e \
  REDIS_URL=redis://localhost:6379/0 RQ_SYNC=true DOCUMENT_SCANNER=clamd \
  CLAMD_HOST=127.0.0.1 CLAMD_PORT=3310 \
  CORS_ORIGINS="http://localhost:5173,http://localhost:4173" RL_LOGIN_IP="[1000,300]" \
  FRONTEND_BASE_URL=http://localhost:4173 VITE_API_URL=http://localhost:8000 \
  S3_ENDPOINT_URL= S3_ACCESS_KEY= S3_SECRET_KEY= NEO4J_URI= NEO4J_USERNAME= NEO4J_PASSWORD= \
  MAIL_HOST=127.0.0.1 MAIL_PORT=2   # CI has no SMTP server: nothing listens on port 2
ts(){ date -u +%FT%TZ; }
echo "=== $(ts) fresh lifelink_e2e"
su postgres -c "psql -q -c 'DROP DATABASE IF EXISTS lifelink_e2e WITH (FORCE)' -c 'CREATE DATABASE lifelink_e2e OWNER lifelink'"
redis-cli -n 0 flushdb >/dev/null
cd "$B"
.venv/bin/alembic upgrade head 2>&1 | tail -1
.venv/bin/python -m scripts.e2e_seed 2>&1 | tail -1
.venv/bin/python -m scripts.seed_demo_data 2>&1 | grep -E "Demo accounts|Graph resync" | sed 's/password: [^)]*)/password: <documented demo password>)/' | cut -c1-120
nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 > "$OUT/e2e_api.log" 2>&1 & API=$!
for i in $(seq 1 60); do curl -sf http://127.0.0.1:8000/health/ready >/dev/null && break; sleep 1; done
cd "$F"
npm run build 2>&1 | tail -1
nohup npx vite preview --host 127.0.0.1 --port 4173 --strictPort > "$OUT/e2e_preview.log" 2>&1 & PRE=$!
for i in $(seq 1 30); do curl -sf http://127.0.0.1:4173/ >/dev/null && break; sleep 1; done
echo "=== $(ts) playwright"
PLAYWRIGHT_JUNIT_OUTPUT_NAME="$OUT/playwright_junit.xml" E2E_PYTHON="$B/.venv/bin/python" \
  npx playwright test --reporter=list,junit "$@"
RC=$?
echo "=== $(ts) playwright exit $RC"
cd "$B" && .venv/bin/python -m scripts.check_invariants > "$OUT/invariants_after_e2e.txt" 2>&1
echo "invariants exit $?"
kill $API $PRE 2>/dev/null
pkill -f "vite preview --host 127.0.0.1 --port 4173" 2>/dev/null
exit $RC
