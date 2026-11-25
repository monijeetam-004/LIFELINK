# Audit remediation — traceability matrix (all 116 claims)

**Source audit:** `LIFELINK_PAPER_VS_CODE_AUDIT.md`, 2026-10-31, run against HEAD `cf7ee37` (tag `qa-final-2026-10-20`).
**Remediation branch:** `finalize/audit-remediation-2026-10-31`.
**Final test results:** see [FINAL_REMEDIATION_REPORT.md §7](FINAL_REMEDIATION_REPORT.md#7-tests-that-were-actually-run).

## How to read this table

**Audit verdict:**

* V = verified
* PV = partially verified
* NV = not verified
* C = contradicted
* NI = not implemented
* NA = not applicable

**Severity** comes from the audit's prioritised gap list (§8): H = high, M = medium, L = low. A dash means the claim was verified, so there was no gap.

**Status after remediation:**

| Status | Meaning |
|---|---|
| **FIXED+VERIFIED** | Code or paper changed, and a test or run executed on 2026-10-31 shows the new behaviour. |
| **PAPER CORRECTED** | The code was already right, or cannot be made to match; the paper now states what the code does. |
| **IMPLEMENTED, PARTLY VERIFIED** | Code changed and unit/integration-tested, but one stated part is not verified (named in the row). |
| **UNCHANGED, RE-VERIFIED** | No change was needed. The claim's tests are part of the final full regression run. |
| **HISTORICAL ONLY** | Evidence is a dated earlier run that was not repeated. |
| **EXTERNAL PENDING** | Needs a real third-party service that is not reachable from this sandbox. |
| **NOT FIXED** | Deliberately not changed; the reason is given. |

**Test files added in this remediation (backend):**

* `test_audit_d4_distance_policy.py` (9 tests)
* `test_audit_c15_c21_c20_fulfillment.py` (10)
* `test_donor_shortlist.py` (13)
* `test_audit_e4_checkpoint_operations.py` (6)
* 2 new tests in `test_gap_neo4j.py`

**Frontend:** `DonorShortlistPanel.test.tsx` (5 tests); `Live.test.tsx` (forecast) extended.

---

## A. Architecture and infrastructure

| ID | Sev | Audit | What was done | Files | Evidence | Status / remaining limitation |
|---|---|---|---|---|---|---|
| A1 | L | PV | The count changed: 169 operations = 168 in 19 routers (new `shortlists` router) + `GET /` on the app. The paper now says so. | `main.tex` §III-A, Fig. 1 | `app.openapi()` → 169; 19 router modules | **PAPER CORRECTED** |
| A2 | — | V | One new table, `donor_contact_attempts`, so 40 tables. Paper updated. | migration `f9a0b1c2d3e4`; `models/donor_contact.py` | migration check in the final suite | **FIXED+VERIFIED** (count updated) |
| A3 | — | V | One new migration, `f9a0b1c2d3e4`, so 32 in one chain. | `alembic/versions/` | `alembic heads` = 1; `alembic check` clean; the native clean start migrated an empty DB to `f9a0b1c2d3e4` with schema = models | **FIXED+VERIFIED** |
| A4 | — | V | — | — | final suite | **UNCHANGED, RE-VERIFIED** |
| A5 | — | V | — | — | final suite; native clean start (worker delivered mail) | **UNCHANGED, RE-VERIFIED** |
| A6 | — | V | The monitor also times out unanswered donor invitations and writes due checkpoints. | `workers/deadline_monitor.py` | `test_timeout_marks_unanswered_and_escalates`, `test_deadline_monitor_writes_due_checkpoints` | **FIXED+VERIFIED** |
| A7 | — | V | — | — | final suite (real MinIO, clamd); native clean start | **UNCHANGED, RE-VERIFIED** |
| A8 | M | PV | A graph answer is now also compared with the PostgreSQL optimum. A stale projection that misses a better route, or misses a reachable source, falls back to the relational result. | `services/pathway_planner.py` | `test_planner_rejects_a_stale_graph_that_misses_a_newer_better_route`, `…_misses_a_reachable_source` (driver replaced) | **IMPLEMENTED, PARTLY VERIFIED** — never run against a real Neo4j server |
| A9 | M | NV | No change. The real-server test is still skipped. | — | `test_real_neo4j_paths_equal_relational_paths` skipped | **EXTERNAL PENDING** |
| A10 | — | V | 63 route definitions = 62 pages + `*` catch-all; the counting rule is now stated. | `App.tsx` | `grep -c path=` → 63 | **UNCHANGED, RE-VERIFIED** |
| A11 | L | **C** | Fig. 1 now says "63 routes (62 pages + catch-all)". | `main.tex` (TikZ source; the PDF was rebuilt) | rebuilt PDF, page 3 | **FIXED+VERIFIED** |
| A12 | — | V | — | — | final suite | **UNCHANGED, RE-VERIFIED** |
| A13 | — | V | — | — | final suite | **UNCHANGED, RE-VERIFIED** |
| A14 | — | V | — | — | final suite | **UNCHANGED, RE-VERIFIED** |
| A15 | — | V | The shortlist panel uses the existing mechanism. Its query key falls under the `blood-requests` / `emergencies` prefixes, so live events refresh it. It polls every 20 s only while the stream is down. | `DonorShortlistPanel.tsx` | vitest | **UNCHANGED, RE-VERIFIED** |
| A16 | — | NV | Fig. 1 now says "Mailpit (tested) / Gmail (pending)". | `main.tex` | Mailpit real in the final suite and the native clean start | **EXTERNAL PENDING** (Gmail) |
| A17 | — | NV | SMS is refused for synthetic (`@synthetic.lifelink.example`) accounts. Fig. 1 now shows MSG91 as pending. | `services/sms_service.py` | `test_simulator_is_off_by_default_admin_only_and_synthetic_only` | **EXTERNAL PENDING** (MSG91) |
| A18 | — | NV | Shown in Fig. 1 as optional and pending. | `main.tex` | — | **EXTERNAL PENDING** (live geocoder) |
| A19 | — | V | Container clean start re-run on 2026-10-31: 11 PASS, 1 BLOCKED (Neo4j). The first run that day found a **new defect**: the worker inherited the checkpoint key without a path and could not start. Fixed: the worker gets no key, and a test covers every `.env`-reading service. The harness now generates the key and tears down with a throwaway value. | `docker-compose.yml`, `docker_clean_start.sh`, `test_audit_e4_checkpoint_operations.py` | `docker/docker_clean_start_run.log`; failed first run kept | **FIXED+VERIFIED** (locally built base images; official images pending) |

## B. Blood-domain correctness

| ID | Sev | Audit | What was done | Files | Evidence | Status / remaining limitation |
|---|---|---|---|---|---|---|
| B1–B5 | — | V | — | — | final suite | **UNCHANGED, RE-VERIFIED** |
| B6 | L | PV | The red-cell 42-day value is now cited to AABB COI and the ARC Compendium, as in the code. Lifeblood is kept for plasma and the 7-day platelet note. | `main.tex`, `references.bib` | rebuilt PDF | **PAPER CORRECTED** |
| B7–B15 | — | V | Quantity and inventory invariants were rechecked. Two new invariants: 46, a handed-off reservation is closed and its shipment serves the same emergency; 47, a `RECOVERY_REQUIRED` plan holds no active reservation. | `services/invariant_check.py` | final suite; `check_invariants` on the demo DB | **UNCHANGED, RE-VERIFIED** |
| B16 | — | V in HEAD | The D1 lock is kept. The shortlist's confirm step re-checks "no other active commitment". | `services/donor_commitment.py` (unchanged) | `test_qa_round_races.py` in the final suite | **UNCHANGED, RE-VERIFIED** |
| B17, B18 | — | V | — | — | final suite | **UNCHANGED, RE-VERIFIED** |
| B19 | — | NA | — | — | — | NA |
| B20 | — | NA | Shortlist responses expose no medical notes, phone numbers or coordinates, only a rounded distance and the basis. Viewers who do not manage the request get 404. | `api/routes/shortlists.py`, `schemas/shortlist.py` | `test_access_control_and_privacy` | **FIXED+VERIFIED** for the new surface. Field-level encryption was **not** added (see final report §10) |

## C. Emergency pathway planning

| ID | Sev | Audit | What was done | Files | Evidence | Status / remaining limitation |
|---|---|---|---|---|---|---|
| C1–C14 | — | V | — | — | final suite | **UNCHANGED, RE-VERIFIED** |
| C15 | M | PV | A backup is promoted only together with a successful reservation, inside one SAVEPOINT. Otherwise the next backup is tried, then a fresh round. If nothing can be reserved, the emergency is `RECOVERY_REQUIRED`, with an event, an audit entry and a coordinator notification. It never appears secured. A partial reservation inside a failed attempt is rolled back. | `services/emergency_pathway_service.py`, `schemas/pathway.py` (`reservation_status`) | `test_backup_whose_units_cannot_be_reserved_is_skipped_for_the_next`, `test_nothing_reservable_means_recovery_required_never_a_false_secured_state`, `test_partial_reservation_inside_a_failed_attempt_is_rolled_back`, `test_unreserved_plans_fail_over_exactly_as_before`; pre-existing concurrency test | **FIXED+VERIFIED** |
| C16–C19 | — | V | — | — | final suite | **UNCHANGED, RE-VERIFIED** |
| C20 | M | NI | A shipment created by the hand-off is linked to its emergency and plan. If it fails or is cancelled, the emergency is re-planned for the missing units only. A duplicate failure event changes nothing. Route disruption during execution is reported, not silently re-planned. Shipments not linked to an emergency keep reconciliation only. | `services/pathway_handoff_service.py`, `api/routes/shipments.py` | `test_handoff_dispatch_fail_replans_and_duplicate_failure_is_harmless`, `test_route_disruption_during_execution_does_not_replan_silently` | **FIXED+VERIFIED** |
| C21 | M | PV | New `POST /emergencies/{id}/pathway/handoff`: in one transaction, each delivery leg's reserved units move to a new shipment (a TRANSFER ledger row; never released). A local-stock leg keeps its reservation. The hand-off is idempotent and all-or-nothing. Shipments carry `emergency_id` and `pathway_id`. The clean-start workflow uses it. UI: a "Hand off to shipment" button. | `pathway_handoff_service.py`, `inventory_service.py`, `models/shipment.py`, `DecisionTransparency.tsx`, `clean_start_check.py` | `test_hand_off_transfers_the_reservation_without_releasing_it`, `test_local_stock_leg_…`, `test_hand_off_requires_a_secured_valid_plan_and_is_all_or_nothing`, `test_concurrent_handoffs_create_one_set_of_shipments`; native clean start 2026-10-31 `critical_workflow` PASS | **FIXED+VERIFIED** |
| C22 | — | V | — | — | final suite | **UNCHANGED, RE-VERIFIED** |

## D. Algorithms and explainability

| ID | Sev | Audit | What was done | Files | Evidence | Status / remaining limitation |
|---|---|---|---|---|---|---|
| D1 | — | V | The four lexicographic strategies are unchanged. The shortlist is a separate, named policy (`CONFIDENCE_SHORTLIST`). | `core/matching_engine.py` (unchanged) | final suite | **UNCHANGED, RE-VERIFIED** |
| D2 | — | V | — | — | — | **UNCHANGED, RE-VERIFIED** |
| D3 | M | V (code) / clinical concern | The interval is now a cited, configurable policy. `IN_DCR_2020` is the default: India, Drugs and Cosmetics (Second Amendment) Rules 2020, G.S.R. 166(E), Schedule F Part XII-B, H(4), 90 days (male) / 120 days (female). Sex is not recorded, so **120 days applies to all donors**. `US_21CFR630` (56 days) remains selectable. Flagged for clinical review. | `core/eligibility.py`, `core/config.py`, `main.tex`, `references.bib` | final suite; `test_create_appointment_rejects_date_before_eligibility` now follows the policy | **IMPLEMENTED, PARTLY VERIFIED** — the source was read in a secondary copy of the gazette text; clinical validation is pending |
| D4 | **H** | **C** | One shared policy, `core/distance_policy.py`, now serves blood-request alerts, SOS alerts, ranking and the shortlist. The 50 km limit is inclusive. A donor with an unknown location is never treated as near. A request with no location alerts nobody unless `DONOR_ALERT_ALLOW_UNSCOPED=true`. Withheld alerts are recorded as events. The admin override needs a reason and is audited. Exact coordinates are never exposed. The SOS form gained a City field; both forms say that without a location no donor is alerted. | `distance_policy.py`, `notification_service.py`, `blood_requests.py`, `emergencies.py`, `matching.py`, `SosModal.tsx`, `BloodRequestModal.tsx` | `test_audit_d4_distance_policy.py` (9: boundary below/at/above, missing coordinates, stale location, no-location request, SOS, override); walkthrough scenario E (an 80 km donor got 0 alerts); Playwright alert specs now file with a city | **FIXED+VERIFIED** |
| D5 | — | V | The admin can now actually reach the ranking view; before this, an admin got 404 there. | `matching.py` | `test_ranking_override_is_admin_only_needs_a_reason_and_is_audited` | **FIXED+VERIFIED** |
| D6 | — | V | Shortlist entries also carry factor values and a short explanation. | `donor_shortlist_service.py` | `test_activity_and_weights` | **UNCHANGED, RE-VERIFIED** |
| D7 | — | NA | The weighted score is for the **shortlist only**, labelled provisional. The four strategies stay lexicographic, and the paper says both. | `main.tex` §III-D2 | — | NA (documented) |
| D8, D9 | — | V | The shortlist breaks ties by id. | — | — | **UNCHANGED, RE-VERIFIED** |

## E. Auditability and security

| ID | Sev | Audit | What was done | Files | Evidence | Status / remaining limitation |
|---|---|---|---|---|---|---|
| E1 | — | V | — | — | final suite | **UNCHANGED, RE-VERIFIED** |
| E2 | L | PV | The paper now states the trigger semantics exactly: DELETE, TRUNCATE, changes to sealed rows and content changes are rejected; the one-time seal is allowed. | `main.tex` §III-J | `test_guard_refuses_update_delete_truncate_of_audit_entries`, `test_guard_freezes_unsealed_content_but_lets_the_sealer_seal` | **PAPER CORRECTED** |
| E3 | M | PV | `AUDIT_CHECKPOINT_REQUIRED` makes the API refuse to start without a usable key and path. The container stack sets it and requires `AUDIT_CHECKPOINT_KEY` from `.env`, never committed. The log lives on its own volume that the database never mounts. The deadline monitor writes due checkpoints. Readiness reports checkpoint status. Verification reports what it can vouch for. Setup and rotation steps: `docs/AUDIT_CHECKPOINTS.md`. | `config.py`, `audit_checkpoint_service.py`, `health.py`, `deadline_monitor.py`, `docker-compose.yml`, `Dockerfile`, `.env.example` | `test_audit_e4_checkpoint_operations.py` (6); `test_hardening_audit_chain.py` (recompute, truncation, forged checkpoints, rotation) | **FIXED+VERIFIED**: tests, and the 2026-10-31 container run (readiness: `enabled: true, required: true, log_verifies: true`). Native/dev defaults remain opt-in, with a readiness warning |
| E4 | **H** | **C** | The paper's threat-model sentence is rewritten. The chain alone detects edits, deletions and reordering, but not full recomputation or truncation. Those are detected only with checkpoints, which are required in the container stack. The checkpoint log is local to the deployment, so a host owner could defeat it; independent anchoring is listed as future work. | `main.tex` §III-J, Limitations | rebuilt PDF | **FIXED+VERIFIED** (claim now accurate). **Remaining:** no external anchor |
| E5–E11 | — | V | New endpoints use the same origin check, RBAC and object-level 404s. | `shortlists.py` | `test_access_control_and_privacy`; final suite | **UNCHANGED, RE-VERIFIED** |
| E12 | — | V | — | — | — | **UNCHANGED** (historical round) |

## F. Frontend and operational workflows

| ID | Sev | Audit | What was done | Files | Evidence | Status / remaining limitation |
|---|---|---|---|---|---|---|
| F1 | — | V | The shortlist panel was added to the patient/family request drawer, the emergency drawer and the hospital request page. The hand-off button and the "securing" state were added to the decision screen. | `DonorShortlistPanel.tsx`, `RequestTrackDrawer.tsx`, `EmergencyTrackDrawer.tsx`, `RequestManagementDetail.tsx`, `DecisionTransparency.tsx` | `DonorShortlistPanel.test.tsx` (5); browser spec `e2e/remediation_shortlist_demo.spec.ts` (4 scenarios × 3 widths: state text, labels, no overflow, axe 0 violations; `docs/QA/evidence/remediation-2026-10-31/screenshots/`) | **FIXED+VERIFIED** for the hospital page. Not verified: the hand-off button in the browser (API-verified in the walkthrough only), and the patient/family drawers |
| F2–F8 | — | V | — | — | final suite | **UNCHANGED, RE-VERIFIED** |
| F9 | L | PV | Both baselines are shown, each with its own error and verdict. A mixed result is stated per baseline, never as one overall claim. | `Analytics.tsx` | `Live.test.tsx` (2 tests) | **FIXED+VERIFIED** |
| F10 | — | V | — | — | final suite | **UNCHANGED, RE-VERIFIED** |
| F11, F12 | — | NA | — | — | — | NA |

## G. Testing and experimental evidence

| ID | Sev | Audit | What was done | Files | Evidence | Status / remaining limitation |
|---|---|---|---|---|---|---|
| G1–G3 | M | PV | All three suites were re-run on the remediated code. The paper uses those counts and dates them; earlier counts are named as earlier. | `main.tex` macros | final report §7 | **FIXED+VERIFIED** |
| G4 | — | V | All 169 operations were called in the 2026-10-31 run. Four were seen only with error statuses: the graph resync (no Neo4j) and three shortlist operations, for which a success-path test was added afterwards. | `main.tex`, `test_donor_shortlist.py` | `backend_run2_960e749/routes_final.log` | **FIXED+VERIFIED** (the graph resync success path is external pending) |
| G5 | — | V | The new shortlist panel was audited with axe on 2026-10-31: hospital request page, 4 scenarios × 375/768/1280 px, 0 violations. The existing screens were not re-audited. | `e2e/remediation_shortlist_demo.spec.ts` | `screenshots/axe.jsonl` | **FIXED+VERIFIED** (new panel); **HISTORICAL ONLY** (other screens) |
| G6 | — | V | — | — | final suite | **UNCHANGED, RE-VERIFIED** |
| G7 | L | PV | Native clean start re-run on 2026-10-31 against real MinIO and Mailpit: 12 checks, 11 PASS, 1 BLOCKED (Neo4j), 0 FAIL. | `native_app_check.sh` | `docs/QA/evidence/remediation-2026-10-31/native_clean_start_run.log` | **FIXED+VERIFIED** |
| G8 | L | **C** | The container clean start was re-run on 2026-10-31: 12 checks, 11 PASS, 1 BLOCKED (Neo4j). The paper reports this, dated. | `main.tex` | `docker/docker_check_result.json` | **FIXED+VERIFIED** |
| G9 | — | V | — | — | — | **UNCHANGED** |
| G10 | L | PV | Table V is replaced by the 2026-10-20 run (27/27, real MinIO and Mailpit), labelled with its date and code version. The 23/23 stand-in round is named as such. | `main.tex` | `docs/QA/evidence/failure-injection/run2_after_fixes.txt` | **HISTORICAL ONLY** (not re-run on the remediated code) |
| G11 | — | V | — | — | — | **UNCHANGED** |
| G12 | — | V | The seed-1337 data are unchanged. A **separate** synthetic shortlist dataset was added (seed 20261031, 400 donors). | `scripts/seed_shortlist_demo.py` | final report §6 | **UNCHANGED** (E1–E4 data untouched) |
| G13 | — | V | Not re-executed. The planner changed: failover now reserves, and the graph cross-check was added. E1 is labelled with its date. | `main.tex` | raw rows (audit) | **HISTORICAL ONLY** |
| G14 | L | PV | The demo-hospital attribution is now worded as interpretation. The stored output keeps aggregates only. | `main.tex` | — | **PAPER CORRECTED**; per-trial data not added |
| G15 | — | V | E3 ran under the 56-day interval on 2026-10-13. The paper says so; E3 was not re-run under the new policy. | `main.tex` | — | **HISTORICAL ONLY** |
| G16 | — | V | — | — | — | **HISTORICAL ONLY** |
| G17, G18 | M | V (historical) | Figures and text are now labelled "measured 2026-10-06/07 on an earlier code version; not re-measured". | `main.tex` | — | **HISTORICAL ONLY** |
| G19 | L | PV | The Docker CMD bound (`--timeout-graceful-shutdown 5`) is kept. The paper's shutdown row is dated 2026-10-20 (5.22 s). | `backend/Dockerfile` | Dockerfile check in `test_qa_round_races.py` | **HISTORICAL ONLY** (timing not re-measured) |
| G20 | — | PV | The external product ratings remain author judgement; the paper says so. | — | — | **NOT FIXED** (cannot be verified from code) |
| G21 | — | NA | — | — | — | NA |
| G22 | — | V | The abstract keeps "a median of 20 ms" for the historical E1 run. | — | — | **HISTORICAL ONLY** |
