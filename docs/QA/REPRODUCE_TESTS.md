# Reproducing the QA results

**Paths.** Every command below is the one used in this round. Paths are relative to the repository root unless shown otherwise.

**Disposable environment.** Use a disposable environment: the commands create and drop databases whose names contain `test`, `e2e`, `qa` or `bench`, and flush Redis databases 0, 5 and 6.

**Credentials.** MinIO root credentials are read from a 0600 file outside the repository (`/srv/realsvc/minio.env` in the sandbox). They are never printed.

## 0. Services

| Service | Version used | How it was run |
|---|---|---|
| PostgreSQL | 16 | `service postgresql start` |
| Redis | 7.0.15 | `redis-server --daemonize yes --save '' --appendonly no` |
| clamd | ClamAV 1.5.4 | Project-built signatures: `backend/clamav/build_local_signatures.py` |
| MinIO | RELEASE.2025-04-22T22-12-26Z | Official .deb, 127.0.0.1:9000 |
| Mailpit | v1.21.8 | Official binary; SMTP 127.0.0.1:1025, HTTP :8025 |
| Node | 22.22.0 | Host; CI pins Node 20 |
| Python | 3.11.17 | Backend venv from `requirements-dev.lock` |
| Chromium | Playwright's pinned build at `/opt/pw-browsers/chromium` | — |

The sandbox helper `/srv/realsvc/services_up.sh` starts any service that is not running and prints the health of each.

## 1. Backend suite (with real MinIO, Mailpit and clamd)

```bash
cd backend
export CLAMD_TEST_REQUIRED=1 \
  S3_TEST_ENDPOINT=http://127.0.0.1:9000 S3_TEST_ACCESS_KEY=<minio user> S3_TEST_SECRET_KEY=<minio password> \
  SMTP_TEST_HOST=127.0.0.1 SMTP_TEST_PORT=1025 SMTP_TEST_API=http://127.0.0.1:8025/api/v1/messages \
  QA_ROUTE_LOG=../docs/QA/evidence/final/routes_final.log
.venv/bin/python -m pytest -p no:cacheprovider -o addopts= -q -rs --junitxml=../docs/QA/evidence/final/backend_junit_final.xml
```

* `QA_ROUTE_LOG` records every (method, route, status) the suite produced. The API coverage table is built from it.
* Only the new tests of this round: `pytest tests/test_qa_round_races.py -v`.

## 2. Frontend unit tests and static checks

```bash
cd frontend
npx vitest run
npx tsc --noEmit
npx eslint .
npm run build
npm run audit:gate
cd ../backend
.venv/bin/ruff check .
.venv/bin/alembic heads
.venv/bin/alembic check
```

## 3. Browser end-to-end tests (CI-identical)

Scripted since 2026-10-31: `docs/QA/tools/e2e_ci_harness.sh OUT_DIR [SPEC ...]` performs every step below.

This mirrors the `e2e` job of `.github/workflows/ci.yml`:

* a fresh `lifelink_e2e` database;
* `RQ_SYNC=true`;
* **no** SMTP server listening;
* `RL_LOGIN_IP="[1000,300]"` for the suite only.

```bash
# backend: alembic upgrade head; python -m scripts.e2e_seed; python -m scripts.seed_demo_data;
#          uvicorn app.main:app --port 8000
# frontend: npm run build; npx vite preview --port 4173
E2E_PYTHON=$PWD/../backend/.venv/bin/python npx playwright test
cd ../backend && python -m scripts.check_invariants
```

## 4. QA evidence runs (full stack, real worker, real Mailpit, screenshots)

```bash
docs/QA/tools/qa_stack.sh up        # fresh lifelink_qa, API :8000 + RQ worker, preview :4173
cd frontend
SECRET_KEY=qa-round-secret-key-not-for-production-0123456789 \
DATABASE_URL=<…>/lifelink_qa REDIS_URL=redis://127.0.0.1:6379/5 \
QA_SCREENSHOT_DIR=../docs/QA/evidence/workflows MAILPIT_URL=http://127.0.0.1:8025 \
E2E_PYTHON=../backend/.venv/bin/python npx playwright test e2e/qa_evidence.spec.ts e2e/qa_failover_ui.spec.ts
```

The Playwright process needs the stack's `DATABASE_URL`, `REDIS_URL` and `SECRET_KEY`, because the spec's helpers issue a verification token directly.

**Route crawl** (every route × 375/768/1280 px, axe, and an authorisation check for each guarded route):

```bash
cd backend
ENVIRONMENT=test .venv/bin/python -m scripts.qa_inventory ../docs/QA/evidence/inventory --routes-log <routes log>
.venv/bin/python ../docs/QA/tools/crawl_params.py ../docs/QA/evidence/inventory/inventory.json ../docs/QA/evidence/frontend-routes
cd ../frontend
node scripts/qa-crawl.mjs ../docs/QA/evidence/frontend-routes ../docs/QA/evidence/frontend-routes/params.json
```

## 5. Failure injection and security probe

**Failure injection:**

```bash
docs/QA/tools/qa_stack.sh up && docs/QA/tools/qa_stack.sh down     # seeded DB, then free the worker
docs/QA/tools/failure_injection_run.sh docs/QA/evidence/failure-injection/run2_after_fixes.txt
```

* The script stops and restarts Redis, PostgreSQL, MinIO, clamd, the RQ worker and Mailpit. It finally sends SIGTERM to the API, which is started through `scripts/run_production.sh`.
* Adapt the `SERVICES` table in `failure_injection.py` to your machine.

**Security probe** (production entrypoint; no proxy is trusted, so forged `X-Forwarded-For` headers are attacker input):

```bash
FORWARDED_ALLOW_IPS=192.0.2.1 HOST=127.0.0.1 PORT=8500 PATH=backend/.venv/bin:$PATH \
  DATABASE_URL=<…>/lifelink_qa REDIS_URL=redis://127.0.0.1:6379/6 … backend/scripts/run_production.sh &
backend/.venv/bin/python docs/QA/tools/security_probe.py --api http://127.0.0.1:8500 --out docs/QA/evidence/security/security_probe_live.txt
bandit -r backend/app -f txt -o docs/QA/evidence/security/bandit_app.txt
pip-audit -r backend/requirements.lock --no-deps --disable-pip
```

## 6. Performance

```bash
cd backend
python -m scripts.benchmark_pathways --database-url <…>/lifelink_bench --sizes small medium large --requests 50 --seed 7 --out ../docs/QA/evidence/performance
WEB_CONCURRENCY=2 PORT=8600 scripts/run_production.sh &       # on the demo-seeded QA database
python -m scripts.loadtest_hardening --base http://127.0.0.1:8600 --concurrency 1,8,32 --seconds 15 --sse 100 --per-path --server-pid <pid>
python ../docs/QA/tools/query_counts.py                        # SQL statements per hot endpoint
```

## 7. Clean starts

* **Native** (fresh venv from the hashed lock, a new `initdb` cluster, fresh Redis and clamd, `npm ci`):

  ```bash
  docs/final-verification/clean_start/run_clean_start.sh <repo> <empty work dir> <moto_server> <maildev>
  ```

  This harness keeps the S3 and SMTP stand-ins by design. The real MinIO and Mailpit are exercised by the Docker clean start and by §1 and §4.

* **Docker** (`down -v` → `.env` from `.env.example` → `up --build` → checks inside the API container → Playwright against the containerised frontend → security-header check):

  ```bash
  docs/final-verification/external_validation/scripts/docker_clean_start.sh <evidence dir>
  ```

## 8. Remaining external validations

The exact commands for Neo4j, Gmail, MSG91, the live geocoder and the official base images are in INTEGRATION_STATUS.md.
