# LifeLink — Database Map (Phase 0 Audit)

_Audit date: 2026-09-06_

## Current state

**No database exists.** No `backend/`, no SQLAlchemy models, no Alembic
migrations, no `docker-compose`, no connection string, no ORM anywhere in the
repo. The frontend holds all "data" as **hardcoded JSX literals** and one
`localStorage` key.

### Client-side "persistence" today

| Store | Key | Shape | Written by | Read by |
|---|---|---|---|---|
| `localStorage` | `lifelink_user` | `{ role, name, email, mode }` | `AuthPage.handleSubmit` | nothing (never read back) |

That is the entire data layer. It is not authentication and must be removed.

## Target schema — Phase 1 (Auth Gateway)

PostgreSQL. UUID primary keys (`gen_random_uuid()` / `uuid4`). `created_at` /
`updated_at timestamptz` on every table. Managed by Alembic (baseline migration
to be created).

### `users`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `email` | citext UNIQUE NOT NULL | |
| `password_hash` | text NOT NULL | Argon2id (`argon2-cffi`) |
| `full_name` | text NOT NULL | |
| `role` | enum `user_role` NOT NULL | `PATIENT, FAMILY, DONOR, HOSPITAL, ADMIN` |
| `is_email_verified` | bool NOT NULL default false | |
| `is_active` | bool NOT NULL default true | admin suspend |
| `password_changed_at` | timestamptz NOT NULL | used to invalidate sessions issued earlier |
| `created_at` / `updated_at` | timestamptz | |

### `sessions` (server-side session store; Redis-backed at runtime, table optional for audit)

Primary implementation: **Redis** hash `session:{sid}` → `{user_id, created_at,
ip, user_agent}` with TTL. Cookie stores only the opaque `sid`.
Optional mirror table `sessions` for audit/"log out other devices":

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | = sid |
| `user_id` | uuid FK → users | ON DELETE CASCADE |
| `issued_at` | timestamptz | |
| `expires_at` | timestamptz | |
| `revoked_at` | timestamptz null | |
| `ip` / `user_agent` | text | |

### `email_verification_tokens`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `user_id` | uuid FK → users | ON DELETE CASCADE |
| `token_hash` | text NOT NULL | store hash, not raw token |
| `expires_at` | timestamptz NOT NULL | e.g. 24h |
| `consumed_at` | timestamptz null | single use |

### `password_reset_otps`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `user_id` | uuid FK → users | ON DELETE CASCADE |
| `otp_hash` | text NOT NULL | hash of 6-digit code |
| `expires_at` | timestamptz NOT NULL | e.g. 10 min |
| `attempts` | int NOT NULL default 0 | lock after N |
| `consumed_at` | timestamptz null | |

### `audit_logs`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `actor_user_id` | uuid FK null | null = system/anonymous |
| `action` | text NOT NULL | `user.register`, `user.login`, `user.login_failed`, `user.logout`, `auth.email_verified`, `auth.password_reset_requested`, `auth.password_reset`, `auth.sessions_invalidated` |
| `target_type` / `target_id` | text / uuid null | |
| `ip` / `user_agent` | text | |
| `metadata` | jsonb | no secrets/OTP/passwords |
| `created_at` | timestamptz | |

### Rate-limit counters

Redis only (no table): `rl:{scope}:{key}` counters with TTL, e.g.
`rl:login:ip:{ip}`, `rl:login:email:{email}`, `rl:register:ip:{ip}`,
`rl:otp:email:{email}`.

## Phase 2 — IMPLEMENTED (migration `0002_phase2`, 2026-09-06)

| Table | Purpose |
|---|---|
| `patient_profiles` | 1:1 with a PATIENT/FAMILY user; blood type, DOB, contact, city, emergency contact |
| `donor_profiles` | 1:1 with a DONOR user; blood type, city, `is_available`, `last_donation_date`, note |
| `family_links` | FAMILY↔PATIENT authorisation (`PENDING`/`ACCEPTED`/`REVOKED`), unique per pair |
| `blood_requests` | requester + beneficiary, blood type, units, urgency, status, hospital/city, `needed_by`, `fulfilled_at` |
| `blood_request_events` | append-only tracking timeline |
| `donor_responses` | donor `ACCEPTED`/`DECLINED` per request, unique per (request, donor) |
| `emergencies` | SOS: creator + beneficiary, blood type, urgency, status, location, `resolved_at` |
| `emergency_events` | SOS timeline |
| `notifications` | per-user `type`/`title`/`body`/`data` jsonb + `read_at` |
| `donations` | donor donation history, optional `blood_request_id` link |

Enums: `blood_type` (8), `urgency`, `blood_request_status`, `donor_response_status`,
`emergency_status`, `family_link_status`. All UUID PKs; FK `ON DELETE CASCADE`
(or `SET NULL` for actor/optional refs); indexed on the common query columns.
Down→up round-trip verified.

## Phase 3 — IMPLEMENTED (migration `0003_phase3`, 2026-09-06)

| Table | Purpose |
|---|---|
| `hospital_profiles` | 1:1 with a HOSPITAL user; name, license_no, address, `city`, `latitude`/`longitude`, `verified` |
| `hospital_inventory` | units per `blood_type` per hospital, unique `(hospital_id, blood_type)` |
| `hospital_request_responses` | a hospital's handling status (`ACKNOWLEDGED`/`PREPARING`/`DISPATCHED`/`DECLINED`) per incoming request, unique `(hospital_id, request_id)` |

New enum: `hospital_response_status`.
Added columns: `donor_profiles.latitude`, `.longitude`, `.share_location` (opt-in,
default false); `blood_requests.latitude`, `.longitude` (set from the hospital
profile when a hospital files the request). All new columns nullable / defaulted;
down→up round-trip verified.

## Phase 6 — IMPLEMENTED (migration `0005_phase6`, 2026-09-06)

| Table | Purpose |
|---|---|
| `certificates` | one PDF certificate per donation: `donation_id` (FK → `donations` CASCADE, **unique**), denormalised `donor_user_id` (FK → `users` CASCADE), `serial` (unique, `LL-<year>-<hex>`), `verify_code` (unique + indexed, `secrets.token_urlsafe(16)` — the public identifier), `storage_key` (S3/MinIO object key), timestamps |

No new enum types. PDFs live in object storage, not the database. Down→up
round-trip verified.

## Phase 5 — IMPLEMENTED (migration `0004_phase5`, 2026-09-06)

| Change | Detail |
|---|---|
| `emergency_status` enum | + `MATCHING`, `ASSISTANCE_FOUND` (added via `ALTER TYPE … ADD VALUE` in an alembic autocommit block; downgrade rebuilds the type from the four base values) |
| new table `emergency_responses` | `id`, `emergency_id` → `emergencies` CASCADE, `responder_user_id` → `users` CASCADE, `responder_role` (`user_role` enum), `status` (`donor_response_status` enum), `note`, timestamps; `UNIQUE(emergency_id, responder_user_id)`; indexed on both FKs |
| `emergency_events.event_type` | new free-text values: `responder_accepted`, `responder_declined`, `responder_withdrew`, `assistance_found`, `reopened` |

No new enum types created (reuses `user_role`, `donor_response_status`).
Down→up round-trip verified.

## Phase 4 — IMPLEMENTED (2026-09-06) — no schema change

The donor matching workflow was completed with **no migration**. Existing tables
carry it:

- `donor_responses.status` now legitimately transitions `ACCEPTED → DECLINED`
  (withdrawal); uniqueness `(request_id, donor_user_id)` already made this an
  in-place update.
- `blood_request_events.event_type` gains the values `donor_withdrew` and
  `reopened` (free-text column — no enum).
- Donor eligibility (`eligible` / `eligible_on`) is **derived at read time** from
  `donor_profiles.last_donation_date` (`app/core/eligibility.py`,
  `DONATION_INTERVAL_DAYS = 56`) — not stored.

## Target schema — later phases (outline only, designed when their phase starts)

Derived from what the existing pages display:

- `patient_profiles`, `family_links` (FAMILY ↔ PATIENT), `donor_profiles`
  (blood_group, last_donation_at, availability, geo point), `hospital_profiles`
  (name, license_no, geo point, verified).
- `blood_requests` (patient_id, blood_group, units, urgency, status, hospital_id).
- `blood_inventory` (hospital_id, blood_group, units).
- `matches` / `match_offers` (request_id, donor_id, score components:
  compatibility, distance_km, urgency_weight, availability, account_status).
- `emergencies` (creator_id, type, status: CREATED→ACKNOWLEDGED→RESOLVED / CANCELLED,
  geo point) + `emergency_events` (audit trail).
- `notifications` (user_id, type, payload jsonb, read_at).
- `donations` (donor_id, hospital_id, request_id, donated_at, units).
- `certificates` (donation_id, serial, verify_code, storage_key, issued_at).
- `verification_requests` (subject_user_id, documents, status, reviewer_id).
- `donor_locations` / geo columns use PostGIS `geography(Point,4326)` **or**
  lat/lng numerics + Haversine; decide at phase start. Map markers must come
  from these real rows, replacing the current CSS-background placeholder image.

## Notes

- Nothing to migrate from — greenfield schema.
- Enable `citext` and (if chosen) `postgis` extensions in the baseline migration.
- All IDs UUID per security brief; never expose sequential integers.
