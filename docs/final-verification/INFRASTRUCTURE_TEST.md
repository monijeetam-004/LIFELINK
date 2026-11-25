# Infrastructure test report

## 1. Components

| Component | Result | Evidence |
|---|---|---|
| PostgreSQL 16 | **PASS**: the whole suite, a migration round trip on a fresh database, the migration-integrity tests (§2), and concurrency tests with real row locks | §2; FINAL_TEST_REPORT.md |
| Redis | **PASS**: rate limits, RQ, realtime | suite; CLEAN_START_VERIFICATION.md |
| ClamAV (clamd) | **PASS**. Real daemon, with a project-local signature DB (the official mirror is blocked; documented). Verified cases: clean file, EICAR, suspicious PDF (JavaScript policy rule; the launch and embedded-file rules are loaded but not separately tested), the ClamAV project test files (`clam.exe`, `clam-upx.exe`), unsupported type, oversized, corrupted, timeout (silent server), unavailable daemon. | `tests/test_gap_clamav_real.py`, `tests/test_hardening_malware_scanning.py`, `backend/clamav/build_local_signatures.py` |
| Docker Compose | **PASS WITH EXTERNAL VALIDATION REQUIRED** (2026-10-13). A real `docker compose up --build` of the unchanged `backend/docker-compose.yml` (project `lifelink`, profiles `mailpit`), with only the image sources replaced by locally built images through `external_validation/docker/docker-compose.local-images.yml`. All 9 long-running services were healthy and `createbuckets` completed. The migration doctor ran before alembic. The official base images could not be pulled (403). | `evidence/external/docker/*` |
| Backend image | **PASS**. The real Dockerfile was built on a locally debootstrapped base. The container migrated the database, served `/health/ready` and ran the deadline monitor. | `evidence/docker_image_run.log` |
| Frontend image | **PASS WITH EXTERNAL VALIDATION REQUIRED** (2026-10-13). The real `frontend/Dockerfile` (npm ci → build → nginx with `nginx.conf`) was built on local Node 20.18 and nginx 1.24 images. Served from the container: the SPA, deep links, sign-in and the live planner passed in Playwright with 0 failed assets and 0 page or console errors. The bundle secret scan is clean. Not verified: the official `node:20.18-bookworm-slim` and `nginxinc/nginx-unprivileged` bases (nginx ran with a root master here). | `evidence/external/docker/frontend_*`, `e2e/container_frontend.spec.ts` |
| Neo4j | **BLOCKED — REAL SERVER UNAVAILABLE**. Every channel was retried on 2026-10-13: registries 403, dist/Maven unreachable, GitHub 404. The handling of graph answers is unit-tested. | NEO4J_SETUP.md, `evidence/external/neo4j/neo4j_attempts.txt` |
| MinIO / S3 | **PASS** (2026-10-13). The **real MinIO** release `RELEASE.2025-04-22T22-12-26Z` (official .deb, checksum verified), natively and in compose. Checked: the infra tests, private bucket, presign, wrong credentials refused, the LifeLink document workflow, outage → 503 → recovery, and the S3 infra tests again inside the full backend regression (`S3_TEST_*` → real MinIO). | `evidence/external/minio/*` |
| Mailpit / SMTP | **PASS** (2026-10-13). **Real Mailpit v1.21.8** (official release binary), natively and in compose. The real LifeLink verification e-mail went through the RQ worker and was checked through Mailpit's API. | `evidence/external/mailpit/*` |
| Gmail | **BLOCKED — SMTP NETWORK ACCESS UNAVAILABLE**. No credentials, and smtp.gmail.com gives no SMTP greeting through the proxy. Not claimed. | GMAIL_SETUP.md, `evidence/external/gmail/*` |
| SMS (MSG91) | **BLOCKED — CREDENTIALS/PROVIDER CONFIGURATION REQUIRED**. No account, and the MSG91 hosts are unreachable. The console provider and the retry logic are tested. `scripts/send_test_sms.py` is ready for one controlled send. | SMS_SETUP.md, `evidence/external/sms/msg91_attempt.txt` |
| Geocoder (Nominatim-compatible) | **PARTIAL**. The HTTP contract is tested with an in-process transport. The live public Nominatim was attempted on 2026-10-13, and the proxy refused it (403). The fallbacks were re-verified with the real resolver. | `test_external_geocoder_success_failure_and_garbage`, `evidence/external/geocoder/live_geocoder_attempt.txt` |
| Deadline monitor worker | **PASS**, in the tests, as a container, in the clean start, and as a long-running compose service. It writes a heartbeat after each pass, and its compose health check reads that heartbeat. | `test_deadline_monitor_*`, `evidence/docker_image_run.log`, `evidence/external/docker/compose_ps_final.txt` |
| Map tiles (OpenStreetMap) | **PARTIAL**. The tile host is unreachable here. DonorMap shows an explicit fallback banner on tile errors and still renders its pins; that fallback passes an E2E test with tile requests aborted. | `e2e/gap_live_pages.spec.ts`, `screenshots/gap-map-tile-fallback-1280.png` |
| Clean start from nothing | **PASS WITH EXTERNAL VALIDATION REQUIRED**. Native: 10 checks PASS. Docker (2026-10-13): 11 of 12 PASS with real MinIO and Mailpit. Neo4j is BLOCKED in both. | CLEAN_START_VERIFICATION.md, `clean_start/`, `evidence/external/docker/` |

Egress probe: `evidence/egress_probe.txt`. Re-probed for the final external validation: `evidence/external/egress_probe_2026-10-13.txt`.

**Defect found by the Docker run and fixed (2026-10-13).** The `worker` and `deadline-monitor` containers reuse the backend image, whose `HEALTHCHECK` probes HTTP :8000. Neither process serves HTTP, so both were permanently `unhealthy` (`evidence/external/docker/earlier_attempts/attempt6_unhealthy_workers.log`).

The fix:

* `docker-compose.yml` now gives the worker a health check that confirms it is registered with RQ in Redis.
* The deadline monitor checks for a heartbeat file under 300 s old. The monitor writes the file after each pass.

Tests: `test_compose_gives_non_http_services_their_own_health_checks` and `test_deadline_monitor_writes_a_heartbeat_after_each_pass`.

## 2. Migration integrity

Raw output of every command below: `evidence/migration_integrity.txt` (sections A–G) and `evidence/migration_roundtrip.txt`.

### 2.1 The reported failure: `Can't locate revision identified by '0007_phase7b'`

**What it means.** Alembic reads the revision stored in the database's `alembic_version` table and looks for that revision in `backend/alembic/versions/`. When it is not there, Alembic cannot tell which migrations have already been applied, so it stops before changing anything.

**Does `0007_phase7b` exist anywhere in the repository? No.**

* It is not among the 31 files in `alembic/versions/`, and no file in `alembic/` or `app/` references it. `tests/test_migration_integrity.py::test_phantom_revision_is_not_in_or_referenced_by_the_repository` checks this.
* The git history in this archive has a single import commit and contains no such string (`git log --all -S 0007_phase7b` is empty).
* The project's own phase reports never mention it. The Phase 0 audit lists the chain then in use as `0001_initial → 0002_phase2 → 0003_phase3 → 0004_phase5 → 0005_phase6 → c6cfa33e7862 (phase 7) → 4a9d4f2ed50b (phase 8)`. There was never a `0006` or `0007`, and Phase 7 itself was autogenerated as `c6cfa33e7862`.

**Is there an incorrect or missing dependency in the chain? No.** The chain checks out with Alembic's own commands, on real databases (evidence §A):

* `alembic heads` returns one head, `e1f2a3b4c5d6`.
* `alembic branches` is empty.
* `alembic history` is one linear list of 31 revisions from `0001_initial` to `e1f2a3b4c5d6`.
* `scripts/migration_doctor.py` independently parses every file and finds no duplicate revision ids, no dangling `down_revision`, exactly one base and one head, and every file reachable from the head.

**Can the current chain build a completely fresh database? Yes.**

* On a database created empty seconds earlier: the doctor reports EMPTY, then `alembic upgrade head` applies all 31 revisions (exit 0). `alembic current` shows `e1f2a3b4c5d6 (head)`, `alembic check` reports "No new upgrade operations detected", and the doctor reports AT_HEAD with the schema equal to the models (39 tables, every column).
* The same holds on a brand-new PostgreSQL *cluster* (initdb) in the clean-start test.
* `evidence/migration_roundtrip.txt` also shows head → −1 → head and base → head.

**Why did a database carry `0007_phase7b`? An old Docker volume is the most likely cause.**

* `backend/docker-compose.yml` lives in a folder called `backend/` and did not set a project name, so Compose named its volume `backend_pgdata`.
* Every other project whose compose file sits in a folder called `backend/` gets the same volume name. A previous LifeLink copy or another project would leave behind a database stamped with its own revisions (`0007_phase7b` follows the hand-numbered style of 0001–0005).
* The container then ran `alembic upgrade head` against that foreign database and failed.

The developer's machine was not available to inspect, so the volume's contents are not confirmed here. What is confirmed is the following. The revision is absent from this repository's history. The volume name is shared by any `backend/` compose project. And `tests/test_migration_integrity.py` reproduces the exact error message by stamping a database with `0007_phase7b`.

### 2.2 Fix: diagnose instead of failing cryptically, and never delete data

* **`scripts/migration_doctor.py`** (new, read-only). It classifies the database as one of the following:

  | State | Exit code | Meaning |
  |---|---|---|
  | EMPTY | 0 | Fresh database |
  | BEHIND | 0 | Known revision with N migrations pending |
  | AT_HEAD | 0 | Nothing to do |
  | UNKNOWN_REVISION | 2 | The database names a revision this repository does not have |
  | UNVERSIONED | 3 | Tables exist but there is no `alembic_version` |
  | MULTIPLE_DB_HEADS | 4 | More than one row in `alembic_version` |
  | REPO_INVALID | 5 | The repository's own chain is broken |

  It compares the live schema with the current models (missing tables, extra tables, column differences) and prints the safe recovery path. It never stamps, deletes or alters anything.
* **The compose `api` service** now runs `python -m scripts.migration_doctor && alembic upgrade head && uvicorn …`. A stale volume therefore stops the container with an explanation, instead of an opaque traceback.
* **The compose project is pinned** (`name: lifelink`), so new volumes are `lifelink_pgdata` and so on, and can no longer collide with another `backend/` project. An existing `backend_pgdata` is left untouched. Nothing is deleted.
* **Automated guards**: `tests/test_migration_integrity.py` (7 tests, real scratch databases, real `alembic` CLI):
  * one linear chain;
  * `alembic heads` / `branches`;
  * the phantom revision is absent;
  * the compose ordering;
  * fresh → EMPTY → upgrade → AT_HEAD;
  * existing database 2 revisions behind, holding data → BEHIND → upgrade, data kept;
  * stamped `0007_phase7b` → `alembic upgrade head` fails with the exact message → doctor UNKNOWN_REVISION (exit 2, writes nothing) → recovery keeps the data;
  * foreign schema with the same stamp: stamping is refused as unsafe;
  * UNVERSIONED.

### 2.3 Procedures

**A. Fresh development database (normal).**
```bash
cd backend && cp .env.example .env            # edit SECRET_KEY
docker compose up --build                     # doctor -> alembic upgrade head -> API
# or without Docker: createdb lifelink; python -m scripts.migration_doctor; alembic upgrade head
```

**B. Existing development database (normal migration; keeps data).**
```bash
python -m scripts.migration_doctor            # expect BEHIND or AT_HEAD
pg_dump -Fc -U lifelink lifelink > before-upgrade.dump   # always in production; advisable in dev
alembic upgrade head && alembic check
```
Verified on the seeded development database (AT_HEAD, no-op) and on a database two revisions behind that held data (evidence §C and §D).

**C. Stale or invalid migration state (e.g. `0007_phase7b`).** Do **not** delete the volume first.
1. Run `python -m scripts.migration_doctor` (in Docker: `docker compose run --rm api python -m scripts.migration_doctor`). It names the state and shows the schema comparison.
2. Back up: `docker compose exec db pg_dump -U lifelink -Fc lifelink > stale-volume.dump`.
3. Then do one of the following:
   * **The doctor says the schema matches the current models.** The data can be kept: run `alembic stamp --purge head`, then `alembic upgrade head` (a no-op) and `alembic check`. Verified in evidence §F, where the data was kept.
   * **The schema does not match** (a foreign or older database). Stamping would falsely claim migrations ran, so do not stamp. Keep the dump. Either start this checkout on its own new volume, which is the default now that the project is named `lifelink` (the old `backend_pgdata` remains for inspection), or restore the specific data you need into a freshly migrated database.

**D. Destructive development reset (deletes data; never production).**
```bash
docker compose down
docker volume ls | grep pgdata                 # identify the stale volume (e.g. backend_pgdata)
docker volume rm backend_pgdata                # PERMANENTLY deletes every row in it
docker compose up --build
```
Use this only after the backup in C.2, and only when that database's contents are disposable.

**E. Production migration safety.** Production does not use this compose file.
* Run migrations as a separate, approved step, as the table owner, after a `pg_dump`: `python -m scripts.migration_doctor && alembic upgrade head && alembic check`.
* Then deploy the application running as the least-privilege role (`scripts/least_privilege_role.sql`, `evidence/least_privilege_check.txt`).
* The doctor's non-zero exit codes are meant to stop a deployment pipeline.
* No production database was available here. This procedure is documented and was exercised on development databases only.

### 2.4 Result matrix

| Scenario | Result | Evidence |
|---|---|---|
| Repository chain: one head, no branches, no missing/duplicate/orphaned revisions | **PASS** | §A; `test_repository_chain_*`, `test_alembic_cli_*` |
| Fresh-database migration (empty database, and a brand-new initdb cluster) | **PASS** | §B; CLEAN_START_VERIFICATION.md step 9 |
| Existing-database migration (seeded dev DB; DB 2 revisions behind with data) | **PASS** | §C, §D; `test_fresh_existing_stale_and_recovered_databases` |
| Stale-database recovery (`0007_phase7b` stamp, schema matches): data kept | **PASS** | §E, §F |
| Stale foreign database: correctly refused for stamping | **PASS** | `test_foreign_schema_with_phantom_stamp_*` |
| Destructive development reset | Documented (§2.3 D); not needed to make anything pass | — |
| Production migration safety | Procedure documented; not run against a production database | §2.3 E |
| Schema after migration equals the current models | **PASS** | doctor AT_HEAD with `matches_current_models: true`; `alembic check`; `test_alembic_head_schema_matches_orm_metadata` |
| Application starts after migrations | **PASS** | §G (`/health/live`, `/health/ready` on the freshly migrated database); clean-start step 10 |
| The developer's actual stale volume | Not available here: the cause is inferred, as above, not observed | — |
