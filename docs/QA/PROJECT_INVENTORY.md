# Project inventory

**Sources.** This inventory was generated from the **source code**, not from the paper or earlier reports:

* `backend/scripts/qa_inventory.py` (new in this round) imports the FastAPI app and the SQLAlchemy metadata;
* it parses `frontend/src/App.tsx`;
* it reads the route log of the final backend run.

Raw output: `evidence/inventory/inventory.json`.

**Repository identity.**

* Baseline: tag `qa-baseline-2026-10-20` = commit `0422954`, with a clean working tree at the start.
* The final commit of this round is named in FINAL_AUDIT_REPORT.md §1.

## 1. Components

| Component | Location | Runtime | Dependencies |
|---|---|---|---|
| API | `backend/app` (FastAPI): 18 routers in `app/api/routes`, services in `app/services`, domain rules in `app/core` | `uvicorn app.asgi:application` (image CMD) or `scripts/run_production.sh` | PostgreSQL, Redis; optional: S3/MinIO, clamd, SMTP, Neo4j, geocoder, MSG91 |
| RQ worker | `app/workers/worker.py`, jobs in `app/workers/tasks.py` (`send_email`, `rescan_documents`, `retry_sms`) | `python -m app.workers.worker` | Redis, PostgreSQL, SMTP, clamd |
| Deadline monitor (scheduled loop, every 60 s) | `app/workers/deadline_monitor.py` (`run_once`, heartbeat) | `python -m app.workers.deadline_monitor` | PostgreSQL |
| Realtime publisher | `app/services/realtime_service.py`: background thread, outbox → Redis pub/sub → SSE (`GET /events/stream`) | Inside every API process | PostgreSQL, Redis |
| Scheduled maintenance (operator-run) | `scripts/audit_checkpoint.py` (write hourly, verify daily), `scripts/rq_queue_maintenance.py`, `scripts/rescan_documents.py`, `scripts/db_backup.sh` / `db_restore.sh` | cron or CronJob (OPERATIONS_RUNBOOK.md) | PostgreSQL, Redis, an external checkpoint path |
| Frontend | `frontend/src` (React 19, Vite, TanStack Query): 63 routes for 5 roles | `vite preview` (dev and E2E) or the nginx image (`frontend/Dockerfile`, `nginx.conf`) | API |
| Database schema | 39 tables; `backend/alembic`: 31 revisions in one linear chain, head `e1f2a3b4c5d6` | `alembic upgrade head`, after `scripts/migration_doctor.py` | PostgreSQL 16 |
| Containers | `backend/docker-compose.yml`: db, redis, mailpit (profile), minio, createbuckets, clamav, neo4j (profile `graph`), api, worker, deadline-monitor, frontend | `docker compose up -d --build` | Docker Engine and Compose |
| CI | `.github/workflows/ci.yml`: secret-scan, backend, migrations, frontend, dependency-audit, docker, e2e, e2e-object-storage, ci-summary | GitHub Actions | — |
| Configuration | `backend/.env.example` (90 settings, placeholders only), `frontend/.env.example` | — | — |

## 2. User-facing features by role

The roles come from the `RequireAuth` guards in `App.tsx`, and the backend enforces the same roles with `require_roles`. Every feature row is mapped to its tests in FEATURE_TEST_MATRIX.md.

| Role | Features |
|---|---|
| Public | Landing page, registration, sign-in, e-mail verification, forgot and reset password, certificate verification by code, help and legal pages, error and offline pages; live research pages (graph engine, simulation, research mode, mathematical model) — these show a sign-in prompt to anonymous visitors |
| Patient | Dashboard, blood requests (guided form, history), SOS emergency and its tracking, find donors (map), notifications |
| Family | Linked patients (request, accept, expire), act on a linked patient's behalf |
| Donor | Dashboard, mobile view, eligibility, ready console (accept or decline requests and emergencies), donations (log), milestones, identity verification (document upload), certificates |
| Hospital | Dashboard and map, inventory by group and component, unit lifecycle (reserve, allocate, quarantine, release, discard, thaw), request detail and allocation, resource pathways, shipments with cold chain and receipt, facility verification, donation reviews, analytics and forecasting |
| Admin | Overview, verifications queue (approve, reject, re-scan), users (suspend), facilities (revoke verification), donors, transport routes (create, disrupt, recover), shipments, audit feed with chain verification, analytics |
| All signed-in | Profile, settings, notifications and preferences, live updates (SSE), matching strategy lab, decision transparency, donor recommendation |

## 3. External integrations

The full dependency matrix, with real versus stand-in evidence, is in INTEGRATION_STATUS.md.

| Integration | Code | Configuration |
|---|---|---|
| SMTP: Mailpit (dev) or Gmail | `app/services/email_service.py` | `MAIL_*` |
| SMS: MSG91, or the console provider (dev only) | `app/services/sms/` | `SMS_PROVIDER`, `MSG91_*` |
| Object storage: S3 / MinIO, or local (dev) | `app/core/storage.py` | `S3_*` (S3 when `S3_ENDPOINT_URL` and both keys are set, otherwise local files) |
| Malware scanning: clamd | `app/services/document_scanner.py` | `DOCUMENT_SCANNER`, `CLAMD_*` |
| Graph engine: Neo4j | `app/core/graph.py`, `app/services/graph_service.py` | `NEO4J_*` |
| Geocoder: Nominatim-compatible | `app/services/geocoding.py` | `GEOCODER_URL`, `GEOCODER_*` |
| Map tiles: OpenStreetMap | `frontend/src/components/DonorMap.tsx` | `VITE_MAP_TILE_URL` |

## 4. Status vocabulary

Status values used in this inventory and the matrix:

* **IMPLEMENTED_AND_VERIFIED.** Exercised by executed tests that assert behaviour, with the evidence named.
* **IMPLEMENTED_BUT_PARTIAL.** Exercised, but one layer is missing, for example a success path without a negative path, or a live service unreachable.
* **IMPLEMENTED_BUT_UNVERIFIED.** No executed test reaches it.
* **BROKEN**, **NOT_IMPLEMENTED** and **BLOCKED_BY_ENVIRONMENT**, as defined in the audit brief.

**How the API status was derived.** The API status column is derived mechanically from the final backend run's route log:

* an operation for which the suite observed both a success response and at least one 4xx response is **IMPLEMENTED_AND_VERIFIED**;
* an operation with only success responses is marked "success path; no 4xx observed";
* an operation never called is **IMPLEMENTED_BUT_UNVERIFIED**.

The status code alone does not prove correctness. Content, persistence and side effects are asserted by the tests themselves, which are listed per feature in FEATURE_TEST_MATRIX.md.

## 5. Generated tables

**Summary:**

| Item | Count |
|---|---|
| API operations | 160 |
| Frontend routes | 63 |
| Database tables | 39 |

**API operations by authentication:**

* session: 145;
* public: 13;
* optional: 2.

**Origin check.** 85 operations carry the `Origin` check, which covers every state-changing operation.

### API operations

| # | Method | Path | Module | Auth | Roles | Origin check | Calls in final run | Status codes observed | Status |
|---|---|---|---|---|---|---|---|---|---|
| 1 | GET | `/admin/analytics/overview` | admin | session | ADMIN | — | 35 | 200×5, 401×4, 403×24, 422×2 | IMPLEMENTED_AND_VERIFIED |
| 2 | GET | `/admin/audit-log` | admin | session | ADMIN | — | 66 | 200×29, 400×1, 401×4, 403×32 | IMPLEMENTED_AND_VERIFIED |
| 3 | POST | `/admin/audit-log/checkpoint` | admin | session | ADMIN | yes | 29 | 200×1, 401×4, 403×23, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 4 | GET | `/admin/audit-log/head` | admin | session | ADMIN | — | 25 | 200×1, 401×4, 403×20 | IMPLEMENTED_AND_VERIFIED |
| 5 | POST | `/admin/audit-log/verify` | admin | session | ADMIN | yes | 46 | 200×11, 401×4, 403×28, 409×1, 422×2 | IMPLEMENTED_AND_VERIFIED |
| 6 | GET | `/admin/blood-requests` | admin | session | ADMIN | — | 45 | 200×15, 401×4, 403×21, 422×5 | IMPLEMENTED_AND_VERIFIED |
| 7 | POST | `/admin/blood-requests/{request_id}/transition` | admin | session | ADMIN | yes | 31 | 200×3, 400×2, 401×4, 403×21, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 8 | GET | `/admin/certificates` | admin | session | ADMIN | — | 35 | 200×10, 401×5, 403×20 | IMPLEMENTED_AND_VERIFIED |
| 9 | POST | `/admin/certificates/{certificate_id}/revoke` | admin | session | ADMIN | yes | 29 | 200×2, 401×4, 403×21, 404×1, 422×1 | IMPLEMENTED_AND_VERIFIED |
| 10 | GET | `/admin/donors` | admin | session | ADMIN | — | 34 | 200×10, 401×4, 403×20 | IMPLEMENTED_AND_VERIFIED |
| 11 | GET | `/admin/emergencies` | admin | session | ADMIN | — | 29 | 200×3, 401×4, 403×21, 422×1 | IMPLEMENTED_AND_VERIFIED |
| 12 | GET | `/admin/facilities` | admin | session | ADMIN | — | 37 | 200×12, 401×4, 403×20, 422×1 | IMPLEMENTED_AND_VERIFIED |
| 13 | GET | `/admin/facilities/{facility_id}` | admin | session | ADMIN | — | 40 | 200×15, 401×4, 403×20, 404×1 | IMPLEMENTED_AND_VERIFIED |
| 14 | POST | `/admin/facilities/{facility_id}/revoke-verification` | admin | session | ADMIN | yes | 26 | 200×1, 401×4, 403×20, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 15 | GET | `/admin/graph/pathway-log` | admin | session | ADMIN | — | 25 | 200×1, 401×4, 403×20 | IMPLEMENTED_AND_VERIFIED |
| 16 | POST | `/admin/graph/resync` | admin | session | ADMIN | yes | 32 | 401×4, 403×24, 503×4 | BLOCKED_BY_ENVIRONMENT (needs Neo4j; authorization and the honest 503 are verified) |
| 17 | GET | `/admin/system/health` | admin | session | ADMIN | — | 31 | 200×7, 401×4, 403×20 | IMPLEMENTED_AND_VERIFIED |
| 18 | GET | `/admin/system/metrics` | admin | session | ADMIN | — | 24 | 200×2, 401×5, 403×17 | IMPLEMENTED_AND_VERIFIED |
| 19 | GET | `/admin/transport-routes` | admin | session | ADMIN | — | 27 | 200×2, 401×4, 403×21 | IMPLEMENTED_AND_VERIFIED |
| 20 | POST | `/admin/transport-routes` | admin | session | ADMIN | yes | 50 | 201×18, 400×2, 401×4, 403×24, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 21 | POST | `/admin/transport-routes/{route_id}/disrupt` | admin | session | ADMIN | yes | 34 | 200×8, 401×4, 403×20, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 22 | POST | `/admin/transport-routes/{route_id}/recover` | admin | session | ADMIN | yes | 27 | 200×2, 401×4, 403×20, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 23 | GET | `/admin/users` | admin | session | ADMIN | — | 62 | 200×25, 401×4, 403×28, 422×5 | IMPLEMENTED_AND_VERIFIED |
| 24 | GET | `/admin/users/stats` | admin | session | ADMIN | — | 26 | 200×2, 401×4, 403×20 | IMPLEMENTED_AND_VERIFIED |
| 25 | POST | `/admin/users/{user_id}/reactivate` | admin | session | ADMIN | yes | 32 | 200×4, 401×4, 403×24 | IMPLEMENTED_AND_VERIFIED |
| 26 | PATCH | `/admin/users/{user_id}/role` | admin | session | ADMIN | yes | 31 | 200×2, 400×1, 401×4, 403×24 | IMPLEMENTED_AND_VERIFIED |
| 27 | POST | `/admin/users/{user_id}/suspend` | admin | session | ADMIN | yes | 40 | 200×9, 400×1, 401×4, 403×24, 422×2 | IMPLEMENTED_AND_VERIFIED |
| 28 | POST | `/auth/change-password` | auth | session | any signed-in | yes | 14 | 200×4, 401×8, 403×1, 429×1 | IMPLEMENTED_AND_VERIFIED |
| 29 | POST | `/auth/forgot-password` | auth | none | — | yes | 14 | 202×13, 403×1 | IMPLEMENTED_AND_VERIFIED |
| 30 | POST | `/auth/login` | auth | none | — | yes | 1809 | 200×1786, 401×15, 403×2, 429×6 | IMPLEMENTED_AND_VERIFIED |
| 31 | POST | `/auth/logout` | auth | none | — | yes | 41 | 204×39, 403×2 | IMPLEMENTED_AND_VERIFIED |
| 32 | GET | `/auth/me` | auth | session | any signed-in | yes | 86 | 200×61, 401×25 | IMPLEMENTED_AND_VERIFIED |
| 33 | POST | `/auth/register` | auth | none | — | yes | 89 | 201×61, 403×1, 409×15, 422×8, 429×3, 503×1 | IMPLEMENTED_AND_VERIFIED |
| 34 | POST | `/auth/reset-password` | auth | none | — | yes | 35 | 200×4, 400×13, 403×1, 429×17 | IMPLEMENTED_AND_VERIFIED |
| 35 | GET | `/auth/session` | auth | optional | — | yes | 3 | 200×3 | IMPLEMENTED_AND_VERIFIED (success path; no 4xx observed) |
| 36 | POST | `/auth/verify-email` | auth | none | — | yes | 30 | 200×17, 400×11, 403×1, 422×1 | IMPLEMENTED_AND_VERIFIED |
| 37 | POST | `/auth/verify-email/resend` | auth | none | — | yes | 6 | 202×5, 403×1 | IMPLEMENTED_AND_VERIFIED |
| 38 | GET | `/blood-requests` | blood_requests | session | any signed-in | — | 9 | 200×8, 401×1 | IMPLEMENTED_AND_VERIFIED |
| 39 | POST | `/blood-requests` | blood_requests | session | FAMILY, HOSPITAL, PATIENT | yes | 303 | 200×9, 201×273, 401×2, 403×10, 404×1, 409×3, 422×3, 429×2 | IMPLEMENTED_AND_VERIFIED |
| 40 | GET | `/blood-requests/{request_id}` | blood_requests | session | any signed-in | — | 60 | 200×44, 401×2, 404×12, 422×2 | IMPLEMENTED_AND_VERIFIED |
| 41 | POST | `/blood-requests/{request_id}/cancel` | blood_requests | session | any signed-in | yes | 27 | 200×13, 401×1, 403×1, 404×5, 409×6, 422×1 | IMPLEMENTED_AND_VERIFIED |
| 42 | POST | `/blood-requests/{request_id}/fulfill` | blood_requests | session | any signed-in | yes | 15 | 200×6, 401×1, 403×1, 404×4, 409×3 | IMPLEMENTED_AND_VERIFIED |
| 43 | POST | `/blood-requests/{request_id}/respond` | blood_requests | session | DONOR | yes | 68 | 200×48, 400×4, 401×1, 403×8, 404×1, 409×6 | IMPLEMENTED_AND_VERIFIED |
| 44 | GET | `/certificates` | certificates | session | DONOR | — | 15 | 200×4, 401×3, 403×8 | IMPLEMENTED_AND_VERIFIED |
| 45 | POST | `/certificates` | certificates | session | DONOR | yes | 76 | 201×56, 401×2, 403×9, 404×3, 409×5, 503×1 | IMPLEMENTED_AND_VERIFIED |
| 46 | GET | `/certificates/verify/{code}` | certificates | none | — | — | 9 | 200×5, 404×4 | IMPLEMENTED_AND_VERIFIED |
| 47 | GET | `/certificates/{certificate_id}` | certificates | session | any signed-in | — | 21 | 200×5, 401×3, 404×12, 422×1 | IMPLEMENTED_AND_VERIFIED |
| 48 | GET | `/certificates/{certificate_id}/download` | certificates | optional | — | — | 36 | 200×9, 401×24, 404×1, 422×1, 503×1 | IMPLEMENTED_AND_VERIFIED |
| 49 | GET | `/donations` | donation_review | session | ADMIN, HOSPITAL | — | 9 | 200×4, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 50 | GET | `/donations/{donation_id}` | donation_review | session | ADMIN, HOSPITAL | — | 11 | 200×2, 401×1, 403×8 | IMPLEMENTED_AND_VERIFIED |
| 51 | POST | `/donations/{donation_id}/reject` | donation_review | session | ADMIN, HOSPITAL | yes | 17 | 200×8, 401×1, 403×7, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 52 | POST | `/donations/{donation_id}/verify` | donation_review | session | ADMIN, HOSPITAL | yes | 75 | 200×62, 401×1, 403×9, 404×1, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 53 | GET | `/donors/me` | donors | session | DONOR | — | 22 | 200×13, 401×2, 403×7 | IMPLEMENTED_AND_VERIFIED |
| 54 | PUT | `/donors/me` | donors | session | DONOR | yes | 227 | 200×221, 401×1, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 55 | GET | `/donors/me/appointments` | donors | session | DONOR | — | 9 | 200×3, 401×1, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 56 | POST | `/donors/me/appointments` | donors | session | DONOR | yes | 31 | 201×20, 400×3, 401×1, 403×6, 404×1 | IMPLEMENTED_AND_VERIFIED |
| 57 | POST | `/donors/me/appointments/{appointment_id}/cancel` | donors | session | DONOR | yes | 14 | 200×3, 401×1, 403×5, 404×4, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 58 | PUT | `/donors/me/availability` | donors | session | DONOR | yes | 12 | 200×6, 401×1, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 59 | GET | `/donors/me/donations` | donors | session | DONOR | — | 10 | 200×5, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 60 | POST | `/donors/me/donations` | donors | session | DONOR | yes | 151 | 200×37, 201×96, 400×2, 401×2, 403×9, 404×1, 409×1, 422×3 | IMPLEMENTED_AND_VERIFIED |
| 61 | GET | `/donors/me/requests` | donors | session | DONOR | — | 10 | 200×5, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 62 | GET | `/donors/me/responses` | donors | session | DONOR | — | 16 | 200×9, 401×2, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 63 | GET | `/emergencies` | emergencies | session | any signed-in | — | 3 | 200×2, 401×1 | IMPLEMENTED_AND_VERIFIED |
| 64 | POST | `/emergencies` | emergencies | session | FAMILY, HOSPITAL, PATIENT | yes | 190 | 200×21, 201×152, 400×3, 401×1, 403×7, 422×4, 429×2 | IMPLEMENTED_AND_VERIFIED |
| 65 | GET | `/emergencies/active` | emergencies | session | DONOR, HOSPITAL | — | 9 | 200×3, 401×2, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 66 | GET | `/emergencies/{emergency_id}` | emergencies | session | any signed-in | — | 50 | 200×36, 401×1, 404×13 | IMPLEMENTED_AND_VERIFIED |
| 67 | POST | `/emergencies/{emergency_id}/acknowledge` | emergencies | session | ADMIN, DONOR, HOSPITAL | yes | 17 | 200×11, 401×1, 403×3, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 68 | POST | `/emergencies/{emergency_id}/assistance-found` | emergencies | session | any signed-in | yes | 8 | 200×3, 401×1, 403×1, 404×3 | IMPLEMENTED_AND_VERIFIED |
| 69 | POST | `/emergencies/{emergency_id}/cancel` | emergencies | session | any signed-in | yes | 17 | 200×7, 401×1, 403×1, 404×7, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 70 | GET | `/emergencies/{emergency_id}/match-candidates` | emergencies | session | any signed-in | — | 10 | 200×8, 401×1, 404×1 | IMPLEMENTED_AND_VERIFIED |
| 71 | GET | `/emergencies/{emergency_id}/match-strategies` | emergencies | session | any signed-in | — | 3 | 200×1, 401×1, 404×1 | IMPLEMENTED_AND_VERIFIED |
| 72 | POST | `/emergencies/{emergency_id}/overrides` | emergencies | session | ADMIN | yes | 8 | 201×1, 401×1, 403×6 | IMPLEMENTED_AND_VERIFIED |
| 73 | POST | `/emergencies/{emergency_id}/overrides/{override_id}/revoke` | emergencies | session | ADMIN | yes | 7 | 200×1, 401×1, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 74 | POST | `/emergencies/{emergency_id}/pathway` | emergencies | session | any signed-in | yes | 19 | 200×14, 401×1, 403×3, 404×1 | IMPLEMENTED_AND_VERIFIED |
| 75 | POST | `/emergencies/{emergency_id}/pathway/release` | emergencies | session | any signed-in | yes | 3 | 200×1, 401×1, 403×1 | IMPLEMENTED_AND_VERIFIED |
| 76 | POST | `/emergencies/{emergency_id}/pathway/reserve` | emergencies | session | any signed-in | yes | 3 | 200×1, 401×1, 403×1 | IMPLEMENTED_AND_VERIFIED |
| 77 | GET | `/emergencies/{emergency_id}/pathways` | emergencies | session | any signed-in | — | 3 | 200×2, 401×1 | IMPLEMENTED_AND_VERIFIED |
| 78 | POST | `/emergencies/{emergency_id}/resolve` | emergencies | session | any signed-in | yes | 18 | 200×8, 401×1, 403×1, 404×4, 409×4 | IMPLEMENTED_AND_VERIFIED |
| 79 | GET | `/emergencies/{emergency_id}/resource-pathways` | emergencies | session | any signed-in | — | 16 | 200×3, 401×1, 403×4, 404×3, 422×5 | IMPLEMENTED_AND_VERIFIED |
| 80 | POST | `/emergencies/{emergency_id}/respond` | emergencies | session | DONOR, HOSPITAL | yes | 42 | 200×29, 400×3, 401×1, 403×5, 404×1, 409×3 | IMPLEMENTED_AND_VERIFIED |
| 81 | GET | `/events/stream` | events | none | — | — | 18 | 200×16, 401×2 | IMPLEMENTED_AND_VERIFIED |
| 82 | GET | `/family/links` | family | session | any signed-in | — | 12 | 200×10, 401×2 | IMPLEMENTED_AND_VERIFIED |
| 83 | POST | `/family/links` | family | session | FAMILY | yes | 77 | 201×61, 401×1, 403×7, 404×4, 409×4 | IMPLEMENTED_AND_VERIFIED |
| 84 | POST | `/family/links/{link_id}/accept` | family | session | any signed-in | yes | 41 | 200×26, 401×1, 403×8, 404×1, 409×5 | IMPLEMENTED_AND_VERIFIED |
| 85 | POST | `/family/links/{link_id}/reject` | family | session | any signed-in | yes | 19 | 200×8, 401×1, 403×7, 404×1, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 86 | POST | `/family/links/{link_id}/revoke` | family | session | any signed-in | yes | 19 | 200×9, 401×1, 403×6, 404×1, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 87 | GET | `/forecasts` | forecasts | session | ADMIN, HOSPITAL | — | 5 | 200×1, 401×1, 403×3 | IMPLEMENTED_AND_VERIFIED |
| 88 | POST | `/forecasts` | forecasts | session | ADMIN, HOSPITAL | yes | 11 | 201×4, 401×1, 403×6 | IMPLEMENTED_AND_VERIFIED |
| 89 | GET | `/forecasts/{run_id}` | forecasts | session | ADMIN, HOSPITAL | — | 7 | 200×2, 401×1, 403×3, 404×1 | IMPLEMENTED_AND_VERIFIED |
| 90 | GET | `/health` | health | none | — | — | 8 | 200×8 | IMPLEMENTED_AND_VERIFIED |
| 91 | GET | `/health/live` | health | none | — | — | 5 | 200×5 | IMPLEMENTED_AND_VERIFIED |
| 92 | GET | `/health/ready` | health | none | — | — | 5 | 200×4, 503×1 | IMPLEMENTED_AND_VERIFIED |
| 93 | GET | `/hospitals/directory` | hospitals | session | any signed-in | — | 15 | 200×13, 401×2 | IMPLEMENTED_AND_VERIFIED |
| 94 | GET | `/hospitals/me` | hospitals | session | HOSPITAL | — | 127 | 200×120, 401×1, 403×6 | IMPLEMENTED_AND_VERIFIED |
| 95 | PUT | `/hospitals/me` | hospitals | session | HOSPITAL | yes | 426 | 200×420, 401×1, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 96 | GET | `/hospitals/me/activity` | hospitals | session | HOSPITAL | — | 6 | 200×1, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 97 | GET | `/hospitals/me/emergencies` | hospitals | session | HOSPITAL | — | 6 | 200×1, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 98 | GET | `/hospitals/me/inventory` | hospitals | session | HOSPITAL | — | 58 | 200×53, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 99 | PUT | `/hospitals/me/inventory` | hospitals | session | HOSPITAL | yes | 16 | 200×9, 401×1, 403×5, 422×1 | IMPLEMENTED_AND_VERIFIED |
| 100 | POST | `/hospitals/me/inventory/add` | hospitals | session | HOSPITAL | yes | 177 | 200×165, 401×1, 403×5, 409×4, 422×2 | IMPLEMENTED_AND_VERIFIED |
| 101 | POST | `/hospitals/me/inventory/allocate` | hospitals | session | HOSPITAL | yes | 27 | 200×16, 401×1, 403×5, 409×5 | IMPLEMENTED_AND_VERIFIED |
| 102 | GET | `/hospitals/me/inventory/components` | hospitals | session | HOSPITAL | — | 15 | 200×6, 401×1, 403×8 | IMPLEMENTED_AND_VERIFIED |
| 103 | POST | `/hospitals/me/inventory/expire` | hospitals | session | HOSPITAL | yes | 10 | 200×2, 401×1, 403×5, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 104 | GET | `/hospitals/me/inventory/log` | hospitals | session | HOSPITAL | — | 7 | 200×2, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 105 | POST | `/hospitals/me/inventory/reconcile` | hospitals | session | HOSPITAL | yes | 8 | 200×1, 401×1, 403×5, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 106 | POST | `/hospitals/me/inventory/release` | hospitals | session | HOSPITAL | yes | 10 | 200×3, 401×1, 403×5, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 107 | POST | `/hospitals/me/inventory/reserve` | hospitals | session | HOSPITAL | yes | 38 | 200×28, 401×1, 403×5, 409×4 | IMPLEMENTED_AND_VERIFIED |
| 108 | GET | `/hospitals/me/requests` | hospitals | session | HOSPITAL | — | 11 | 200×6, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 109 | POST | `/hospitals/me/requests/{request_id}/allocate` | hospitals | session | HOSPITAL | yes | 52 | 200×31, 400×1, 401×1, 403×5, 404×2, 409×11, 422×1 | IMPLEMENTED_AND_VERIFIED |
| 110 | GET | `/hospitals/me/requests/{request_id}/events` | hospitals | session | HOSPITAL | — | 9 | 200×1, 401×1, 403×4, 404×3 | IMPLEMENTED_AND_VERIFIED |
| 111 | POST | `/hospitals/me/requests/{request_id}/fulfill` | hospitals | session | HOSPITAL | yes | 17 | 200×6, 401×1, 403×5, 404×1, 409×4 | IMPLEMENTED_AND_VERIFIED |
| 112 | POST | `/hospitals/me/requests/{request_id}/respond` | hospitals | session | HOSPITAL | yes | 14 | 200×4, 401×1, 403×5, 404×1, 422×3 | IMPLEMENTED_AND_VERIFIED |
| 113 | GET | `/hospitals/me/resource-pathways` | hospitals | session | HOSPITAL | — | 20 | 200×6, 401×1, 403×8, 422×5 | IMPLEMENTED_AND_VERIFIED |
| 114 | GET | `/hospitals/me/routes` | hospitals | session | HOSPITAL | — | 7 | 200×2, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 115 | GET | `/hospitals/me/units` | hospitals | session | HOSPITAL | — | 9 | 200×4, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 116 | POST | `/hospitals/me/units/{unit_id}/discard` | hospitals | session | HOSPITAL | yes | 8 | 200×1, 401×1, 403×5, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 117 | POST | `/hospitals/me/units/{unit_id}/quarantine` | hospitals | session | HOSPITAL | yes | 8 | 200×1, 401×1, 403×5, 404×1 | IMPLEMENTED_AND_VERIFIED |
| 118 | POST | `/hospitals/me/units/{unit_id}/release-quarantine` | hospitals | session | HOSPITAL | yes | 7 | 200×1, 401×1, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 119 | POST | `/hospitals/me/units/{unit_id}/thaw` | hospitals | session | HOSPITAL | yes | 9 | 200×1, 401×1, 403×5, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 120 | GET | `/` | main | none | — | — | 1 | 200×1 | IMPLEMENTED_AND_VERIFIED |
| 121 | GET | `/blood-requests/{request_id}/match-candidates` | matching | session | any signed-in | — | 8 | 200×5, 401×1, 404×2 | IMPLEMENTED_AND_VERIFIED |
| 122 | GET | `/blood-requests/{request_id}/match-strategies` | matching | session | any signed-in | — | 3 | 200×1, 401×1, 404×1 | IMPLEMENTED_AND_VERIFIED |
| 123 | GET | `/map/data` | matching | session | FAMILY, HOSPITAL, PATIENT | — | 8 | 200×4, 401×1, 403×3 | IMPLEMENTED_AND_VERIFIED |
| 124 | GET | `/matching/blood-compatibility` | matching | session | any signed-in | — | 3 | 200×1, 401×2 | IMPLEMENTED_AND_VERIFIED |
| 125 | GET | `/notifications` | notifications | session | any signed-in | — | 122 | 200×117, 401×1, 403×2, 422×2 | IMPLEMENTED_AND_VERIFIED |
| 126 | GET | `/notifications/preferences` | notifications | session | any signed-in | — | 7 | 200×5, 401×2 | IMPLEMENTED_AND_VERIFIED |
| 127 | PUT | `/notifications/preferences` | notifications | session | any signed-in | yes | 14 | 200×8, 401×2, 403×1, 422×3 | IMPLEMENTED_AND_VERIFIED |
| 128 | POST | `/notifications/read-all` | notifications | session | any signed-in | yes | 3 | 200×1, 401×1, 403×1 | IMPLEMENTED_AND_VERIFIED |
| 129 | GET | `/notifications/unread-count` | notifications | session | any signed-in | — | 6 | 200×5, 401×1 | IMPLEMENTED_AND_VERIFIED |
| 130 | POST | `/notifications/{notification_id}/read` | notifications | session | any signed-in | yes | 16 | 200×11, 401×1, 403×1, 404×3 | IMPLEMENTED_AND_VERIFIED |
| 131 | GET | `/pathways/config` | pathways | session | any signed-in | — | 2 | 200×1, 401×1 | IMPLEMENTED_AND_VERIFIED |
| 132 | POST | `/pathways/simulate` | pathways | session | ADMIN, HOSPITAL | yes | 9 | 200×2, 401×1, 403×5, 404×1 | IMPLEMENTED_AND_VERIFIED |
| 133 | GET | `/patients/managed` | patients | session | FAMILY | — | 18 | 200×12, 401×2, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 134 | GET | `/patients/me` | patients | session | FAMILY, PATIENT | — | 15 | 200×3, 401×4, 403×4, 503×4 | IMPLEMENTED_AND_VERIFIED |
| 135 | PUT | `/patients/me` | patients | session | FAMILY, PATIENT | yes | 8 | 200×2, 401×1, 403×4, 422×1 | IMPLEMENTED_AND_VERIFIED |
| 136 | GET | `/shipments` | shipments | session | ADMIN, HOSPITAL | — | 9 | 200×5, 401×1, 403×3 | IMPLEMENTED_AND_VERIFIED |
| 137 | POST | `/shipments` | shipments | session | ADMIN, HOSPITAL | yes | 90 | 200×1, 201×76, 400×2, 401×1, 403×5, 409×5 | IMPLEMENTED_AND_VERIFIED |
| 138 | GET | `/shipments/by-code/{shipment_code}` | shipments | session | ADMIN, HOSPITAL | — | 9 | 200×3, 401×1, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 139 | GET | `/shipments/units/{unit_code}` | shipments | session | ADMIN, HOSPITAL | — | 12 | 200×5, 401×1, 403×6 | IMPLEMENTED_AND_VERIFIED |
| 140 | GET | `/shipments/{shipment_id}` | shipments | session | ADMIN, HOSPITAL | — | 12 | 200×6, 401×1, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 141 | POST | `/shipments/{shipment_id}/arrive` | shipments | session | ADMIN, HOSPITAL | yes | 20 | 200×15, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 142 | POST | `/shipments/{shipment_id}/cancel` | shipments | session | ADMIN, HOSPITAL | yes | 13 | 200×4, 401×1, 403×5, 409×3 | IMPLEMENTED_AND_VERIFIED |
| 143 | POST | `/shipments/{shipment_id}/delay` | shipments | session | ADMIN, HOSPITAL | yes | 11 | 200×3, 401×1, 403×5, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 144 | POST | `/shipments/{shipment_id}/dispatch` | shipments | session | ADMIN, HOSPITAL | yes | 61 | 200×49, 401×1, 403×7, 409×4 | IMPLEMENTED_AND_VERIFIED |
| 145 | POST | `/shipments/{shipment_id}/fail` | shipments | session | ADMIN, HOSPITAL | yes | 29 | 200×21, 401×1, 403×5, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 146 | POST | `/shipments/{shipment_id}/in-transit` | shipments | session | ADMIN, HOSPITAL | yes | 20 | 200×15, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 147 | POST | `/shipments/{shipment_id}/receive` | shipments | session | ADMIN, HOSPITAL | yes | 27 | 200×12, 401×1, 403×6, 409×8 | IMPLEMENTED_AND_VERIFIED |
| 148 | POST | `/shipments/{shipment_id}/reconcile` | shipments | session | ADMIN, HOSPITAL | yes | 32 | 200×11, 401×1, 403×8, 409×3, 422×9 | IMPLEMENTED_AND_VERIFIED |
| 149 | GET | `/shipments/{shipment_id}/telemetry` | shipments | session | ADMIN, HOSPITAL | — | 7 | 200×2, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 150 | POST | `/shipments/{shipment_id}/telemetry` | shipments | session | ADMIN, HOSPITAL | yes | 19 | 200×12, 401×1, 403×4, 422×2 | IMPLEMENTED_AND_VERIFIED |
| 151 | POST | `/shipments/{shipment_id}/telemetry/simulate` | shipments | session | ADMIN | yes | 8 | 200×1, 401×1, 403×6 | IMPLEMENTED_AND_VERIFIED |
| 152 | GET | `/shipments/{shipment_id}/timeline` | shipments | session | ADMIN, HOSPITAL | — | 10 | 200×4, 401×1, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 153 | GET | `/admin/verifications` | verifications | session | ADMIN | — | 21 | 200×6, 401×2, 403×13 | IMPLEMENTED_AND_VERIFIED |
| 154 | GET | `/admin/verifications/{request_id}` | verifications | session | ADMIN | — | 24 | 200×5, 401×2, 403×10, 404×3, 422×4 | IMPLEMENTED_AND_VERIFIED |
| 155 | POST | `/admin/verifications/{request_id}/approve` | verifications | session | ADMIN | yes | 27 | 200×10, 401×2, 403×9, 409×6 | IMPLEMENTED_AND_VERIFIED |
| 156 | GET | `/admin/verifications/{request_id}/documents/{index}` | verifications | session | ADMIN | — | 41 | 200×11, 401×1, 403×14, 404×6, 409×6, 422×1, 503×2 | IMPLEMENTED_AND_VERIFIED |
| 157 | POST | `/admin/verifications/{request_id}/documents/{index}/rescan` | verifications | session | ADMIN | yes | 10 | 200×3, 401×1, 403×6 | IMPLEMENTED_AND_VERIFIED |
| 158 | POST | `/admin/verifications/{request_id}/reject` | verifications | session | ADMIN | yes | 26 | 200×11, 401×2, 403×8, 409×3, 422×2 | IMPLEMENTED_AND_VERIFIED |
| 159 | POST | `/verifications` | verifications | session | DONOR, HOSPITAL | yes | 131 | 201×90, 401×3, 403×8, 409×8, 422×19, 429×1, 503×2 | IMPLEMENTED_AND_VERIFIED |
| 160 | GET | `/verifications/me` | verifications | session | any signed-in | — | 15 | 200×13, 401×2 | IMPLEMENTED_AND_VERIFIED |


### Frontend routes

| # | Route | Guard | Roles | E2E specs that open it | Crawl (375/768/1280): console errors · failed requests · max overflow · axe serious+critical (tile-host failures excluded) | Status |
|---|---|---|---|---|---|---|
| 1 | `/` | public | — | document_storage_security, phase23_security_hardening, phase26_accessibility, phase27_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 2 | `/login` | public | — | clean_start, container_frontend, gap_live_pages, phase23_security_hardening, phase_j_journeys, qa_evidence, qa_failover_ui | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 3 | `/register` | public | — | qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 4 | `/verify-email` | public | — | qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 5 | `/verify/:code` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 6 | `/forgot-password` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 7 | `/reset-password` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 8 | `/dev-index` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 9 | `/403` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 10 | `/session-expired` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 11 | `/500` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 12 | `/offline` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 13 | `/recommendation` | public | — | gap_live_pages | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 14 | `/decision-transparency` | public | — | gap_live_pages, phase27_accessibility, qa_failover_ui | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 15 | `/mathematical-model` | public | — | gap_live_pages, phase27_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 16 | `/graph-engine` | public | — | container_frontend, gap_live_pages | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 17 | `/simulation` | public | — | gap_live_pages, phase27_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 18 | `/research-mode` | public | — | gap_live_pages | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 19 | `/help` | public | — | phase26_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 20 | `/legal` | public | — | phase26_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 21 | `/legal/:section` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 22 | `/patient` | RequireAuth | PATIENT | phase21_error_loading_empty_offline, phase23_security_hardening, phase26_responsive_overflow, phase27_accessibility, phase31_1_domain_signals, phase_f_realtime, phase_j_journeys, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 23 | `/patient/find-donor` | RequireAuth | PATIENT | phase27_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 24 | `/patient/sos-tracking` | RequireAuth | PATIENT | phase27_accessibility, phase31_1_domain_signals | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 25 | `/patient/new-request` | RequireAuth | PATIENT | phase21_error_loading_empty_offline, phase27_accessibility, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 26 | `/patient/history` | RequireAuth | PATIENT | phase26_responsive_overflow, phase_f_realtime, phase_j_journeys, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 27 | `/family` | RequireAuth | FAMILY | family_link_notifications, phase26_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 28 | `/family/patients` | RequireAuth | FAMILY | family_link_notifications, phase26_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 29 | `/family/patients/:patientId` | RequireAuth | FAMILY | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 30 | `/donor` | RequireAuth | DONOR | document_storage_security, phase26_accessibility, phase26_donation_idempotency, phase26_responsive_overflow, phase27_accessibility, phase31_1_domain_signals, phase_e_domain_integrity, phase_j_journeys, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 31 | `/donor/mobile` | RequireAuth | DONOR | phase_j_journeys | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 32 | `/donor/eligibility` | RequireAuth | DONOR | qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 33 | `/donor/ready-console` | RequireAuth | DONOR | phase26_accessibility, phase27_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 34 | `/donor/milestones` | RequireAuth | DONOR | phase26_donation_idempotency, phase26_responsive_overflow, phase31_1_domain_signals, phase_e_domain_integrity, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 35 | `/donor/identity-verification` | RequireAuth | DONOR | document_storage_security, phase27_accessibility, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 36 | `/hospital` | RequireAuth | HOSPITAL | clean_start, container_frontend, document_storage_security, gap_live_pages, phase26_accessibility, phase27_accessibility, phase_e_domain_integrity, phase_g_integration, phase_j_journeys, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 37 | `/hospital/inventory` | RequireAuth | HOSPITAL | phase_g_integration, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 38 | `/hospital/donors` | RequireAuth | HOSPITAL | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 39 | `/hospital/requests/:requestId` | RequireAuth | HOSPITAL | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 40 | `/hospital/analytics-mobile` | RequireAuth | HOSPITAL | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 41 | `/hospital/units` | RequireAuth | HOSPITAL | clean_start, container_frontend, gap_live_pages | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 42 | `/hospital/resource-pathways` | RequireAuth | HOSPITAL | gap_live_pages | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 43 | `/hospital/shipments` | RequireAuth | HOSPITAL | gap_live_pages, phase_e_domain_integrity, phase_j_journeys, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 44 | `/hospital/verification` | RequireAuth | HOSPITAL | document_storage_security, phase27_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 45 | `/hospital/donation-reviews` | RequireAuth | HOSPITAL | phase_e_domain_integrity, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 46 | `/admin` | RequireAuth | ADMIN | admin_notification_surface, document_storage_security, gap_live_pages, phase23_security_hardening, phase26_accessibility, phase26_responsive_overflow, phase31_1_domain_signals, phase_e_domain_integrity, phase_g_integration, qa_evidence, qa_failover_ui | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 47 | `/admin/verifications` | RequireAuth | ADMIN | admin_notification_surface, document_storage_security, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 48 | `/admin/users` | RequireAuth | ADMIN | admin_notification_surface, phase23_security_hardening, phase26_accessibility, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 49 | `/admin/analytics` | RequireAuth | ADMIN | phase26_accessibility, phase31_1_domain_signals, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 50 | `/admin/transport-routes` | RequireAuth | ADMIN | gap_live_pages, phase26_responsive_overflow, qa_failover_ui | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 51 | `/admin/shipments` | RequireAuth | ADMIN | qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 52 | `/admin/facilities` | RequireAuth | ADMIN | admin_notification_surface, gap_live_pages, phase_g_integration, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 53 | `/admin/donors` | RequireAuth | ADMIN | phase26_accessibility, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 54 | `/admin/audit-feed` | RequireAuth | ADMIN | phase_e_domain_integrity, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 55 | `/admin/verification` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 56 | `/analytics` | RequireAuth | ADMIN, HOSPITAL | gap_live_pages | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 57 | `/certificate` | RequireAuth | any signed-in | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 58 | `/certificate/:id` | RequireAuth | any signed-in | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 59 | `/matching/strategy-lab` | RequireAuth | PATIENT, FAMILY, HOSPITAL, ADMIN | phase27_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 60 | `/profile` | RequireAuth | any signed-in | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 61 | `/settings` | RequireAuth | any signed-in | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 62 | `/notifications` | RequireAuth | any signed-in | admin_verification_notifications, blood_request_notifications, offline_network_failure, phase21_error_loading_empty_offline, phase26_accessibility, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 63 | `*` | public | — | phase21_error_loading_empty_offline, phase25_error_pages_and_500 | not crawled | IMPLEMENTED_AND_VERIFIED |


### Database tables

| Table | Columns | Indexes | CHECK constraints | UNIQUE constraints | Foreign keys |
|---|---|---|---|---|---|
| `audit_logs` | 14 | 4 | 0 | 1 | actor_user_id->users |
| `blood_request_events` | 6 | 2 | 0 | 0 | actor_user_id->users, request_id->blood_requests |
| `blood_requests` | 21 | 6 | 2 | 1 | patient_user_id->users, requester_user_id->users |
| `blood_units` | 24 | 11 | 0 | 1 | donation_id->donations, hospital_id->hospital_profiles, last_log_id->hospital_inventory_logs, reserved_for_emergency_id->emergencies, reserved_for_request_id->blood_requests, reserved_for_shipment_id->shipments, source_shipment_id->shipments, source_unit_id->blood_units |
| `certificates` | 11 | 3 | 0 | 1 | donation_id->donations, donor_user_id->users, revoked_by_user_id->users |
| `demand_forecast_runs` | 10 | 2 | 0 | 0 | facility_id->hospital_profiles, requested_by_user_id->users |
| `donations` | 16 | 4 | 0 | 1 | blood_request_id->blood_requests, donor_user_id->users, hospital_id->hospital_profiles, verified_by_user_id->users |
| `donor_appointments` | 9 | 3 | 0 | 0 | donor_user_id->users, hospital_id->hospital_profiles |
| `donor_profiles` | 19 | 3 | 0 | 0 | user_id->users |
| `donor_responses` | 7 | 2 | 0 | 1 | donor_user_id->users, request_id->blood_requests |
| `email_verification_tokens` | 6 | 2 | 0 | 0 | user_id->users |
| `emergencies` | 24 | 2 | 3 | 1 | created_by_user_id->users, patient_user_id->users, target_facility_id->hospital_profiles |
| `emergency_events` | 6 | 2 | 0 | 0 | actor_user_id->users, emergency_id->emergencies |
| `emergency_overrides` | 8 | 1 | 0 | 0 | created_by_user_id->users, emergency_id->emergencies, revoked_by_user_id->users |
| `emergency_pathway_legs` | 14 | 2 | 1 | 1 | destination_facility_id->hospital_profiles, pathway_id->emergency_pathways, source_facility_id->hospital_profiles |
| `emergency_pathway_steps` | 10 | 2 | 0 | 1 | from_facility_id->hospital_profiles, leg_id->emergency_pathway_legs, pathway_id->emergency_pathways, route_id->transport_routes, to_facility_id->hospital_profiles |
| `emergency_pathways` | 34 | 3 | 0 | 0 | emergency_id->emergencies, origin_facility_id->hospital_profiles, selected_facility_id->hospital_profiles |
| `emergency_responses` | 8 | 2 | 0 | 1 | emergency_id->emergencies, responder_user_id->users |
| `family_links` | 11 | 2 | 0 | 1 | family_user_id->users, patient_user_id->users, requested_by_user_id->users |
| `graph_pathway_logs` | 12 | 4 | 0 | 0 | emergency_id->emergencies, origin_facility_id->hospital_profiles, requested_by_user_id->users |
| `hospital_inventory` | 8 | 1 | 3 | 1 | hospital_id->hospital_profiles |
| `hospital_inventory_logs` | 16 | 4 | 0 | 0 | actor_user_id->users, emergency_id->emergencies, hospital_id->hospital_profiles, request_id->blood_requests, shipment_id->shipments |
| `hospital_profiles` | 13 | 2 | 0 | 0 | user_id->users |
| `hospital_request_responses` | 8 | 2 | 0 | 1 | hospital_id->hospital_profiles, request_id->blood_requests |
| `notification_preferences` | 10 | 1 | 0 | 0 | user_id->users |
| `notifications` | 8 | 4 | 0 | 0 | user_id->users |
| `password_reset_otps` | 7 | 1 | 0 | 0 | user_id->users |
| `pathway_reservations` | 13 | 2 | 1 | 0 | created_by_user_id->users, emergency_id->emergencies, facility_id->hospital_profiles, leg_id->emergency_pathway_legs, pathway_id->emergency_pathways |
| `patient_profiles` | 12 | 2 | 0 | 0 | user_id->users |
| `realtime_events` | 9 | 4 | 0 | 1 | — |
| `registration_counters` | 2 | 0 | 0 | 0 | — |
| `shipment_events` | 6 | 2 | 0 | 0 | actor_user_id->users, shipment_id->shipments |
| `shipment_items` | 10 | 3 | 0 | 0 | blood_unit_id->blood_units, outcome_by_user_id->users, resulting_unit_id->blood_units, shipment_id->shipments |
| `shipment_temperature_readings` | 9 | 2 | 0 | 1 | shipment_id->shipments, submitted_by_user_id->users |
| `shipments` | 32 | 7 | 0 | 2 | created_by_user_id->users, destination_facility_id->hospital_profiles, origin_facility_id->hospital_profiles, reconciled_by_user_id->users, redirected_to_facility_id->hospital_profiles, transport_route_id->transport_routes |
| `sms_notifications` | 19 | 2 | 0 | 1 | notification_id->notifications, recipient_user_id->users |
| `transport_routes` | 10 | 3 | 1 | 1 | destination_facility_id->hospital_profiles, origin_facility_id->hospital_profiles |
| `users` | 13 | 1 | 0 | 0 | — |
| `verification_requests` | 12 | 4 | 0 | 1 | reviewer_user_id->users, supersedes_request_id->verification_requests, user_id->users |

