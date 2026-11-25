# LifeLink — Implementation Status

_Audit: 2026-09-06 · Phase 1–6: 2026-09-06_

---

## Phase 6 — Donation certificates — DELIVERED

Real PDF certificates for completed donations. **Reused unchanged:** the
`donations` table + `/donors/me/donations` flow, `audit_service`,
`notification_service`, `app.core.security.sign` (HMAC), the S3/MinIO helper
foundation in `app.core.storage`, `require_roles` / `verify_origin` /
`get_optional_user`, the ported `DonationCertificate` page markup, TanStack Query.

### Backend (migration `0005_phase6`)

New table **`certificates`** — `donation_id` (unique → one cert per donation),
denormalised `donor_user_id`, `serial` (`LL-<year>-<hex>`, unique),
`verify_code` (`secrets.token_urlsafe(16)`, unique, indexed), `storage_key`.
No new enum. Down→up round-trip verified.

`app.core.storage` gained `put_bytes` / `get_bytes` / `object_exists` /
`delete_prefix` and a `storage_backend()` switch: **S3/MinIO when configured**,
otherwise a local directory (`STORAGE_LOCAL_DIR`) so the unit suite needs no
MinIO. `main.py` gained a lifespan hook that calls `ensure_bucket()` on boot.

`app.services.certificate_service` — ReportLab A4-landscape PDF (border, seal,
donor name, donation details, serial, verification URL + QR), `get_or_issue`
(idempotent per donation), `load_pdf`, and HMAC signed-download tokens
(`make_download_token` / `check_download_token`, 10-min TTL).

**Endpoints:**

| Verb | Path | Who | Purpose |
|---|---|---|---|
| GET | `/certificates` | donor | my certificates (hydrated with donation fields, `verify_path`, `download_token`) |
| POST | `/certificates` | donor | issue for one of **my** donations (404 otherwise); idempotent — returns the existing cert; audits `certificate.issued`, notifies `certificate.ready` |
| GET | `/certificates/{id}` | owner / admin | metadata (404 to everyone else) |
| GET | `/certificates/{id}/download` | owner / admin (cookie) **or** `?token=` signed | streams the PDF (`application/pdf`, `Content-Disposition` = serial) |
| GET | `/certificates/verify/{code}` | **public** | `{valid, serial, donor_name, donated_on, units, hospital_name, issued_at}` — safe fields only; 404 on unknown code |

### Frontend (design/markup preserved)

| File | Change |
|---|---|
| `src/lib/domain.ts` | + `Certificate` / `CertificateVerification` types; `listCertificates`, `issueCertificate`, `getCertificate`, `verifyCertificate`, `certificateDownloadUrl` |
| `src/pages/DonationCertificate.tsx` | bound to real certificates (`/certificate` = newest, `/certificate/:id` = specific); donor name / hospital / serial / issue date / verification URL are real; **`window.print()` removed** — "Download PDF" is a real signed-URL link; multi-cert switcher; empty/loading states |
| `src/pages/VerifyCertificate.tsx` | **new** — public `/verify/:code` page (valid → safe fields; unknown → "Not verified") |
| `src/pages/DonorDashboard.tsx` | Donation-history rows gained **Get certificate** (issues + opens) / **Certificate** (opens) |
| `src/App.tsx` | + `/certificate/:id` (guarded) and public `/verify/:code` routes |

### Verification (executed 2026-09-06, Dockerised PG16 + Redis7 + Mailpit + **MinIO**)

| Check | Result |
|---|---|
| `alembic upgrade head` (0001→0005) + full down→up round-trip | PASS |
| backend `pytest` | PASS — **74/74** (`tests/test_certificates.py`, 5 new) |
| `ruff check .` · `mypy app` | PASS · PASS (65 files) |
| frontend `typecheck` · `lint` · `build` | PASS · PASS · PASS |
| Phase 6 E2E vs live API + **real MinIO** (completed donation → issue → PDF in MinIO → cookie download (`%PDF`) → signed-token download without cookie → bad/no token 401 → public verify returns safe fields only, no secrets → unknown code 404 → other-donor & patient & anon all blocked → no regression in donations/donor feeds) | **ALL PASS** (27 assertions) |
| Playwright walkthrough (donor: "Get certificate" on a donation → `/certificate/:id` renders real name/serial/hospital/date → "Download PDF" hits the signed URL and returns a real `application/pdf` → public `/verify/:code` shows "Authentic certificate" + safe fields → invalid code shows "Not verified") | **PASS**, no page errors (only the intentional 404 XHRs) |

### Remaining work (not blocking)

- No certificate revocation / re-issue endpoint (verification always reports
  `valid: true` for an existing code).
- Certificate PDF is a fixed template; no localisation or per-hospital branding.
- `DonorDashboardMobile` still static.

---

## Phase 5 — Emergency / SOS network — DELIVERED

Extends the Phase 2 emergency foundation into a full lifecycle with donor **and**
hospital responders. **Reused unchanged:** `emergencies` / `emergency_events`
tables + routes, `notification_service`, `audit_service`, `domain_service`,
`resolve_beneficiary`, the blood-compatibility matrix, `require_roles` /
`verify_origin`, the `SosModal` create form, `RequestTrackDrawer` as the pattern
for `EmergencyTrackDrawer`, TanStack Query.

### Backend (migration `0004_phase5`)

- `emergency_status` enum gains **`MATCHING`** and **`ASSISTANCE_FOUND`**
  (`ALTER TYPE … ADD VALUE` in an autocommit block; downgrade rebuilds the type).
  Full down→up round-trip verified.
- New table **`emergency_responses`** (`emergency_id`, `responder_user_id`,
  `responder_role`, `status` ACCEPTED/DECLINED, `note`; unique per
  emergency+responder) — reuses the existing `user_role` and
  `donor_response_status` enums.

**Lifecycle:** `create → ACTIVE → (acknowledge) ACKNOWLEDGED →
(first responder accepts) MATCHING → (creator/hospital) ASSISTANCE_FOUND →
(creator) RESOLVED`; `CANCELLED` from any open state. A withdrawal that empties
the responder list reopens `MATCHING → ACKNOWLEDGED`.

**Endpoints** (cookie-auth, role-enforced, `Origin`-checked on writes):

| Verb | Path | Who | Purpose |
|---|---|---|---|
| POST | `/emergencies` | patient / family / hospital | broadcast (also pings area hospitals) |
| GET | `/emergencies` | creator / beneficiary | my emergencies |
| GET | `/emergencies/active` | donor / hospital | open emergencies I can respond to (compatible + available donor, or area hospital) |
| GET | `/emergencies/{id}` | creator, beneficiary, accepted-family, admin, area/candidate responder | detail + timeline + responders (names for creator only) + `my_response` |
| GET | `/emergencies/{id}/match-candidates` | viewer | factual compatible donors + area hospitals, `has_accepted`, **no score**; names for the creator only |
| POST | `/emergencies/{id}/acknowledge` | donor / hospital / admin | ACTIVE → ACKNOWLEDGED, notify creator |
| POST | `/emergencies/{id}/respond` | donor / hospital | accept/decline; donor accept enforces blood-type set + availability + compatibility; first accept → MATCHING; withdrawal notifies creator + may reopen |
| POST | `/emergencies/{id}/assistance-found` | creator, or a hospital that accepted | → ASSISTANCE_FOUND, notify creator + accepted responders |
| POST | `/emergencies/{id}/resolve` | creator | → RESOLVED, notify accepted responders |
| POST | `/emergencies/{id}/cancel` | creator | → CANCELLED, notify accepted responders |

Notifications: `emergency.new`, `emergency.acknowledged`,
`emergency.responder_accepted`, `emergency.you_responded`,
`emergency.responder_withdrew`, `emergency.assistance_found`,
`emergency.resolved`, `emergency.cancelled`. Every transition writes an
`audit_logs` row.

### Frontend (design/markup unchanged)

| File | Change |
|---|---|
| `src/components/EmergencyTrackDrawer.tsx` | **new** — status badge, responder list, timeline, `match-candidates` (creator), and role-appropriate actions (creator: assistance-found / resolve / cancel · responder: acknowledge / "I can help now" / withdraw) |
| `src/lib/domain.ts` | + emergency responder / detail / open / match types & calls; `EmergencyStatus` gains the two new states |
| `src/pages/PatientDashboard.tsx` | + "Active Emergency SOS" panel (only when the patient has an open one) → `EmergencyTrackDrawer` (creator) |
| `src/pages/DonorDashboard.tsx` | + "Active Emergencies Near You" panel from `/emergencies/active` → `EmergencyTrackDrawer` (responder) |
| `src/pages/HospitalDashboard.tsx` | "Incoming Emergencies" rows are now clickable → `EmergencyTrackDrawer` (responder) |

### Verification (executed 2026-09-06, Dockerised PG16 + Redis7 + Mailpit)

| Check | Result |
|---|---|
| `alembic upgrade head` (0001→0004) + full down→up round-trip | PASS |
| backend `pytest` | PASS — **69/69** (`tests/test_emergency_network.py`, 7 new) |
| `ruff check .` · `mypy app` | PASS · PASS (61 files) |
| frontend `npm run typecheck` · `lint` · `build` | PASS · PASS · PASS |
| Phase 5 E2E vs live API (create→ACTIVE + broadcast → acknowledge → respond→MATCHING + both-party notifications → 2nd responder + withdrawal keeps MATCHING → assistance-found → resolve; timeline is the full chain; hospital responder path; incompatible/unavailable donor blocked; role boundaries; **no regression** in blood-request / donor / hospital feeds) | **ALL PASS** (37 assertions) |
| Playwright walkthrough (patient: SOS via modal → "Active Emergency SOS" panel → drawer → assistance-found → resolved → panel clears · donor: "Active Emergencies Near You" → drawer → "I can help now" → MATCHING + "You're responding ✓" + confirmation notification → withdraw) | **PASS**, 0 console errors |

### Remaining work (not blocking)

- No proximity ranking for emergencies (emergencies carry free-text
  `location_text`, not coordinates); hospital/area matching is case-insensitive
  substring on the hospital's city.
- `DonorDashboardMobile` still static.
- Emergency responder identities are shown to the creator only; a fuller
  contact-exchange / coordination channel is later-phase.

---

## Phase 4 — Donor matching workflow — DELIVERED

Completes the donor side of matching on the Phase 2/3 foundation. **Reused
unchanged:** `blood_requests` / `donor_responses` / `donations` /
`blood_request_events` tables and routes, `notification_service`,
`audit_service`, `domain_service`, the blood-compatibility matrix,
`haversine_km`, `RequestTrackDrawer`, `NotificationsBell`, TanStack Query,
`api.ts` / `domain.ts`. **No migration** — head stays `0003_phase3`.

### Backend (new / changed)

| Area | Change |
|---|---|
| `app/core/eligibility.py` | **new** — real 56-day donation-interval rule (`eligible_on`, `is_eligible`) |
| `GET /donors/me` | response now includes computed `eligible` / `eligible_on` |
| `POST /blood-requests/{id}/respond` | ACCEPT enforced server-side (must be `is_available` **and** past the interval — else `400`); DECLINE always allowed. `ACCEPTED→DECLINED` = **withdrawal**: `donor_withdrew` event, requester notified, request reopens `MATCHING→PENDING` + `reopened` event when no acceptances remain. Donor gets a `blood_request.you_accepted` confirmation on accept. Audit `meta.withdrew`. |
| `GET /donors/me/responses` | **new** — the donor's full match/commitment history across every status, with `my_response` and real `distance_km` where available |

### Frontend (`DonorDashboard`, design/markup unchanged)

- Fabricated **"Readiness Score 94 / Optimal"** ring → real **"Donation
  Readiness"** (recovery % toward the interval; arc computed from `eligible` /
  `eligible_on`).
- "Respond Now" **disabled with a reason** when unavailable or not yet eligible
  (mirrors the server rule).
- Accepted request → **"Accepted ✓" + "Withdraw"** (calls `respond` DECLINED).
- **Donation History** panel also lists closed commitments from
  `/donors/me/responses`.
- `RequestTrackDrawer` timeline: `donor_withdrew` / `reopened` labels.

### Verification (executed 2026-09-06, Dockerised PG16 + Redis7 + Mailpit)

| Check | Result |
|---|---|
| `alembic upgrade head` + full down→up round-trip (0001↔0003) | PASS (Phase 4 adds no migration) |
| backend `pytest` | PASS — **62/62** (`tests/test_donor_matching.py`, 8 new) |
| `ruff check .` · `mypy app` | PASS · PASS (61 files) |
| frontend `npm run typecheck` · `lint` · `build` | PASS · PASS · PASS |
| Phase 4 E2E vs live API (eligibility + availability enforced on accept → both-party notifications → history persists → 2nd donor accepts / 1st withdraws stays MATCHING → last withdrawal reopens to PENDING → fulfil records donation + history FULFILLED → authz boundaries) | **ALL PASS** (28 assertions) |
| Playwright walkthrough (donor login → real Donation-Readiness ring → real open request → Respond Now → Accepted ✓ + Withdraw + confirmation notification → Withdraw → buttons return → unavailable ⇒ Respond Now disabled w/ reason) | **PASS**, 0 console errors |

### Remaining work (not blocking)

- **Donor identity verification** (document upload + reviewer decision) is
  deferred to the Admin phase — it requires the `AdminVerificationPortal`
  reviewer surface; building the upload half alone would be a dead feature.
- `DonorDashboardMobile` still static.
- "Pre-Schedule Now" / "Review Criteria" on `DonorDashboard` remain inert
  (scheduling is not part of the matching workflow).

---

## Phase 3 — Hospital + Live Map + Matching foundation — DELIVERED

Built on the Phase 1/2 architecture. Reused unchanged: auth, `require_roles`,
`verify_origin`, `audit_service`, `notification_service`, `domain_service`,
blood-compatibility matrix, `blood_requests` / `emergencies` / `notifications`
tables and their routes, the frontend `api.ts` / `domain.ts` client,
`NotificationsBell`, `Modal`, `BloodRequestModal`, `SosModal`,
`RequestTrackDrawer`, TanStack Query, `react-leaflet` (already in `package.json`).

### Backend (new — migration `0003_phase3`)

New tables: `hospital_profiles` (name, license, address, city, lat/lng, verified),
`hospital_inventory` (units per blood type, unique per hospital+type),
`hospital_request_responses` (a hospital's handling status per request).
New enum `hospital_response_status`. New nullable columns:
`donor_profiles.latitude/longitude/share_location`,
`blood_requests.latitude/longitude`. Down→up round-trip verified.

**Endpoints** (cookie-auth, role-enforced):

| Area | Endpoints |
|---|---|
| Hospital profile | `GET/PUT /hospitals/me` |
| Inventory | `GET/PUT /hospitals/me/inventory` (always returns all 8 types) |
| Incoming requests | `GET /hospitals/me/requests?include_closed=` (scoped to the hospital's city / name), `POST /hospitals/me/requests/{id}/respond` (ACKNOWLEDGED/PREPARING/DISPATCHED/DECLINED), `GET /hospitals/me/requests/{id}/events` |
| Emergencies | `GET /hospitals/me/emergencies` (active in the hospital's city); `POST /emergencies` now also allows HOSPITAL |
| Matching foundation | `GET /blood-requests/{id}/match-candidates` — compatible + available donors, `has_accepted`, real haversine `distance_km` where coords exist, `last_donation_date`; ordered accepted-first then nearest then most-eligible. **No score. No fabricated match.** Donor name only for the request's own manager; hospitals get an anonymous `donor_ref`. |
| Live map | `GET /map/data?blood_type=` — HOSPITAL/PATIENT/FAMILY only; anonymised available-donor pins `{blood_type, latitude, longitude}` (opted-in donors only, coords rounded to ~1 km), plus the hospital's own centre |
| Blood requests | `POST /blood-requests` now also allows HOSPITAL (beneficiary = self; inherits hospital coords); hospitals in a request's city are notified (`blood_request.incoming`) and can view request detail (aggregate only — **donor identities stay hidden from hospitals**) |

**Privacy**: donor exact location is never exposed. Map pins are opt-in
(`share_location`), carry no id/name, and are coarsened server-side. Hospital
views of requests and matches never include donor names or contact details.

**Tests** — `tests/test_hospital.py`, `tests/test_matching_and_map.py`
(11 new; 54 total). `ruff` + `mypy` clean.

### Frontend (wired; design/markup unchanged)

| File | Change |
|---|---|
| `src/lib/domain.ts` | + hospital / inventory / match-candidate / map types & calls; donor `latitude/longitude/share_location` |
| `src/components/DonorMap.tsx` | **new** — real `react-leaflet` map (OpenStreetMap tiles, `leaflet/dist/leaflet.css`), hospital-centred, anonymised blood-type-coloured donor pins from `/map/data`; **replaces the static Stitch map image** |
| `src/components/HospitalInventoryModal.tsx` | **new** — edit units for all 8 blood types |
| `src/components/RequestTrackDrawer.tsx` | extended: `mode="hospital"` (handling-status buttons) + a factual "Compatible donors" section from `/match-candidates` for the manager and hospital views |
| `src/pages/HospitalDashboard.tsx` | KPIs → real (active emergencies, total stock, unhandled requests, donors on map); **Blood Inventory cards → real** + "View Full Inventory"/"Manage" → modal; **Request Management board → real incoming requests** grouped by handling status, card click → hospital drawer; "Active Donors (Live)" → `DonorMap`; Verification-queue panel replaced by real "Incoming Emergencies"; EMERGENCY SOS → `SosModal`; New Request → `BloodRequestModal`; bell → `NotificationsBell` |
| `src/pages/DonorDashboard.tsx` | + "Share approx. location" opt-in (browser geolocation, coarsened, persisted via `/donors/me`) so donors can appear on the hospital map |

### Verification (executed 2026-09-06, Dockerised PG16 + Redis7 + Mailpit)

| Check | Result |
|---|---|
| `alembic upgrade head` (0001→0003) + full down→up round-trip | PASS |
| backend `pytest` | PASS — 54/54 |
| `ruff check .` · `mypy app` | PASS · PASS (60 files) |
| frontend `npm run typecheck` · `lint` · `build` | PASS · PASS · PASS |
| Phase 3 E2E vs live server (hospital profile/inventory persist → city-scoped incoming requests → hospital respond → requester notified + timeline → donor identities hidden from hospital → match-candidates factual + anonymised for hospital + real distance → map pins anonymised/coarsened/role-guarded → hospital emergency) | **ALL PASS** |

### Remaining work (not blocking)

- Donor **verification queue** on the hospital dashboard is not built (donor
  identity verification is Phase 4 / admin); the panel now shows real incoming
  emergencies instead.
- Hospital dashboard "Avg Match Time" and the Analytics/Simulation nav links
  remain static (analytics is a later phase).
- `DonorDashboardMobile` still static.
- Map uses public OpenStreetMap tiles; no self-hosted tile server.
- City-based scoping is string-exact; no geocoding of free-text cities.

---

## Phase 2 — Patient / Family / Donor domain — DELIVERED

Built on the Phase 1 architecture (FastAPI + SQLAlchemy + Alembic + Redis, cookie
auth, `require_roles`, `verify_origin`, `audit_service`).

### Backend (new)

**Migration `0002_phase2`** — 10 tables + 6 enums:
`patient_profiles`, `donor_profiles`, `family_links`, `blood_requests`,
`blood_request_events`, `donor_responses`, `emergencies`, `emergency_events`,
`notifications`, `donations`. Enums: `blood_type`, `urgency`,
`blood_request_status`, `donor_response_status`, `emergency_status`,
`family_link_status`. UUID PKs, FK cascades, indexes; down→up round-trip verified.

**Endpoints** (all cookie-auth, role-enforced server-side, `Origin` checked on writes):

| Area | Endpoints |
|---|---|
| Patient/Family profile | `GET/PUT /patients/me` · `GET /patients/managed` (FAMILY) |
| Donor profile | `GET/PUT /donors/me` · `PUT /donors/me/availability` |
| Family links | `GET /family/links` · `POST /family/links` · `POST /family/links/{id}/accept` · `.../revoke` |
| Blood requests | `POST /blood-requests` · `GET /blood-requests?status_filter=` · `GET /blood-requests/{id}` (timeline + responses) · `POST .../cancel` · `POST .../fulfill` · `POST .../respond` (DONOR) |
| Donor work | `GET /donors/me/requests` (compatible open) · `GET/POST /donors/me/donations` |
| Emergencies (SOS) | `POST /emergencies` · `GET /emergencies` · `GET /emergencies/{id}` · `POST .../cancel` · `.../resolve` · `.../acknowledge` (DONOR/HOSPITAL/ADMIN) |
| Notifications | `GET /notifications` · `GET /notifications/unread-count` · `POST /notifications/{id}/read` · `POST /notifications/read-all` |

**Domain rules**
- Blood compatibility from a real donor→recipient matrix
  (`app/models/enums.py`); donors only see/serve compatible requests. **No
  fabricated compatibility scores.**
- FAMILY may file requests/emergencies for a PATIENT only via an `ACCEPTED`
  `family_links` row.
- Donor `ACCEPTED` response → request `PENDING`→`MATCHING` + requester notified.
- `fulfill` records a `donations` row per accepted donor and bumps
  `donor_profiles.last_donation_date`.
- New request/emergency → notifies available donors whose blood type is
  compatible (optionally city-scoped).
- Security events also written to `audit_logs`
  (`blood_request.*`, `emergency.*`, `family.*`).

**Tests** — `tests/test_profiles.py`, `test_family.py`, `test_blood_requests.py`,
`test_emergencies_and_notifications.py` (21 new; 43 total). `ruff` + `mypy` clean.

### Frontend (wired; design/markup unchanged)

| File | Change |
|---|---|
| `src/lib/domain.ts` | **new** — typed Phase 2 API client (profiles, requests, emergencies, donations, notifications) |
| `src/components/NotificationsBell.tsx` | **new** — live bell + dropdown (polls unread count, list/read/read-all); dropped into both dashboards' existing header bell slot |
| `src/components/Modal.tsx`, `BloodRequestModal.tsx`, `RequestTrackDrawer.tsx`, `SosModal.tsx` | **new** — create-request form, request tracking (status + timeline + donor responses + cancel/fulfill), SOS create — all in the existing design tokens |
| `src/pages/PatientDashboard.tsx` | wired: "New Request" (sidebar + hero) → BloodRequestModal; "Emergency SOS" (header + hero) → SosModal; **"Recent Requests Timeline" table → real `/blood-requests`** with real status + accepted-donor count; "Track" → RequestTrackDrawer; bell → NotificationsBell. Left stat cards now show the patient's real open-request / total-accepts counts (replaced the two fabricated network stats). |
| `src/pages/DonorDashboard.tsx` | wired: real name; **availability toggle** (persists via `/donors/me/availability`); blood-group setter; **"Emergency Requests" list → real compatible open requests** with Respond/Not-now (`/blood-requests/{id}/respond`); "Next Eligibility" computed from `last_donation_date + 56d`; "Your Impact" → real donation count / units; **Milestones panel replaced by real Donation History** (same grid slot); bell → NotificationsBell. |

### Verification (executed 2026-09-06, Dockerised PG16 + Redis7 + Mailpit)

| Check | Result |
|---|---|
| `alembic upgrade head` (0001→0002) + down→up round-trip | PASS |
| backend `pytest` | PASS — 43/43 |
| `ruff check .` · `mypy app` | PASS · PASS (55 files) |
| frontend `npm run typecheck` · `lint` · `build` | PASS · PASS · PASS |
| Phase 2 E2E vs live server + Mailpit (profiles → request → notify → donor accept → MATCHING → track timeline → fulfill → donation history → family link → file-for-patient → SOS → acknowledge → resolve → notifications → authz) | **ALL PASS** |

### Remaining Phase 2 work (not blocking)

- `DonorDashboardMobile.tsx` (`/donor/mobile`, a Stitch "reference" screen on a
  different token set) left static; functional donor experience is `/donor`.
- Patient "Donors Nearby (128)" / "Avg Response Time (4.2)" — network-wide
  analytics; left as static placeholders pending Phase 3 (matching/analytics).
- Donor "Readiness Score 94", "Pre-Schedule Now" / "Review Criteria",
  achievement badges — decorative / scheduling, out of Phase 2 scope.
- Family-link **management UI** (request/accept/revoke) has endpoints + tests
  but no dedicated screen yet; surfaced via notifications.
- Profile-edit screens (beyond the donor blood-group inline setter) not built;
  endpoints exist.

---

## Phase 1 — Auth Gateway — DELIVERED

A new `backend/` (FastAPI + SQLAlchemy + PostgreSQL + Alembic + Redis/RQ) was
added beside `frontend/`. The frontend auth flow is now wired to it.

### Backend (new)

| Capability | Endpoint(s) | Notes |
|---|---|---|
| Register | `POST /auth/register` | roles PATIENT/FAMILY/DONOR/HOSPITAL; Argon2id; sends verification email; 409 on duplicate; rate-limited per IP |
| Login | `POST /auth/login` | generic 401; sets `httpOnly` session cookie; rate-limited per IP + per account; transparent password rehash |
| Logout | `POST /auth/logout` | deletes Redis session + clears cookie |
| Current user | `GET /auth/me` | 401 when unauthenticated |
| Email verification | `POST /auth/verify-email`, `POST /auth/verify-email/resend` | single-use HMAC-hashed token, 24 h TTL |
| Forgot password | `POST /auth/forgot-password` | always 202 (no enumeration); emails 6-digit OTP |
| Reset password | `POST /auth/reset-password` | OTP attempt-capped + TTL; sets new Argon2 hash; **invalidates all sessions** (Redis purge + `password_changed_at` check) |
| Health | `GET /health` | DB + Redis; 503 when degraded |
| Roles | `require_roles(...)` dependency | server-side; 401 unauth / 403 wrong role |

Tables (Alembic `0001_initial`): `users`, `email_verification_tokens`,
`password_reset_otps`, `audit_logs`. UUID PKs, `user_role` native enum.
Sessions live in Redis (opaque id in cookie); `audit_logs` records
register/login/login_failed/logout/email_verified/password_reset_*/sessions_invalidated.

Email delivered via RQ worker (`app.workers.worker`) to Mailpit, or inline when
`RQ_SYNC=true`. MinIO/S3 config + `app/core/storage.py` helper added as
foundation (not used until the certificates phase).

Tests: `backend/tests/` — health, full register→verify→login→logout flow,
password-reset + session invalidation, rate limiting, role authorization.
(Require a disposable Postgres DB + Redis; not run in this offline environment.)

### Frontend (wired, design unchanged)

| File | Change |
|---|---|
| `src/lib/api.ts` | **new** — axios client, `VITE_API_URL`, `withCredentials`, `X-Requested-With`, `toApiError` |
| `src/lib/auth.tsx` | **new** — `AuthProvider` / `useAuth` (`user`, `loading`, `login`, `register`, `logout`, `refresh`) bootstrapped from `/auth/me` |
| `src/lib/types.ts`, `src/lib/roles.ts` | **new** — `User`/`Role` types, role→home map |
| `src/components/RequireAuth.tsx` | **new** — route guard (auth + role), renders `AccountMenu` |
| `src/components/AccountMenu.tsx` | **new** — fixed account chip with sign-out + "verify email" nudge (non-invasive; per-header integration deferred) |
| `src/components/AuthShell.tsx` | **new** — shared chrome for the secondary auth pages |
| `src/pages/AuthPage.tsx` | **rewritten submit only** — real `login`/`register`, error states, 5th role (FAMILY), "Forgot password?" → `/forgot-password`; **all markup/design preserved**; `localStorage` fake auth removed |
| `src/pages/VerifyEmail.tsx`, `ForgotPassword.tsx`, `ResetPassword.tsx` | **new** — in the ported visual language |
| `src/App.tsx` | route guards on `/patient` `/donor` `/hospital` `/admin/verification` `/analytics` `/certificate`; new `/verify-email` `/forgot-password` `/reset-password` routes; explanatory/research pages left public |
| `src/main.tsx` | wrapped in `<AuthProvider>` |
| `package.json` | added `typecheck` script (`tsc --noEmit`) |
| `.env.example`, `src/vite-env.d.ts` | **new** |

### Not done in Phase 1 (unchanged from audit)

All domain data (`PatientDashboard`, `DonorDashboard`, `HospitalDashboard`,
`AdminVerificationPortal`, `DonationCertificate`, `Analytics`, notifications,
SOS, matching, map markers) is still hardcoded Stitch markup. `href="#"` nav
links, handler-less action buttons, and stripped script hooks remain — those
belong to Phase 2+. See `FRONTEND_INTERACTION_MAP.md`.

### Verification (executed 2026-09-06)

Ran against Dockerised Postgres 16 + Redis 7 + Mailpit:

| Check | Result |
|---|---|
| `alembic upgrade head` (+ downgrade→upgrade) | PASS |
| backend `pytest` | PASS — 22/22 |
| `ruff check .` | PASS |
| `mypy app` | PASS — 34 files |
| frontend `npm run typecheck` | PASS |
| frontend `npm run lint` | PASS |
| frontend `npm run build` | PASS |
| `docker compose config` | VALID |
| **full `docker compose up --build`** (db, redis, mailpit, minio, createbuckets, api, worker) | PASS — api auto-ran `alembic upgrade head`, worker listening on `emails`, MinIO bucket `lifelink-dev` created |
| E2E vs local uvicorn (RQ_SYNC): register → Mailpit verify → login → /auth/me → logout → forgot → OTP reset → old session 401 → CSRF Origin 403 | PASS |
| E2E vs compose stack (async RQ→worker→Mailpit): same flow + rate-limit + health | PASS — worker logged `emails: Job OK` for both emails |
| `audit_logs` persistence | PASS (8–9 action types) |

Fixes applied during verification:
- `frontend/package.json`: `react-leaflet` `^4.2.1` → `^5.0.0` (React 19 peer
  conflict blocked `npm install`; lib is unused in `src`).
- `app/api/routes/auth.py`: removed duplicate `import uuid`; wrapped a long
  decorator (ruff).
- `app/services/session_service.py`: `cast(...)` on redis return values (mypy).
- `alembic/env.py`, `alembic/versions/0001_…`: import order (ruff --fix).
- `src/pages/AuthPage.tsx`: removed unused `ShieldCheck` import (tsc).
- Pre-existing Stitch-port defects that blocked `tsc`/`build` (flagged in Phase 0):
  invalid lowercased JSX attrs `viewbox`/`preserveaspectratio`/`marker*`/`refx/y`
  in `Analytics`, `GraphDecisionEngine`, `SimulationMode`, and `disabled=""` in
  `AdminVerificationPortal` — corrected mechanically (casing / boolean), no
  visual or behavioural change.
- Added `frontend/eslint.config.js` (flat config for ESLint 9; the legacy
  `.eslintrc.cjs` could not run under ESLint 9). Mirrors the old rules.

### Remaining caveats

- No browser/UI render test was performed (typecheck + lint + build pass; auth
  API verified end-to-end via HTTP against both a local uvicorn and the full
  Docker Compose stack).
- `docker-compose.yml` publishes db on `5432` and redis on `6379`; on a host
  where those are taken, use a port override (verification used one).

---

## Phase 0 Audit (original)

_Audit date: 2026-09-06_

## TL;DR

The repository is **frontend-only**. Despite the task framing ("backend already
implemented, some functionality already works"), **no backend exists** in this
repo — no FastAPI app, no database, no migrations, no Redis/RQ, no tests, no
`docs/` folder, no `.git` history. The frontend is a **static, pixel-faithful UI
port from a Google Stitch export** (see `frontend/README.md`), with **zero API
wiring** and **fake, localStorage-based "authentication"**.

This means Phase 1 (Auth Gateway) is effectively a **greenfield backend build**
plus **wiring an existing static frontend** to it. Nothing that "already works"
on the auth/data path needs to be preserved except the **visual design and page
markup**, which are good and must be kept.

## Repository shape

```
LifeLink/
  frontend/            React 19 + Vite 6 + TS 5.7 + Tailwind 3.4
    src/
      App.tsx          Router: 15 routes, all public, no guards
      main.tsx         BrowserRouter + QueryClientProvider (client created, unused)
      pages/*.tsx      14 page components (1:1 Stitch port, static markup)
      components/
        ShaderBackground.tsx   WebGL canvas — the only genuinely functional component
      index.css        merged Stitch design tokens
    public/reference/  logo + 3 design-language markdown notes
    package.json       deps: axios, @tanstack/react-query, react-hook-form,
                       framer-motion, reactflow, leaflet, react-leaflet,
                       recharts, lucide-react  — most are UNUSED in src/
  (no backend/)
  (no docs/ — created by this audit)
```

- `node_modules` is **not installed**; `npm install` has never been run here.
- No `.env`, no env-based config, no API base URL anywhere.
- `package.json` scripts: `dev`, `build` (`tsc -b && vite build`), `preview`,
  `lint`. **There is no `typecheck` script and no `test` script.**

## Component-by-component status

Legend: ✅ working · 🟡 partial/cosmetic · ❌ missing/fake

### Auth & session

| Item | Status | Notes |
|---|---|---|
| Register | ❌ | No API. `AuthPage` form `handleSubmit` writes `localStorage["lifelink_user"]` and `setTimeout(650ms)` → navigate to a role dashboard. |
| Login | ❌ | Same fake handler; no credential check. |
| Logout | ❌ | No logout control anywhere. |
| Session persistence | ❌ | Only a localStorage JSON blob `{role,name,email,mode}`. No cookie, no token, no refresh. |
| `/auth/me` | ❌ | No endpoint, no consumer. |
| Email verification | ❌ | Not present. |
| Forgot-password OTP | ❌ | "Forgot password?" is `<a href="#">`. |
| Password reset | ❌ | Not present. |
| Secure httpOnly cookies | ❌ | Not present. |
| Rate limiting | ❌ | No backend. |
| Session invalidation after reset | ❌ | No backend. |
| Server-side role authorization | ❌ | No backend. Frontend "role" is a radio button the user picks freely. |
| Route guards | ❌ | Every route in `App.tsx` renders unconditionally. |

Roles in UI today: `patient, donor, hospital, admin` (4). Brief requires **5**:
`PATIENT, FAMILY, DONOR, HOSPITAL, ADMIN` — `FAMILY` is missing.

### Frontend ↔ Backend plumbing

| Item | Status | Notes |
|---|---|---|
| Centralized typed API client | ❌ | None. `axios` imported nowhere. |
| Environment-based API URL | ❌ | None. |
| Cookie credentials (`withCredentials`) | ❌ | N/A yet. |
| TanStack Query usage | ❌ | `QueryClient` created in `main.tsx`; no `useQuery`/`useMutation` in any page. |
| Loading/empty/error/success states | ❌ | Pages render one hardcoded "happy" state. |

### Feature pages (all static markup, no data layer)

| Page | Route | Status | Notes |
|---|---|---|---|
| `AuthPage` | `/`, `/login`, `/register` | 🟡 | Full visual design incl. shader; fake submit. |
| `PatientDashboard` | `/patient` | 🟡 | Hardcoded stats (`#donor-count` 128, `#response-time` 4.2), request cards, "Track"/"Archive" buttons inert. |
| `DonorDashboard` / `...Mobile` | `/donor`, `/donor/mobile` | 🟡 | Hardcoded eligibility, "Respond Now"/"Alert Me"/"Pre-Schedule" inert. |
| `HospitalDashboard` | `/hospital` | 🟡 | Hardcoded inventory bars, request queue; **map is a CSS `background-image`** to a Google-hosted Stitch PNG (`data-location="New York Medical District"`) — **not Leaflet**, no real coordinates/markers. |
| `AdminVerificationPortal` | `/admin/verification` | 🟡 | Hardcoded verification queue; Approve/Reject/pagination buttons inert; a `disabled=""` attr will need cleanup for React. |
| `DonationCertificate` | `/certificate` | 🟡 | Static certificate; "Download" button = `window.print()`. No server PDF, no verify. |
| `DonorRecommendation` | `/recommendation` | 🟡 | Landing/search-results hybrid; `#counter1..4` render `0s/0%/0k+` (Stitch `<script>` counters were stripped in port); stray `<canvas id="shader-canvas-ANIMATION_2">` with no JS. |
| `Analytics` | `/analytics` | 🟡 | Static numbers; `recharts` unused. |
| `SimulationMode` | `/simulation` | 🟡 | `#runSim` button + `#step-0..7` nodes + `#log-container` are Stitch DOM hooks whose driver script was stripped — button does nothing. |
| `DecisionTransparency` | `/decision-transparency` | 🟡 | Static explanatory page. |
| `MathematicalModel` | `/mathematical-model` | 🟡 | Static; `#counter-u` etc. static. |
| `GraphDecisionEngine` | `/graph-engine` | 🟡 | Static SVG; `reactflow` unused. |
| `ResearchMode` | `/research-mode` | 🟡 | Static. |

### Infra / cross-cutting

| Item | Status |
|---|---|
| Database (Postgres) + schema + Alembic | ❌ missing |
| Redis + RQ worker | ❌ missing |
| Email (Mailpit/SMTP) | ❌ missing |
| Object storage (MinIO/S3) + signed URLs | ❌ missing |
| Notifications backend (list/read/unread) | ❌ missing (bell icons are decorative) |
| Certificates PDF service | ❌ missing |
| Emergency/SOS state machine | ❌ missing (buttons decorative) |
| Matching engine (compatibility/proximity/urgency) | ❌ missing |
| Audit logging | ❌ missing |
| Tests (pytest / vitest) | ❌ missing |
| CI | ❌ missing |

## What is genuinely working (keep, do not rebuild)

1. **Vite/TS/Tailwind build setup** and the **merged design system** (`tailwind.config.ts`, `src/index.css`). Keep as-is.
2. **All page markup and visual design** — 14 pages, pixel-faithful. Reuse; wire data in, do not redesign.
3. **`ShaderBackground.tsx`** — real, self-contained WebGL component. Keep.
4. **Router skeleton** in `App.tsx` — reuse; add guards + `FAMILY` + logout.
5. **Dependency choices** in `package.json` — the right libs are already pinned (TanStack Query, axios, Leaflet, react-hook-form). Just unused.

## Minimum implementation order (proposed, for after audit sign-off)

1. **Backend scaffold**: FastAPI app, settings via env, SQLAlchemy + Postgres,
   Alembic baseline, Docker Compose (api, db, redis, worker, mailpit, minio).
2. **Auth core**: `users` table (UUID PK, Argon2 hash), register/login/logout,
   httpOnly SameSite session cookie, `/auth/me`, 5-role enum incl. `FAMILY`.
3. **Auth completeness**: email verification token + Mailpit, forgot-password
   OTP, password reset, session invalidation on reset, rate limiting (Redis).
4. **Frontend plumbing**: typed API client (`src/lib/api.ts`), `VITE_API_URL`,
   `withCredentials`, `AuthProvider` + `useAuth`, real `AuthPage` submit,
   route guards (`<RequireAuth role=...>`), logout control.
5. **Add `typecheck` script** (`tsc --noEmit`) and wire lint/typecheck/build green.
6. Then Phase 2+: per-role data endpoints, matching, emergency, notifications,
   certificates, admin, storage — connecting the existing pages one by one.

**Phase 1 is not started.** This document is the audit deliverable.
