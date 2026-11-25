# LifeLink — Phase I: Infrastructure Reproducibility

**Date:** 2026-10-05 · **Scope (Phase A plan):** "Make the docker-compose stack reliably bring up MinIO + Postgres + Redis + Mailpit + API + worker with readiness checks and automatic bucket init, closing the 10 Playwright failures at the root (not just classifying them)", plus the Phase A matrix rows assigned to infrastructure: MinIO/Mailpit healthcheck gating, Vitest in CI (and, from the Phase E handoff, invariant checks in CI), worker correlation id, and demo seed of requests/emergencies/notifications.

## 1. Environment constraint, stated first

A Docker daemon can be started in this sandbox, but **every container registry and the MinIO/Mailpit download hosts are blocked** (Docker Hub, quay.io, ghcr.io, gcr mirror, ECR public → 403; `dl.min.io` unreachable). No image can be pulled, so `docker compose up` cannot be executed here. Everything that can be verified without images was verified for real; the compose file itself was validated with `docker compose config` (with and without a local `.env`). Where a real MinIO was needed, an S3-API stand-in (moto server 5.2.3) was used, and that is said wherever it applies.

## 2. Root cause of the Phase 34 "10 Playwright failures"

The 10 specs (`document_storage_security` A/B/C/E, `admin_notification_surface`, `admin_verification_notifications`, `phase23_security_hardening` E, `phase31_1_domain_signals` ×3) are exactly the ones that **store an object** (a verification document or a certificate PDF). With `S3_ENDPOINT_URL` configured but no object store serving it, each `put_bytes` raised `EndpointConnectionError` → honest 503. Two infrastructure defects made that happen on a real `docker compose up`, too:

1. `minio` had **no healthcheck**, and `api`/`worker` did not depend on it at all, so the API could start and accept uploads before MinIO answered.
2. `createbuckets` depended on `minio` only by start order and was never awaited by `api`.

**Fix (`backend/docker-compose.yml`):** MinIO healthcheck (`mc ready local`); Mailpit healthcheck (`/mailpit readyz`); `createbuckets` waits for MinIO healthy and now runs under `set -e`; `api` waits for db + redis + mailpit + minio healthy **and** `createbuckets` completed successfully, and has its own readiness healthcheck (`/health/ready`); `worker` waits for db + redis + mailpit healthy and `api` healthy (so migrations are done). The app's own startup `ensure_bucket()` stays as a second line of defence.

**Evidence the application side of that path works:**

| Check | Result |
|---|---|
| Real uvicorn + fresh e2e DB, `S3_ENDPOINT_URL` → S3-API stand-in with **no bucket pre-created**; the same 5 spec files | **26/26 passed**; bucket created by app startup; 9 objects (`certificates/…`, `verifications/…`) actually in the bucket; 0 storage errors in the backend log |
| `tests/test_phase_i_object_storage.py` (new, in-process S3 API): bucket init idempotent; verification upload → object in bucket, bytes intact, admin download identical; certificate PDF stored in bucket, owner download identical, stranger 401 | 3/3 passed |
| `test_phase20_secure_file_storage.py::test_s3_backend_round_trip…` — previously `importorskip("moto")` with moto **not declared** anywhere, so it would silently skip in CI | moto added to `requirements-dev.txt`; skip removed; runs everywhere |

**CI:** new job `e2e-object-storage` runs those storage-dependent specs against a **real MinIO** container (`docker run minio/minio`, waits for `/minio/health/live`, asserts readiness reports `"backend":"s3"`). It is part of the required `ci-summary`. Authored here, not executable in this sandbox (no image pulls) — the identical flow was run against the stand-in above.

## 3. Clean-environment bootstrap (executed)

A copy of the working tree with only tracked and non-ignored files (no `.env`, no `.venv`, no `node_modules`, no build output — what a clone gets), followed literally through the README's "Local (no Docker)" path:

| Step | Result |
|---|---|
| `python -m venv` + `pip install -r requirements-dev.txt` | 41 s; `pip check` clean |
| `npm ci` | 447 packages, 10 s |
| `cp .env.example .env`, set SECRET_KEY/DATABASE_URL/REDIS_URL/CORS | — |
| `alembic upgrade head` on an empty database | head `e4f5a6b7c8d9` |
| `python -m scripts.seed_demo_data` ×2 | identical row counts after run 2 (1065 users, 1 request, 1 emergency, 62 notifications) |
| API (uvicorn) + **real RQ worker** (`RQ_SYNC=false`) + `vite build` + `vite preview` | `/health/ready` 200, frontend 200 |
| The 62 email jobs queued by the seed | drained by the worker into an SMTP sink (62 messages) |
| Login as each of the 5 demo roles | 200, correct role |
| `forgot-password` with `X-Request-Id: cleanroom-check-1` | 202 → email delivered by the worker; worker log line carries `[cleanroom-check-1]`; recipient address **not** in the worker log |
| Verification upload as demo donor (S3 stand-in at the `.env.example` endpoint `localhost:9000`) | 201; bucket `lifelink-dev` auto-created; object stored; admin download byte-identical |
| `GET /events/stream` as demo hospital | `ready` frame received |

## 4. Bugs found and fixed

1. **Compose startup not gated on object storage / mail / bucket init** (§2).
2. **Worker never installed the app's logging config.** In a real worker process the job-level `lifelink.email` "sent/failed" lines were silently dropped — only RQ's own lines appeared (found in the clean-room run: `grep "sent for"` → 0 while 62 mails had been delivered). Fixed: `configure_logging()` in `app/workers/worker.py`.
3. **Worker logged personal data.** RQ's default `log_job_description=True` printed each job's description — the recipient's full address and the start of the email body — and stored it in Redis. Fixed: `log_job_description=False` and a fixed job `description="send_email"`.
4. **No correlation between API request and worker job.** The originating request id now travels in RQ job `meta` and is restored into the worker's logging context (call signature unchanged, so already-queued jobs still run; a job without it logs `-`).
5. **Undeclared test dependency** (moto) that turned the only S3 round-trip test into a silent skip outside this machine.

## 5. Matrix rows

| Row | Outcome |
|---|---|
| MinIO / Mailpit healthcheck gating | Done (§2). |
| Vitest in CI | `npm run test` step added to the `frontend` job. |
| Invariant checks in CI (Phase E handoff) | `python -m scripts.check_invariants` runs against the E2E database after the full Playwright suite. |
| Worker correlation id | Done (§4.4); `tests/test_phase_i_worker_correlation.py` (4 tests, real Redis + `rq.SimpleWorker`). |
| Demo requests/emergencies/notifications | `seed_demo_activity()` creates them **through the real API** as the demo users (every domain rule, event, notification and audit entry produced as for a real click-through), idempotent via the API's own idempotency keys; `tests/test_phase_i_demo_seed.py`. Clearly marked "Demo data … not a real request/emergency". |

## 6. Tests added

| File | Tests | Result |
|---|---|---|
| `tests/test_phase_i_object_storage.py` | 3 | pass |
| `tests/test_phase_i_worker_correlation.py` | 4 | pass |
| `tests/test_phase_i_demo_seed.py` | 1 | pass |

Full regression in the Phase J report.

## 7. Remaining limitations

- `docker compose up` and the CI `e2e-object-storage` job were **not executed** here (no image pulls); the compose file is validated and every application-side path was exercised against an S3-API stand-in, not MinIO itself.
- The `api` compose service is dev-oriented (`--reload`, migrations on boot) — unchanged and labelled as such since Phase 28; production uses the image's own entrypoint and a separate migration step.
- The demo seed takes ~3 minutes on a fresh database (Argon2 hashing of ~1000 synthetic donor passwords).
