# LifeLink — API Map (Phase 0 Audit)

_Audit date: 2026-09-06_

## Current state

**There is no backend and there are no API calls in the frontend.**

- No `backend/` directory, no FastAPI/Flask app, no OpenAPI spec.
- `axios` is a dependency but is **imported in zero files**.
- `@tanstack/react-query` `QueryClient` is instantiated in
  [`frontend/src/main.tsx`](../frontend/src/main.tsx) but **no `useQuery` /
  `useMutation`** exists in any page.
- No `fetch()` calls anywhere in `src/`.
- No environment variable / base-URL configuration.

### Only outbound network today

| Source | Destination | Purpose |
|---|---|---|
| `index.html` | `fonts.googleapis.com` | Inter / Plus Jakarta / Material Symbols webfonts |
| `HospitalDashboard.tsx`, others | `lh3.googleusercontent.com/aida-public/...` | Stitch-generated placeholder images (portraits, "map" background) |

These are static asset loads, not an API.

## Target API surface — Phase 1 (Auth Gateway)

To be implemented under `backend/` as FastAPI. All JSON. Session carried in a
**`__Host-lifelink_session` httpOnly, Secure, SameSite=Lax cookie**. CORS
`allow_credentials=true`, origin = `VITE_API_URL` host.

| Method | Path | Auth | Body / params | Returns | Notes |
|---|---|---|---|---|---|
| `POST` | `/auth/register` | none | `{email, password, full_name, role}` | `201 {user}` | role ∈ enum; sends verification email; rate-limited per IP |
| `POST` | `/auth/login` | none | `{email, password}` | `200 {user}` + Set-Cookie | Argon2 verify; rate-limited per IP + per account; generic error on failure |
| `POST` | `/auth/logout` | session | — | `204` | deletes server session + clears cookie |
| `GET` | `/auth/me` | session | — | `200 {user}` / `401` | for `AuthProvider` bootstrap |
| `POST` | `/auth/verify-email` | none | `{token}` | `200` | consumes single-use token |
| `POST` | `/auth/verify-email/resend` | none | `{email}` | `202` | rate-limited; no account enumeration |
| `POST` | `/auth/forgot-password` | none | `{email}` | `202` | always 202; emails 6-digit OTP if account exists |
| `POST` | `/auth/reset-password` | none | `{email, otp, new_password}` | `200` | validates OTP (attempt-limited, TTL); on success **invalidates all sessions** for that user |
| `GET` | `/health` | none | — | `200 {status, db, redis}` | liveness/readiness |

### Role enum (server-authoritative)

`PATIENT`, `FAMILY`, `DONOR`, `HOSPITAL`, `ADMIN`

### Error shape (proposed)

```json
{ "detail": "human message", "code": "MACHINE_CODE" }
```

## Phase 2 — IMPLEMENTED (2026-09-06)

Patient/Family/Donor domain is live. All require the session cookie, enforce roles
server-side, and check `Origin` on writes. See
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md#phase-2--patient--family--donor-domain--delivered)
for the full endpoint table and domain rules. Summary:

- `GET/PUT /patients/me`, `GET /patients/managed`
- `GET/PUT /donors/me`, `PUT /donors/me/availability`, `GET/POST /donors/me/donations`, `GET /donors/me/requests`
- `GET /family/links`, `POST /family/links`, `POST /family/links/{id}/accept|revoke`
- `POST/GET /blood-requests`, `GET /blood-requests/{id}`, `POST /blood-requests/{id}/cancel|fulfill|respond`
- `POST/GET /emergencies`, `GET /emergencies/{id}`, `POST /emergencies/{id}/cancel|resolve|acknowledge`
- `GET /notifications`, `GET /notifications/unread-count`, `POST /notifications/{id}/read`, `POST /notifications/read-all`

## Phase 6 — IMPLEMENTED (2026-09-06)

Donation certificates. Migration `0005_phase6` adds one table (`certificates`);
no new enum. PDFs are generated with ReportLab and stored via the existing
S3/MinIO layer (`app.core.storage`, local-dir fallback for tests).

- `GET /certificates` — donor's certificates (hydrated `donor_name` / `donated_on`
  / `units` / `hospital_name`, plus `verify_path` and a short-lived
  `download_token`)
- `POST /certificates` `{donation_id}` — donor issues for **their own** donation
  (404 otherwise); **idempotent** (returns the existing certificate);
  audits `certificate.issued`, notifies `certificate.ready`
- `GET /certificates/{id}` — owner or admin only
- `GET /certificates/{id}/download` — streams the PDF; auth by session cookie
  (owner/admin) **or** `?token=` HMAC signed token (10-min TTL)
- `GET /certificates/verify/{code}` — **public**; returns
  `{valid, serial, donor_name, donated_on, units, hospital_name, issued_at}`
  (safe fields only); 404 on an unknown non-guessable code

## Phase 5 — IMPLEMENTED (2026-09-06)

Emergency / SOS network — full lifecycle
`ACTIVE → ACKNOWLEDGED → MATCHING → ASSISTANCE_FOUND → RESOLVED` (+ `CANCELLED`).
Migration `0004_phase5`: two new `emergency_status` values + `emergency_responses`
table (reuses `user_role`, `donor_response_status` enums).

- `GET /emergencies/active` — donor/hospital feed of open emergencies they can act on
- `GET /emergencies/{id}` — now returns `responders` (names for the creator only),
  `accepted_count`, `my_response`
- `GET /emergencies/{id}/match-candidates` — factual compatible donors + area
  hospitals, `has_accepted`; **no score**; donor names for the creator only
- `POST /emergencies/{id}/acknowledge` — donor/hospital/admin, ACTIVE → ACKNOWLEDGED
- `POST /emergencies/{id}/respond` — donor/hospital accept/decline; donor accept
  enforces blood-type + availability + compatibility; first accept → MATCHING;
  `ACCEPTED→DECLINED` withdrawal notifies the creator and may reopen
- `POST /emergencies/{id}/assistance-found` — creator (or an accepted hospital) → ASSISTANCE_FOUND
- `POST /emergencies/{id}/resolve` / `/cancel` — creator; now also notify accepted responders

Notification types: `emergency.new`, `.acknowledged`, `.responder_accepted`,
`.you_responded`, `.responder_withdrew`, `.assistance_found`, `.resolved`,
`.cancelled`. All transitions audited.

## Phase 4 — IMPLEMENTED (2026-09-06)

Completes the donor matching workflow on the Phase 2/3 foundation. **No schema
change** (migration head stays `0003_phase3`).

- `GET /donors/me` now also returns `eligible` (bool) and `eligible_on`
  (date | null) — real 56-day donation-interval rule (`app/core/eligibility.py`).
- `POST /blood-requests/{id}/respond` (DONOR):
  - **ACCEPT is enforced server-side** — rejected `400` if the donor is not
    `is_available` or is still inside the donation interval. DECLINE is always
    allowed.
  - **Withdrawal** — an `ACCEPTED → DECLINED` flip emits a `donor_withdrew`
    timeline event, notifies the requester (`blood_request.donor_withdrew`), and
    if no acceptances remain on a `MATCHING` request, reopens it to `PENDING`
    with a `reopened` event.
  - On accept the donor also gets a `blood_request.you_accepted` confirmation.
  - Every action recorded in `audit_logs` (`blood_request.responded`,
    `meta.withdrew`).
- `GET /donors/me/responses` — the donor's full match/commitment history
  (every `DonorResponse` + its request summary + `my_response`, real
  `distance_km` where coords + opt-in exist), any status, newest first.

Donor **identity verification** (document upload + admin review) remains
deferred: it needs the ADMIN reviewer surface, which is out of scope here.

## Phase 3 — IMPLEMENTED (2026-09-06)

- `GET/PUT /hospitals/me`, `GET/PUT /hospitals/me/inventory`
- `GET /hospitals/me/requests`, `POST /hospitals/me/requests/{id}/respond`, `GET /hospitals/me/requests/{id}/events`
- `GET /hospitals/me/emergencies`
- `GET /blood-requests/{id}/match-candidates` — factual matching foundation (compatibility, availability, `has_accepted`, real `distance_km`, `last_donation_date`; ordered; **no score**; donor names only for the request manager)
- `GET /map/data?blood_type=` — anonymised, coarsened, opt-in donor pins + hospital centre (HOSPITAL/PATIENT/FAMILY)
- `POST /blood-requests` and `POST /emergencies` now also accept HOSPITAL

Not yet implemented (Phase 4+): certificates, admin user management / analytics,
donor identity verification.

## Target API surface — later phases (not Phase 1/2, listed so the map is complete)

Grouped by the existing page that will consume each. Endpoints are **proposed**,
subject to design when their phase starts.

### Patient / Family — `PatientDashboard`
- `GET/PUT /patients/me/profile`
- `GET /blood-requests`, `POST /blood-requests`, `GET /blood-requests/{id}`,
  `POST /blood-requests/{id}/cancel`
- `GET /blood-requests/{id}/matches`
- `GET /notifications`, `POST /notifications/{id}/read`, `POST /notifications/read-all`
- `POST /emergencies` (SOS), `GET /emergencies/{id}`
- `GET/PUT /settings`

### Donor — `DonorDashboard`
- `GET/PUT /donors/me/profile`
- `PUT /donors/me/availability`
- `GET /donors/me/match-offers`, `POST /match-offers/{id}/accept|decline`
- `GET /donors/me/donations` (history)
- `GET /donors/me/certificates`, `GET /certificates/{id}/download` (signed PDF URL)

### Hospital — `HospitalDashboard`
- `GET/PUT /hospitals/me/profile`
- `GET/PUT /hospitals/me/inventory`
- `GET /hospitals/me/requests`, `POST /requests/{id}/respond`
- `GET /hospitals/me/emergencies`, `POST /emergencies/{id}/acknowledge|resolve`
- `GET /hospitals/me/nearby-donors` (server-filtered by privacy/permission; real coords)
- `GET /notifications` …

### Emergency / SOS (shared)
- `POST /emergencies`, `POST /emergencies/{id}/cancel`,
  `POST /emergencies/{id}/acknowledge`, `POST /emergencies/{id}/resolve`,
  `GET /emergencies/{id}` (status), `GET /emergencies/{id}/audit`

### Admin — `AdminVerificationPortal`, `Analytics`
- `GET /admin/users`, `PATCH /admin/users/{id}` (suspend/role/verify)
- `GET /admin/verifications`, `POST /admin/verifications/{id}/approve|reject`
- `GET /admin/audit-logs`
- `GET /admin/system-health`
- `GET /admin/analytics` (real aggregates only)

### Certificates — `DonationCertificate`
- `POST /certificates` (server renders PDF → object storage)
- `GET /certificates/{id}` (metadata), `GET /certificates/{id}/download` (signed URL)
- `GET /certificates/verify/{code}` (public verify)

## Notes for implementation

- No existing endpoints to preserve — the entire surface is new.
- Keep paths RESTful and role-scoped; **authorization enforced server-side**,
  route guards on the client are UX only.
- Polling is acceptable for notifications/emergency status (no WebSocket
  infrastructure exists today).

## Donor shortlist and unified priority: IMPLEMENTED (2026-10-31)

See `docs/UNIFIED_DONOR_PRIORITY.md` for the model.

* **Shortlists.** `GET /blood-requests/{id}/shortlist` and `GET /emergencies/{id}/shortlist` return the eligible donors, the contacts and their counts, plus each candidate's `priority` block:
  * the score (0–100; a ranking measure, not a probability), evidence coverage, evidence level and missing-data baseline;
  * the availability status and the contributions of each factor.

  Optional `?strategy=UNIFIED_PRIORITY|NEAREST|TIME_AWARE|TIME_RESILIENCE|EVENT_DRIVEN` (default `UNIFIED_PRIORITY`). An unknown value returns 422. Factor detail is FULL for hospital and admin users who may view the request, and SUMMARY for patients and family. Donors and unrelated users get 404.
* **Contacting.** `POST …/shortlist/contact-next[?strategy=]` contacts the next bounded batch in that order. Each contact row records the strategy, the model tag and a factor snapshot.
* **Confirming.** `POST …/shortlist/contacts/{contact_id}/confirm` confirms an accepted donor after the eligibility re-check.
* **Policy.** `GET /shortlist/policy` returns the model, version, fingerprint, strategies and hard filters. Weights and `min_coverage` are returned only to hospital and admin users.
* **Demo simulator.** `POST /demo/shortlist/{subject}/{id}/simulate` exists only with `DEMO_MODE`. It is admin-only and acts on synthetic donors only.
* **Unchanged:** `GET /blood-requests/{id}/match-candidates?strategy=` and `/match-strategies`, the four lexicographic strategies.
