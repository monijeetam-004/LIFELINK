# LifeLink Backend

> See the top-level [`../README.md`](../README.md) for current status, the
> full API surface, security summary, and known limitations — it supersedes
> the phase framing below, which predates the admin/verification/appointments
> work. This file still covers accurate day-to-day mechanics (Docker/local
> setup, migrations, tests).

FastAPI + SQLAlchemy + PostgreSQL + Alembic + Redis/RQ. 13 routers: auth,
patients, donors, hospitals, family, blood_requests, emergencies,
notifications, matching, certificates, health, and — added in the latest
session — **admin** and **verifications**.

## Stack

| Concern | Choice |
|---|---|
| Web framework | FastAPI |
| ORM / migrations | SQLAlchemy 2.0 + Alembic |
| Database | PostgreSQL 16 |
| Cache / queue | Redis 7 + RQ |
| Password hashing | Argon2id (`argon2-cffi`) |
| Sessions | Opaque id in `httpOnly` `SameSite=Lax` cookie; state in Redis |
| Mail (dev) | Mailpit (SMTP :1025, UI :8025) |
| Object storage (dev) | MinIO (S3-compatible) — wired, used by later phases |

## Quick start (Docker)

```bash
cd backend
cp .env.example .env            # edit SECRET_KEY at least
docker compose up --build
# API:      http://localhost:8000      (docs at /docs)
# Mailpit:  http://localhost:8025
# MinIO:    http://localhost:9001      (lifelink / lifelink-secret)
```

The `api` container runs `alembic upgrade head` on start. Startup order is
health-gated (Phase I): `db`, `redis`, `mailpit` and `minio` must be healthy
and the one-shot `createbuckets` service must have exited successfully
before `api` starts; `worker` waits for `api` to be healthy (i.e. migrations
done). `python -m scripts.seed_demo_data` adds demo accounts, ~1000 synthetic
donors, facilities, routes, and a small amount of real demo activity (one
blood request, one acknowledged emergency, the notifications they produce),
created through the API itself and safe to re-run.

## Local (no Docker)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
# Bring up Postgres + Redis + Mailpit however you like (compose services work).
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
# separate shell — email worker:
python -m app.workers.worker
```

Set `RQ_SYNC=true` in `.env` to deliver email inline and skip running a worker.

## Migrations

```bash
alembic upgrade head          # apply
alembic downgrade -1          # roll back one
alembic revision -m "msg"     # new migration (hand-edit) — autogenerate also works
```

`DATABASE_URL` (env) is authoritative; `alembic.ini` has no URL.

## Tests

Requires a **disposable** Postgres DB and Redis. Redis DB index 15 is flushed by
the suite.

```bash
cd backend
createdb lifelink_test           # or: psql -c 'CREATE DATABASE lifelink_test;'
export TEST_DATABASE_URL=postgresql+psycopg://lifelink:lifelink@localhost:5432/lifelink_test
export REDIS_URL=redis://localhost:6379/15
ENVIRONMENT=test pytest
ruff check .
mypy app
```

## API (Phase 1)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | DB + Redis liveness (503 if degraded) |
| `POST` | `/auth/register` | Create account (role ∈ PATIENT/FAMILY/DONOR/HOSPITAL), send verification email |
| `POST` | `/auth/login` | Verify credentials, set session cookie |
| `POST` | `/auth/logout` | Destroy session, clear cookie |
| `GET` | `/auth/me` | Current user (401 if not authed) |
| `POST` | `/auth/verify-email` | Consume verification token |
| `POST` | `/auth/verify-email/resend` | Re-send verification email (rate-limited) |
| `POST` | `/auth/forgot-password` | Email a 6-digit OTP (always 202, no enumeration) |
| `POST` | `/auth/reset-password` | OTP + new password; **invalidates all sessions** |

### Security properties

- Argon2id hashing; transparent rehash on login when parameters change.
- Tokens and OTPs are stored only as keyed HMAC-SHA256 digests (`sign()`).
- Session invalidation on password reset via both active Redis deletion and a
  `password_changed_at > session.issued_at` check on every request.
- Fixed-window rate limits (Redis) on login (per IP + per account), register
  (per IP), verify-resend, forgot-password, reset-password.
- Roles enforced server-side via `require_roles(...)`; the frontend's route
  guards are UX only.
- Lightweight CSRF defense: `Origin` allow-list check on auth mutations +
  `SameSite=Lax` cookie + CORS allow-list.
- Security events written to `audit_logs` (register, login, login_failed,
  logout, email_verified, password_reset_requested, password_reset,
  sessions_invalidated) with no secrets in `meta`.

## Layout

```
app/
  core/        config, security, redis, ratelimit, storage, time
  db/          Base, engine/session
  models/      user, email_verification_token, password_reset_otp, audit_log
  schemas/     pydantic request/response models
  services/    auth_service, session_service, email_service, audit_service
  api/
    deps.py    get_db, get_current_user, require_roles, verify_origin
    cookies.py session cookie set/clear
    routes/    auth.py, health.py
  workers/     queue, tasks, worker entrypoint
alembic/       migrations (0001_initial)
tests/         pytest suite
```
