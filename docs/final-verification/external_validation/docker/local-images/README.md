# Local substitutes for the official container images (verification only)

The verification sandbox cannot pull from any container registry: Docker Hub,
ECR Public, quay.io, ghcr.io and gcr.io all answer 403 at the egress proxy
(`evidence/external/egress_probe_2026-10-13.txt`). To still run a real
`docker compose up` of LifeLink, each official image is replaced here by an
image built locally on an Ubuntu 24.04 base. Every one runs the **real
software**; the stand-ins moto and MailDev are **not** used:

| Compose service | Official image | Local substitute runs |
|---|---|---|
| db | postgres:16 | PostgreSQL 16 (Ubuntu package), with the official image's POSTGRES_* init behaviour |
| redis | redis:7 | Redis 7.0 (Ubuntu package) |
| clamav | clamav/clamav:1.4 | ClamAV clamd (Ubuntu package), with the project-local signature DB (the official mirror is blocked) |
| mailpit | axllent/mailpit:v1.21.8 | the **official Mailpit v1.21.8 release binary** from GitHub |
| minio | minio/minio:RELEASE.2025-04-22T22-12-26Z | the **official MinIO .deb of that exact release** from GitHub (sha256 verified) |
| createbuckets | minio/mc:RELEASE.2025-04-16T18-13-26Z | the **official mcli .deb of that release** (sha256 verified) |
| frontend build stage | node:20.18-bookworm-slim | the official Node.js 20.18.3 Linux binary, from the npm package `node-linux-x64` |
| frontend runtime | nginxinc/nginx-unprivileged:1.27-alpine | nginx (Ubuntu package), listening on 8080 |
| api / worker / deadline-monitor | python:3.12-slim | Python 3.12 (Ubuntu), via the backend Dockerfile's `BASE_IMAGE` build arg |

These Dockerfiles are not part of the product and are never used in deployment.
The repository's `docker-compose.yml` and Dockerfiles are used unchanged apart
from the overridable base-image build args. The overrides live in
`docker-compose.local-images.yml`.
