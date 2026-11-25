# LifeLink — Privacy / Access Control Matrix (Phase 31)

Per the master prompt's Section 18. Legend: **O** = CREATE, **R** = READ, **U** = UPDATE, **D** = DELETE, **A** = APPROVE/REJECT, **X** = EXPORT/DOWNLOAD. A blank cell means that role has no access path to that action at all (not merely "denied at runtime" — no route exists for it). Every checkmark below was verified by reading the actual route's authorization logic (`require_roles`, an explicit ownership/relationship check, or both) — not inferred from naming. Routes identified as self-scoped (operating on `/me`, never taking another user's ID) are noted as having **no ID-based IDOR surface at all**, which is a stronger guarantee than "checked and passed."

| Resource | PATIENT | FAMILY (linked, ACCEPTED) | DONOR | HOSPITAL | ADMIN |
|---|---|---|---|---|---|
| Own `User` account | R,U (self) | R,U (self) | R,U (self) | R,U (self) | R,U (self) + R,U,A on any user |
| `PatientProfile` | O,R,U (self, via `/patients/me` — no ID param, no IDOR surface) | R,U (linked patient only, via `resolve_beneficiary`) | — | — | R (via `/admin/users`) |
| `DonorProfile` | — | — | O,R,U (self, `/donors/me`) | R (anonymized directory only — never raw contact info) | R,U (suspend-adjacent fields only) |
| `HospitalProfile` | R (directory, for donor/facility search) | R (directory) | R (directory) | O,R,U (self, `/hospitals/me`) | R,U (verification, facility registry) |
| `FamilyLink` | O,R,A (own links, as either side) | O,R,A (own links) | — | — | R (audit visibility only) |
| `BloodRequest` | O,R,U,D (own, or as patient) | O,R,U,D (linked patient's, via `resolve_beneficiary`) | R (open/matching list, anonymized), respond (accept/decline) | R (own city/area), respond (`HospitalRequestResponse`) | R,U,D (any — explicit admin override with audit trail) |
| `DonorResponse` / `HospitalRequestResponse` | R (on own requests, to see who responded) | R (on linked patient's requests) | O (own, accept/decline) | O (own, `HospitalRequestResponse`) | R |
| `Donation` | — | — | O,R (self-logged; no delete — a logged donation is a permanent fact record) | R (only donations crediting one of its own requests) | R |
| `Certificate` | — | — | O,R,X (own, via `donor_user_id` ownership check in `certificates.py`) | — | R (registry list), **A** (revoke — new this phase, admin-only) |
| `Emergency` | O,R,U,D (own, or as patient) | O,R,U,D (linked patient's) | R (open list), respond | R (open list, area-matched), respond | R,U,D (any, with audit trail) |
| `EmergencyPathway` | R (via emergency detail, if manager) | R (via emergency detail, if manager) | — | O,R (own facility as origin, or admin) | R,O (any) |
| `VerificationRequest` | — | — | O,R (self, `DONOR_IDENTITY` kind only — role-gated) | O,R (self, `HOSPITAL_FACILITY` kind only) | R,A (`/admin/verifications`) |
| `AuditLog` | — | — | — | — | R only (append-only; no route ever updates/deletes) |
| `Notification` | R,U (own — mark read) | R,U (own) | R,U (own) | R,U (own) | — (admin has no notification-inbox override route; not needed) |
| `TransportRoute` | R (via resource-pathway results) | R | — | R (own-area pathway queries) | O,R,U (create/disrupt/recover) |

## IDOR probes actually run this phase (and prior phases, re-confirmed)

Every cross-tenant probe below was exercised as a real HTTP request against the real test database, not reasoned about abstractly:

1. **Cross-patient**: Patient A requesting Patient B's `BloodRequest`/`Emergency` by ID → 404 (the `_can_view`/`_can_manage` helpers in `blood_requests.py`/`emergencies.py` return "not found," not "forbidden," for a resource that exists but isn't yours — deliberately, so existence itself isn't leaked to an unauthorized prober).
2. **Unauthorized family access**: A FAMILY user with no `ACCEPTED` `FamilyLink` (PENDING, REJECTED, REVOKED, or no link at all) acting on a patient's behalf → rejected by `resolve_beneficiary`/`has_accepted_link`, verified in `tests/test_family.py` and `tests/test_phase13_family_link_security.py`.
3. **Cross-donor**: A donor reading another donor's `Certificate` by ID → 404 (`certificates.py`'s `get_certificate` checks `cert.donor_user_id == user.id`). A donor downloading another donor's certificate PDF via a guessed/forged download token → 401 (`make_download_token`/token verification is HMAC-signed per certificate ID, not a guessable sequential ID).
4. **Cross-hospital**: A hospital querying another facility's resource-pathways or inventory as if it were its own → 403 with an audit record (`hospital.facility_access_denied`), verified directly in `emergencies.py::emergency_resource_pathways` this session.
5. **Admin bypass correctness**: Admin routes are gated by `require_roles(UserRole.ADMIN)` uniformly (verified: every `/admin/*` route in `admin.py` carries this dependency — grepped, none found missing it), and admin actions that mutate state (suspend, role-change, transition override, certificate revocation) write an `AuditLog` entry naming the acting admin, never anonymously.
6. **Verification cross-kind**: A DONOR submitting a `HOSPITAL_FACILITY` verification (or vice versa) → 403 (`_ROLE_FOR_KIND` mapping enforced server-side in `submit_verification`, not merely hidden in the frontend form).
7. **Public endpoints leak nothing extra**: `GET /certificates/verify/{code}` and any other unauthenticated route were checked field-by-field against their Pydantic response schema (`CertificateVerifyOut`) to confirm no internal ID, storage key, or unrelated PII crosses that boundary — verified this phase when `revoked_at`/`revocation_reason` were added (both judged safe-to-disclose, since a revocation reason is meant to be publicly checkable, same as the certificate's validity itself).

## What "server-side" actually means here

No route in this codebase was found relying on a frontend route guard as its only protection — every access-control decision above is re-derived from `user.role` and explicit ownership/relationship lookups on the server for every single request, never trusted from a client-supplied header, hidden form field, or prior page having been reached. This was verified by reading the authorization logic directly in `blood_requests.py`, `emergencies.py`, `certificates.py`, `verifications.py`, `family.py`, `admin.py`, and `patients.py`/`donors.py`/`hospitals.py`'s self-scoped `/me` routes.

## Known, documented limitation

`FamilyLink` duplicate-invite prevention (at most one PENDING/ACCEPTED link per family/patient pair) is an application-level pre-flight check, not a DB constraint — see `docs/LIFELINK_INVARIANTS.md` Section 7. This is a data-quality gap, not an access-control one: it cannot be used to gain unauthorized access to a patient's data, only to create a confusing duplicate pending invite.
