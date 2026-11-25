# Phase 20 Report

## 1. Status

**COMPLETE.**

## 2. Baseline

Backend: 655 tests. Playwright: 21/21.

## 3. Final Results

- Backend: **689/689** passed (655 baseline + 34 new Phase 20 tests), 0 failures, 0 errors.
- Playwright: **27/27** passed (21 baseline + 6 new Phase 20 E2E scenarios).
- TypeScript (`tsc --noEmit`): **PASS**, 0 errors.
- ESLint (`npm run lint`): **PASS**, 0 findings.
- Production build (`npm run build`): **PASS** (pre-existing chunk-size advisory only, unrelated to this phase, unchanged).
- Database (real PostgreSQL): **PASS** — every backend test, including all 34 new ones, ran against a real Postgres test database; no schema change was needed (§8).
- Storage integration: **PASS** for the real local-filesystem backend (the actual reachable dev/test storage in this sandbox) and for a genuine boto3-request-level `moto` simulation of the S3 code path; **BLOCKED** for a real running MinIO server (no Docker daemon in this sandbox; the MinIO binary download is blocked by the egress proxy allowlist) — see §13.
- Ruff: 101 findings, identical to the pre-Phase-20 baseline (all pre-existing and unrelated; the new test file introduced zero new findings after cleanup).
- Mypy: 3 pre-existing errors, identical to the pre-Phase-20 baseline (all unrelated to this phase's files).

## 4. Audit Findings (what was already correct)

Full detail in `LIFELINK_PHASE_20_AUDIT.md`. Summary of what was verified
correct and preserved, not touched:

- The single storage abstraction (`app/core/storage.py`, S3/MinIO with a
  local-disk dev/test fallback) — reused by both real consumers, no second
  storage mechanism introduced.
- Opaque, server-generated storage keys for both verification documents
  and certificates; the browser never constructs a storage path; no
  storage credential is ever exposed to the frontend (confirmed:
  `frontend/.env.example` has no `S3_`/`MINIO_` variable at all).
- Filename sanitization (`_safe_filename`) — already defeated every
  malicious filename the spec called out by name; verified with a new
  parametrized test rather than assumed.
- Magic-byte content validation (Phase 14) — already required the sniffed
  byte signature to match the declared `Content-Type`, not merely both be
  individually allowed.
- File size limits and bounded chunked reads (Phase 17).
- The full IDOR access matrix (§8 of the audit) — including the
  non-negotiable "family link cannot bypass donor document/certificate
  access" guarantee, both structurally (FAMILY cannot even link to a
  DONOR/HOSPITAL account) and behaviorally.
- Certificate download's dual-auth model (session or signed, expiring
  token) and its pre-existing `certificate.viewed`/`certificate.downloaded`/`certificate.access_denied`
  audit events.
- Notification bodies for every document-related event — never contain
  document contents, storage paths, keys, or credentials.
- The frontend upload/download UX (idle/selecting/uploading/success/error/retry
  states, accessible labels, honest error surfacing) — already complete;
  no redesign was needed or performed.
- No delete/replace/versioning exists for either verification documents or
  certificates, confirmed by reading both models and both routers — this
  is correct for the current product and was not added.

## 5. Genuine Gaps Found

All six below were verified as real gaps (via a throwaway monkeypatch probe
against the actual pre-Phase-20 code, or by reading the actual response
headers) before any fix was written, per the audit's own methodology:

1. `storage.put_bytes` failures during verification upload were unhandled.
2. `storage.get_bytes` failures during verification document download were
   unhandled, and did not distinguish "object missing" from "backend broken."
3. `storage.put_bytes` failures during certificate issuance were unhandled.
4. Certificate download's existing generic failure handler did not
   distinguish "object missing" from "backend broken" (unlike the other
   two, it *did* already return a clean 503 for a broken backend — the gap
   was narrower here: a missing object still 503'd instead of 404'ing).
5. Neither document-serving response set `Cache-Control` — a private
   identity/facility document or certificate PDF could be cached by an
   intermediary or the browser's own disk cache.
6. No malware/document-security-scanning integration point existed at all
   anywhere in the codebase (confirmed by grep) — not fabricated as
   present, and not built as a real scanner either; see §7.

## 6. Bugs Found

### Bug 1 — Unhandled storage exception on verification upload
- **Root cause**: `storage.put_bytes()` in `submit_verification()`'s
  storage-write loop had no exception handling.
- **Impact**: Security/reliability. A real storage-backend outage (MinIO
  down, disk full) surfaced as a bare, unhandled exception — an opaque
  500, and depending on the exception type, a risk of leaking a
  backend-internal detail (a filesystem path, an S3 error body) into the
  response.
- **Reproduction**: monkeypatched `storage.put_bytes` to raise; `POST
  /verifications` raised unhandled before the fix.
- **Fix**: wrapped the write loop; on any exception, best-effort delete
  every key already written in this request (`storage.delete_key`, new),
  then raise a clean `503`.
- **Regression test**: `test_storage_failure_on_verification_upload_is_a_clean_503_and_nothing_persists`,
  `test_storage_failure_partway_through_multi_file_upload_cleans_up_already_written_objects`.

### Bug 2 — Unhandled storage exception, and no missing-object distinction, on verification document download
- **Root cause**: `storage.get_bytes()` in `admin_get_verification_document()`
  had no exception handling.
- **Impact**: Security/reliability, same class as Bug 1.
- **Reproduction**: monkeypatched `storage.get_bytes` to raise a generic
  exception, and separately to raise `FileNotFoundError`; both surfaced
  unhandled before the fix.
- **Fix**: new `StorageObjectNotFoundError` (raised by `storage.get_bytes`
  on a genuinely missing object, normalized across both backends); route
  catches it → clean `404`; any other exception → clean `503`.
- **Regression test**: `test_storage_failure_on_verification_document_download_is_a_clean_503`,
  `test_missing_verification_document_object_is_a_clean_404_not_503`.

### Bug 3 — Unhandled storage exception on certificate issuance
- **Root cause**: `storage.put_bytes()` in `certificate_service.get_or_issue()`
  had no exception handling. (The write already happened before
  `db.add(cert)` — the ordering itself was already correct; only the
  exception handling was missing.)
- **Impact**: Security/reliability, same class as Bug 1.
- **Reproduction**: monkeypatched `storage.put_bytes` to raise;
  `POST /certificates` raised unhandled before the fix.
- **Fix**: new `CertificateStorageError`, raised by `get_or_issue()` on any
  storage exception; route catches it → clean `503`. No `Certificate` row
  is ever created when this happens (verified: a retried issuance
  afterward is a normal `201`, not a duplicate/conflict).
- **Regression test**: `test_storage_failure_on_certificate_issuance_is_a_clean_503_and_no_row_created`.

### Bug 4 — Certificate download did not distinguish missing-object from broken-backend
- **Root cause**: the existing generic `except Exception` in
  `download_certificate()` caught everything, including a genuinely-missing
  storage object, as a `503`.
- **Impact**: Reliability/UX (not a security hole — the 503 was already
  honest and leaked nothing) — a permanently-gone object told the client
  "try again later" when retrying can never help.
- **Fix**: split into `except StorageObjectNotFoundError → 404` and
  `except Exception → 503` (unchanged message/behavior for the true
  broken-backend case).
- **Regression test**: `test_missing_certificate_object_is_a_clean_404_not_503`.

### Bug 5 — Missing `Cache-Control` on private document/certificate responses
- **Root cause**: neither response set the header at all.
- **Impact**: Security. A private identity/facility document or a
  donor-specific certificate PDF could be retained by an intermediary
  cache or the browser's own disk cache.
- **Reproduction**: printed the actual response headers dict from a live
  request before the fix — confirmed absent.
- **Fix**: `Cache-Control: private, no-store` added to both responses.
- **Regression test**: `test_authorized_document_download_is_never_cacheable`,
  `test_certificate_download_is_never_cacheable`; also exercised end to end
  in the new Playwright test (C).

### Bug 6 (defensive, not a live exploit path) — dead presign functions would fail confusingly on the local backend
- **Root cause**: `presign_get`/`presign_put` unconditionally called
  `get_s3_client()` regardless of `storage_backend()`. Confirmed via grep
  that neither is called anywhere in the app today, so this was never
  reachable in practice — but if a future caller invoked either while
  running on the local-disk backend, it would fail with a confusing,
  unrelated boto3/credentials error instead of a clear one.
- **Fix**: both now raise a clear `RuntimeError` up front when
  `storage_backend() != "s3"`.
- **Regression test**: `test_presign_functions_refuse_to_run_on_the_local_backend`.

## 7. Security Improvements

- **Upload validation**: unchanged (already correct) — magic-byte
  cross-check, filename sanitization, bounded reads, all preserved and
  re-verified with new tests against the spec's exact required attack
  strings.
- **Storage privacy**: `Cache-Control: private, no-store` added to both
  document-serving responses (Bug 5).
- **IDOR protection**: unchanged (already correct) — re-verified with real
  database rows across the full role matrix, including the malformed-ID
  (422) and nonexistent-ID (404) cases this phase added explicit coverage
  for.
- **Filename security**: unchanged (already correct) — re-verified against
  the spec's exact required malicious-filename list.
- **Download authorization**: unchanged (already correct); storage-layer
  failures no longer leak internals or crash unhandled (Bugs 1-4).
- **Storage failure handling**: net-new — Bugs 1-4 above. This is the
  phase's largest real contribution.
- **Audit logging**: unchanged (already correct) — no document contents,
  binary data, storage credentials, or signed-URL secrets were ever
  written to an audit record, before or after this phase; verified with a
  new explicit test asserting on the actual stored `meta` JSON.
- **Secret handling**: verified, not changed — `backend/.env` is
  git-ignored (`backend/.gitignore`: `.env`, `.env.*`, with `.env.example`
  explicitly excluded from the ignore), `backend/.env.example` already
  contained placeholder-only values with clear "never commit a real value
  here" comments for every credential (Gmail app password, MinIO
  keys, Neo4j password, MSG91 auth key), and the frontend has zero
  storage-related environment variables. **No secret value is reproduced
  anywhere in this report or the audit document.**
- **Malware scanning**: honestly implemented as a real, live integration
  point (`app/services/document_scanner.py`) with a `NoOpScanner` that
  never claims to have inspected the file — see §14 below.

## 8. Database Changes

**MIGRATION: NONE.** `VerificationRequest.documents` is already a JSON
column; the new per-document `scanned_by` field fits inside it with no
schema change. No column, table, or index was added anywhere by this
phase.

## 9. Backend Files Changed

- `app/core/storage.py` — new `StorageObjectNotFoundError`; `get_bytes`
  rewritten to raise it on a genuinely missing object (both backends); new
  `delete_key()` for best-effort single-object cleanup;
  `presign_get`/`presign_put` guarded against running on the non-S3 backend.
- `app/services/document_scanner.py` — **new file**. `ScanResult`,
  `DocumentSecurityScanner` protocol, `NoOpScanner`, `get_scanner()`.
- `app/api/routes/verifications.py` — scanner call wired into the real
  upload validation path; storage-write loop wrapped with honest
  error handling + compensating cleanup; `admin_get_verification_document`
  now distinguishes 404 vs 503 and sets `Cache-Control: private, no-store`.
- `app/services/certificate_service.py` — new `CertificateStorageError`;
  `get_or_issue`'s storage write wrapped to raise it.
- `app/api/routes/certificates.py` — `issue_certificate` catches
  `CertificateStorageError` → 503; `download_certificate` now distinguishes
  `StorageObjectNotFoundError` (404) from any other failure (503, unchanged
  message) and sets `Cache-Control: private, no-store`.
- `tests/test_phase20_secure_file_storage.py` — **new file**, 34 tests
  (§11).

## 10. Frontend Files Changed

None. The audit (§14 of `LIFELINK_PHASE_20_AUDIT.md`) found the existing
upload/download UX already met every Phase 20 requirement — idle,
selecting, uploading, success, failure, and implicit-retry states; honest,
non-leaking error messages; accessible labels on every interactive
element. No component was modified, and none was redesigned.

## 11. Tests Added

`backend/tests/test_phase20_secure_file_storage.py` — 34 tests:

- Authenticated/unauthenticated upload (2)
- Malicious filenames, parametrized over the spec's exact required list —
  `../../secret.txt`, `..\..\secret.txt`, `C:\secret.txt`, `/etc/passwd`,
  `<svg>.svg`, `document<script>.pdf`, a 500-character filename, and
  embedded control characters (8)
- Authorized document/certificate download never cacheable (2), unauthorized
  document download denied (1)
- Cross-user IDOR (1), cross-role access (1)
- Malformed verification/certificate ID → 422 (2), nonexistent ID → 404 (2)
- Storage failure on verification upload, including partial multi-file
  cleanup (2); storage failure on verification document download (1);
  missing verification document object (1); storage failure on certificate
  issuance (1); missing certificate object (1)
- Successful upload audited with safe-only metadata (1)
- No storage key/credentials ever exposed via API responses (1)
- Family-link cannot bypass donor document/certificate access (1)
- Certificate/verification-document storage isolation (1)
- Replacement "not supported" consistency proof (1), delete "not
  supported" proof (1)
- Scanner metadata recorded internally, never exposed via API (1)
- Genuine boto3-level S3 round-trip + not-found, via `moto` (1)
- Dead presign functions refuse to run on the local backend (1)

`frontend/e2e/document_storage_security.spec.ts` — 6 scenarios (§12).

## 12. E2E Tests

All six drive the real production frontend build against the real backend
and real local-disk storage, with real seeded accounts:

- **(A)** Donor uploads a verification document through the real UI
  (`IdentityVerification.tsx`, real `setInputFiles`) and sees it move to
  "Submitted — awaiting review."
- **(B)** Hospital uploads a facility verification document through the
  real UI (`FacilityVerification.tsx`) with the same outcome.
- **(C)** An authorized admin opens the review queue, locates the real
  document link, and downloads it through the real, authenticated browser
  session — asserts `200`, the correct `Content-Type`, the new
  `Cache-Control: private, no-store` header, and the real file bytes.
- **(D)** A logged-in non-admin (hospital) attempts the admin document
  route directly with their own real session cookie — asserts a clean
  `403`, never a document.
- **(E)** A real aborted `POST /verifications` shows the app's honest
  network-failure message, keeps the picked file selected, and a retry
  after the network "returns" succeeds for real.
- **(F)** A nonexistent document index on a real verification request
  returns a clean `404` with an honest `detail`, never a broken/blank
  viewer.

## 13. Real Infrastructure

- **PostgreSQL**: real, for every one of the 689 backend tests.
- **Redis**: real, exercised by the rate-limiting paths every test run
  already goes through.
- **Backend**: real FastAPI app, both under `pytest` (local-disk storage
  backend, matching `tests/conftest.py`'s forced empty S3 settings) and as
  a live `uvicorn` dev process for the Playwright suite (also run with the
  local-disk storage backend, for the same reason — see below).
- **Frontend**: the real production build (`vite build` + `vite preview`),
  not the dev server, driving all 27 Playwright scenarios.
- **Storage — local-disk backend**: real, both in the pytest suite and the
  live dev server used for E2E.
- **Storage — S3/MinIO backend**: **PASS** for a genuine boto3-request-level
  simulation via `moto` (a real AWS/S3 API simulator at the botocore
  layer — the actual `boto3` client construction and request/response
  contract, including this phase's own `StorageObjectNotFoundError`
  translation of a real `ClientError`); **BLOCKED** for a real, running
  MinIO server. This sandbox has no Docker daemon at all
  (`docker ps` fails to connect to the Docker socket — confirmed, not
  assumed) and the MinIO server binary download is refused by the egress
  proxy allowlist with a `403` from the proxy itself. This mirrors the
  same class of environmental limitation earlier phases already documented
  for Neo4j. **This was not worked around by fabricating a passing result**
  — the local dev backend in this sandbox is genuinely configured for real
  S3/MinIO in `.env` (a placeholder pointing at `localhost:9000`, matching
  `.env.example`'s documented dev setup for a machine that *does* have
  MinIO running), and when this phase's live dev server was briefly started
  with that real configuration, every upload attempt correctly surfaced as
  this phase's new honest `503` rather than crashing unhandled — a live,
  unplanned demonstration that the exact fix this phase made is correct.
  The dev server used for the Playwright runs recorded in this report was
  deliberately started with the S3 credentials overridden empty (forcing
  the local-disk fallback), exactly matching `tests/conftest.py`'s own
  convention, so that E2E testing could proceed honestly on the one
  storage backend actually reachable here.

## 14. Malware Scanning — honest statement

LifeLink did not have any malware/antivirus scanning before this phase
(confirmed by grepping the entire backend for `clamav`/`malware`/`virus`/`scanner`
— zero matches). This phase did **not** add real scanning — doing so would
require a real scanner service (e.g. ClamAV) this environment cannot stand
up, and pretending otherwise would violate the phase's explicit "do not
mark an uploaded document as malware scanned when no scanner actually ran"
instruction.

What this phase built instead: a real, live integration point
(`app/services/document_scanner.py`) — `DocumentSecurityScanner` (a
`Protocol`), `NoOpScanner` (the only implementation today; always reports
`clean=True` because it performs no actual inspection), and `get_scanner()`
(currently always returns `NoOpScanner`). This is wired into the *real*
upload request path in `submit_verification()` — called after magic-byte
validation, before a file is queued for storage — not an orphaned class
nothing calls. Every stored document records which scanner produced its
verdict (`scanned_by: "noop"`), specifically so nothing downstream (an
admin, an audit-log reader, a future engineer) can mistake "noop" for "this
file was actually scanned for malware." This field is never surfaced to
the submitter, the frontend, or any notification as a security claim —
`VerificationDocumentOut` does not expose it (verified with a dedicated
test).

**Production requirement**: before this system should be considered to
have real malware protection, `get_scanner()` needs a real implementation
(e.g. a `ClamAVScanner` calling a ClamAV daemon over its socket protocol)
selected via configuration. No code at any call site needs to change to add
one — that is the entire purpose of the seam this phase built. **Until
then, LifeLink has no malware scanning, and no part of this system claims
otherwise.**

## 15. Limitations

1. **Real MinIO/S3 integration was not testable in this sandbox.**
   *Why*: no Docker daemon, and the MinIO binary download is blocked by
   the egress proxy. *Impact*: the S3 code path is verified at the
   boto3-request level via `moto`, which is strong but not identical to a
   real running MinIO/S3 endpoint (network latency/failure modes, actual
   bucket policies, and real credential handshakes are not exercised).
   *Blocks production*: no — the code path is identical regardless of
   which S3-compatible endpoint it talks to, and the abstraction has been
   in production use (per prior phases) already. *Recommended*: a future
   phase run in an environment with Docker/MinIO available should re-run
   this project's storage tests against a real MinIO container as a final
   confirmation.
2. **No real malware scanner exists.** *Why*: out of scope for what this
   sandboxed environment can stand up, and honestly documented rather than
   faked (§14). *Impact*: an uploaded file that is a genuine malware
   payload disguised as a valid PDF/PNG/JPEG (i.e., one that also passes
   the real magic-byte check) would currently be accepted and stored.
   *Blocks production*: **yes**, for a deployment that must defend against
   malicious upload content, not just malformed/spoofed uploads. *Recommended*:
   implement `ClamAVScanner` (or an equivalent) against `DocumentSecurityScanner`
   and select it via configuration before handling real user-submitted
   identity/facility documents in production.
3. **The verification-document route's role-gated 403 is not individually
   audited** (see the audit's §12 reasoning) — this is a deliberate
   consistency choice matching how every other admin-only route in the
   codebase already behaves, not an oversight, but it is worth naming
   explicitly as a boundary: only resource-scoped access attempts (a real
   object someone tried and failed to access) are audited anywhere in this
   codebase, never blanket role mismatches.

## 16. GO / NO-GO

**GO.**

Justification: every genuine gap this audit found (six items, §5) has a
real fix, a real regression test, and was verified against real
infrastructure wherever that infrastructure is reachable in this sandbox;
the one place real infrastructure was not reachable (MinIO) is honestly
documented as blocked, not faked, and is not a code-path difference from
what was tested. The full pre-existing backend (655) and Playwright (21)
baselines pass unchanged, plus 34 new backend and 6 new E2E tests, with
zero new ruff/mypy findings. The one genuine limitation that would block a
security-sensitive production deployment — no real malware scanner — is
explicitly named as blocking in §15 rather than glossed over, with a clear,
already-built integration point for closing it.
