# LifeLink — Phase E: Domain Integrity, Fulfilment, Verification & Tamper-Evident Audit

**Date:** 2026-10-05 · **Scope:** the eight Phase E areas — fulfilment lifecycle, donation verification, clinical-rule/source integrity, verification-document versioning, tamper-evident audit logging, cross-domain invariants, shipment component correctness, safe failed-shipment reconciliation. Phase F was not started.

Companion permanent docs written this phase: `LIFELINK_FULFILLMENT_LIFECYCLE.md`, `LIFELINK_VERIFICATION_LIFECYCLE.md`, `LIFELINK_AUDIT_CHAIN.md`; updated: `LIFELINK_INVARIANTS.md` (28–39), `LIFELINK_PHASE_35_RECON_MATRIX.md` (Phase E status section — this repository's recon matrix; there is no separate `LIFELINK_RECON_MATRIX.md`).

## 1. Objective and outcome

Make every important business state explicitly verifiable, auditable, versioned where necessary, resistant to tampering, and semantically honest. All eight areas were implemented with migrations, real-PostgreSQL concurrency tests, frontend integration and documentation. Regression found one real pre-existing double-allocation bug (§17), which was fixed with deterministic regression tests.

## 2. Reconnaissance findings that shaped the design

- Fulfilment was all-or-nothing; `/allocate` deducted inventory but never recorded *how much of the request* it met, so a "partial" state would have been cosmetic.
- Logging a donation has **no** coupling to inventory (`BloodUnit` is created only by a hospital's ADD/receive). The real honesty gap was certificate issuance turning a self-report into a public, verifiable PDF.
- `Donation.hospital_name` is free text; no structural link to a facility existed.
- Verification submissions were already append-only (new row per submission, no replace/delete); what was missing was explicit version identity and integrity.
- Clinical figures were already cited (Phase 35); provenance was prose-only, and a dead `blood_unit_shelf_life_days` setting still advertised a knob nothing read.
- `audit_logs` was append-only by convention, with no tamper evidence; every write already went through one function, `audit_service.record()`.
- The frontend had no notion of blood components at all; the shipment form could only ever ship the backend's `WHOLE_BLOOD` default.
- Phase D disclosed that a FAILED shipment's units had no reconciliation path.

## 3. Fulfilment lifecycle (area 1)

`BloodRequest.fulfilled_units` is the single source of truth (DB `CHECK` 0…`units_needed`); `HospitalRequestResponse.units_allocated` is each hospital's contribution. `PARTIALLY_FULFILLED` is entered only by an allocation that actually moved inventory, never reopens to PENDING/MATCHING, and is an open status. Quantity is validated **before** inventory is touched (over-allocation → 409, zero side effects). Full allocation now fulfils by itself. Manual fulfil paths declare the remaining gap and keep the per-hospital breakdown consistent. `REJECTED` was deliberately not added. Full design: `LIFELINK_FULFILLMENT_LIFECYCLE.md`.

## 4. Concurrent fulfilment (races A–D, real PostgreSQL)

| Race | Proven outcome |
|---|---|
| A — two hospitals race for the final units | exactly one 200 / one 409; loser's inventory untouched |
| B — concurrent partials (2 hospitals; and 5 hospitals × 1 unit vs 4 needed) | no lost update; exactly 4 of 5 succeed |
| C — allocation vs cancel | either legal history; inventory matches the winner |
| D — allocation vs another hospital's manual fulfil | always FULFILLED, quantity exact, breakdown sums |

Each race test passed 5 consecutive runs. All serialise on the existing request row lock — no new locking mechanism.

## 5. Donation verification (area 2)

`DonationVerificationStatus` = `DONOR_REPORTED` (default, and every historical row) / `VERIFIED` / `REJECTED`; corrections are audited decisions; same-state calls 409; concurrent reviewers → one 200, one 409. New `/donations` resource (list queue, get, verify, reject). Authorization: never the donor; a hospital only for donations linked to **its own** facility via the new optional `Donation.hospital_id`; a matching free-text name grants nothing; unlinked donations are admin-only. No "clinically verified" tier (no clinical workflow exists). Full detail: `LIFELINK_VERIFICATION_LIFECYCLE.md`.

## 6. Certificate gate

`POST /certificates` returns 409 unless the donation is `VERIFIED`. First issuance only — existing certificates keep resolving, and a later rejection does not revoke (revocation stays an explicit admin action). Pre-Phase-E certificates were not retroactively revoked.

## 7. Clinical-rule and source integrity (area 3)

Machine-readable provenance on every shelf-life entry (`source_id`, `source_version` — `None` where the source has none, never invented — `retrieved_on`) and on the component-compatibility tables. `RULESET_VERSION` plus a SHA-256 fingerprint of the whole table, pinned by a regression test, so any edit to a number, URL or note fails CI until deliberately re-cited and re-pinned (a test proves the fingerprint detects both a figure change and a URL change). Each new unit records `expiry_rule_ref` naming the exact rule that started its clock. The dead `blood_unit_shelf_life_days` setting was removed (and a stale model comment that still cited it corrected). The plasma post-thaw limitation remains disclosed and test-pinned. No new clinical facts were introduced.

## 8. Verification-document versioning (area 4)

`version` (unique per user+kind) and `supersedes_request_id`; earlier versions never modified (test reads the old bytes back from storage). Per-file SHA-256 at submission; every admin download re-verifies — mismatch → 409 + CRITICAL audit event; legacy files served as `unverified-legacy`, never given a back-dated checksum. Approve/reject audit entries bind the decision to `version` + checksums. Concurrent resubmissions can't share a version.

## 9. Tamper-evident audit chain (area 5)

SHA-256 hash chain over a canonical serialisation of each `audit_logs` row, retrofitted into the existing `audit_service.record()` path so every existing call site participates. Admin verify (with optional external anchor) and head endpoints, admin UI panel, CLI check. Documented and labelled as **tamper-evident, not immutable, not a blockchain**; the two attacks the chain alone cannot catch (full recompute, tail truncation) are proven by tests and caught by anchoring. Full design: `LIFELINK_AUDIT_CHAIN.md`.

## 10. Audit-chain concurrency

The naive SELECT-latest-then-INSERT design was not used, and neither was a global lock inside business transactions (it would deadlock with the row locks almost every write route already holds and serialise every write). Business transactions insert plain rows; a short sealing transaction under `pg_advisory_xact_lock` chains them after the business transaction has ended. Proven: 16 concurrent writers × 5 commits → contiguous `seq` 1…80, no fork, verify OK; concurrent row-locking business writes alongside sealing → no deadlock. A real bug in the first version (pool exhaustion) was caught by this test — §17.

## 11. Shipment component correctness (area 7)

Frontend gained a `BloodComponent` type; the create-shipment form now has a component selector and sends `component`; every unit chip (hospital and admin views) shows blood type + component. Unknown component strings are shown raw, never guessed. E2E proves a PLASMA shipment round-trips.

## 12. Safe failed-shipment reconciliation (area 8)

`ShipmentReconciliationStatus` is a separate axis (FAILED's terminal transition table untouched). FAILED sets `REQUIRED`; `POST /shipments/{id}/reconcile` (origin or admin only, exactly once, row-locked) records `RETURNED_TO_ORIGIN` / `REDIRECTED` / `QUARANTINED` with a mandatory note. Restocking requires every carried unit code echoed back **and** `cold_chain_maintained=true`; returned units are new rows linked to the originals, keep the original expiry, and come back EXPIRED if already past it; originals stay ALLOCATED. REDIRECTED/QUARANTINED change no inventory. Nothing is automatic. Concurrent double-restock → exactly once.

## 13. Cross-domain invariants (area 6)

Invariants 28–39 added (`LIFELINK_INVARIANTS.md`). 11 are executable SQL checks (`app/services/invariant_check.py`) run after a full end-to-end scenario touching every area, each proven to fire on a deliberately corrupted row, and runnable against any database (`python backend/scripts/check_invariants.py`). Against the migrated dev database (7k+ audit rows, 1,247 donations, 817 requests): all 11 checks hold and the chain verifies.

## 14. Frontend state honesty

- `PARTIALLY_FULFILLED` handled everywhere a status is rendered (TypeScript forced every status map); 7 screens that hard-coded `["PENDING","MATCHING"]` as "open" now use a shared `OPEN_REQUEST_STATUSES` — before, a partially-met request would have shown as closed. One analytics ordering array omitted the new status silently (not caught by types) and was fixed.
- `FulfillmentProgress` shows server quantities; "remaining" is display-only; the patient "Fulfilled units" total now sums real `fulfilled_units`.
- Donations show "Self-reported · not yet verified" / "Verified by facility" / "Disputed"; certificate buttons appear only for verified donations; the log-donation form can link a real facility.
- Hospital "Donation Verification" queue (new route + nav item); admin queue on the Donors page with an "unlinked only" filter.
- Verification version and checksum status in the admin queue and submitters' status banners.
- Audit-chain panel: "No evidence of tampering across N sealed entries" / exact break; unsealed count; head for anchoring; "does not make the log immutable".
- New notification types: `donation.verified`, `donation.rejected`, `shipment.reconciled`.

## 15. Migrations

One revision, `d3e4f5a6b7c8_phase_e_domain_integrity` (down-revision `c2d3e4f5a6b7`): `PARTIALLY_FULFILLED` enum value (autocommit block — PG rejects using a new enum value in the same transaction); `fulfilled_units` + two `CHECK`s; `units_allocated`; donation verification columns + `donation_verification_status` type + indexes; verification `version`/`supersedes_request_id` + unique; audit chain columns, unique `chain_seq`, partial unsealed index; `expiry_rule_ref`; shipment reconciliation columns + type. Backfills use only existing data (detail in the migration docstring); the audit backfill sealed all 7,291 historical rows. Downgrade refuses (rather than silently discards) when real verification decisions or `PARTIALLY_FULFILLED` rows exist.

Verified: dev DB upgrade → `verify_chain` OK → downgrade → upgrade → identical head hash (deterministic); fresh empty DB `alembic upgrade head`; `test_alembic_head_schema_matches_orm_metadata` (ORM vs migrated schema) passes; `alembic check` reports only the pre-existing Phase 24 `notifications` DESC-index autogenerate false positive. Dev DB backed up (`pg_dump`) before upgrading.

## 16. Performance (EXPLAIN ANALYZE, dev database)

| Query | Plan | Time |
|---|---|---|
| Chain head | Index Scan Backward on `audit_logs_chain_seq_key` | 0.11 ms |
| Unsealed count / queue | `ix_audit_logs_unsealed` (partial) | 0.1–0.14 ms |
| Verify page (2,000 entries) | Index Scan on `chain_seq` | 1.3 ms |
| Hospital donation queue | Index Scan `ix_donations_hospital_id` | 0.07 ms |
| Admin unlinked queue (1,247 rows) | Seq Scan + top-N sort | 0.8 ms — fine at this size; an index on `(verification_status, donated_on)` is the lever if it grows |
| Verification version lookup | Seq Scan (5 rows); unique `(user_id, kind, version)` serves it at scale | 0.06 ms |

Write overhead: `record()` + commit median ≈ 2.5–4 ms without sealing vs ≈ 8–11 ms with sealing (one extra short transaction per auditing request, ≈ +6 ms). Full verification ≈ 68 µs/entry. A `synchronous_commit=off` optimisation for the sealer showed no measurable gain and was reverted. (The perf probes wrote 530 `perf.phase_e_probe` audit rows to the **dev** database — real, labelled probe events, not production data; corrected in Phase G from an earlier "≈ 240" estimate after counting them. They are left in place: deleting audit rows would break the hash chain, which is exactly what it is for.)

## 17. Bugs found and fixed this phase

1. **Pre-existing double-allocation across reservation contexts (real, Phase 34-era logic surfaced by a Phase D test).** `allocate_stock` let any allocation consume any reservation, so a direct allocation could take units reserved for a shipment. Seen as an intermittent failure of `test_phase_d_concurrency.py::test_direct_allocation_vs_shipment_creation_cannot_double_commit_stock` in the full run (passes in isolation — order-dependent). Fixed (invariant 39) with deterministic regressions confirmed to fail on the old logic. This is the one place Phase D/34 code was reopened, on the evidence of a failing regression, as the brief allows.
2. **Audit sealing pool deadlock (in this phase's own first version).** Sealing in `after_commit` needed a second pooled connection while the first was still held; 16 concurrent writers exhausted the pool. Moved to `after_transaction_end`. Caught by the concurrency test before anything shipped.
3. **Frontend "open" status lists** (7 screens) would have hidden partially-fulfilled requests; one analytics ordering array silently dropped the new status.
4. **Patient "Fulfilled units"** counted `units_needed` of fully-fulfilled requests only.
5. **Dead/misleading config** `blood_unit_shelf_life_days` and the model comment that still cited it.
6. **"Immutable" wording** in three code comments about the audit trail/inventory log corrected to "append-only, tamper-evident".

## 18. Test inventory and regression results

**New backend tests (185):** fulfilment 25, donation verification 19, document versioning 9, clinical sources 82 (parametrised: 64 plasma ABO pairs), audit chain 18, shipment reconciliation 15, invariants 17. **New frontend:** 11 Vitest component tests (`PhaseE.test.tsx`), 3 Playwright E2E (`phase_e_domain_integrity.spec.ts`).

**Existing tests changed, and why (no assertion was weakened to hide a failure):**
- `test_blood_request_lifecycle_phase10.py` (3 assertions): the exact `allowed_transitions` sets now include `PARTIALLY_FULFILLED` — an intended new edge.
- `test_hospital.py::test_hospital_allocate_and_fulfill_request`: full allocation now fulfils by itself; the test now asserts that, plus `fulfilled_units == 2`, and that the redundant `/fulfill` is a 409.
- Certificate fixtures in `test_certificates.py`, `test_phase15_certificate_security.py`, `test_phase20_secure_file_storage.py`, `test_phase26_donation_idempotency.py`, `test_admin_phase6.py`, `test_phase31_domain_fixes.py`, `test_phase23_security_hardening.py`, `test_phase14_verification_security.py`, `test_authz_phase8.py`: each now performs the real admin verification step before issuing a certificate (the new precondition), through the real endpoint (one builds the row directly in the DB and sets `VERIFIED` there, with a comment). Their own assertions are unchanged.
- Playwright `phase23_security_hardening.spec.ts`, `phase31_1_domain_signals.spec.ts`: same — a shared `verifyDonationAsAdmin` helper calls the real review endpoint.

**Results:**
- Backend full suite: **1,109 passed, 1 skipped, 0 failed** (§18a).
- Phase E backend tests: 185/185; race tests 5/5 repeated; Phase D race test 10/10 repeated after the fix.
- Frontend: `tsc --noEmit` clean; ESLint clean (`src` and `e2e`); Vitest 138/138 across 15 files (127 existing + 11 new); `npm run build` OK (main chunk 785.5 kB / 227.6 kB gzip vs 780.3 kB at Phase 30).
- Playwright (fresh `lifelink_e2e` DB, CI-equivalent env): **72 passed, 1 failed, 1 skipped of 74.** The failure is `blood_request_idempotency.spec.ts`'s rapid double-click test — the same pre-existing, CPU-timing flake disclosed in the Phase 31.1 report; it passed when re-run in isolation immediately after. The skip is the pre-existing Phase 21 test C. An earlier full run had 3 additional failures caused by **my own** standalone pre-run of the new spec, which logged a same-day donation for the shared e2e donor and made it ineligible for later notification specs; the spec now dates its donation outside the eligibility window, and the clean re-run on a fresh DB is the result reported above.

### 18a. Final backend full-suite run

Run after every Phase E change including the §17 fix, full suite (`pytest tests --ignore=tests/e2e`, logging plugin enabled): **1,109 passed, 1 skipped (pre-existing), 0 failed, 0 errors.** Earlier full runs during the phase are disclosed rather than discarded: run 1 stopped on an ORM/migration drift test because models were edited while it ran (a timing artifact — the same test passes on current code); run 2 surfaced the real §17 bug plus two `caplog` errors caused only by my `-p no:logging` flag (both tests pass without it).

## 19. Remaining limitations and recommended Phase F scope

**Limitations (disclosed, not hidden):**
- The audit chain is tamper-evident only; without an external anchor, a full recompute or tail truncation by someone with DB write access is undetectable. No keyed HMAC yet (needs a key-management decision).
- History before the migration is attested only from the migration onward.
- Sealing adds ≈ 6 ms per auditing write; a crash between commit and seal leaves rows unsealed until the next seal (reported, never counted as verified).
- Donation verification is a records check, not a clinical result; donations without `hospital_id` are admin-only forever unless the donor re-logs them linked.
- Certificates issued before Phase E were issued without verification and remain valid until an admin revokes them.
- Partial returns of a failed shipment cannot be restocked (all-or-nothing; record via QUARANTINED). REDIRECTED is recorded, not verified.
- Legacy verification documents have no checksum. Document integrity covers storage tampering, not a coordinated rewrite of both storage and DB row.
- Units created before Phase E have no `expiry_rule_ref`.
- Plasma post-thaw 24 h window still not tracked (Phase 35 limitation, unchanged).
- Admin "unlinked donations" queue is a sequential scan (fine at current size).

**Recommended Phase F (not started):** a scheduled external anchoring job for the audit head (+ optional HMAC with an externally held key); a periodic straggler sealer/alert on `unsealed > 0` for N minutes; partial-return reconciliation with per-unit outcomes; donor-side "link this past donation to a facility" flow; the real-time event layer already planned as Phase F (push for request/fulfilment/verification/reconciliation changes instead of polling); wiring `scripts/check_invariants.py` into CI against the migrated e2e database; Vitest in CI (still not run in CI per the Phase 35 matrix).

## Completion gate

| Condition | Status |
|---|---|
| PARTIALLY_FULFILLED is real (quantity-backed), not cosmetic | ✔ |
| Frontend quantities never trusted | ✔ (server computes; client cannot set) |
| Races A–D on real Postgres | ✔ |
| Donor can't verify own donation; foreign hospital can't verify | ✔ |
| Self-attestation never labelled "verified" | ✔ |
| Certificates gated on verification | ✔ |
| Documents never overwritten; versioned; checksummed | ✔ |
| No unsourced clinical rules; provenance machine-readable & pinned | ✔ |
| Audit chain documented as tamper-evident, not immutable | ✔ |
| No naive SELECT-latest-hash; concurrency tested | ✔ |
| No fake audit events | ✔ (only real actions are audited; perf probes are labelled, dev-only) |
| Authorization not weakened | ✔ (only additions; full authz suites pass) |
| Failed shipments never auto-recovered; units never silently available | ✔ |
| Shipment component honoured in UI | ✔ |
| Cross-domain invariants documented and executable | ✔ |
| Migrations up/down/fresh verified | ✔ |
| Performance measured | ✔ |
| Full regression run, failures investigated not suppressed | ✔ (§17, §18) |
| Docs written | ✔ |
