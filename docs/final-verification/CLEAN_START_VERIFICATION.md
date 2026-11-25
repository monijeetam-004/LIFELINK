# Clean-start verification

**Question:** can LifeLink be rebuilt from an empty machine into a *usable* system, using only the documented setup steps?

**Answer: yes, with external validation still required.**

* Every LifeLink component was rebuilt from nothing and works end to end: database, migrations, API, worker, deadline monitor, malware scanning, e-mail delivery through the worker, document storage, seed data, the frontend build, and a complete emergency-to-delivery workflow in the browser and over the API.
* Three external services could not be obtained in this environment (container registries, dist.neo4j.org and dl.min.io are blocked; see `evidence/egress_probe.txt`). They are reported as unverified, never as PASS:
  * **Neo4j**: not run at all.
  * **MinIO**: the S3 code path was verified against an S3 stand-in instead.
  * **Mailpit**: the SMTP path was verified against the MailDev stand-in instead.

**Final run:** 2026-10-13, 06:35:11 to 06:40:58 UTC, on the final code. Verdict: **PASS WITH EXTERNAL VALIDATION REQUIRED**.

* Complete log: `clean_start/clean_start.log`.
* Machine-readable result: `clean_start/clean_start_result.json`.
* Harness: `clean_start/run_clean_start.sh`. Checker: `backend/scripts/clean_start_check.py`. Browser check: `frontend/e2e/clean_start.spec.ts`.

## What "clean" means here

Nothing from the development environment was reused:

* **Source:** a fresh copy of the working tree without `.venv`, `node_modules`, `.env`, `dist`, caches, `.git` or `.local-storage`.
* **Python:** a new Python 3.12 venv built from the hashed lock file, exactly as the README says (`pip install --require-hashes --no-deps -r requirements-dev.lock` then `pip install --no-deps -e .`).
* **Frontend:** a fresh `npm ci` and `npm run build`.
* **PostgreSQL:** a brand-new cluster (`initdb`) on its own port, so no earlier database, volume or role is involved.
* **Redis:** a fresh server with persistence switched off.
* **ClamAV:** a new `clamd` with a signature database built at run time from `backend/clamav/build_local_signatures.py`.
* **Configuration:** `.env` copied from `.env.example`, changing only documented keys (ports, `DOCUMENT_SCANNER=clamd`, `RQ_SYNC=false`). `SECRET_KEY` and the database password were generated at run time and are masked in the log. `S3_SECRET_KEY=lifelink-secret` in the log is the public development default from `.env.example`.
* **No manual changes:** no database rows were edited by hand. All data came from migrations, the documented seed and the workflow itself.

**Docker.** `docker compose up` could not run here because image pulls are blocked. The same services were therefore started as native processes, in the order the compose file defines: database and cache, then migrations, then API, then worker. The compose file itself is valid (`evidence/docker_compose_services.txt`).

## Procedure and timestamps

| Step | UTC | Command / action | Result |
|---|---|---|---|
| 0 | 06:35:11 | empty work dir `/srv/cleanstart` | — |
| 1 | 06:35:11 | fresh copy of the source tree | OK |
| 2 | 06:35:14 | `python3.12 -m venv .venv`; `pip install --require-hashes --no-deps -r requirements-dev.lock`; `pip install --no-deps -e .` | exit 0 |
| 3 | 06:36:23 | `initdb` (scram-sha-256); `CREATE ROLE lifelink`; `CREATE DATABASE lifelink` | new cluster on :55432 |
| 4 | 06:36:25 | `redis-server --save '' --appendonly no` | PONG |
| 5 | 06:36:25 | `python clamav/build_local_signatures.py …`; `clamd` | 6 body + 17 hash signatures; PONG |
| 6 | 06:36:25 | S3 stand-in (moto_server) :9300; SMTP stand-in (MailDev) :1036, web :1091 | HTTP 200 / 200 |
| 7 | 06:36:31 | Neo4j: not obtainable; `NEO4J_URI` left empty | — |
| 8 | 06:36:31 | `cp .env.example .env` plus documented overrides | shown, masked, in the log |
| 9 | 06:36:32 | `python -m scripts.migration_doctor` → `alembic upgrade head` → `alembic current` → `alembic check` → doctor again | EMPTY (31 pending) → **`e1f2a3b4c5d6 (head)`** → "No new upgrade operations detected" → AT_HEAD, 39 tables, schema equals the models |
| 10 | 06:36:40 | `uvicorn app.main:app`; `python -m app.workers.worker`; `python -m app.workers.deadline_monitor` | `/health/live` `{"status":"alive"}`; `/health/ready` `{"status":"ready"}`; worker listening on `emails`, `sms`; monitor first pass OK |
| 11 | 06:36:46 | `python -m scripts.seed_demo_data` | exit 0 |
| 12 | 06:39:51 | frontend: `cp .env.example .env` (`VITE_API_URL=http://127.0.0.1:8200`); `npm ci`; `npm run build`; `vite preview` :4300 | 450 packages; built |
| 13 | 06:40:40 | `python -m scripts.clean_start_check …` | 10 PASS, 2 BLOCKED, 0 FAIL |
| 14 | 06:40:49 | `npx playwright test e2e/clean_start.spec.ts` | 1 passed |
| 15 | 06:40:58 | API log scanned for errors and tracebacks | 0 |

## Functional checks (items 16–17)

| Required check | How it was really exercised | Result |
|---|---|---|
| API readiness | `GET /health/ready` → 200 `ready` (db and redis AVAILABLE) | **PASS** |
| Database connectivity | `SELECT version()` through `DATABASE_URL` → PostgreSQL 16.15 | **PASS** |
| Alembic reaches the intended head | the doctor reports AT_HEAD; DB revision `e1f2a3b4c5d6` equals the repository's single head; the schema equals the models | **PASS** |
| Neo4j connectivity | no server obtainable; not configured; the planner ran on the relational engine | **BLOCKED** (external validation required) |
| Redis connectivity | `PING` → True; redis 7.0.15 | **PASS** |
| MinIO bucket exists, writable, readable | bucket `lifelink-dev` exists; 1,024 B written, read back byte-identical, then deleted. The server is **moto (an S3 stand-in), not MinIO** | **PASS WITH EXTERNAL VALIDATION REQUIRED**: the S3 code path works; MinIO itself is unverified |
| ClamAV reachable and really scans | `PING` → PONG; `VERSION` → ClamAV 1.5.4; a clean payload comes back CLEAN; EICAR comes back INFECTED (`LifeLink.Test.EICAR`) | **PASS** |
| Mail catcher accepts and displays a test e-mail | the verification e-mail for a new account was delivered over SMTP and appeared in the catcher's HTTP API within 2.2 s. The catcher is **MailDev, a stand-in for Mailpit** | **PASS WITH EXTERNAL VALIDATION REQUIRED**: SMTP delivery works; Mailpit itself is unverified |
| Worker processes a representative job | `POST /auth/register` with `RQ_SYNC=false` enqueued the e-mail job; the separate worker process sent it; the token in the e-mail then verified the account (`POST /auth/verify-email` → 200) | **PASS** |
| Synthetic seed data | 1,066 users (1,002 donors, 61 hospitals, 1 each of patient, family and admin); 61 facilities (41 hospitals of which 26 verified, 20 banks of which 15 verified); 85 routes (78 operational, 7 disrupted); 9,675 available blood units; 614 donations | **PASS** |
| Frontend connects to the API | the built SPA is served (200, root present) and its bundle targets the fresh API. In Chromium, demo-hospital sign-in → `/auth/login` 200 → `/hospitals/me/units` 200 with 183 units → first unit rendered in the table | **PASS** |
| A complete critical workflow end to end | see below | **PASS** |
| Malware-scanned document upload (extra) | the new donor uploads an EICAR PDF → 422, nothing stored; then a clean PDF → 201, stored in object storage, scan CLEAN; exactly 1 verification request on the account | **PASS** |

### The workflow (over HTTP, demo accounts, real services)

1. The receiving hospital (LifeLink Demo Hospital, Mumbai) holds 20 O-negative units. The patient's request asks for 23, so a delivery is needed.
2. The admin adds routes to the two nearest verified facilities that hold O-negative stock: Mumbai Blood Bank (4.9 km) and Pune Community Hospital (150.8 km).
3. The patient sends an SOS with coordinates. `geocode_source` is `USER_INPUT`.
4. The hospital plans the delivery: **VIABLE**, 23 of 23 units, 1 leg, ETA 38 min, 1 stored backup, engine `RELATIONAL_ROUTES`.
5. The hospital reserves the 23 units.
6. The admin disrupts the primary route. The outcome is **BACKUP_PROMOTED** and the emergency is still reachable; delivery now comes from Pune Blood Bank over 2 routes.
7. The hospital releases the reservation and the source facility ships `SHP-C765B2B02E` (23 units). It is dispatched, logger readings come back **WITHIN_RANGE**, and it goes in transit and arrives.
8. The hospital receives the shipment: its O-negative stock goes from **20 to 43**.
9. The patient resolves the emergency (8 timeline events), and the admin's audit-chain verification returns **ok = true**.

## Verdict per item 19

* **Overall: PASS WITH EXTERNAL VALIDATION REQUIRED.**
* **LifeLink itself: PASS.** Every LifeLink-owned component and workflow passed on a clean machine.
* **Still to validate against the real products:** Neo4j (graph engine and resync), MinIO (bucket policy and real server), Mailpit (UI and API) and the Docker images. The commands are in DOCKER_SETUP.md, NEO4J_SETUP.md, MINIO_SETUP.md and MAILPIT_SETUP.md.
* **Not part of a clean start:** Gmail and MSG91 SMS need real credentials (GMAIL_SETUP.md, SMS_SETUP.md).

## Defects this test found (fixed and re-run)

* **Non-reproducible seed.** The "deterministic" seed chose which routes to disrupt from random UUIDs, so three fresh databases had 6, 7 and 11 disrupted routes. The choice is now keyed by stable e-mails, giving 7 every time; a test guards this.
* **Two defects in the checker itself, not in LifeLink:**
  * the workflow looked up the failover source among the two partner facilities only;
  * the browser check raced the units request.

  Both were fixed, and the whole clean start was re-run from scratch.

## Addendum (2026-10-13): Docker clean start with real MinIO and real Mailpit

This addendum is part of the final external validation (FINAL_EXTERNAL_VALIDATION_REPORT.md). The native run above is kept unchanged as historical evidence.

**What was different this time:**

* `docker compose up --build` ran for real.
* Every service came from the unchanged `backend/docker-compose.yml`: db, redis, mailpit, minio, createbuckets, clamav, api, worker, deadline-monitor and frontend.
* Official images cannot be pulled here (403), so only the image *sources* were replaced, through `external_validation/docker/docker-compose.local-images.yml`:
  * MinIO and Mailpit are the **official release binaries**;
  * PostgreSQL 16, Redis 7, ClamAV and nginx come from the Ubuntu 24.04 archive;
  * Node 20.18.3 is the official binary.
* Harness: `external_validation/scripts/docker_clean_start.sh`. Full log: `evidence/external/docker/docker_clean_start_run.log`.

**Volume safety:** step 1 listed the existing volumes before anything was removed. They were `lifelink_pgdata`, `lifelink_miniodata`, `lifelink_clamavdb` and one anonymous volume, all created by this sandbox's own earlier verification attempts. They held no developer data, and the migration doctor and recovery procedure are untouched.

| Step | UTC | Action | Result |
|---|---|---|---|
| 0 | 08:41:20 | fresh source copy (no `.env`, venv, node_modules, dist, .git) | OK |
| 1–2 | 08:41:21 | list volumes, then `docker compose --profile mailpit --profile graph down -v` | no containers or volumes left in project `lifelink` |
| 3 | 08:41:35 | `.env` from `.env.example`; only `SECRET_KEY` generated (masked) | OK |
| 4–6 | 08:41:35 | `compose config`, `build`, `up -d` | OK |
| 7 | 08:41:51 | wait for health | all healthy |
| 8–9 | 08:41:52 | api log: migration doctor → `alembic upgrade head` → uvicorn; head `e1f2a3b4c5d6` | OK |
| 11 | 08:41:54 | `createbuckets`: `lifelink-dev` created, policy **private** | OK |
| 12 | 08:41:54 | documented seed inside the api container | 1,066 users, 61 facilities, 85 routes |
| 14 | 08:44:44 | `scripts/clean_start_check.py` inside the api container | **11 PASS, 1 BLOCKED (Neo4j)** of 12 |
| 15 | 08:45:01 | Playwright against the containerised frontend (`container_frontend.spec.ts`, `clean_start.spec.ts`) | 2/2 passed; 0 failed assets, 0 page or console errors |
| 16–17 | 08:45:17 | service versions; health of the non-HTTP services after 70 s | worker and deadline-monitor **healthy** |

**Checks that ran against the real products this time:**

* **object_storage:** server header `MinIO`; a 1,024 B object written, read back identical, and deleted.
* **document_upload:** a clean PDF gave 201 and was stored in MinIO (the listing shows the object). An EICAR PDF gave 422.
* **worker_and_mail:** the RQ worker delivered the verification e-mail to Mailpit in 11.4 s, and the token in that e-mail verified the account.

**Defect found and fixed:**

* `worker` and `deadline-monitor` were permanently `unhealthy`, because they inherited the API's HTTP health check.
* They now have their own checks: RQ registration, and a heartbeat file.
* The run above is the re-run after the fix.

**Verdict for this run:** **PASS WITH EXTERNAL VALIDATION REQUIRED**.

* Neo4j could not be obtained.
* The official base images could not be pulled.
* MinIO and Mailpit are no longer outstanding.
