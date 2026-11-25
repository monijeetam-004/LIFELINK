# LifeLink — Threat Model (Phase 31)

Per the master prompt's Section 31. Attacker classes, each with real asset/surface/mitigation/test/residual-risk — no theatrical claims, no mitigation listed that isn't backed by a real test in this repository's suite. Supersedes/extends the Phase 23 security audit's findings with this phase's new surfaces (verification submission concurrency, certificate revocation, donor double-booking).

## 1. Unauthenticated attacker

**Assets at risk**: public endpoints (`/auth/*`, `/certificates/verify/{code}`, `/health/*`), account enumeration, credential stuffing.
**Attack surface**: login, registration, password reset, public certificate verification.
**Mitigation**: bcrypt password hashing (`app/core/security.py`); rate limiting on login/register/password-reset (`app/core/ratelimit.py`, Redis-backed, per-account and per-IP); generic error messages that don't confirm whether an email is registered; `CertificateVerifyOut` exposes only pre-approved safe fields (verified this phase when `revoked_at`/`revocation_reason` were added).
**Test**: `tests/test_auth_hardening_phase7.py`, `tests/test_rate_limit_and_authz.py`, `tests/test_phase15_certificate_security.py::test_public_verification_json_has_no_private_fields`.
**Residual risk**: rate limiting is per-process Redis state, not a distributed WAF — a sufficiently distributed credential-stuffing attack across many IPs is not defended against at this layer (documented, not solved — a real production deployment would sit this behind a proper edge WAF/CDN, which is out of scope for this application layer).

## 2. Authenticated malicious user (any role)

**Assets at risk**: other users' `BloodRequest`/`Emergency`/`Certificate`/`VerificationRequest` rows via ID guessing (IDOR).
**Attack surface**: every `{resource_id}`-parameterized route.
**Mitigation**: server-side ownership/relationship checks on every such route (see `docs/LIFELINK_PRIVACY_ACCESS_MATRIX.md` for the full matrix and the specific probes run); UUIDv4 primary keys (not sequential, so guessing a valid-looking ID is already impractical even before the ownership check runs).
**Test**: the IDOR probes listed in the privacy/access matrix doc, run as real HTTP requests.
**Residual risk**: none identified this phase beyond what's already documented as a data-quality (not access-control) gap in the family-link duplicate-invite check.

## 3. Malicious donor

**Assets at risk**: double-booking (accepting multiple simultaneous commitments to create a false sense of available supply); bypassing the donation-interval eligibility gate; forging a donation/certificate.
**Attack surface**: `POST /blood-requests/{id}/respond`, `POST /emergencies/{id}/respond`, `POST /donors/me/donations`, `POST /certificates`.
**Mitigation**: **New this phase** — `donor_commitment.find_conflicting_commitment` blocks a second simultaneous `ACCEPTED` response across requests/emergencies; the 56-day eligibility gate (`is_eligible`) is now enforced consistently at both request-accept and emergency-accept time (previously only the former); `Certificate` issuance requires an already-logged `Donation` owned by the caller (1:1, unique-constrained) — a donor cannot certificate a donation that isn't theirs, though (documented limitation, not new) they *can* self-attest a donation that never happened, since there is no hospital-confirmation step behind donation logging. That specific gap is why certificate **revocation** (new this phase) exists: it gives an admin a real remedy once a fraudulent self-attested donation is discovered, rather than no remedy at all.
**Test**: `tests/test_phase31_domain_fixes.py` (double-booking, eligibility gate, hospital-exemption), `tests/test_donor_matching.py`.
**Residual risk**: donation logging remains honor-system at the point of creation (disclosed, not hidden, in the frontend and in `docs/LIFELINK_DOMAIN_MODEL.md`'s Donation section) — revocation is a detective/corrective control, not a preventive one.

## 4. Malicious hospital

**Assets at risk**: inventory manipulation beyond its own facility; fabricating `verified` status; accessing another facility's resource-pathways.
**Attack surface**: `/hospitals/me/inventory/*`, `/hospitals/me/resource-pathways`, `/emergencies/{id}/resource-pathways`, `/emergencies/{id}/pathway`.
**Mitigation**: every inventory mutation is scoped to the caller's own `HospitalProfile.user_id` (never a client-supplied hospital ID); `verified` is settable only by `verification_service` on admin approval, never self-settable; cross-facility resource-pathway/pathway-selection attempts are explicitly checked and audited (`hospital.facility_access_denied`).
**Test**: verified directly this session in `emergencies.py::emergency_resource_pathways`/`select_emergency_pathway`; `tests/test_hospital.py`.
**Residual risk**: none identified.

## 5. Compromised account (any role)

**Assets at risk**: full account takeover consequences — every resource that role/relationship would normally grant.
**Attack surface**: session/JWT validity window, password-change/reset flows.
**Mitigation**: `password_changed_at` invalidates all prior sessions immediately on password change (`session_service.py`); admin suspension (`is_active=False`) takes effect on the suspended user's very next request, not merely on next login; every admin mutation is audit-logged with the acting admin's real ID.
**Test**: `tests/test_change_password.py`, `tests/test_password_reset.py`, `tests/test_admin.py`.
**Residual risk**: no MFA exists anywhere in this codebase (never claimed) — a compromised password alone is sufficient for full account access, which is a real, disclosed limitation appropriate to flag for any future phase considering it, not a Phase 31 scope item (no domain-logic or data-integrity issue turns on it).

## 6. Abusive family-link user

**Assets at risk**: unauthorized ongoing access to a patient's medical/request/emergency data via a link the patient never actually accepted, or one they've since revoked.
**Attack surface**: `POST /family/link-requests`, every `resolve_beneficiary`-gated route.
**Mitigation**: only `ACCEPTED` links grant on-behalf-of access (`FamilyLinkStatus.PENDING`/`REJECTED`/`REVOKED`/`EXPIRED` all correctly excluded — verified by direct code read of `has_accepted_link`); a link can be revoked by either party at any time, taking effect immediately (no caching of "was accepted" beyond the current row's live status).
**Test**: `tests/test_family.py`, `tests/test_phase13_family_link_security.py`.
**Residual risk**: the duplicate-PENDING-invite race noted in the invariants doc (Section 7) — a data-quality nuisance, not an access-control bypass.

## 7. Notification-abuse actor

**Assets at risk**: spamming a user with excessive notifications; using the notification system to fingerprint who is/isn't a matching donor for a given blood type/location (privacy leak via side channel).
**Attack surface**: `POST /blood-requests`, `POST /emergencies` (both trigger `notify_matching_donors`).
**Mitigation**: rate limiting on request/emergency creation per account; `notify_matching_donors` never reveals *who* was notified back to the requester (the requester sees an anonymized candidate count/list at most, never donor identities pre-acceptance); quiet-hours and per-user opt-in preferences are honored for every channel except emergency alerts (a deliberate, documented exception — an emergency must reach people even during quiet hours).
**Test**: `tests/test_alert_watcher.py`, `tests/test_phase27_notification_scaling.py`.
**Residual risk**: none newly identified this phase.

## 8. Certificate-fraud actor

**Assets at risk**: presenting a fabricated or revoked certificate as currently valid to a third party (e.g. an employer's "did you donate blood" verification check).
**Attack surface**: `GET /certificates/verify/{code}`.
**Mitigation**: **New this phase** — revocation gives an honest, permanent "no longer valid" signal distinguishable from "never existed," closing the specific gap the master prompt's Section 20 named (before this phase, a revoked-in-spirit certificate had no way to actually be marked as such — the public endpoint would report `valid: true` forever). `verify_code` is a non-guessable random token, not a sequential ID, so enumeration is impractical.
**Test**: `tests/test_phase31_domain_fixes.py` (revoke → public verify reflects it; only admin can revoke; revoke requires a real reason; revoking an unknown certificate 404s).
**Residual risk**: revocation is not itself notarized/timestamped by any external authority — it is LifeLink's own admin's word, recorded in `AuditLog` and on the certificate row. That is an appropriate trust boundary for a decision-support/community platform, not a legally-binding attestation service, and the software never claims otherwise.

## 9. File-upload attacker (verification documents)

**Assets at risk**: uploading a malicious payload (executable, script, oversized file) disguised as an identity/facility document.
**Attack surface**: `POST /verifications`.
**Mitigation** (all pre-existing, re-verified this phase, not re-tested from scratch): declared content-type must match the file's actual sniffed magic bytes (rejects an executable disguised as a PDF, or an HTML/script payload disguised as an image); file-size and file-count caps; document-scanner integration point (`NoOpScanner` by default — a real AV scanner would plug in here with zero route changes, and this is disclosed as the current state rather than claimed as "files are scanned for malware").
**Test**: `tests/test_phase14_verification_security.py`.
**Residual risk**: `NoOpScanner` means no real malware scanning happens in this deployment today — disclosed, not hidden.

## 10. API automation / concurrency-abuse attacker

**Assets at risk**: exploiting a check-then-act race to create duplicate state (double-accept, double-submit, double-issue) faster than a human could, via scripted concurrent requests.
**Attack surface**: every accept/create/issue endpoint.
**Mitigation**: row-locking (`get_for_update`) plus DB-level unique/partial-unique constraints plus SAVEPOINT-based insert-or-detect-conflict patterns across every identified race this phase and prior phases — most recently extended to `VerificationRequest` submission (`uq_verification_one_pending_per_kind`, new this phase) and cross-resource donor double-booking (`donor_commitment`, new this phase).
**Test**: real-thread, real-PostgreSQL concurrency tests throughout the suite (`tests/test_concurrency.py`, `tests/test_phase12_emergency_sos_engine.py`'s concurrent-transition tests, `tests/test_phase31_domain_fixes.py::test_concurrent_duplicate_verification_submissions_only_one_succeeds`) — never a `time.sleep`-based approximation.
**Residual risk**: the compatibility invariant (Section 1 of `LIFELINK_INVARIANTS.md`) and the family-link duplicate-invite check remain application-level only, as documented there — neither is exploitable for privilege escalation or data corruption, only for a cosmetic inconsistency in the worst case.

## Production hardening addendum (2026-10)

- **9. File-upload attacker** — uploads are now malware-scanned with ClamAV `clamd` before they can be served or approved; infected files are refused and audited; scanner failure quarantines rather than passing (`docs/LIFELINK_MALWARE_SCANNING.md`, invariant 49).
- **1/5. Unauthenticated / compromised account** — an account whose email was never verified can no longer use the API (403 on every route but me/logout/change-password; invariant 48, `docs/LIFELINK_EMAIL_VERIFICATION.md`). This closes "register with someone else's address and act immediately".
- **11. Insider / database-level tamperer (new)** — someone with SQL access to the database. The audit hash chain detects edits, deletions, insertions and reordering of sealed entries; an append-only trigger guard refuses those statements for non-owner roles; HMAC-signed checkpoints stored outside the database (key not in the DB) detect a full recompute or truncation. Not covered: an attacker holding both the table-owner role and the checkpoint key; removal of the newest checkpoint lines unless the checkpoint store is append-only (`docs/LIFELINK_AUDIT_CHAIN.md`, invariant 50).
- **12. Supply-chain / third-party asset host (new)** — Python installs are hash-locked (`--require-hashes`); fonts and images are self-hosted, so no third-party host can alter what the UI loads; the CSP no longer allows Google font/image hosts (`docs/LIFELINK_DEPENDENCIES.md`, invariant 52). Remaining: two dev-only (build-time) npm advisories under Tailwind 3, documented.
- **Resource exhaustion via event streams** — streams no longer each hold a Redis connection; a slow consumer is resynced instead of buffering without bound (queue limit 1,000; invariant 51).
