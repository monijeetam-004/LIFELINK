# LifeLink

A blood-donation coordination platform: patients and families raise blood
requests and emergencies, donors respond, hospitals manage inventory and
incoming requests, and admins govern the platform. Monorepo:

```
LifeLink/
  frontend/   React 19 + Vite + TypeScript + Tailwind + TanStack Query
  backend/    FastAPI + SQLAlchemy + PostgreSQL + Alembic + Redis/RQ + S3/MinIO
  docs/       earlier audit + design + status documents (see note below)
```

## QA audit (2026-10-20): latest status. Read this first

**Status: PASS WITH DOCUMENTED LIMITATIONS.** The full audit is in `docs/QA/FINAL_AUDIT_REPORT.md`.

**Final test results:**

| Suite | Result |
|---|---|
| Backend | **1,405 passed, 0 failed, 1 skipped** (the skipped test needs Neo4j) |
| Frontend unit tests | **172/172** |
| Browser E2E | **107 passed, 0 failed** |
| Invariants and audit chain | All OK |
| Live security probe | 27/27 |
| Live failure injection | 27/27 |
| Clean start | Docker and native both pass |

**Fixed in this round** (each with a regression test):

* a donor could commit to two open requests at once (race);
* the frontend image could be framed (no anti-framing headers);
* the API image did not bound graceful shutdown;
* a start-up storage failure was silently swallowed;
* a public page called an authenticated endpoint;
* a test depended on PATH.

**Outstanding** (environment or clinical; nothing is claimed for these): Neo4j, Gmail, MSG91, live geocoder, official container images, and clinical review. **The 56-day donation interval needs review for Indian guidance** (`docs/QA/CLINICAL_ASSUMPTIONS_AND_LIMITATIONS.md`).

**Setup:**

```bash
# Backend (PostgreSQL 16 and Redis 7 running; see backend/.env.example)
cd backend
python3 -m venv .venv
.venv/bin/pip install --require-hashes --no-deps -r requirements-dev.lock
.venv/bin/pip install --no-deps -e .
cp .env.example .env            # set SECRET_KEY; never commit .env
.venv/bin/python -m scripts.migration_doctor
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.seed_demo_data      # synthetic demo data
.venv/bin/uvicorn app.main:app --port 8000
.venv/bin/python -m app.workers.worker          # e-mail / SMS / re-scan jobs
.venv/bin/python -m app.workers.deadline_monitor

# Frontend
cd ../frontend && npm ci && npm run dev         # or: npm run build && npx vite preview

# Or everything in containers
cd backend && cp .env.example .env && docker compose up -d --build
```

**Testing:**

```bash
cd backend  && CLAMD_TEST_REQUIRED=1 .venv/bin/python -m pytest      # needs clamd; S3_TEST_*/SMTP_TEST_* for the infra tests
cd frontend && npx vitest run && npx tsc --noEmit && npx eslint . && npm run build
cd frontend && E2E_PYTHON=../backend/.venv/bin/python npx playwright test   # API :8000 + preview :4173; DB seeded with scripts.e2e_seed and scripts.seed_demo_data
```

Every other command (failure injection, security probe, crawl, performance, clean starts) is in `docs/QA/REPRODUCE_TESTS.md`.

## Final external validation (2026-10-13): latest status

The real external services were attempted. The details are in `docs/final-verification/FINAL_EXTERNAL_VALIDATION_REPORT.md`.

| Service | Result |
|---|---|
| MinIO | **PASS**, against the real MinIO release: private bucket, presign, the document workflow, outage and recovery, and in Docker |
| Mailpit | **PASS**, against the real Mailpit v1.21.8: the verification e-mail goes through the RQ worker and is checked through the Mailpit API |
| Docker compose and frontend image | **PASS WITH EXTERNAL VALIDATION REQUIRED**. A real `docker compose up --build`: all services healthy, 11 of 12 checks pass, browser tests pass against the containerised frontend. Official base images could not be pulled here |
| Neo4j | **BLOCKED — REAL SERVER UNAVAILABLE** |
| Gmail | **BLOCKED — SMTP NETWORK ACCESS UNAVAILABLE** (no credentials provided) |
| MSG91 SMS | **BLOCKED — CREDENTIALS/PROVIDER CONFIGURATION REQUIRED** |
| Live geocoder | **PARTIAL**. The service is unreachable; the fallbacks are verified |

**Fixed in this phase:** the `worker` and `deadline-monitor` containers were always "unhealthy", because they inherited the API's HTTP health check. They now have their own checks.

**To finish the blocked items yourself:** put the credentials in `backend/.env` (git-ignored, never committed), then run:

* Gmail: `python -m scripts.send_test_email you@example.com` (see GMAIL_SETUP.md);
* MSG91: `python -m scripts.send_test_sms +91XXXXXXXXXX` (see SMS_SETUP.md);
* Neo4j: `docker compose --profile graph up -d neo4j`, then `python -m scripts.verify_neo4j` (see NEO4J_SETUP.md).

Overall verdict: **PASS WITH EXTERNAL VALIDATION REQUIRED**.

## Final verification — gap elimination (2026-10-12) — read this first

Everything after this section is older and kept for the record. Where an older section disagrees with this one, this section is correct. Evidence, setup guides and reports are in **`docs/final-verification/`**:

| Report | What it covers |
|---|---|
| `FINAL_GAP_MATRIX.md` | Every gap, rated PASS / PARTIAL / BLOCKED, with the test or evidence for each |
| `FINAL_TEST_REPORT.md` | Exact counts from the final runs (backend, frontend, browser) |
| `API_COVERAGE.md` | All 160 API operations, and whether each was exercised |
| `CLEAN_START_VERIFICATION.md` | Rebuilding from an empty machine to a working system |
| `INFRASTRUCTURE_TEST.md` | Every external dependency; **migration integrity**, including the `0007_phase7b` error |
| `SECURITY_AUDIT.md`, `ACCESSIBILITY_AUDIT.md`, `PERFORMANCE_REPORT.md`, `FRONTEND_COVERAGE.md` | One report per area |
| `RESEARCH_LIMITATIONS.md` | What the numbers do *not* show |
| `*_SETUP.md` | Docker, Neo4j, MinIO, Mailpit, Gmail and SMS setup |

The paper and its experiment scripts are in `docs/paper/`.

**What changed in this round:**
* **Emergency planning.**
  * The pathway planner uses compatible blood groups, unit counts and components, and counts only units that are still usable on arrival.
  * It plans across several facilities, classifies each plan against the deadline, stores every route it uses, and keeps up to 3 validated backups.
  * When a route fails, it re-plans exactly the emergencies that use it.
* **Facilities and alerts.**
  * Only verified facilities can act as sources; an admin can revoke verification.
  * Donor alerts respect a 50 km distance limit, which an admin can override (the override is audited).
  * Hospital alerts go only to verified facilities nearby that hold compatible stock.
* **Blood units and shipments.**
  * Shipments carry cold-chain telemetry and record an outcome for each unit at receipt.
  * Thawed plasma gets a 24-hour post-thaw expiry; units can be quarantined and discarded.
* **New workers and engines.**
  * A deadline monitor worker and demand forecasting (with backtests).
  * A console SMS provider for development, which records messages as `LOGGED_ONLY`, never "sent".
  * Without Neo4j, a hop-bounded route engine runs over PostgreSQL. It replaces the earlier straight-line fallback.
* **Pages.** Seven research and demo pages now run the real planner.

**Running it.** The commands are the same as below, plus:
* **Migration check.** `python -m scripts.migration_doctor` is read-only. Run it before `alembic upgrade head`; it explains stale or foreign databases instead of failing. Docker runs it automatically.
* **Compose project name.** The compose project is now named `lifelink`, so its volumes are `lifelink_pgdata` and so on. If you ran the stack before, your old `backend_pgdata` volume is **not** used and **not** deleted. To recover or reset it, see `INFRASTRUCTURE_TEST.md` §2.3.
* **Clean start.** To reproduce it: `docs/final-verification/clean_start/run_clean_start.sh`, then `python -m scripts.clean_start_check`.
* **Paper experiments.** `python -m scripts.experiment_pathways OUT` and `python -m scripts.experiment_coverage_matching OUT` (on a freshly seeded database); `python -m scripts.benchmark_pathways --database-url …_bench`.

**Final numbers:**
* Backend: **1,394 passed, 1 skipped** (it needs a live Neo4j), 0 failed.
* Frontend unit tests: **170 / 170**.
* Playwright: **106 passed**, plus 1 test that runs only in the clean start, where it passed.
* API operations exercised: **160 / 160**. 159 returned a 2xx; the graph resync needs Neo4j.
* Clean start: **PASS WITH EXTERNAL VALIDATION REQUIRED**. Neo4j, MinIO and Mailpit could not be obtained here, and Gmail and SMS have no credentials.

## Start here (current state — production hardening, 2026-10)

Everything below this section is the historical README, kept for record;
where it disagrees with this section, this section wins. Current reports:
`docs/LIFELINK_HARDENING_REPORT.md` (latest round, with exact test results),
`docs/LIFELINK_MASTER_COMPLETION_STATE.md` (implemented / verified / partially
verified / not executable here / out of scope), `docs/LIFELINK_FINAL_PRODUCTION_READINESS_REPORT.md`
(Phases A–L), `docs/OPERATIONS_RUNBOOK.md`, `docs/LIFELINK_INVARIANTS.md`.

**Requirements:** Python 3.12 (3.11 works for development), Node 20+,
PostgreSQL 16, Redis 7. Optional: ClamAV `clamd` (malware scanning), an
S3-compatible store (MinIO) for documents/certificates, SMTP, Neo4j.

### Run locally (no Docker)

```bash
# backend
cd backend
python3.12 -m venv .venv && . .venv/bin/activate
pip install --require-hashes --no-deps -r requirements-dev.lock   # exact, hashed pins
pip install --no-deps -e .
cp .env.example .env        # set SECRET_KEY, DATABASE_URL, REDIS_URL at minimum
alembic upgrade head
uvicorn app.main:app --reload            # API on :8000, docs at /docs
python -m app.workers.worker             # second shell: email/SMS/rescan jobs (or RQ_SYNC=true)

# frontend
cd ../frontend
cp .env.example .env        # VITE_API_URL=http://localhost:8000
npm ci
npm run dev                 # http://localhost:5173
```

Optional demo data: `python -m scripts.seed_demo_data` (demo accounts are
printed by the script; never run against production — it refuses).

Uploaded verification documents are only downloadable/approvable after a
real malware scan: set `DOCUMENT_SCANNER=clamd` and run `clamd`
(see `docs/LIFELINK_MALWARE_SCANNING.md`). With the default
`DOCUMENT_SCANNER=none` uploads work but stay `PENDING_SCAN`.

### Run with Docker

```bash
cd backend && cp .env.example .env && docker compose up --build
```

Brings up Postgres, Redis, Mailpit, MinIO (+ bucket init), ClamAV, the API
(migrations run first) and the worker, each health-gated. **Not executed in
the environment that produced this build (registry pulls blocked) — see the
hardening report §7.**

### Test

```bash
cd backend && pytest                              # needs Postgres + Redis (TEST_DATABASE_URL)
CLAMD_TEST_REQUIRED=1 pytest tests/test_hardening_malware_scanning.py   # with a clamd on :3310
cd ../frontend && npm test && npx tsc --noEmit && npm run lint && npm run build
npx playwright test                               # needs the API on :8000 and `vite preview` on :4173
```

### Production checklist (abridged — full list in `docs/OPERATIONS_RUNBOOK.md`)

`ENVIRONMENT=production` refuses to start with an insecure configuration
(default/short `SECRET_KEY`, insecure cookies, wildcard CORS, no malware
scanner). Also: run the API as a database role that does **not** own
`audit_logs`; set `AUDIT_CHECKPOINT_KEY`/`AUDIT_CHECKPOINT_PATH` (append-only
storage) and schedule `python -m scripts.audit_checkpoint write` hourly and
`verify` daily; serve the frontend with the CSP header from
`frontend/vite-csp-plugin.ts` plus `frame-ancestors`.

`docs/` predates this build and describes an earlier planning phase; treat
this README, not `docs/`, as current. The old `docs/IMPLEMENTATION_STATUS.md`
etc. are kept only for history.

**Doc-staleness note (added in Phase 16's documentation-consistency audit):**
the "Status" table below and the "What this build explicitly did not do"
section were written *before* the Neo4j graph engine existed in this
codebase. A later phase (Phase 12) built it for real, and Phases 14-16
re-audited and hardened it; those two spots below are now factually wrong
and are left in place only as a historical record of that earlier session,
not as current status. For the real, current state of the graph/resource-
pathway engine, see `LIFELINK_PHASE_0_AUDIT.md` §7 and
`LIFELINK_PHASE_16_REPORT.md` (both far more current and detailed than this
file). Short version: Neo4j-backed resource-pathway discovery, versioned
`EmergencyPathway` selection with automatic re-optimization on transport
disruption, and an honest graph-degradation path (today: `GRAPH` / `RELATIONAL_ROUTES`; the old `HAVERSINE_FALLBACK` was replaced in the gap-elimination round) are
all real, wired into the live emergency/hospital flow, and covered by 58
passing tests (`test_graph_api.py`, `test_graph_engine.py`,
`test_graph_service.py`, `test_graph_service_logic.py`,
`test_phase16_graph_engine_security.py`) — none of it is the
Flask/discrete-math-pipeline rebuild the "did not do" section below refers
to, which genuinely was never attempted and remains out of scope.

## Status

**Frontend:** all 24 screens across five portals (Patient, Family, Donor,
Hospital, Admin) plus shared account screens are implemented and routed —
`/patient`, `/family` (a genuinely separate portal, not a `/patient` alias),
`/donor`, `/hospital`, `/admin`. Every screen is wired to a real backend
endpoint wherever one exists; where the backend genuinely has no such
capability, the screen shows an honest, labeled "Backend required" state
instead of fabricated data — never a fake success toast or invented numbers.

**Backend:** FastAPI, not Flask (see "What this build explicitly did not do"
below for why that matters). 13 routers, all real, all backed by Postgres via
SQLAlchemy + Alembic migrations, all covered by pytest:

health · auth · patients · donors · family · blood_requests · emergencies ·
notifications · hospitals · matching · certificates · **admin** · **verifications**

The last two, plus authenticated password change, notification preferences,
and donor appointment pre-scheduling, are what this build session added —
closing the gaps the frontend's Admin portal, identity-verification screen,
and settings screen were previously waiting on. Everything else (auth, the
8-type blood-compatibility engine, matching, emergencies, hospital inventory,
certificates, family links) already existed and was re-audited rather than
replaced.

| Capability | State |
|---|---|
| Auth (register/login/logout/verify/forgot/reset) | Real, pre-existing |
| Authenticated password change | **Real, new** — `POST /auth/change-password` |
| 5-role RBAC (`PATIENT`/`FAMILY`/`DONOR`/`HOSPITAL`/`ADMIN`) | Real, pre-existing, server-enforced |
| Blood-type compatibility (8×8 matrix) | Real, pre-existing, tested |
| Blood requests + matching + donor responses | Real, pre-existing |
| Emergency network (create/respond/resolve/match) | Real, pre-existing |
| Hospital inventory + incoming-request handling | Real, pre-existing |
| Family links (request/accept/revoke) | Real, pre-existing — re-audited this session, not replaced |
| Certificates (generate from real donations, verify by code) | Real, pre-existing |
| Notifications (in-app) | Real, pre-existing |
| Notification preferences (email digest, quiet hours) | **Real, new** — with a real (if partial) effect: see below |
| Donor identity / hospital facility verification + document upload | **Real, new** |
| Admin: user directory, role change, suspend/reactivate | **Real, new** |
| Admin: platform KPIs, audit log, system health | **Real, new**, computed from live queries — never hardcoded |
| Donor appointment pre-scheduling | **Real, new**, donor-side only (see limitations) |
| Neo4j graph engine, pathway versioning, transport-failure resilience simulation, `/research/compare` | ~~Not built — see below~~ **Stale: built for real in Phase 12, hardened in Phases 14-16 — see the doc-staleness note above.** `/research/compare` specifically (the Research Mode page) remains a static Stitch-export demo page, now clearly labeled as such in-app. |

## What this build explicitly did not do, and why

A later request asked for this project to be rebuilt around Flask, Neo4j/Aura,
a resource/dependency graph, pathway versioning, transport-failure-recovery
simulation, and an "8-stage discrete-math matching pipeline." None of that
exists anywhere in this codebase or in any earlier phase of it. The real
backend is FastAPI with a simpler, already-solid, already-tested architecture.
The frontend's Graph Decision Engine, Simulation Mode, Research Mode, and
Mathematical Model pages are static/explanatory Stitch design ports — they
were never wired to a real backend graph system, in this build or before it.

Given this project's own repeated, explicit rule — no fake APIs, no fake
success states, real database operations only — building a graph/resilience
subsystem from scratch in one session, just to claim the request was
"complete," would have meant fabricating exactly the kind of fake backend
this rule exists to prevent. That's a genuine multi-week systems project, not
something that becomes real by the end of a session. It was not attempted.

One relevant note: `backend/.env` (not `.env.example`, and not part of this
deliverable — see "What's excluded" below) contains a real, live Neo4j Aura
connection string and password that appear to have been provisioned for a
future phase and never used. That credential is real and currently unrotated.
If you still have access to that Aura instance, consider rotating it, since
it has been sitting in a plaintext local file.

Also not attempted, for the same reason (nothing to honestly build on): a
global `/api/v1` route prefix (would only relabel 71 already-working,
already-frontend-wired endpoints — pure regression risk, zero functional
gain), a migration to Flask, CSV/PDF exports beyond the certificate PDF that
already existed, and synthetic seed data (`lifelink_india_synthetic_donors_1000.csv`
and similar demo-data files were referenced as "existing" and to-be-preserved,
but no such files or seeding scripts exist anywhere in this project — there
was nothing to preserve, and generating 1000 synthetic records from scratch
was out of scope for this pass).

## Quick start (Docker)

```bash
cd backend
cp .env.example .env            # edit SECRET_KEY at minimum
docker compose up --build       # api :8000 (docs at /docs), postgres, redis,
                                 # mailpit :8025, minio :9001
```

```bash
cd frontend
cp .env.example .env            # VITE_API_URL=http://localhost:8000
npm install
npm run dev                     # http://localhost:5173
```

`docker compose up` runs `alembic upgrade head` automatically before the API
starts. The email worker runs as its own `worker` service; set `RQ_SYNC=true`
to deliver mail inline instead (handy for quick local testing without the
worker running).

### Windows (PowerShell) local dev, no Docker

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env             # edit SECRET_KEY, DATABASE_URL, etc.
# Postgres + Redis must be reachable at the URLs in .env — via Docker Desktop,
# WSL2, or native Windows installs; this project does not require Docker,
# only *something* serving Postgres 16 and Redis 7 at those addresses.
alembic upgrade head
uvicorn app.main:app --reload
# separate PowerShell window — email worker:
python -m app.workers.worker
```

**Windows note on the worker command above:** `python -m app.workers.worker` uses
RQ's default `Worker` class, which forks a child process per job — `os.fork` doesn't
exist on Windows, so that command will fail there. The working, Windows-safe
alternative (same Redis, same queues, no code change) is the standalone `rq` CLI
with `SimpleWorker`, which runs jobs in-process instead of forking:

```powershell
rq worker --worker-class rq.worker.SimpleWorker emails sms
```

`emails`/`sms` are the two queue names this project already uses
(`RQ_QUEUE_NAME`/`RQ_SMS_QUEUE_NAME` in `.env.example`, unchanged), so this is a
drop-in replacement for `python -m app.workers.worker` on Windows — not a
different queue system. On macOS/Linux either command works.

```powershell
cd frontend
Copy-Item .env.example .env
npm install
npm run dev
```

## Full API surface (71 endpoints, `backend/app/api/routes/`)

```
GET  /health

POST /auth/register              POST /auth/login                POST /auth/logout
GET  /auth/me                    POST /auth/verify-email          POST /auth/verify-email/resend
POST /auth/forgot-password       POST /auth/reset-password        POST /auth/change-password        [new]

GET/PUT /patients/me             GET /patients/managed

GET/PUT /donors/me               PUT /donors/me/availability      GET /donors/me/requests
GET  /donors/me/responses        GET/POST /donors/me/donations
GET/POST /donors/me/appointments                                 POST /donors/me/appointments/{id}/cancel   [new]

GET/POST /family/links           POST /family/links/{id}/accept   POST /family/links/{id}/revoke

GET/POST /blood-requests         GET /blood-requests/{id}         POST /blood-requests/{id}/cancel
POST /blood-requests/{id}/fulfill                                 POST /blood-requests/{id}/respond
GET  /blood-requests/{id}/match-candidates

GET/POST /emergencies            GET /emergencies/active          GET /emergencies/{id}
POST /emergencies/{id}/acknowledge   POST /emergencies/{id}/assistance-found
POST /emergencies/{id}/cancel        POST /emergencies/{id}/resolve
POST /emergencies/{id}/respond       GET /emergencies/{id}/match-candidates

GET  /notifications               GET /notifications/unread-count
POST /notifications/{id}/read     POST /notifications/read-all
GET/PUT /notifications/preferences                                                                   [new]

GET/PUT /hospitals/me            GET/PUT /hospitals/me/inventory   GET /hospitals/me/requests
GET  /hospitals/me/requests/{id}/events        POST /hospitals/me/requests/{id}/respond
GET  /hospitals/me/emergencies
GET  /hospitals/directory                                                                             [new]

GET  /map/data

GET  /certificates                POST /certificates               GET /certificates/{id}
GET  /certificates/{id}/download  GET /certificates/verify/{code}

POST /verifications               GET /verifications/me                                                [new]
GET  /admin/verifications         GET /admin/verifications/{id}    POST /admin/verifications/{id}/approve
POST /admin/verifications/{id}/reject      GET /admin/verifications/{id}/documents/{index}              [new]

GET  /admin/users                 GET /admin/users/stats           PATCH /admin/users/{id}/role
POST /admin/users/{id}/suspend    POST /admin/users/{id}/reactivate                                     [new]
GET  /admin/blood-requests        GET /admin/emergencies           GET /admin/audit-log
GET  /admin/system/health                                                                               [new]
```

Every response follows FastAPI's standard shape (`{"detail": "..."}` on
error, the declared Pydantic model on success) — this was already the
convention throughout the pre-existing routers, so the new ones match it
rather than introducing a second envelope style.

## Notification preferences: what's real and what isn't yet

`GET`/`PUT /notifications/preferences` is a real, persisted per-user setting
(`email_enabled`, `sms_enabled`, `emergency_alerts_enabled`, `digest_frequency`,
quiet hours). It has a real effect: every in-app notification now optionally
echoes by email, gated by these preferences (`email_enabled=true` and
`digest_frequency=REALTIME`, or an emergency alert with
`emergency_alerts_enabled=true`), and quiet hours genuinely hold back
non-emergency email (never emergency email, never in-app) —
`app/services/notification_service.py::_in_quiet_hours`. What's **not**
built: a batched `DAILY` digest job (no scheduler exists for it) — `DAILY`/`OFF`
currently just suppress the immediate send rather than queuing a digest.

**SMS is disabled.** `sms_enabled` is a real, stored preference bit, but it
has no effect in this deployment — no MSG91 (or any) SMS provider is
configured, on purpose. See `LIFELINK_SMS_STATUS.md` for the plain-language
statement and `LIFELINK_SMS_PROVIDER.md` for the (dormant) provider
architecture. The Settings page does not expose an SMS control to users.

## Donor appointment pre-scheduling: scope

Donor-side only — create, list, cancel your own pre-scheduled appointment
request. There is no hospital-facing confirm/reject workflow yet; a hospital
receives a real notification when a donor requests an appointment at their
facility, but has no endpoint to act on it beyond that. This matches exactly
what the frontend's Eligibility Center screen needed and nothing more.

## Tests

```bash
# backend — needs a disposable Postgres 16 DB + Redis reachable at
# TEST_DATABASE_URL / REDIS_URL (see backend/.env.example)
cd backend
ENVIRONMENT=test SECRET_KEY=test-secret RQ_SYNC=true pytest
ruff check app tests

# frontend
cd frontend
npx tsc --noEmit && npm run lint && npm run build
```

As of this build: **163 backend tests passing**, 0 failing (covering auth,
RBAC/authorization-403 checks for every role on every admin/verification
endpoint, the blood-compatibility and eligibility logic, matching, emergency
state transitions, hospital inventory, family links, certificates, the new
verification workflow including document upload/download round-trips through
real object storage, admin user governance including the suspend-blocks-login
check, change-password's session-rotation behavior, notification preferences,
and donor appointments). Frontend: `tsc --noEmit`, `eslint`, and `vite build`
all clean.

## Security summary

Argon2id password hashing · opaque session id in an `httpOnly`
`SameSite=Lax` cookie, session state in Redis, invalidated the instant
`password_changed_at` moves past a session's issue time (used by both the
password-reset flow and the new change-password endpoint, which rotates the
caller's own session so they aren't logged out by their own action) ·
5-role RBAC enforced server-side via `require_roles()` on every sensitive
route, verified by explicit 403 tests for every non-owning role on every
admin and verification endpoint · object-level authorization (a family
member, donor, or hospital account can only ever see/act on rows tied to
their own id — never someone else's by guessing an id) · `Origin` allow-list
CSRF check (`verify_origin()`) on every mutating request · CORS configured
with an explicit origin list and `allow_credentials=True` — never a wildcard
origin with credentials · fixed-window Redis-backed rate limiting on login,
registration, email verification resend, forgot/reset password, the new
change-password endpoint, and the new verification-submission endpoint ·
every sensitive/admin action (suspend, reactivate, role change, verification
approve/reject, password change, and all the pre-existing ones) recorded to
`audit_logs` with actor, action, target, IP, and user agent, with password
and token fields explicitly stripped from anything written there · uploaded
verification documents validated server-side for content-type and size
before ever touching storage, stored under per-request keys, and served back
only through an admin-authenticated streaming endpoint — never a public URL ·
donor identity (name/phone/email/photo) never exposed to non-owning,
non-authorized roles in any response schema, matching the pre-existing
`donor_ref`-not-`donor_name` convention in the matching endpoints.

## Migrations

```bash
cd backend
alembic upgrade head             # apply
alembic downgrade -1             # roll back one
alembic revision --autogenerate -m "message"   # new migration
```

Six migrations total: `0001_initial` (auth) → `0002_phase2` (profiles, family
links, blood requests, emergencies, notifications, donations) →
`0003_phase3` (hospital + geo) → `0004_phase5` (emergency lifecycle) →
`0005_phase6` (certificates) → the new migration added this session (admin
suspend fields on `users`, `donor_profiles.identity_verified`,
`verification_requests`, `notification_preferences`, `donor_appointments`).
`DATABASE_URL` (env) is authoritative; `alembic.ini` carries no URL.

## Gmail SMTP setup (Phase 11)

Email delivery goes through one generic SMTP client
(`backend/app/services/email_service.py::_smtp_send`, stdlib `smtplib` only —
no SendGrid/Twilio/Mailgun/Resend, no per-provider code path). Gmail is
**configuration only**: pointing the existing `MAIL_*` settings at Google's
SMTP relay makes real Gmail delivery work with zero code changes.

1. **Turn on 2-Step Verification** on the sending Google account, then
   generate a **Google App Password** at
   <https://myaccount.google.com/apppasswords> (a 16-character code — this is
   not your normal Gmail password, and App Passwords are only offered once
   2-Step Verification is on).
2. In your local `backend/.env` (never `.env.example`, never anything
   committed to git), set:

   ```
   MAIL_HOST=smtp.gmail.com
   MAIL_PORT=587
   MAIL_USERNAME=your-gmail-address@gmail.com
   MAIL_PASSWORD=<the 16-character App Password>
   MAIL_FROM=your-gmail-address@gmail.com
   MAIL_STARTTLS=true
   ```

3. Restart the API and the email worker so the new settings are picked up.
   Registration, email verification, password reset, etc. now send through
   Gmail exactly the same way they previously sent through Mailpit — same
   code, same call sites, only the destination SMTP server changed.

**Local dev without Gmail (default, unchanged):** leave `MAIL_HOST=localhost`,
`MAIL_PORT=1025` — Mailpit keeps working exactly as before, viewable at
<http://localhost:8025>.

**Tests never touch Gmail or any real SMTP server.** `ENVIRONMENT=test` makes
`email_service.deliver()` short-circuit before it ever opens a socket (see
`Settings.is_test` / `app/services/email_service.py`) — this was true before
Phase 11 and is unchanged; the Phase 11 test suite additionally verifies the
Gmail-shaped STARTTLS+login code path using an in-process fake SMTP object
(`tests/test_phase11_registration_limits_and_gmail.py`), never a real network
connection.

**Secret handling:** your real App Password lives only in your local,
git-ignored `.env`. It is never logged, never returned in any API response
(including error bodies — a failed send raises with the SMTP server's own
error text, not the credential), and this project's `.gitignore` already
excludes `.env`. Never paste a real App Password into a chat with an
assistant, an issue, a commit, or a test file — `.env.example` only ever
carries the placeholder `<YOUR_GOOGLE_APP_PASSWORD>`.

## Registration limits (Phase 11)

Each of the four self-registerable roles — `PATIENT`, `FAMILY`, `DONOR`,
`HOSPITAL` — has its own **independent** cap on how many accounts can be
created through `POST /auth/register`, controlled by one setting:

```
MAX_ACCOUNTS=100   # PER ROLE, not a shared total — see below
```

`MAX_ACCOUNTS=100` means up to 100 `PATIENT` accounts **and** up to 100
`FAMILY` accounts **and** up to 100 `DONOR` accounts **and** up to 100
`HOSPITAL` accounts — 400 new self-service accounts maximum, not 100 shared
across all four. Filling one role's quota has no effect on any other role:
`DONOR` reaching 100 never blocks `PATIENT`, `FAMILY`, or `HOSPITAL`
registration.

`ADMIN` is not in this system at all: `ADMIN` was never a public registration
option (see `REGISTERABLE_ROLES` in `backend/app/schemas/auth.py`), so it has
no counter, consumes no slot, and is entirely unaffected by `MAX_ACCOUNTS`.
The seeded demo admin account continues to sign in normally through
`POST /auth/login` and the regular Sign In page, with the frontend
auto-redirecting an authenticated `ADMIN` session to `/admin` — none of that
changed in this phase.

**How it's enforced (not a `SELECT COUNT(*) FROM users` query):** a new
`registration_counters` table (one row per self-registerable role, added by
migration `ca362e8cb9cc`) tracks only accounts created through
`POST /auth/register` since this feature shipped. The 1000+ synthetic/demo
users already in this project's seed data (`scripts/seed_demo_data.py`) are
**not** retroactively counted — every counter starts at 0 regardless of how
many users of that role already exist. Each registration call takes a
row-level lock on that one role's counter (`SELECT ... FOR UPDATE`) and
checks it atomically with creating the new `User` row, so two concurrent
requests for the same role when it's one slot from full can never both
succeed — this is verified under real concurrent load in
`tests/test_phase11_registration_limits_and_gmail.py` using the real
Postgres test database, not mocks.

When a role is full, `POST /auth/register` returns `409 Conflict` naming
that specific role, e.g.:

```json
{"detail": "DONOR account limit reached. Maximum 100 DONOR accounts are currently allowed."}
```

— never a generic "maximum accounts reached" message that hides which role
is full.

## What's excluded from this deliverable's zip

`node_modules/`, Python `__pycache__/`/`.venv/`, `.git/`, `backend/.env` (real
local secrets — including the live Neo4j credential noted above), any build
artifacts (`frontend/dist/`), Playwright reports/test results, local Redis
dumps (`dump.rdb`), local DB backups (`backend/backups/`), local object
storage (`.local-storage/`), earlier phase zips, and log/cache directories. `backend/.env.example`
and `frontend/.env.example` are included and contain no real credentials —
copy them to `.env` and fill in your own values before running anything.
