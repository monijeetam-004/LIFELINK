# Docker setup

`backend/docker-compose.yml` runs the whole stack for development and E2E:

| Service | Image / build | Profile | Purpose |
|---|---|---|---|
| db | postgres:16 | default | PostgreSQL |
| redis | redis:7 | default | rate limits, RQ queues, realtime |
| minio + createbuckets | minio/minio, minio/mc (pinned RELEASE tags) | default | S3 object storage (private bucket) |
| clamav | clamav/clamav:1.4 | default | malware scanning (clamd) |
| mailpit | axllent/mailpit:v1.21.8 | `mailpit` | development mail catcher |
| neo4j | neo4j:5.24 | `graph` | optional graph projection |
| api | `backend/Dockerfile` | default | FastAPI (runs `alembic upgrade head` first) |
| worker | `backend/Dockerfile` | default | RQ worker (email, SMS, scheduled SMS retries) |
| deadline-monitor | `backend/Dockerfile` | default | `python -m app.workers.deadline_monitor` |
| frontend | `frontend/Dockerfile` (node build → nginx-unprivileged) | default | the SPA on :5173 |

```bash
cd backend
cp .env.example .env            # fill in secrets; never commit .env
docker compose --profile mailpit up -d --build          # + --profile graph for Neo4j
docker compose ps
curl -s localhost:8000/health/ready
```

Mail: `MAIL_HOST` is passed through as `${MAIL_HOST:-mailpit}` — a Gmail
configuration in `.env` is never overridden by Mailpit.

## What was verified in the sandbox (and what was not)

* `docker compose config` — **PASS** (all 11 services resolve; `evidence/docker_compose_services.txt`).
* `docker compose up` — **BLOCKED**: every registry returns 403/no route
  (`evidence/docker_compose_up_blocked.log`, `evidence/egress_probe.txt`).
* The real `backend/Dockerfile` — **PASS (built and run)**. Because
  `python:3.12-slim` cannot be pulled, the image was built with
  `--build-arg BASE_IMAGE=lifelink-base:noble-local-ca`, a base made locally
  with `debootstrap` (Ubuntu 24.04 + python3.12 from the Ubuntu archive) plus
  the sandbox's proxy CA so pip can reach PyPI. That base is never
  published or referenced by the repository. The build installed the
  hash-locked requirements, and the container then:
  ran `alembic upgrade head` on a fresh database (to `e1f2a3b4c5d6`),
  served `/health/ready` → `ready` as non-root `appuser`, and ran
  `python -m app.workers.deadline_monitor --once`
  (`evidence/docker_image_run.log`, `evidence/docker_build_api_tail.log`).
* `frontend/Dockerfile` — **not built** (node and nginx base images are
  registry-only). The SPA build itself (`npm run build`) passes.

To reproduce the offline-base build elsewhere:

```bash
sudo debootstrap --variant=minbase --components=main,universe \
  --include=python3,python3-venv,python3-pip,ca-certificates,libpq5 noble rootfs http://archive.ubuntu.com/ubuntu/
tar -C rootfs -c . | docker import - lifelink-base:noble-local
docker build --build-arg BASE_IMAGE=lifelink-base:noble-local -t lifelink-api:local backend/
```
