# Phase 20 Audit — Secure Document & File Storage Hardening

This audit was written **before** any Phase 20 implementation work, per the
phase spec's explicit "audit first" requirement. Every claim below was
verified by reading the actual code (`app/core/storage.py`,
`app/api/routes/verifications.py`, `app/services/certificate_service.py`,
`app/api/routes/certificates.py`, their schemas, and every existing test
file that already touches this surface), grepping for the relevant terms
across the whole repo, and — for the storage-failure claims specifically —
writing a throwaway monkeypatch probe (`tests/_scratch_storage_probe.py`,
deleted once its findings were recorded here) that exercised the *real*,
unmodified-at-the-time code paths and printed the actual response. Nothing
here is inferred from "this looks like it should be a problem."

## 0. Bottom line

LifeLink already has **one** file-storage architecture
(`app/core/storage.py`, S3/MinIO with a transparent local-disk fallback),
and it is already reused correctly by both real consumers (verification
documents, certificate PDFs). It did not need replacing, and this phase did
not introduce a second one. The architecture, authorization model, and
filename/content-validation hardening were already largely correct — this
phase's real work was closing four unhandled-exception gaps in storage
failure handling, adding one missing privacy header, and adding the
explicitly-required (and previously entirely absent) malware-scanning
integration point, honestly implemented as a no-op today.

## 1. Every file/document flow, inventoried

Grepped for: `UploadFile`, `multipart`, `File(`, `attachment`, `document`,
`verification`, `certificate`, `storage`, `MinIO`, `S3`, `boto3`, `object
key`, `download`, `signed URL`, `presigned`, `send_file`, `FileResponse`,
`Content-Disposition`, `MIME`, `Content-Type`, `filename`, `storage_key`,
`document_id`, `certificate_id`, across both `backend/app` and
`frontend/src`. Result: exactly two real upload/download flows exist in
this product. There is no third, hidden, or partially-built one.

### Flow A — Verification documents (`app/api/routes/verifications.py`)

| | |
|---|---|
| Upload endpoint | `POST /verifications` |
| Download endpoint | `GET /admin/verifications/{request_id}/documents/{index}` |
| Authenticated roles (upload) | DONOR (for `DONOR_IDENTITY`), HOSPITAL (for `HOSPITAL_FACILITY`) — enforced by `_ROLE_FOR_KIND` |
| Authenticated roles (download) | ADMIN only |
| Owning resource | `VerificationRequest` row (`user_id`) |
| File types | PDF, PNG, JPEG (`settings.verification_allowed_content_type_list`) |
| Size limits | `settings.verification_max_file_bytes` (10 MB/file), `settings.verification_max_files` (5/request) |
| Validation | content-type allowlist, magic-byte sniff-and-cross-check, bounded chunked read, filename sanitization, (new) scan hook |
| Storage backend | `app.core.storage` (shared abstraction) |
| Storage key format | `verifications/{user_id}/{request_id}/{uuid4().hex}_{safe_filename}` |
| Download mechanism | direct backend streaming (`Response(content=data, ...)`), never a redirect/presigned URL |
| Authorization | admin-only route guard; no per-document ownership check needed beyond that since only admins can reach it at all |
| Audit events | `verification.submitted` (new upload), `admin.verification_approved`, `admin.verification_rejected` |
| Notifications | every active admin notified on submission (Phase 18); submitter notified on approve/reject |
| Frontend components | `pages/donor/IdentityVerification.tsx`, `pages/hospital/FacilityVerification.tsx` (upload); `pages/admin/VerificationsQueue.tsx` (review + document links) |
| Tests before Phase 20 | `tests/test_verifications.py` (19), `tests/test_phase14_verification_security.py` (14) |

### Flow B — Donation certificates (`app/services/certificate_service.py` + `app/api/routes/certificates.py`)

| | |
|---|---|
| Issue endpoint | `POST /certificates` (server-generates a PDF, is not a file upload) |
| Download endpoint | `GET /certificates/{certificate_id}/download` |
| Authenticated roles | DONOR (issue, own certificates); download accepts either a valid session (owner or ADMIN) **or** a signed, time-limited download token — no auth required for the token path |
| Owning resource | `Certificate` row (`donor_user_id`), 1:1 with a `Donation` the caller owns |
| File type | `application/pdf` (server-rendered via ReportLab — there is no user-supplied file here) |
| Storage backend | `app.core.storage` (same shared abstraction as Flow A) |
| Storage key format | `certificates/{donor_id}/{certificate_id}.pdf` |
| Download mechanism | direct backend streaming, never a redirect/presigned URL |
| Authorization | ownership check (`donor_user_id == user.id`) OR admin OR a valid HMAC-signed, expiring download token (`make_download_token`/`check_download_token`) |
| Audit events | `certificate.issued`, `certificate.viewed`, `certificate.access_denied`, `certificate.downloaded` |
| Notifications | donor notified `certificate.ready` on issuance |
| Frontend components | `pages/DonationCertificate.tsx` (download link), `pages/VerifyCertificate.tsx` (public verify, no document access) |
| Tests before Phase 20 | `tests/test_certificates.py` (9), `tests/test_phase15_certificate_security.py` (13) |

**Confirmed there is no third flow.** No hospital-facing route ever touches
verification documents (only `/admin/verifications*` reads document bytes)
— so a "HOSPITAL reviews documents" row in the IDOR matrix below is N/A by
design, not an unaudited gap. `object_exists`, `presign_get`, `presign_put`
in `storage.py` are defined but never called by any route (confirmed by
grep) — real, but dead, code; see §3 and §9.

## 2. Storage architecture

**Already correct — preserved, reused, not replaced.** `app/core/storage.py`
is the single storage abstraction:

```
Frontend → authenticated API request → Backend (route: auth + validation)
    → app.core.storage (put_bytes / get_bytes / delete_key)
        → S3/MinIO (storage_backend() == "s3")   [prod / MinIO-configured dev]
        → local disk under STORAGE_LOCAL_DIR      [test suite / no-MinIO dev]
```

`storage_backend()` picks the branch purely from whether
`S3_ENDPOINT_URL`/`S3_ACCESS_KEY`/`S3_SECRET_KEY` are all set — every caller
(`put_bytes`, `get_bytes`, `ensure_bucket`, `delete_key`, `delete_prefix`)
is backend-agnostic, so nothing in the route layer branches on which
backend is active. The browser never constructs a storage path — every
`Content-Disposition`/document link points at a *backend* route
(`/admin/verifications/.../documents/{index}`,
`/certificates/{id}/download`), never at MinIO/S3 directly, and the
frontend has zero `S3_`/`MINIO_`-prefixed environment variables (confirmed:
`frontend/.env.example` declares only `VITE_API_URL`). No second storage
mechanism was introduced.

On the local-disk fallback, `_local_path(key)` already rejected any key
containing a literal `..` path segment (normalizing `\` to `/` first) —
this pre-existing guard is defense-in-depth only, since every key passed to
it is app-generated (see §3), never taken from client input.

## 3. Private object storage / opaque keys

**Already correct.** Both storage-key formats
(`verifications/{user_id}/{request_id}/{uuid4().hex}_{filename}`,
`certificates/{donor_id}/{certificate_id}.pdf`) are entirely
server-generated. The `uuid4().hex` component in the verification key means
the *sanitized, still-somewhat-guessable* original filename is never the
sole boundary — even if two users uploaded a file named `id.pdf`, the keys
never collide and are never predictable. The certificate key uses the
already-non-sequential `Certificate.id` (a UUID). The real security
boundary in both flows is the **route's own authorization check**, not key
secrecy — which is the correct design here, since both routes stream bytes
back through the backend rather than ever handing out a storage URL: an
attacker who somehow guessed a real key still cannot fetch it directly, S3
is never public, and `VerificationDocumentOut` (schema) deliberately omits
the `key` field from every API response (confirmed: `tests/test_verifications.py`
already asserted `"key" not in mine[0]["documents"][0]` before this phase).
Certificate responses likewise never include `storage_key`
(`CertificateOut` has no such field). No MinIO/S3 credential is ever
constructed on, or sent to, the frontend.

## 4. Filename security

**Already correct — verified against the spec's exact required cases.**
`_safe_filename()` in `verifications.py`: splits on `/` after normalizing
`\`→`/` (drops every directory component — defeats `../../secret.txt`,
`..\..\secret.txt`, `/etc/passwd`, and `C:\secret.txt`, since a Windows
drive prefix is not a `/`-delimited segment and gets swallowed by the same
split), then whitelists to `[A-Za-z0-9._-]` (defeats `<svg>.svg`,
`document<script>.pdf`, embedded null/control bytes, and any
Content-Disposition-header-injection character), then truncates to 150
characters (defeats an arbitrarily long filename). This phase added a
dedicated parametrized test
(`tests/test_phase20_secure_file_storage.py::test_malicious_filenames_never_produce_a_dangerous_stored_name`)
asserting on every one of the spec's exact listed strings — it passes
against the pre-existing, unmodified sanitizer. The original filename is
retained only as **display metadata** (`documents[].filename`) and a safe
`Content-Disposition` value; it never determines the storage path (the
`uuid4().hex`-prefixed key does).

## 5. Content validation

**Already correct — reviewed carefully, not regressed.** `_sniff_content_type()`
(Phase 14) checks the file's actual leading bytes against a signature table
(`%PDF-`, PNG's magic bytes, JPEG's `\xff\xd8\xff`) and requires the
sniffed type to **match** the client-declared `Content-Type` — not merely
both be independently allowed. This closes both the "executable declared as
PDF" case and the "real PNG declared as PDF" case (both already covered by
`test_phase14_verification_security.py`). Only the three file types
LifeLink's actual verification workflow needs are supported — no type was
added for completeness. This phase changed nothing about the validation
logic itself; it only added the (currently inert) scanner call *after* this
check (§14).

## 6. File size limits

**Already correct.** `settings.verification_max_file_bytes` (10 MB) and
`settings.verification_max_files` (5) are enforced, and — Phase 17 hardening,
already in place — `_read_bounded()` reads in 64 KB chunks and aborts as
soon as the running total exceeds the limit, so an oversized upload is
never fully buffered first. 10 MB is generous enough for a real
photographed ID or scanned facility license (typically 1-4 MB) while still
bounding worst-case per-request memory; this phase did not change the
number, since nothing suggested it was ever a real problem for legitimate
documents.

## 7. Secure downloads — genuine gaps found here

Audited both download endpoints line by line.

- **Authentication/authorization**: already correct on both (admin-only for
  documents; owner/admin/signed-token for certificates).
- **IDOR**: already correct (see §8).
- **Storage errors → honest API errors**: **genuinely broken before this
  phase** for the verification-document route, and for certificate
  *issuance* — any `storage.put_bytes`/`get_bytes` exception propagated as
  an unhandled exception (proven with a throwaway monkeypatch probe before
  writing any fix; see the Bugs section of the final report). Certificate
  *download* already had a generic `except Exception → 503` — the one place
  in the codebase that had already gotten this right — but did not
  distinguish a genuinely-missing object from a broken backend.
- **Missing files → honest not-found**: **genuinely absent** — nothing
  before this phase distinguished "object never existed / was deleted out
  of band" from "storage backend is erroring." Fixed via the new
  `StorageObjectNotFoundError` (§10).
- **Enumeration resistance**: not a concern for these two routes — the
  verification-document route is admin-only regardless of which document ID
  is guessed, so there's no oracle to protect; the certificate route already
  returns the same 404 for a real document belonging to someone else as for
  a random UUID (`test_certificate_id_manipulation_across_donors`, Phase 14).
- **`Content-Disposition` safety**: already correct — the verification
  filename is pre-sanitized (§4), and the certificate filename is a
  server-generated serial (`LL-{year}-{8 hex}`), never client input.
- **`Content-Type` from trusted metadata**: already correct — the *sniffed*
  content type (not the client's claim) is what gets stored and served back
  for documents; certificates always serve `application/pdf`.
- **Cache behavior**: **genuinely missing** — neither response set any
  `Cache-Control` header before this phase (proven via the same probe:
  printed the real response headers dict and found it absent). A private
  document or certificate PDF is exactly the kind of response an
  intermediary or the browser's own disk cache must never retain. Fixed by
  adding `Cache-Control: private, no-store` to both.
- **No presigned URLs exist anywhere** in this codebase (confirmed by grep
  — `presign_get`/`presign_put` are defined but never called), so there was
  nothing to audit for presigned-URL expiration/scope/reuse. Direct backend
  streaming was already the chosen (and correct, for this product's size)
  architecture; this phase did not introduce presigned URLs, per the "choose
  the existing architecture unless the audit demonstrates a concrete reason
  to change it" instruction — nothing demonstrated a need.

## 8. IDOR — access matrix

| Role | Own verification/document | Another user's verification/document | Own certificate | Another donor's certificate | Admin routes |
|---|---|---|---|---|---|
| PATIENT | N/A (cannot submit) | 403 (admin routes gated) | N/A | N/A | 403 |
| DONOR | full (submit, list own via `/verifications/me`) | 403 on admin routes (only path that reads document bytes) | full (issue/view/download own) | 404 on view, 401 on download w/o valid token | 403 |
| HOSPITAL | full for `HOSPITAL_FACILITY` only | 403 on admin routes | N/A | N/A | 403 |
| FAMILY | N/A (cannot submit either kind) | 403; **structurally** cannot even link to a DONOR/HOSPITAL account (`FamilyLinkCreate` validates the target is a PATIENT) — confirmed by grep of `app/models/family.py` and `app/api/routes/family.py` | N/A | 404 on view, 401 on download | 403 |
| ADMIN | full read/approve/reject on every request; document bytes | (is the authorized reviewer — not "another user's" from an admin's perspective) | can view any certificate | can view any certificate | full |

Every "denied" cell above was tested with **real database rows** (a real
submitted `VerificationRequest`, a real `Certificate` tied to a real
`Donation`), not mocked authorization — both pre-existing
(`test_phase14_verification_security.py`,
`test_phase15_certificate_security.py`) and this phase's additions
(`test_phase20_secure_file_storage.py`) log in as real seeded users via
`auth_client()` and hit the real FastAPI app against real Postgres.

The FAMILY→DONOR-document non-negotiable case explicitly called out by the
spec: already proven both structurally and behaviorally before this phase
(`test_family_link_does_not_grant_access_to_donor_verification_or_documents`);
this phase added the certificate-side twin of that same guarantee
(`test_family_link_cannot_bypass_donor_document_or_certificate_access`),
since the original test only covered documents, not certificates.

Malformed/nonexistent IDs: `request_id`/`certificate_id` are typed
`uuid.UUID` path parameters, so a non-UUID string is a clean FastAPI 422
(new tests confirm this explicitly for both routes); a well-formed but
nonexistent UUID is a clean 404 (already proven for certificates in Phase
14/15; this phase adds the explicit verification-side equivalent).

## 9. Delete / replace / versioning

**NOT SUPPORTED — confirmed by reading the models and grepping the routers,
not assumed.** `VerificationRequest` and `Certificate`
(`app/models/verification.py`, `app/models/certificate.py`) have no
version/superseded/replaced-by column, and neither router defines a DELETE
route or any PUT/PATCH that mutates `documents`/`storage_key`. Each
verification submission is an independent, immutable row with its own
`documents` list; a rejected request's original documents are retained
forever and are never touched by a later resubmission (proven by this
phase's `test_verification_replacement_not_supported_rejected_documents_remain_retrievable`,
which submits, rejects, resubmits, and confirms the *first* request's
document is still byte-for-byte retrievable by an admin afterward — the one
real consistency property this design needs, and it already holds).
Certificates are 1:1 with a `Donation` and issuance is idempotent
(`get_or_issue` returns the existing row rather than creating a duplicate)
— there is no "replace a certificate" concept to audit. This phase did not
add delete/replace/versioning, per the spec's explicit "do not invent
document versioning if the current product does not need it."

## 10. Storage failure resilience — the phase's main bug-finding

Simulated real storage failures via monkeypatching the actual
`storage.put_bytes`/`storage.get_bytes` module functions (the same
functions the route/service code calls through `storage.<fn>` attribute
lookups, so the patch is exactly what the real call site sees) and
recording the *actual* response, before writing any fix:

| Scenario | Before Phase 20 | After Phase 20 |
|---|---|---|
| `storage.put_bytes` fails during verification upload | unhandled exception (bare 500) | clean `503`, no partial DB row committed, any already-written sibling files in the same multi-file request are best-effort deleted (new `storage.delete_key`) |
| `storage.get_bytes` fails (backend broken) on document download | unhandled exception (bare 500) | clean `503` |
| `storage.get_bytes` raises "not found" on document download | unhandled exception (bare 500) — same as a broken backend, no distinction | clean `404` (new `StorageObjectNotFoundError`) |
| `storage.put_bytes` fails during certificate issuance | unhandled exception (bare 500) | clean `503` (new `CertificateStorageError`), no `Certificate` row created |
| `storage.get_bytes` fails (backend broken) on certificate download | already a clean `503` (pre-existing) | unchanged |
| `storage.get_bytes` raises "not found" on certificate download | fell into the same generic `503` as a broken backend | clean `404` (distinguished) |

Transaction ordering was already correct everywhere it mattered and did not
need changing: `certificate_service.get_or_issue` already called
`storage.put_bytes` **before** `db.add(cert)`, so a storage failure there
already meant no row was ever created — the only gap was the *exception
handling* around that call, not the ordering. The verification-upload route
already validated every file (type, size, magic bytes) into memory before
starting any storage I/O, so a storage failure partway through a multi-file
upload is now cleaned up (compensating deletes of whatever *did* get
written) and the whole request 503s — no orphaned row, no partial document
list. None of these paths return `200` with empty/fake content, and none
silently swallow a failure.

This is not a hypothetical: this exact class of bug reproduced itself for
real during this phase's own manual verification, when the local dev
backend was briefly restarted with real (but currently unreachable in this
sandbox — no Docker/MinIO available) S3 credentials configured — every
upload attempt correctly surfaced as the new honest `503` rather than a
raw, unhandled exception, which is precisely the behavior this section
fixed.

## 11. Database consistency

**MIGRATION: NONE.** `VerificationRequest.documents` is already a JSON
column (`list[dict]`), so the new `scanned_by` per-document field (§14)
fits inside the existing schema with no `ALTER TABLE`. No other schema
change was needed anywhere in this phase — no new table, no new column on
`Certificate` or `VerificationRequest`. Confirmed by reading both models in
full before concluding this.

## 12. Audit logging

**Already correct for the events that existed; this phase's new event
reuses the same convention.** `verification.submitted` already recorded
`kind` and `file_count` — never document bytes, storage keys, or filenames.
The existing `certificate.access_denied`/`certificate.viewed`/`certificate.downloaded`
events already followed the same "safe identifiers only" rule. This phase
did not add a new denied-access audit event for the verification-document
route's role-gated 403 — that route's 403 is structurally identical to
every other admin-only route's role check across the whole codebase (dozens
of routes), none of which audit a blanket "you're not an admin" rejection;
only *resource-scoped* ownership checks (a real, specific object someone
tried and failed to access) are audited, which is the deliberate, narrower,
pre-existing pattern `certificates.py` already established. Forcing a new
event onto the verification-document 403 would be inconsistent with that
convention, not a fix. `_FORBIDDEN_META_KEYS` in `audit_service.py` already
blocks `password`/`token`/etc from ever landing in `meta`; nothing this
phase added needed a new key on that blocklist, since no document bytes or
storage credentials are ever passed to `audit_service.record`.

## 13. Notifications

**Already correct — reviewed every document-related notification body.**
`verification.submitted` (to admins): submitter name + kind + count only.
`verification.approved`/`verification.rejected` (to submitter): a
human-written note/reason only. `certificate.ready` (to donor): serial +
donation date only. None of these ever included a storage path, key,
credential, or signed URL. No SMS dependency exists or was added (SMS
remains disabled per `LIFELINK_SMS_STATUS.md`, unchanged by this phase).

## 14. Frontend document UX

**Already correct — reviewed, not redesigned.**
`pages/donor/IdentityVerification.tsx` and
`pages/hospital/FacilityVerification.tsx` already implement: idle ("Not
submitted"), selecting (client-side type/size validation per file, with
inline errors and a per-file remove button), uploading
(`submit.isPending`, spinner, disabled submit), success (explicit success
message, file list cleared), failure (honest `toApiError(...).detail` —
which now surfaces this phase's new `"Document storage is temporarily
unavailable. Please try again."` verbatim, never a generic/fake message),
and PENDING/REJECTED/APPROVED states driven off real server data. A failed
submission does not clear the picked files or disable the submit button —
clicking it again *is* the retry (proven end-to-end by this phase's new
`document_storage_security.spec.ts` test (E), which aborts the real POST
once, asserts the honest error and that the file selection survived, then
lets the retry through and asserts real success). `VerificationsQueue.tsx`
(admin) already shows loading/error/empty states for both the queue list
and the detail drawer, and documents are opened via a real anchor pointing
at the real backend URL (`target="_blank"`) — the same convention
`DonationCertificate.tsx` already uses for its own PDF download link, so
this is a consistent, pre-existing pattern, not a Phase 20 gap. No fake
progress bars, no hardcoded "uploaded" state, no silent failures were
found anywhere in either flow.

## 15. Accessibility

**Already correct.** Both file inputs have an associated `<label>`
(`htmlFor`/`id` pairs: `id-doc-input`, `facility-doc-input`); every remove
button has `aria-label={"Remove " + filename}`; the submit button's
disabled state is driven by real validation state
(`!canSubmit`/`submit.isPending`), not a visual-only cue; error messages
are rendered as real DOM text (picked up by `toBeVisible()` assertions in
this phase's Playwright coverage), not styling alone. No accessibility
regression was introduced since no visual/structural change was made to
these components.

## 16-17. What required frontend work / what required backend work / what needed real-infrastructure testing

- **Backend work required**: storage-failure exception handling (§10),
  `Cache-Control` header (§7), the scanner integration point (§14 of the
  final report), the `StorageObjectNotFoundError`/`CertificateStorageError`
  exception classes, `storage.delete_key` for compensating cleanup.
- **Frontend work required**: none — the existing upload/download UX
  already met every requirement; this phase added E2E coverage proving it,
  not new components.
- **Real-infrastructure testing performed**: real PostgreSQL (every backend
  test, including all new Phase 20 tests), real local-filesystem storage
  backend (the actual reachable dev/test backend in this sandbox — used by
  both the pytest suite and the live dev server), real Redis (rate
  limiting), a genuine boto3-request-level test of `storage.py`'s S3 code
  path via `moto` (installed and verified importable —
  **not** the same claim as "tested against real MinIO"), and a real
  Chromium browser driving the real production frontend build against the
  real backend for all new E2E scenarios.
- **Environmentally BLOCKED, documented honestly, not fabricated**: testing
  against a genuinely running MinIO/S3 server. This sandbox has no Docker
  daemon (`docker ps` fails to connect to the Docker socket at all — it
  does not exist in this environment) and the MinIO server binary download
  is blocked by the egress proxy allowlist (`curl https://dl.min.io/...`
  returns a 403 from the proxy, not from MinIO). This is the same class of
  environmental limitation earlier phases already hit with Neo4j. The
  `moto`-based test is the honest, real substitute described above — real
  botocore-level request/response behavior, not a mock of this project's
  own functions — and is labeled as such in its own test file, never
  conflated with "real MinIO."

---

*Everything in this document reflects the actual, verified pre-Phase-20
state of the repository. The implementation and the two new test files
that follow this audit close every genuine gap identified above; nothing
here was invented to create work, and nothing marked "already correct" was
re-implemented.*
