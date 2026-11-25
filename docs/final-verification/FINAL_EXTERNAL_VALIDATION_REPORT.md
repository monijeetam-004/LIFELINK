# Final external validation report

**Date:** 2026-10-13. **Plan:** `FINAL_EXTERNAL_VALIDATION_PLAN.md`. **Evidence:** `evidence/external/`.

**Verdict: PASS WITH EXTERNAL VALIDATION REQUIRED** (see §11).

The earlier reports are kept unchanged as historical evidence. Where they disagree with this report, this report is the later and correct one.

## 1. Objective

Close the gaps that were still PARTIAL or BLOCKED after the gap-elimination round, using the **real** services wherever they could be obtained:

* G8 Neo4j, G12 Gmail, G14 SMS / MSG91 (BLOCKED);
* G10 MinIO, G11 Mailpit, G4 live geocoder, G9 Docker / frontend image (PARTIAL).

**Rules followed throughout:**

* No stand-in turns a row into PASS. Moto, MailDev, an in-memory graph and mocked HTTP were not used for any result below.
* No credentials were invented.
* No test was changed to make it pass.
* No volume was removed before it was listed and confirmed to be a throwaway.

## 2. Environment

* **Host:** a Linux sandbox with 2 vCPU and 8 GB RAM; Python 3.11 for the development venv and test suites, 3.12 in the containers; Node 20; Docker engine 29.8.2 with Compose v5.5.1 (`evidence/external/docker/docker_versions.txt`).
* **Egress:** all outbound traffic goes through an allow-listing proxy. Re-probed at the start of this phase (`evidence/external/egress_probe_2026-10-13.txt`):

  | Destination | Reachable? |
  |---|---|
  | Every container registry (Docker Hub, ECR Public, ghcr, quay, gcr, mirror.gcr, registry.k8s.io) | No (403) |
  | dist.neo4j.org, neo4j.com, debian.neo4j.com, Maven Central and mirrors | No |
  | proxy.golang.org, dl.min.io | No |
  | smtp.gmail.com, api.msg91.com, control.msg91.com, nominatim.openstreetmap.org | No |
  | **GitHub release downloads, PyPI, npm, the Ubuntu archive** | **Yes** |

* **Real services obtained from official sources:**

  | Service | Source | Integrity |
  |---|---|---|
  | MinIO `RELEASE.2025-04-22T22-12-26Z` | official `minio_20250422221226.0.0_amd64.deb` (GitHub release) | sha256 `d8898351…5c9c5`, equal to the official `.sha256sum` |
  | `mc` `RELEASE.2025-04-16T18-13-26Z` | official `.deb` | — |
  | Mailpit v1.21.8 | official `mailpit-linux-amd64.tar.gz` (GitHub release) | — |
  | Node 20.18.3 | official binary, through the `node-linux-x64` npm package | — |
  | PostgreSQL 16, Redis 7, ClamAV, nginx 1.24 | Ubuntu 24.04 archive | — |

  The MinIO and Mailpit versions are the ones pinned in `backend/docker-compose.yml`.

* **Credentials:**
  * MinIO root credentials were generated at start-up and kept in a 0600 file outside the repository (`/srv/realsvc/minio.env`). They were never printed. The Docker run used the public development defaults from `.env.example`.
  * No Gmail, MSG91 or Neo4j credentials exist in this environment.

## 3. Services tested

| Component | Real service? | How it was run |
|---|---|---|
| MinIO | **Yes** | Natively (127.0.0.1:9000), and as the compose `minio` service with the `createbuckets` job |
| Mailpit | **Yes** | Natively (SMTP 1025, HTTP 8025), and as the compose `mailpit` service |
| PostgreSQL 16, Redis 7, ClamAV clamd | Yes | Natively (test suites) and in compose |
| API, RQ worker, deadline monitor, frontend (nginx) | Yes (LifeLink itself) | In compose, built from the real Dockerfiles |
| Neo4j | Not obtainable | — |
| Gmail SMTP | Not reachable, and no credentials | — |
| MSG91 | Not reachable, and no credentials | — |
| Public Nominatim | Not reachable | The real resolver was pointed at it |

## 4. Exact commands

```bash
# MinIO (native, official .deb) -- credentials from a 0600 file, never echoed
dpkg -x minio_20250422221226.0.0_amd64.deb /srv/realsvc/minio-pkg
MINIO_ROOT_USER=… MINIO_ROOT_PASSWORD=… minio server /srv/realsvc/minio-data --address 127.0.0.1:9000 --console-address 127.0.0.1:9001
mcli alias set ll http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
mcli mb ll/lifelink-dev; mcli anonymous get ll/lifelink-dev

# Mailpit (native, official binary)
mailpit --smtp 127.0.0.1:1025 --listen 127.0.0.1:8025

# Infrastructure tests against the REAL services
cd backend
S3_TEST_ENDPOINT=http://127.0.0.1:9000 S3_TEST_ACCESS_KEY=… S3_TEST_SECRET_KEY=… \
SMTP_TEST_HOST=127.0.0.1 SMTP_TEST_PORT=1025 SMTP_TEST_API=http://127.0.0.1:8025/api/v1/messages \
  .venv/bin/pytest tests/test_gap_infrastructure.py -v

# Native app run on real MinIO + Mailpit (API, worker, monitor, frontend, functional checks)
docs/final-verification/external_validation/scripts/native_app_check.sh       # -> evidence/external/native_real_services_*
docs/final-verification/external_validation/scripts/minio_workflow.py        # document workflow + outage + recovery

# Docker clean start (real compose; local image sources only)
docker compose -f backend/docker-compose.yml \
  -f docs/final-verification/external_validation/docker/docker-compose.local-images.yml \
  --profile mailpit --profile graph down -v
docker compose -f backend/docker-compose.yml \
  -f docs/final-verification/external_validation/docker/docker-compose.local-images.yml up -d --build
docker compose … exec api python -m scripts.clean_start_check --frontend http://frontend:8080 \
  --origin http://localhost:5173 --mail-api http://mailpit:8025/api/v1/messages
CONTAINER_FRONTEND_URL=http://localhost:5173 CONTAINER_API_URL=http://localhost:8000 \
  npx playwright test e2e/container_frontend.spec.ts e2e/clean_start.spec.ts
#   (the whole sequence: docs/final-verification/external_validation/scripts/docker_clean_start.sh)

# Gmail / SMS: attempted with the real scripts (refused: not configured)
python -m scripts.send_test_email you@example.com
python -m scripts.send_test_sms +91XXXXXXXXXX

# Neo4j: every channel (all failed)
docker pull neo4j:5.24 ; docker pull public.ecr.aws/docker/library/neo4j:5.24 ; …
curl -I https://dist.neo4j.org/neo4j-community-5.24.0-unix.tar.gz ; …

# Live geocoder
GEOCODER_URL=https://nominatim.openstreetmap.org/search python - <<'PY'   # calls app.services.geocoding.resolve
PY

# Regression
CLAMD_TEST_REQUIRED=1 S3_TEST_ENDPOINT=http://127.0.0.1:9000 S3_TEST_ACCESS_KEY=… S3_TEST_SECRET_KEY=… \
  SMTP_TEST_HOST=127.0.0.1 SMTP_TEST_PORT=1025 SMTP_TEST_API=http://127.0.0.1:8025/api/v1/messages \
  QA_ROUTE_LOG=… python -m pytest -p no:cacheprovider -o addopts= -q -rs --junitxml=…
cd frontend && npx vitest run && <CI-identical harness> npx playwright test
```

## 5. Results

| Component | Real Service? | Tested? | Result | Evidence |
|---|---|---|---|---|
| Neo4j | No (not obtainable) | Acquisition attempted through every channel; graph-answer handling unit-tested | **BLOCKED — REAL SERVER UNAVAILABLE** | `neo4j/neo4j_attempts.txt` |
| MinIO | **Yes** (official release) | Yes: health, credentials, private bucket, put / get / presign / delete, document workflow, outage and recovery, compose `createbuckets`; infra S3 tests also inside the full regression | **PASS** | `minio/*` |
| Mailpit | **Yes** (official v1.21.8) | Yes: a real LifeLink verification e-mail through the RQ worker, checked through the Mailpit API; infra SMTP test; Docker run | **PASS** | `mailpit/*` |
| Gmail | No (network and credentials) | Reachability and configuration checked; `send_test_email` refused | **BLOCKED — SMTP NETWORK ACCESS UNAVAILABLE** | `gmail/*` |
| MSG91 | No (credentials and network) | Reachability and configuration checked; `send_test_sms` refused | **BLOCKED — CREDENTIALS/PROVIDER CONFIGURATION REQUIRED** | `sms/msg91_attempt.txt` |
| Live geocoder | No (403 at the proxy) | The real resolver was pointed at public Nominatim; fallbacks re-verified live | **PARTIAL** (live service BLOCKED) | `geocoder/live_geocoder_attempt.txt` |
| Docker frontend | Yes (real Dockerfile and nginx.conf, local base images) | Yes: built, served, and browser-tested in the container | **PASS WITH EXTERNAL VALIDATION REQUIRED** (official base images not pullable) | `docker/frontend_*`, `docker/docker_clean_start_run.log` |
| Docker compose stack | Yes (unchanged compose file, local image sources) | Yes: `down -v` → `up --build`; 9 services healthy; 11 of 12 checks PASS, 1 BLOCKED | **PASS WITH EXTERNAL VALIDATION REQUIRED** | `docker/*` |
| PostgreSQL | Yes | Yes (suite and compose) | **PASS** | §9; `docker/docker_check_result.json` |
| Redis | Yes | Yes | **PASS** | same |
| ClamAV | Yes (real clamd) | Yes, `CLAMD_TEST_REQUIRED=1` | **PASS** | §9 |

### 5.1 MinIO: PASS

The following was checked against real MinIO (`minio/minio_server_evidence.txt`, `minio/lifelink_document_workflow_and_outage.txt`):

* **Server:**
  * `/minio/health/live` and `/minio/health/ready` both return 200.
  * `mc admin info` shows 1/1 drives OK.
* **Access control:**
  * the bucket `lifelink-dev` is **private**;
  * an anonymous GET of a stored document returns **403**;
  * wrong credentials are refused ("Access Key Id … does not exist").
* **Infra tests** against real MinIO: `test_s3_round_trip_presign_and_delete` and `test_s3_bucket_is_not_public` passed (`minio/junit_infra_real_minio_mailpit.xml`).
* **The LifeLink document workflow:**
  * a clean PDF upload returns 201, scans CLEAN, and is stored in MinIO (the object appears in the listing);
  * the admin download through LifeLink returns 200, byte-identical, as `application/pdf`;
  * an EICAR PDF returns 422.
* **Outage:**
  * with MinIO stopped, an upload returns **503** "Document storage is temporarily unavailable", and **0** verification requests are stored;
  * after a restart, the upload returns 201.
* **Docker:**
  * the `createbuckets` job creates `lifelink-dev` and sets it private;
  * `object_storage` and `document_upload` pass inside the api container (the server header is `MinIO`);
  * screenshot: `minio_console_docker.png`.

### 5.2 Mailpit: PASS

`mailpit/mailpit_message_evidence.txt` and `worker_log_excerpt.txt`:

* **Delivery path:**
  * A real registration made the API enqueue the e-mail job (`email job 9cc46cb6… enqueued for c***@example.com`, at 08:06:53.318).
  * The RQ worker sent it at 08:06:53.838.
  * Mailpit holds it.
* **Content check through the Mailpit API:**
  * From `no-reply@lifelink.local`; To the registered address; Subject "Verify your LifeLink email";
  * the body greets the user by name;
  * the link is `http://…/verify-email?token=…` (the token is redacted in the evidence);
  * **no secret strings** appear in the raw message.
* **The link works:** the token from that e-mail verified the account (`worker_and_mail` PASS, natively and in Docker).
* **Infra test:** `test_smtp_delivery_reaches_a_real_mail_catcher` passed against Mailpit.
* **Gmail rule:** `test_gmail_is_never_silently_replaced_by_mailpit` passed with Mailpit running.
* **Screenshots:** `mailpit_inbox_docker.png`, `mailpit_verification_message_docker.png`.

### 5.3 Docker / frontend image: PASS WITH EXTERNAL VALIDATION REQUIRED

Full log: `docker/docker_clean_start_run.log`, 08:41:20–08:46:29 UTC. Steps:

1. **Clean-up.** The existing volumes were listed first: `lifelink_pgdata`, `lifelink_miniodata`, `lifelink_clamavdb` and one anonymous volume, all from this sandbox's own earlier attempts. Then `down -v` ran, and no containers or volumes were left.
2. **Configuration.** `.env` was copied from `.env.example`; only `SECRET_KEY` was generated.
3. **Start.** `up -d --build`. The api log shows the **migration doctor → `alembic upgrade head` → uvicorn**, and the database is at head `e1f2a3b4c5d6`.
4. **Health.** All 9 long-running services report **healthy** (`compose_ps_final.txt`), including the worker and deadline-monitor once their checks had run.
5. **Functional checks.** `clean_start_check` ran inside the api container: **11 PASS, 1 BLOCKED (Neo4j)**, out of 12 checks. It covers the database, the alembic head, redis, MinIO, clamd, worker plus mail, document upload, the seed, the frontend, and the critical workflow (SOS → plan → reserve → route failure → backup promoted → shipment with cold chain → receipt → resolve → audit chain ok).
6. **Browser.** The containerised nginx frontend (`container_frontend.spec.ts`, `clean_start.spec.ts`) passed 2/2:
   * deep-link fallback, sign-in, the live planner ("Primary plan") and the units table all worked;
   * **0** failed assets, **0** page errors, **0** console errors;
   * screenshot: `frontend_container_live_planner.png`.
7. **Secrets.** The frontend bundle secret scan found 0 hits (`frontend_bundle_secret_scan.txt`).

**Still to validate on a machine with registry access:** the official `python:3.12-slim`, `node:20.18-bookworm-slim`, `nginxinc/nginx-unprivileged:1.27-alpine` and `postgres:16` bases. Here, nginx ran with a root master process instead of the unprivileged image.

### 5.4 Neo4j: BLOCKED — REAL SERVER UNAVAILABLE

Every channel was tried (`neo4j/neo4j_attempts.txt`):

* `docker pull` of neo4j:5.24 from Docker Hub, ECR Public, ghcr.io and quay.io: all **Forbidden**;
* dist.neo4j.org, neo4j.com, debian.neo4j.com, Maven Central and its GCS mirror: no connection;
* the GitHub release asset: 404;
* `git ls-remote` works (tag 5.24.0), but building needs Maven, which is blocked.

No in-memory graph or substitute was used. The planner ran on RELATIONAL_ROUTES and is labelled as such.

### 5.5 Gmail: BLOCKED — SMTP NETWORK ACCESS UNAVAILABLE

* **Network** (`gmail/smtp_reachability.txt`): direct TCP fails (DNS). Through the proxy, CONNECT to :587 and :465 returns 200, but **no SMTP greeting** arrives (timeout).
* **Credentials:** no `MAIL_USERNAME`, `MAIL_PASSWORD` or `GMAIL_*` variables exist.
* **Send attempt:** `send_test_email` refused: "Gmail SMTP needs MAIL_USERNAME … and MAIL_PASSWORD (a Google App Password)".

No e-mail was sent to Gmail, and none is claimed.

### 5.6 SMS / MSG91: BLOCKED — CREDENTIALS/PROVIDER CONFIGURATION REQUIRED

* **Network:** control.msg91.com and api.msg91.com do not connect.
* **Configuration:** no `MSG91_*` variables, so `msg91_enabled=False` and `sms_provider_configured=False`.
* **Send attempt:** the new `scripts/send_test_sms.py` refused with exit 2, both when unconfigured and with `SMS_PROVIDER=console` (`sms/msg91_attempt.txt`).

No SMS was sent, and none is claimed.

### 5.7 Live geocoder: PARTIAL

`geocoder/live_geocoder_attempt.txt`. With `GEOCODER_URL=https://nominatim.openstreetmap.org/search`, the real `geocoding.resolve` was called:

| Input | Result |
|---|---|
| A valid address | **NO LOCATION** (the proxy returns 403; nothing invented) |
| Nonsense | NO LOCATION |
| "near the railway station, Mumbai" | GAZETTEER (19.09, 72.8567), labelled as an estimate |
| Explicit coordinates | USER_INPUT |
| Out-of-range coordinates | Rejected |

The live service itself was never reached.

## 6. Evidence

All of it is under `docs/final-verification/evidence/external/`:

```
egress_probe_2026-10-13.txt
native_real_services_run.log, native_real_services_check.json
neo4j/neo4j_attempts.txt
minio/  minio_server_evidence.txt, lifelink_document_workflow_and_outage.txt,
        junit_infra_real_minio_mailpit.xml, pytest_infra_real.txt, minio_console_docker.png
mailpit/ mailpit_message_evidence.txt, worker_log_excerpt.txt, junit_infra_real_minio_mailpit.xml,
        pytest_infra_real.txt, mailpit_inbox_docker.png, mailpit_verification_message_docker.png
gmail/  smtp_reachability.txt, send_test_email_attempt.txt
sms/    msg91_attempt.txt
geocoder/ live_geocoder_attempt.txt
docker/ docker_clean_start_run.log, docker_check_result.json, compose_ps_final.txt, images.txt,
        docker_versions.txt, api_log_excerpt.txt, worker_log_excerpt.txt,
        frontend_bundle_secret_scan.txt, frontend_container_live_planner.png,
        earlier_attempts/ (the 6 failed runs, kept)
regression/ (§9)
```

The Docker override and the local-image Dockerfiles are in `external_validation/docker/`, with a README listing each substitution.

## 7. Failures encountered

| # | Failure | Cause | Kind |
|---|---|---|---|
| 1 | Frontend `npm ci` in Docker: "Exit handler never called!", then `npm run build` exit 127 | TLS interception by the sandbox proxy (`SELF_SIGNED_CERT_IN_CHAIN`) | Sandbox |
| 2 | Local postgres image: `initdb: command not found` | `su` resets PATH | Harness |
| 3 | Migration doctor: `TypeError: cannot use a string pattern on a bytes-like object` | The local cluster was initialised as SQL_ASCII | Harness |
| 4 | A stale Mailpit container survived `down` | `down` ran before `.env` (with `COMPOSE_PROFILES`) existed | Harness |
| 5 | **worker and deadline-monitor containers permanently `unhealthy`** | They inherit the backend image's `HEALTHCHECK` on HTTP :8000, but serve no HTTP | **Product defect** |
| 6 | Neo4j, Gmail, MSG91 and Nominatim unreachable | Egress policy, plus missing credentials | External |

## 8. Fixes made

**Product changes (minimal, with tests):**

* **The health-check defect (#5):**
  * `backend/docker-compose.yml`: the worker's health check confirms that this container's RQ worker is registered in Redis. The deadline-monitor's checks for a heartbeat file under 300 s old.
  * `app/workers/deadline_monitor.py` writes that heartbeat after each successful pass.
  * Tests: `test_compose_gives_non_http_services_their_own_health_checks` and `test_deadline_monitor_writes_a_heartbeat_after_each_pass`.
* **`frontend/Dockerfile`:** `NODE_IMAGE` and `NGINX_IMAGE` are now build arguments. The defaults are unchanged (`node:20.18-bookworm-slim`, `nginxinc/nginx-unprivileged:1.27-alpine`).
* **New `backend/scripts/send_test_sms.py`:** sends one controlled MSG91 send. It refuses console or unconfigured providers and masks the number.
* **New `frontend/e2e/container_frontend.spec.ts`:** a browser check of the containerised frontend. It is skipped unless `CONTAINER_FRONTEND_URL` is set.

**Harness and sandbox fixes, outside the product:**

* `NODE_EXTRA_CA_CERTS` in the local Node image;
* full binary paths and `-E UTF8` in the local postgres entrypoint;
* `down` with both profiles.

**Corrected in my own work:** I first attributed failure #1 to Node 20 and changed the default to Node 22. The HTTP log showed the real cause (#1), so the default was reverted to Node 20.18 and the incorrect comment removed.

## 9. Regression results

All runs used the final code, after every fix in §8. The evidence is in `evidence/external/regression/`.

| Suite | Command | Result |
|---|---|---|
| Backend | `CLAMD_TEST_REQUIRED=1` + `S3_TEST_*` → **real MinIO** + `SMTP_TEST_*` → **real Mailpit**; `python -m pytest -q` (08:48–09:20 UTC) | **1,397 passed, 1 skipped, 0 failed** (1,398 collected, 31 min 55 s). The skip is `test_real_neo4j_paths_equal_relational_paths`: no Neo4j server |
| External integration tests (inside that run) | `test_gap_infrastructure.py` (4) and `test_gap_clamav_real.py` (7) | 11/11 passed, against real MinIO, real Mailpit and real clamd |
| New tests (inside that run) | `test_deadline_monitor_writes_a_heartbeat_after_each_pass`, `test_compose_gives_non_http_services_their_own_health_checks` | passed |
| Frontend unit | `npx vitest run` | **170 / 170 passed** |
| Browser E2E | CI-identical harness (fresh `lifelink_e2e`, real clamd, `RQ_SYNC=true`, no mail server, as in CI); `npx playwright test` | **106 passed, 2 skipped, 0 failed** (108 tests, 8.1 min). The 2 skips are `clean_start.spec.ts` and `container_frontend.spec.ts`. They need a clean-start or container stack, and **both passed in the Docker run** (§5.3) |
| Invariants after E2E | `python -m scripts.check_invariants` | all OK; audit chain checked=326, unsealed=0 |
| Static checks | `ruff check .`, `tsc --noEmit`, `eslint .` | clean, clean, clean |

`ruff format --check` lists 179 files whose formatting predates this phase. Formatting is not a CI gate (`.github/workflows/ci.yml`), and the files were left alone to avoid unrelated churn. Every file changed in this phase passes it.

**Compared with the previous round** (1,394 passed, 1 skipped; 170; 106 + 1):

* the backend gained 3 passing tests and lost none;
* vitest is unchanged;
* Playwright gained one spec, which only runs against containers;
* nothing regressed.

### Security check

| Check | Result |
|---|---|
| `backend/.env` committed? | **No.** It is ignored by `backend/.gitignore:3`, and `git ls-files` lists only `backend/.env.example` and `frontend/.env.example` |
| `.env.example` values | Placeholders only. `MAIL_PASSWORD=`, `MSG91_AUTH_KEY=`, `NEO4J_PASSWORD=` and `AUDIT_CHECKPOINT_KEY=` are empty. `S3_ACCESS_KEY=lifelink` / `S3_SECRET_KEY=lifelink-secret` are the public local-development MinIO defaults, as before |
| Generated MinIO root credentials | Found in **0** repository files (evidence, logs and screenshots included). They live only in a 0600 file outside the repository |
| Token patterns in the repo and evidence (AWS keys, private keys, Slack and GitHub tokens, Google API keys, filled `MAIL_PASSWORD` / `MSG91_AUTH_KEY` / `NEO4J_PASSWORD`) | 0 hits |
| Frontend bundle served by the container | 0 hits (`docker/frontend_bundle_secret_scan.txt`) |
| Logs | `SECRET_KEY` and the generated passwords are masked. E-mail addresses are masked in the worker log (`c***@example.com`). Verification tokens are redacted in the evidence |
| Gmail, MSG91 or Neo4j credentials | None exist. None were requested in code, and none were committed |

## 10. Remaining external limitations

| Item | Status | What closes it |
|---|---|---|
| Neo4j | BLOCKED — REAL SERVER UNAVAILABLE | See the commands below |
| Gmail | BLOCKED — SMTP NETWORK ACCESS UNAVAILABLE (credentials also not provided) | See the commands below |
| MSG91 | BLOCKED — CREDENTIALS/PROVIDER CONFIGURATION REQUIRED | See the commands below |
| Live geocoder | PARTIAL | `GEOCODER_URL=<Nominatim-compatible /search>`, then create an SOS with a typed address and check `geocode_source=GEOCODER` |
| Official Docker base images | not pullable here | Run the plain `docker compose up -d --build` (without the override) on a machine with registry access, then `python -m scripts.clean_start_check` inside the api container |

**Credentials the user must supply.** All of them go in `backend/.env`, which is git-ignored. None goes in `.env.example` or in a commit.

* **Gmail** (GMAIL_SETUP.md):

  | Env var | Value |
  |---|---|
  | `MAIL_HOST` | `smtp.gmail.com` |
  | `MAIL_PORT` | `587` |
  | `MAIL_STARTTLS` | `true` |
  | `MAIL_USE_SSL` | `false` |
  | `MAIL_USERNAME` | `<you>@gmail.com` |
  | `MAIL_PASSWORD` | the 16-letter Google App Password |
  | `MAIL_FROM` | `<you>@gmail.com` |

  To check without exposing the password:

  ```bash
  cd backend
  python -c "from app.core.config import settings; print(settings.mail_summary())"
  python -m scripts.send_test_email you@example.com
  ```

  `mail_summary()` prints no password. `send_test_email` sends one message. Run both from a network that allows `smtp.gmail.com:587`.

* **MSG91** (SMS_SETUP.md):

  | Env var | Value |
  |---|---|
  | `SMS_PROVIDER` | `msg91` |
  | `MSG91_ENABLED` | `true` |
  | `MSG91_AUTH_KEY` | your MSG91 auth key |
  | `MSG91_SENDER_ID` | your 6-letter DLT sender ID |
  | `MSG91_FLOW_ID` | your Flow ID (or a `MSG91_TEMPLATE_FLOW_MAP` entry for `EMERGENCY_CREATED`) |
  | `MSG91_DLT_TEMPLATE_ID` | your DLT template ID |

  Then send one message:

  ```bash
  cd backend && python -m scripts.send_test_sms +91XXXXXXXXXX
  ```

  Then confirm delivery on the handset or in MSG91's delivery report.

* **Neo4j** (NEO4J_SETUP.md):

  ```bash
  docker compose --profile graph up -d neo4j
  ```

  Set `NEO4J_URI=bolt://localhost:7687` (`bolt://neo4j:7687` inside compose), `NEO4J_USERNAME` and `NEO4J_PASSWORD`. Then:

  ```bash
  curl -X POST -b cookie.txt -H 'Origin: http://localhost:5173' localhost:8000/admin/graph/resync
  python -m scripts.verify_neo4j
  NEO4J_TEST_URI=bolt://localhost:7687 NEO4J_TEST_USER=neo4j NEO4J_TEST_PASSWORD=… pytest tests/test_gap_neo4j.py -v
  ```

## 11. Final verdict

**PASS WITH EXTERNAL VALIDATION REQUIRED.**

* **Closed against the real services in this phase:**
  * **G10 MinIO: PARTIAL → PASS.**
  * **G11 Mailpit: PARTIAL → PASS.**
  * **G9 Docker / frontend image: PARTIAL → PASS WITH EXTERNAL VALIDATION REQUIRED.** The real compose stack and frontend container run and pass; only the official base images remain unverified.
* **Still open, and only for external reasons:**
  * **G8 Neo4j:** BLOCKED — REAL SERVER UNAVAILABLE.
  * **G12 Gmail:** BLOCKED — SMTP NETWORK ACCESS UNAVAILABLE (credentials also not provided).
  * **G14 MSG91:** BLOCKED — CREDENTIALS/PROVIDER CONFIGURATION REQUIRED.
  * **G4 live geocoder:** PARTIAL (the live service is BLOCKED; the fallbacks are verified).
* **A real product defect was found and fixed:** the worker and deadline-monitor containers were never healthy.
* **No regression:**
  * backend 1,397 passed, 1 skipped (Neo4j);
  * vitest 170 / 170;
  * Playwright 106 passed, plus 2 container-only specs that passed in the Docker run.

The verdict is not FAIL, because nothing that can be tested here fails. It is not an unqualified PASS, because Neo4j, Gmail delivery, MSG91 delivery, the live geocoder and the official base images have not been exercised. No row was marked PASS on the strength of a stand-in.
