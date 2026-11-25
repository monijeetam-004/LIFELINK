#!/usr/bin/env bash
# TRUE Docker clean start of LifeLink (fresh source copy, fresh volumes).
set -u
SRC=/home/claude/lifelink/LIFELINK; W=/srv/dockerclean; OUT=$1
OV=$W/LIFELINK/docs/final-verification/external_validation/docker/docker-compose.local-images.yml
ts(){ date -u +%FT%TZ; }
step(){ echo; echo "=== [$(ts)] $*"; }
step "0. fresh source copy (no .env, .venv, node_modules, dist, caches, .git)"
rm -rf $W; mkdir -p $W/LIFELINK
(cd $SRC && tar --exclude=.venv --exclude=node_modules --exclude=.env --exclude=dist --exclude=__pycache__ --exclude=.ruff_cache \
  --exclude=.pytest_cache --exclude=storage --exclude=.local-storage --exclude=.git --exclude=test-results --exclude=playwright-report \
  --exclude='*.egg-info' -cf - .) | tar -xf - -C $W/LIFELINK
cd $W/LIFELINK/backend
C="docker compose -f docker-compose.yml -f $OV"
step "1. existing volumes before clean-up (destroying only if they belong to this throwaway project)"
docker volume ls --format '{{.Name}}'; echo "(end of list)"
step "2. docker compose down -v (project 'lifelink'; no pre-existing volumes, so nothing of value is deleted)"
# The compose file requires AUDIT_CHECKPOINT_KEY for every command (E4); the
# fresh copy has no .env yet, so give a throwaway value for the tear-down only.
AUDIT_CHECKPOINT_KEY=teardown-only-not-a-key $C --profile mailpit --profile graph down -v --remove-orphans 2>&1 | tail -12
docker ps -a --filter label=com.docker.compose.project=lifelink --format '{{.Names}}'; echo "(containers left in project lifelink: none above = clean)"
docker volume ls --filter label=com.docker.compose.project=lifelink --format '{{.Name}}'; echo "(volumes left in project lifelink: none above = clean)"
step "3. .env from .env.example; only SECRET_KEY and AUDIT_CHECKPOINT_KEY generated"
cp .env.example .env
sed -i "s#^SECRET_KEY=.*#SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')#" .env
# Audit E4 (2026-10-31): the compose stack requires a checkpoint key; a fresh
# random one is generated for this throwaway run and never printed.
sed -i "s#^AUDIT_CHECKPOINT_KEY=.*#AUDIT_CHECKPOINT_KEY=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')#" .env
grep -vE '^\s*#|^\s*$' .env | sed -E 's#^(SECRET_KEY|AUDIT_CHECKPOINT_KEY)=.+#\1=***generated***#' | head -40
step "4. compose config"
$C config --services
step "5. build images"
$C build 2>&1 | grep -E "Built|naming to|ERROR|error" | tail -12
step "6. up -d (health-gated start order from docker-compose.yml)"
$C up -d 2>&1 | tail -15
step "7. wait for health"
for i in $(seq 1 120); do
  st=$($C ps --format '{{.Service}}={{.Health}}' 2>/dev/null | tr '\n' ' ')
  echo "$st" | grep -q "api=healthy" && break; sleep 3
done
$C ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
step "8. api container log: migration doctor + alembic + uvicorn"
$C logs api 2>&1 | grep -vE "GET /health|lifelink.access|uvicorn.access" | head -25
step "9. migration head inside the running stack"
$C exec -T api alembic current 2>&1 | tail -1
$C exec -T api python -m scripts.migration_doctor 2>&1 | head -2
step "10. health"
curl -s http://localhost:8000/health/live; echo; curl -s http://localhost:8000/health/ready; echo
step "11. createbuckets job + bucket privacy"
$C logs createbuckets 2>&1 | tail -4
$C ps -a --format '{{.Service}} {{.Status}}' | grep createbuckets
step "12. seed (inside the api container)"
$C exec -T api python -m scripts.seed_demo_data 2>&1 | grep -E "Demo accounts|Graph resync|Traceback|Error" | sed 's/password: [^)]*)/password: <documented demo password>)/' | cut -c1-150
step "13. frontend container"
curl -s -o /dev/null -w "GET http://localhost:5173/ -> %{http_code}\n" http://localhost:5173/
curl -s -o /dev/null -w "GET http://localhost:5173/hospital/units (SPA fallback) -> %{http_code}\n" http://localhost:5173/hospital/units
step "14. functional checks INSIDE the api container (service DNS: db, redis, minio, mailpit, clamav, frontend)"
$C exec -T api python -m scripts.clean_start_check --api http://localhost:8000 --frontend http://frontend:8080 \
  --origin http://localhost:5173 --mail-api http://mailpit:8025/api/v1/messages --out /tmp/docker_check_result.json
$C cp api:/tmp/docker_check_result.json $OUT/docker_check_result.json
step "15. browser against the CONTAINERISED frontend (host Chromium -> localhost:5173 -> localhost:8000)"
cd $SRC/frontend
CONTAINER_FRONTEND_URL=http://localhost:5173 CONTAINER_API_URL=http://localhost:8000 npx playwright test e2e/container_frontend.spec.ts --reporter=list 2>&1 | tail -12
CLEAN_FRONTEND_URL=http://localhost:5173 CLEAN_API_URL=http://localhost:8000 npx playwright test e2e/clean_start.spec.ts --reporter=list 2>&1 | tail -6
cd $W/LIFELINK/backend
step "16. service evidence"
$C ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}'
$C exec -T minio mc alias set loc http://localhost:9000 lifelink lifelink-secret >/dev/null 2>&1; $C exec -T minio mc anonymous get loc/lifelink-dev
$C exec -T minio mc ls --recursive loc/lifelink-dev | sed -E 's#[0-9a-f-]{36}#<uuid>#g' | head -5
$C exec -T minio minio --version | head -1
curl -s http://localhost:18025/api/v1/info | python3 -c "import json,sys;d=json.load(sys.stdin);print('mailpit',d['Version'],'messages',d['Messages'],'smtp accepted',d['RuntimeStats']['SMTPAccepted'])"
$C exec -T clamav sh -c 'echo VERSION | nc -w 3 127.0.0.1 3310'
$C exec -T -e PGPASSWORD=lifelink db psql -h 127.0.0.1 -U lifelink -d lifelink -Atc "select version(), current_setting('server_encoding')" | cut -c1-90
$C exec -T redis redis-cli INFO server | grep redis_version
step "16b. security headers served by the containerised frontend (nginx.conf)"
curl -sI http://localhost:5173/ | grep -iE "^(HTTP|x-frame-options|content-security-policy|x-content-type-options|referrer-policy)"
A=$(curl -s http://localhost:5173/ | grep -oE '/assets/[^"]+\.js' | head -1); echo "asset: $A"
curl -sI "http://localhost:5173$A" | grep -iE "^(HTTP|x-frame-options|content-security-policy|x-content-type-options|cache-control)"
step "16c. audit checkpoints enabled and required in the stack (E4)"
curl -s http://localhost:8000/health/ready | python3 -c "import json,sys;print('audit_checkpoints', json.load(sys.stdin).get('audit_checkpoints'))"
$C exec -T api sh -c 'ls -l /var/lib/lifelink-audit/ 2>&1 | tail -3'
step "17. health of non-HTTP services after their checks have run (wait 70 s)"
sleep 70; $C ps --format "table {{.Service}}\t{{.Status}}" | grep -E "SERVICE|worker|deadline"
echo "done $(ts)"
