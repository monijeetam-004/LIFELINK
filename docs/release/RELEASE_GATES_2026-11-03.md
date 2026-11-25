# LifeLink release gates: 2026-11-03

* **Branch:** `finalize/audit-remediation-2026-10-31`.
* **HEAD:** `2cfe39a`, verified as the reported HEAD. The working tree at the start held no uncommitted changes.
* **This round's changes are uncommitted.** The brief allows no commit without the owner's authorisation; see §2.
* **Evidence:** `docs/QA/evidence/release-gates-2026-11-03/` (round 1, kept unchanged) and `docs/QA/evidence/privacy-round2-2026-11-03/` (round 2).
* **Privacy remediation round 2** (same day, also uncommitted) is recorded in **§0**. Where §0 and the round-1 rows below disagree, §0 is current; the round-1 rows are kept as they were reported, with corrections marked.
* **Environment:** a 2-vCPU, 7.7 GB Linux sandbox. PostgreSQL 16, Redis 7.0.15, clamd (ClamAV 1.5.4), and the official MinIO and Mailpit binaries all run as native processes. Docker runs on locally built base images, because the sandbox cannot pull from registries. **No outbound access** to `control.msg91.com`, `smtp.gmail.com`, `nominatim.openstreetmap.org`, `neo4j.com` or `oauth2.googleapis.com` (all checked: connection refused or no DNS). No credentials for any of them.

**Status words:**

| Status | Meaning |
|---|---|
| PASS | Executed and passed, with evidence |
| FAIL | Executed and failed, or a defect remains |
| BLOCKED | A dependency or access is missing |
| PENDING APPROVAL | Needs an authorised human |
| N/A | Justified as not applicable |

## 0. Privacy remediation round 2 (2026-11-03)

> **Note for this repository (2026-11-25).** Rounds 2 and 3 were run in a disposable workspace, and their evidence folders (`docs/QA/evidence/privacy-round2-2026-11-03/`, `docs/QA/evidence/round3-validation-20261103T1903Z/`) were not kept. Round 3 (validation only, no code changes) re-ran the native and Docker clean starts (11 PASS, 1 BLOCKED for Neo4j, 0 FAIL each), failure injection (27/27), the demo walkthrough and browser spec (5/5), latency and load tests (0 failures, 0 non-2xx), with the same release decision. The round-2 code was re-applied from the session record onto the 2026-11-03 snapshot and re-tested; that evidence is in `docs/QA/evidence/rebuild-2026-11-25/`.

**Why.** A review of round 1 confirmed, with probes against the real API, that the re-check defect was not closed (the reason was still in the request/emergency event history), and that two further fields let patients and family recover finer distances than the whole-km figure. Details and tests: `PRIVACY_SECURITY_REVIEW.md` §3b.

**Status of each item** (implemented = code changed; tested = a regression test against the real API, in the full run):

| # | Item | Implemented | Tested | Status |
|---|---|---|---|---|
| R2-1 | Re-check reason removed from the event history of blood requests and emergencies (generic notes, no donor ref); reason kept on the shortlist for hospital/admin and in the admin-only `shortlist.confirmation` audit meta | yes | yes: patient, linked family, failed donor, other responding donor, candidate donor, hospital, admin on `GET /emergencies/{id}`; patient, family, failed and declining donor, hospital on `GET /blood-requests/{id}`; hospital on `GET /hospitals/me/requests/{id}/events` and `/hospitals/me/activity`; staff detail and audit meta asserted; audit log 403 for every non-admin | **PASS** |
| R2-2 | Travel-time privacy (separate from distance rounding): SUMMARY travel minutes computed from the whole-km distance | yes | yes, on an SOS with coordinates (staff 3.4 km / 5 min, patient and family 3 km / 4 min) | **PASS** |
| R2-3 | Distance rounding with a real measured distance: exact 0.1 km for hospital/admin, whole km (≥ 1) for patient/family; the test fails if the distance is absent | yes (round 1) | yes (round 2; round 1's test was vacuous for distance, because a patient's blood request has no coordinates; that test now asserts the absence explicitly) | **PASS** |
| R2-4 | Score/baseline precision for SUMMARY: 10-point bands for `score`, `priority.score`, `score_at_contact`; baseline null; UI labels "about N/100" | yes | yes (API by role; 2 vitest tests) | **PASS** |
| R2-5 | Reason hiding by contact **status**, not wording; `[sim-wait]` never shown or mislabelled; a re-check reason is cleared when the contact leaves that status | yes | yes: every status × every reason the code writes × with/without `[sim-wait]`; withdrawal after a failed re-check via the API | **PASS** |
| R2-6 | Authorisation on every shortlist endpoint that exposes this data (read/contact/confirm; donor, unrelated patient, unlinked family → 404; non-managing facility → 403 on actions; refused calls change nothing) | (existing) | yes | **PASS** |
| R2-7 | Per-factor evidence statuses, exact coverage and availability status still visible to patients and family (reveal whether a donor has history elsewhere) | **no** | — | **PENDING APPROVAL** (privacy officer + product owner; recommendation in `PRIVACY_SECURITY_REVIEW.md` §3b) |
| R2-8 | Score band granularity (10 points) and whether SUMMARY should show a score at all | yes (10 points, as the minimal change) | yes | **PENDING APPROVAL** (same reviewers) |
| R2-9 | Event notes and contact rows written **before** round 2 keep their old text; no data migration | **no** (no production data touched) | SUMMARY output fails closed for old contact rows (tested in the status grid) | **PENDING APPROVAL** (an authorised operator decides on rewriting old notes) |
| R2-10 | Synthetic evaluation limitation: the latent model resamples history counts but keeps the stored last-response dates, so some profiles are inconsistent | documented only (code unchanged) | — | **Documented**; F1 remains a synthetic illustration; **no real-world effectiveness claim** (F2 BLOCKED) |
| R2-11 | Documentation corrections: round 1 wrongly said P3 was fixed and that distances had been "full float precision" (they were already 0.1 km) | yes | — | **PASS** |

**Round 2 verification** (`docs/QA/evidence/privacy-round2-2026-11-03/`), all on HEAD `2cfe39a` + the uncommitted tree (code diff sha256 prefix `a377b145617a4e53`, unchanged from start to end of the backend run; new test file hash in `untracked_code_sha256_at_backend_run.txt`):

| Check | Result | Evidence |
|---|---|---|
| Backend full suite (real PostgreSQL, Redis, clamd, MinIO, Mailpit) | **1,481 passed, 1 skipped, 0 failed** (1,482 collected; +7 round-2 tests), 40.9 min. The skip is the live-Neo4j test (D1) | `backend_full.log`, `backend_junit_full.xml`, `backend_full.meta` |
| Focused backend tests (shortlist, unified priority, confirmation, emergencies, blood requests and their lifecycle, family links, authorisation) | **170 passed** | `backend_focused.log`, `backend_focused_junit.xml` |
| Role payload inspection: patient, family, three donors, hospital, admin | as expected (table in `PRIVACY_SECURITY_REVIEW.md` §3b) | `role_payloads.json` |
| Ruff | exit 0 | `ruff.txt` |
| mypy (not a configured gate) | 113 errors at HEAD and 113 on the working tree; **no new error** (one introduced during the round was fixed) | `mypy_vs_head.txt` |
| `tsc -b --noEmit`, ESLint, production build | exit 0 | `tsc.txt`, `eslint.txt`, `build.txt` |
| Vitest | **184 / 184** (23 files; +2) | `vitest_full.log` |
| Playwright, CI-identical harness | **107 passed, 7 skipped, 0 failed** (same 7 skips as round 1: stack-only and demo-stack specs, not re-run separately in round 2); invariants OK after E2E | `playwright_ci_harness.log`, `playwright/` |
| `git diff --check` | clean (exit 0); untracked text files: no trailing whitespace or conflict markers | `git_diff_check.txt` |
| Secrets scan of the round-2 changes; MinIO credentials absent from evidence | no matches | `secrets_scan.txt` |
| Not re-run in round 2 | container and native clean starts (A8, A9), load test (F4), failure injection (F5), demo walkthrough (A7), latency (F3). Round 2 changed only output shaping, an event note, an audit field and a reason reset; those round-1 results are for the round-1 tree | — |

## 1. Release matrix

| # | Item | Status | Evidence (`docs/QA/evidence/release-gates-2026-11-03/`) |
|---|---|---|---|
| A1 | Checkout verified (branch, HEAD `2cfe39a`, clean tree at start; unified-priority commits `ecf5c72`, `2c80172` and migration present) | **PASS** | §3 Gate A |
| A2 | Migration `a7b8c9d0e1f2` checks, all on the scratch DB `lifelink_migtest`:<ul><li>one head;</li><li>three columns, all nullable;</li><li>no data operations (so no backfill);</li><li>upgrade → `alembic check` clean;</li><li>downgrade -1 removes the columns;</li><li>re-upgrade clean</li></ul> | **PASS** | `migration_check.txt` |
| A3 | Backend full suite (real PostgreSQL, Redis, clamd, MinIO, Mailpit): **1,474 passed, 1 skipped, 0 failed**, 27 min. The skip is the live-Neo4j test, which is expected (see D1) | **PASS** | `backend_final.log`, `backend_junit_final.xml`, `backend_final.meta` |
| A4 | All 169 API operations called, each with a 2xx, except `POST /admin/graph/resync` (needs Neo4j) | **PASS** | `api_coverage.txt` |
| A5 | Ruff, `tsc --noEmit`, ESLint, production build: all exit 0. Vitest **182 / 182** (23 files) | **PASS** | `ruff.txt`, `tsc.txt`, `eslint.txt`, `build.txt`, `vitest_final.log` |
| A6 | Playwright, CI-identical harness: **107 passed, 7 skipped, 0 failed**; invariants OK after E2E. The 7 skips are 2 container/clean-start-only specs and 5 demo-stack specs, all run separately (A7, A9) | **PASS** | `playwright_ci_harness.log`, `playwright/` |
| A7 | Demo stack: walkthrough A–G as specified, and browser spec **5 / 5** (axe 0 violations at 375/768/1280, breakdown sums to the score) | **PASS** | `demo_walkthrough.txt`, `shortlist_ui_spec.txt`, `screenshots/` |
| A8 | Native clean start on the final working tree (fresh DB, real MinIO/Mailpit/clamd): **11 PASS, 1 BLOCKED (Neo4j), 0 FAIL** | **PASS** (Neo4j: see D1) | `native/run.log`, `native/native_check_result.json` |
| A9 | Container clean start (fresh source copy and volumes, image build, checks inside the API container, 2 browser specs against nginx, checkpoints required) | **PASS** (Neo4j: see D1). Final tree: **11 PASS, 1 BLOCKED (Neo4j), 0 FAIL**; both browser specs passed; readiness `audit_checkpoints.enabled = true, required = true, log_verifies = true`; worker and monitor healthy | `docker/` (final tree); `docker_at_2cfe39a/` (HEAD before this round's changes: 11 PASS, 1 BLOCKED, both browser specs passed) |
| B1 | Clinical/operational review pack prepared: rules, code locations, rationale, risks, questions, approver roles | **PASS** (preparation) | `docs/release/CLINICAL_OPERATIONAL_REVIEW.md` |
| B2 | Clinical/operational **approval**: interval policy, compatibility, pre-screen criteria, radius, weights | **PENDING APPROVAL** | Needs TMO, BCOL, REG |
| C1 | Server-side access control: roles, 404/403, policy weights hidden, SUMMARY minimal | **PASS** | `test_access_control_and_privacy`, `test_explanation_detail_depends_on_role_and_policy_hides_weights` |
| C2 | Privacy defects P1–P3 fixed (weights and history values leaked to patients and family; full-precision distance; donor-specific re-check reasons), with regression tests | **Round 1 reported PASS; corrected in round 2:** P1 PASS. P2: the distance was already 0.1 km, the whole-km change is real, but its test was vacuous (now R2-3). P3 was **not** fixed: the reason was still in the event history (now R2-1, R2-5). Current status: see §0 | `test_summary_viewers_get_no_weights_history_values_precise_distance_or_reasons`, vitest SUMMARY test; round 2: `test_shortlist_privacy_round2.py` |
| C3 | Logs, audit meta, errors and snapshots inspected: no names, coordinates, free text or medical data; the snapshot is never returned by the API | **PASS** | `docs/release/PRIVACY_SECURITY_REVIEW.md` §1 |
| C4 | Retention period for contact rows and snapshots, donor-name visibility, DPDP assessment; **round 2 adds** R2-7, R2-8, R2-9 | **PENDING APPROVAL** | Needs the privacy officer / DPO and the product owner |
| D1 | Neo4j, real server | **BLOCKED** | No server, no `NEO4J_URI`, no egress to neo4j.com / dist.neo4j.org. 33 graph tests passed with the driver replaced, which is **not** a real test |
| D2 | Gmail SMTP, real delivery | **BLOCKED** | No credentials; `smtp.gmail.com` not resolvable from the sandbox. 51 e-mail tests passed against the **real Mailpit** SMTP server, including Gmail configuration validation |
| D3 | MSG91 SMS, real sandbox delivery | **BLOCKED** | No auth key or DLT template; `control.msg91.com` unreachable. 35 adapter tests passed with a mocked transport (timeouts, retries, failure states) |
| D4 | Geocoder, live service | **BLOCKED** | No `GEOCODER_URL`; Nominatim unreachable. 2 tests passed with a stand-in (success, failure, garbage; no invented coordinates) |
| E1 | Unified model validation:<ul><li>configuration validation;</li><li>fingerprint changes;</li><li>0–100 range and reconciliation over 3,000 random inputs;</li><li>missing-data baseline and coverage;</li><li>unscored fallback;</li><li>unknown availability never shown as confirmed;</li><li>ties;</li><li>current request excluded from history;</li><li>strategies unchanged;</li><li>eligibility before scoring;</li><li>ranking is read-only;</li><li>concurrent contact without duplicates;</li><li>no double acceptance (D1 race tests)</li></ul> | **PASS** | `test_unified_priority.py` (22), `test_donor_shortlist.py`, `test_qa_round_races.py` in the full run |
| F1 | Synthetic comparison without leakage (latent model, 5 seeds × 80 requests), plus the leaked persona model and the independent control, all reported | **PASS** (executed; synthetic only). **Round 2 limitation:** stored last-response dates are kept while counts are resampled, so some profiles are inconsistent (R2-10) | `ranking_comparison.json`, `ranking_comparison.txt` |
| F2 | Real-world effectiveness of the ranking | **BLOCKED** | No real, permitted outcome data exist |
| F3 | Shortlist latency: 5 warm-up + 50 timed calls per mode, 13 eligible donors. p50 83–89 ms, p95 161–170 ms, 0 failures | **PASS** (sandbox observation) | `shortlist_latency.json` |
| F4 | Load test (production runner, 2 workers, demo DB about 1.5k users, generator on the same 2 vCPUs, 20 s per level, 50 SSE streams): C = 1 / 8 / 32 → 114.7 / 180.8 / 185.7 req/s, p95 23 / 130 / 429 ms, **0 non-2xx**, 50 streams on 2 Redis connections | **PASS** (sandbox) | `loadtest.txt` |
| F5 | Failure injection on the final tree (Redis, PostgreSQL, MinIO, clamd, worker, Mailpit stopped and restarted; SIGTERM with an open SSE stream exits in 5.21 s): **27 / 27** | **PASS** | `failure_injection.txt`, `failure_injection_run.log` |
| G1 | Audit hash chain:<ul><li>canonical serialisation and ordering;</li><li>edit, delete and reorder detected;</li><li>append-only trigger;</li><li>concurrent writers never fork;</li><li>HMAC checkpoints detect full recomputation and truncation;</li><li>key rotation (39 tests)</li></ul>Checkpoints enabled and required in the container stack | **PASS** | full-run JUnit (`integration_tests_in_full_run.txt`); A9 readiness output |
| G2 | **Independent anchoring** of the audit log: **not implemented**. The checkpoint log is on the deployment's own volume | **BLOCKED** | Needs an authorised external, independently controlled store (see §4) |
| H1 | Documentation updated; this matrix; review packs; model doc corrected (distance rounding) | **PASS**, with a correction in round 2: the "full precision" statement was itself wrong (R2-11) | `docs/release/`, `docs/UNIFIED_DONOR_PRIORITY.md` |

### Release decision: **NOT READY** (unchanged after round 2)

Round 1: every executable engineering check passed on the round-1 working tree, including both clean starts (A8, A9). Round 2 closed the confirmed privacy leaks (§0) and re-ran the suites listed there; the clean starts were not repeated. Release still requires the following, none of which either round can provide:

* **Human approvals (B2, C4):** clinical and operational approval, and privacy approval, including the round-2 decisions R2-7 to R2-9.
* **Real integrations (D1–D4):** Neo4j, Gmail, MSG91 and the geocoder, each tested against its real service.
* **Independent audit anchoring (G2).**
* **Real-world evidence (F2)** before the unified ordering is relied on clinically.

Nothing here has been treated as a PASS without evidence.

## 2. Changes made in this round (uncommitted)

| File | Change | Gate |
|---|---|---|
| `backend/app/api/routes/shortlists.py` | SUMMARY viewers (patients, family) no longer receive weights, factor values, contributions or components. Distances are 0.1 km (FULL) or whole km (SUMMARY). Donor-specific re-check reasons are replaced by a generic text for SUMMARY viewers | C (defects P1–P3) |
| `backend/app/schemas/shortlist.py` | `PriorityFactorOut.weight` and `contribution` become optional. Additive and backward compatible | C |
| `frontend/src/lib/gapApi.ts`, `frontend/src/components/DonorShortlistPanel.tsx` | Render withheld numbers as "—" | C |
| `backend/tests/test_unified_priority.py` | +3 tests: the SUMMARY privacy contract; random-input property test (score range, coverage, reconciliation, unscored rule); ranking is read-only | C, E |
| `frontend/src/components/DonorShortlistPanel.test.tsx` | +1 test: SUMMARY rendering | C |
| `backend/scripts/evaluate_ranking_strategies.py` | New leakage-free **latent** outcome model, with history and outcomes drawn on separate seeded streams, over 5 seeds | F |
| `docs/QA/tools/shortlist_latency.py` (new) | Endpoint latency tool: warm-up, repetitions, percentiles | F |
| `docs/release/*.md` (new) | This matrix; the clinical/operational and privacy/security review packs | B, C, H |
| `docs/UNIFIED_DONOR_PRIORITY.md`, `docs/remediation/UNIFIED_PRIORITY_REPORT.md` | Corrections: distances are now actually rounded; release-gate pointers | H |

**Round 2 changes (uncommitted):**

| File | Change | Item |
|---|---|---|
| `backend/app/services/donor_shortlist_service.py` | `confirm()` writes generic event notes (no donor ref, no reason). Operational reason constants (`REASON_*`, `OPERATIONAL_REASONS`) used at their write sites. `record_response()` clears a re-check reason when the contact leaves `ELIGIBILITY_RECHECK_REQUIRED` | R2-1, R2-5 |
| `backend/app/services/demo_simulator.py` | Uses `svc.REASON_SIM_TIMEOUT` (same text) | R2-5 |
| `backend/app/api/routes/shortlists.py` | Audit meta `reason` on a failed re-check; `_travel_out` (SUMMARY from whole km); `_score_out` (10-point bands) for `score`, `priority.score`, `score_at_contact`; baseline null for SUMMARY; `_reason_out` by status with fail-closed text | R2-1 – R2-5 |
| `backend/app/schemas/shortlist.py` | `PriorityOut.missing_data_baseline` optional (additive) | R2-4 |
| `frontend/src/lib/gapApi.ts`, `frontend/src/components/DonorShortlistPanel.tsx` | Nullable baseline; banded score shown as "about N/100"; no baseline line | R2-4 |
| `backend/tests/test_shortlist_privacy_round2.py` (new) | 7 tests (role-based API tests and the status grid) | R2-1 – R2-6 |
| `backend/tests/test_unified_priority.py` | The round-1 SUMMARY test's vacuous distance branch now asserts the absence explicitly | R2-3 |
| `frontend/src/components/DonorShortlistPanel.test.tsx` | +2 tests (banded SUMMARY score; FULL unchanged) | R2-4 |
| `docs/release/*.md`, `docs/UNIFIED_DONOR_PRIORITY.md`, `docs/remediation/UNIFIED_PRIORITY_REPORT.md` | §0, corrections, P5 analysis, P6 limitation | R2-7 – R2-11 |
| `docs/QA/evidence/privacy-round2-2026-11-03/` (new) | Fresh evidence; the round-1 evidence folder is unchanged | — |

No migration, no weight or algorithm change, no production data touched.

**Migration:** none in this round. The existing `a7b8c9d0e1f2` was checked (Gate A).

**Rollback** of the unified-priority feature:

* `alembic downgrade f9a0b1c2d3e4` drops the three nullable columns. This loses the stored strategy, model tag and snapshot of each contact; rows and states are kept. It was tested on a scratch database.
* The code before `ecf5c72` has no knowledge of these columns.

## 3. Gate details

### Gate A: checkout and clean start

* **State at the start.** Branch `finalize/audit-remediation-2026-10-31`, HEAD `2cfe39a`, as reported, with no uncommitted changes. The reported feature-test commit `2c80172` and the later documentation commits are in the history. There are no repository agent-instruction files (`CLAUDE.md`, `AGENTS.md`).
* **Code tested.** Every suite in the matrix ran on `2cfe39a` plus the uncommitted working tree. The code diff (`backend`, `frontend`) was hashed when the backend run started (`working_tree_at_backend_run.diff`, sha256 prefix `8adb370204834de1`) and was unchanged at the end.
* **Commands:** as in `docs/QA/REPRODUCE_TESTS.md`, plus `docs/QA/tools/e2e_ci_harness.sh`, `/srv/realsvc/native_app_check.sh` (a sandbox copy of `docs/final-verification/external_validation/scripts/native_app_check.sh`) and `docs/final-verification/external_validation/scripts/docker_clean_start.sh`.
* **Dependencies.** The repository's lockfiles were used: the backend virtual environment from `requirements-dev.lock`, and `npm ci` on an earlier date (`node_modules` present). The container build installs from the hashed lock.
* **Expected skips:** the live-Neo4j test (backend), two stack-only specs and five demo-stack specs (Playwright). Each of the Playwright ones was run in its own environment.

### Gate B: clinical and operational

The review pack is in `docs/release/CLINICAL_OPERATIONAL_REVIEW.md`. It covers the 10 rules (R1–R10), their exact code locations, risks and 7 questions.

**Findings for the reviewers:**

* **R1:** 120 days applies to everyone; apheresis intervals are not modelled.
* **R2:** a whole-blood donor can be shortlisted for plasma or platelet emergencies.
* **R6, R7:** the straight-line 50 km and 45 km/h assumptions.
* **R8:** age, weight, haemoglobin and deferrals are not modelled.
* **R9:** patients and family can confirm a donor for coordination.

No policy was changed.

### Gate C: privacy and security

The review is in `docs/release/PRIVACY_SECURITY_REVIEW.md`. Three defects were found and fixed:

* **P1:** weights and per-donor history values were exposed to patients and family through `priority.factors` and `components`, although the policy endpoint hid the weights.
* **P2:** distances were returned at full precision, while the documentation claimed they were rounded. *(Round 2 correction: they were already 0.1 km; the fix was whole km for patients and family.)*
* **P3:** donor-specific re-check reasons were shown to patients and family. *(Round 2 correction: only the shortlist was fixed; the event history still carried the reason. Closed in round 2, §0.)*

All changes are additive: optional fields become null. API compatibility is kept, and the 0–100 range is unchanged. Retention, donor-name visibility and DPDP applicability are listed for the authorised reviewer.

### Gate D: real integrations

| Integration | Configuration | Timeouts / retries | Executed here | Status |
|---|---|---|---|---|
| Neo4j | `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`; resync `POST /admin/graph/resync`; readiness `dependency_states.neo4j` | `NEO4J_CONNECTION_TIMEOUT_SECONDS=5`; PostgreSQL fallback, plus the stale-projection cross-check | 33 tests with the driver replaced; the clean starts report NOT_CONFIGURED → relational engine | BLOCKED |
| Gmail SMTP | `MAIL_HOST=smtp.gmail.com`, port 587 + STARTTLS, app password (validated at start-up) | RQ job retries; the failed registry; resend endpoint | Real SMTP delivery to **Mailpit** (backend tests, both clean starts, failure injection) | BLOCKED (Gmail itself) |
| MSG91 | `MSG91_ENABLED`, auth key, sender ID, DLT template | `MSG91_TIMEOUT_SECONDS`; worker `retry_sms`; terminal FAILED | Mocked transport only; `scripts/send_test_sms.py` exists for an authorised sandbox | BLOCKED |
| Geocoder | `GEOCODER_URL`, `GEOCODER_TIMEOUT_SECONDS` | Single bounded call; failure → no coordinates | Stand-in tests only | BLOCKED |

Procedures for running the real tests: `docs/final-verification/NEO4J_SETUP.md`, `SMS_SETUP.md`, `docs/LIFELINK_EMAIL.md`. **No real messages were sent and no credentials were used or created.**

### Gate E: scoring model

Added in this round:

* `test_score_range_coverage_and_reconciliation_hold_for_random_inputs`: 3,000 seeded inputs;
* `test_ranking_is_read_only_and_never_changes_any_state`;
* the Gate C privacy test.

The existing tests cover:

* configuration validation;
* fingerprints;
* missing data and the unscored fallback;
* ties;
* history exclusion;
* strategy preservation;
* eligibility before scoring;
* concurrency.

No weight was tuned. The score is still labelled as not a probability.

### Gate F: evaluation and performance

**Leakage in the earlier evaluation.** The seeded response history **and** the simulated "persona" answers both came from one persona per donor, so the earlier result was favoured by construction. That result is kept in the output and labelled as leaked.

**The new latent model.** Each donor gets two latent propensities from its own seeded stream: P(answer) ~ Beta(2, 2) and P(accept | answer) ~ Beta(2, 3). A fresh response history is sampled from them on one stream (30 % of donors have none). Future answers are drawn on another stream, once per (request, donor), so every mode faces the same outcomes. The unified score is recomputed from the sampled history; the four strategies do not read history. Eligibility is unchanged.

**Results:** 80 requests (20 cities × O+/A+/B+/AB+, 2 donors each, median 29.5 eligible) × 5 seeds:

| Mode | Demand met (of 400) | Mean contacts when met | Median batches to first acceptance | Accepted per contact |
|---|---:|---:|---:|---:|
| Unified | 384 | **8.75** | 1 | 0.215 |
| Nearest | 384 | 10.14 | 1 | 0.188 |
| Time-aware | 384 | 10.10 | 1 | 0.188 |
| Time + resilience | 384 | 9.96 | 1 | 0.191 |
| Event-driven | 384 | 10.10 | 1 | 0.188 |

* **Per seed:** the unified mean was lower in all 5 seeds (8.05–9.32 against 9.24–11.40).
* **Safety and coverage:** 0 eligibility violations in every mode; median evidence coverage 0.4.
* **Sensitivity:** with the response and acceptance weights set to zero, the first batch changed in 28 of 80 requests.
* **Control:** with outcomes independent of the donor, all modes are identical, as they must be.

**Round 2 limitation.** The latent model replaces only the invited / answered / accepted counts. The last-response date and last recorded donation (the `activity` factor) are taken as stored, so a donor can have no sampled answers yet score as "responded recently", or the reverse. Some profiles are therefore internally inconsistent, and the comparison does not separate that effect. Documented only; the script was not changed in round 2.

**What this shows, and what it does not.** It shows that **if** past answering predicts future answering, the unified order reaches the needed donors with about 13 % fewer contacts on this synthetic network, with no difference in demand met. It does **not** show that real donors behave that way, and it is not clinical validation.

**Performance** (F3–F5) was measured on this sandbox with the client and server on the same 2 vCPUs. The demo DB is far smaller than the 81k-request volume DB of the historical 2026-10-07 runs, so those figures are **not comparable** and were not re-measured.

### Gate G: audit integrity

**Construction.** `backend/app/core/audit_chain.py` computes `h_i = SHA-256(canon(x_i, i, h_{i-1}))`. A database trigger rejects DELETE, TRUNCATE and changes to sealed or content rows. Optional HMAC-signed checkpoints (`audit_checkpoint_service.py`) are required in the container stack, and the deadline monitor writes them.

**Tests in the full run:** 39 tests across `test_hardening_audit_chain.py`, `test_phase_e_audit_chain.py` and `test_audit_e4_checkpoint_operations.py`. They cover edit, delete and reorder detection; full recomputation and truncation detected by checkpoints; forged and edited checkpoints; key rotation; and concurrent writers.

**Anchoring.** "Independent anchoring" would mean copying each checkpoint to a store the deployment's operators cannot rewrite, for example:

* a WORM / object-lock bucket under separate administration;
* an RFC 3161 timestamping authority;
* a third party.

It is **documented only** (`docs/AUDIT_CHECKPOINTS.md`) and **not implemented**. Without it, someone who controls the host can rewrite both the database and the checkpoint volume. **The log is tamper-evident against database-only attackers, not tamper-proof.**

## 4. Next actions to clear the open gates

| Blocker | Who / what is needed | Exact next step |
|---|---|---|
| B2 clinical/operational approval | A Transfusion Medicine Officer, Blood Centre Operations Lead, and Regulatory/Legal | Review `docs/release/CLINICAL_OPERATIONAL_REVIEW.md`, answer questions 1–7, and sign. Engineering then implements any policy change as configuration or code, with tests |
| C4 privacy approval | Privacy officer / DPO and the product owner | Decide the retention period, donor-name visibility, snapshot contents, and donor notice / DPDP applicability (`PRIVACY_SECURITY_REVIEW.md` §4); and the round-2 decisions R2-7 (factor statuses for patients/family), R2-8 (score band) and R2-9 (old event notes). Engineering then implements them with tests |
| D1 Neo4j | A Neo4j 5.x instance reachable from the test host, with credentials | `NEO4J_TEST_URI=bolt://… NEO4J_TEST_USER=… NEO4J_TEST_PASSWORD=… pytest tests/test_gap_neo4j.py::test_real_neo4j_paths_equal_relational_paths`; then `POST /admin/graph/resync`, stop Neo4j and confirm the fallback (`docs/final-verification/NEO4J_SETUP.md`) |
| D2 Gmail | A dedicated test Google account with an app password, and egress to `smtp.gmail.com:587` | Put the credentials in a git-ignored `.env`. Register a test account to a mailbox you control and check delivery, then a wrong-password failure (`docs/LIFELINK_EMAIL.md`) |
| D3 MSG91 | An MSG91 sandbox auth key, sender ID, an approved DLT template, a consenting test phone number, and egress | `python -m scripts.send_test_sms <consenting test number>`, then a provider-error case (`docs/final-verification/SMS_SETUP.md`) |
| D4 Geocoder | An approved geocoding endpoint (self-hosted Nominatim or similar), its usage policy, and egress | Set `GEOCODER_URL`, then run an SOS with an address, an invalid address, and an endpoint that times out. Check that logs hold no address text |
| G2 audit anchoring | Authorisation and configuration for an independent store | Choose a store (WORM bucket / RFC 3161 TSA). Then implement checkpoint export with verification, and tests |
| F2 real-world evidence | Real, permitted response and fulfilment data (a pilot) | Collect outcomes under the approved policy. Re-run `scripts/evaluate_ranking_strategies.py`, adapted to the real outcome tables, before relying on the unified order |
| Commit | Owner authorisation | Round 1 and round 2 changes are uncommitted. Review `git diff` (and the untracked files listed in `docs/QA/evidence/privacy-round2-2026-11-03/git_status_at_backend_run.txt`) and commit when authorised |
| Clean starts on the round-2 tree | None (engineering) | Re-run A8 and A9 on the final tree before any release candidate is cut |
