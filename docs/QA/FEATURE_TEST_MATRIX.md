# Feature test matrix

**What it maps.** Every workflow area of the audit brief (Phase 4, A–H), every race of Phase 5 and every API operation and frontend route maps to the tests that exercise it and to the evidence of their execution.

**Where the tests live:**

* backend tests: `backend/tests/`, by file and test name;
* browser tests: `frontend/e2e/`, marked "e2e:";
* screenshots: `docs/QA/SCREENSHOT_INDEX.md`, marked "W".

**Status.** All tests named here passed in the final regression (FINAL_AUDIT_REPORT.md §4), unless a row says otherwise.

## 1. End-to-end workflows (brief, Phase 4)

**Visible and persisted state.** The browser tests assert the UI result. The persisted state is asserted either in the same browser test (API read-back) or in the backend tests listed, which run against real PostgreSQL.

### A. Authentication and accounts — **IMPLEMENTED_AND_VERIFIED**

| Item | Tests (backend) | Browser / evidence |
|---|---|---|
| Registration, verification, login, logout, reset | `test_auth_flow.py` (10), `test_password_reset.py` (5), `test_hardening_email_verification.py` (7) | e2e:`qa_evidence` "QA auth" (W01–W06, real Mailpit e-mail and link), e2e:`login` |
| Expired, invalid and reused tokens | `test_token_is_single_use_hashed_and_expiring`, `test_expired_and_invalid_tokens_are_rejected_and_do_not_activate`, `test_reset_password_otp_cannot_be_reused`, `test_expired_password_reset_otp_rejected` | — |
| Session expiry and revocation | `test_session_expires_past_absolute_ttl_cap`, `test_revoked_session_replay_after_logout_rejected`, `…_after_password_reset_rejected`, `test_logout_invalidates_the_session_immediately`, `test_logout_ends_open_stream` | Live probe: captured cookie → 401 after logout |
| Unverified account blocked | `test_unverified_account_is_blocked_server_side_until_verified`, `…_may_still_sign_out_and_change_password` | W04 (API 403 + redirect) |
| Role separation, unauthorised access | `test_authz_phase8.py`, `test_rate_limit_and_authz.py`, `test_phase23_security_hardening.py` | Crawl: **75/75** guarded-route checks denied; live probe |

### B. Donor workflows — **IMPLEMENTED_AND_VERIFIED**

| Item | Tests | Browser / evidence |
|---|---|---|
| Profile and availability | `test_profiles.py`, `test_donor_availability_not_now_state` | W07–W10 |
| Blood-group compatibility | `test_blood_compatibility_matrix_phase9.py` (all 64 pairs, both directions), `test_phase35_blood_component.py` | — |
| Eligibility and last-donation timing | `test_donor_matching.py`, `test_donor_appointments.py`, `test_recently_donated_donor_cannot_accept_emergency` | — (clinical review of the 56-day value required: CLINICAL C10) |
| Ranking and distance limit | `test_matching_engine_phase9.py` (20), `test_donor_distance_limit_below_at_and_above_and_admin_override` | e2e:`gap_live_pages` "decision transparency" (donor recommendation) |
| Stale or unavailable donors | matching-engine stale-availability tests, `test_incompatible_or_unavailable_donor_cannot_accept` | — |
| Accept, decline, duplicate commitment | `test_donor_cannot_accept_two_open_blood_requests_at_once` (sequential); **new:** `test_one_donor_accepting_two_requests_at_once_commits_to_exactly_one`, `…_two_emergencies_at_once…` (concurrent; **defect D1 found and fixed**) | — |

### C. Patient and family — **IMPLEMENTED_AND_VERIFIED**

| Item | Tests | Browser / evidence |
|---|---|---|
| Blood requests and emergency alerts | `test_blood_request_lifecycle_phase10.py` (30), `test_phase12_emergency_sos_engine.py` (27), `test_emergency_network.py` | e2e:`qa_evidence` "QA requests" (W: request, fulfilment), e2e:`phase_j_journeys` |
| Family links and expiry | `test_family.py` (15), `test_phase13_family_link_security.py` (25: pending, rejected, revoked and expired links grant no access; substituted ids) | e2e:`family_link_notifications` |
| Access to another patient's records | `test_unauthorized_patient_access_returns_404`, `test_family_cannot_reach_unlinked_patient_by_substituting_id` | Live probe (cancel another user's request → 404) |
| Deadline warning and escalation | `test_deadline_monitor_warns_flags_escalates_once`; **new:** concurrent passes, exactly once | — |
| Empty, partial and impossible outcomes | `test_partial_when_the_network_cannot_supply_everything`, `test_deadline_classification_boundaries`, `test_phase_e_fulfillment.py` | W (partial, then full fulfilment); e2e:`phase21_error_loading_empty_offline` (empty states) |

### D. Hospital and inventory — **IMPLEMENTED_AND_VERIFIED**

| Item | Tests | Browser / evidence |
|---|---|---|
| Facility verification and revocation | `test_unverified_hospital_cannot_accept_*`, `test_admin_revocation_*`, `test_facility_revocation_fails_over_pathways_sourcing_from_it` | e2e:`gap_live_pages` "admin facilities" |
| Inventory, unit traceability | `test_phase34_blood_unit_lifecycle.py` (27), `test_hospital.py` (13) | W (inventory console, add, reserve and release, invalid release refused) |
| Component and group compatibility | `test_components_are_never_substituted`, `test_planner_uses_only_compatible_types` | — |
| Reserve, allocate, release, cancel; partial and multi-unit | `test_inventory_add_reserve_allocate_release_cycle`, `test_phase_e_fulfillment.py` (races a–d) | W |
| Earliest-expiry allocation | `test_reservation_prefers_the_soonest_expiring_units_fefo`, `test_units_with_no_known_expiry_sort_last_in_fefo_order` | — |
| Expired, quarantined and unavailable units | `test_quarantine_release_and_discard_keep_counts_honest`, `test_reservation_versus_lazy_expiry_race_never_reserves_a_unit_past_its_shelf_life` | e2e:`gap_live_pages` "hospital unit lifecycle" |
| Duplicates and concurrent stock changes | `test_two_simultaneous_reservations_cannot_double_reserve_the_same_units`, `test_release_versus_allocate_race_leaves_no_corrupted_state`, `test_concurrent_emergency_inventory_reservations_never_oversell` | — |

### E. Planning and route resilience — **IMPLEMENTED_AND_VERIFIED** (relational engine; Neo4j BLOCKED)

| Item | Tests | Browser / evidence |
|---|---|---|
| Viable, deadline-risk, partial and impossible plans | `test_deadline_classification_boundaries`, `test_partial_when_…`, `test_multi_facility_plan_fulfils_quantity_no_single_source_can` | e2e:`gap_live_pages` (graph engine, research mode) |
| Missing routes, disconnected facilities | `test_network_is_authoritative_when_both_ends_are_on_it`, `test_off_network_source_gets_a_labelled_straight_line_estimate` | — |
| First, intermediate and final route failure | `test_failure_of_any_route_on_the_path_replans[A-first-route,B-middle-route,C-last-route]` | **New:** e2e:`qa_failover_ui` (admin disrupts the planned route in the UI → old plan INVALIDATED, a new ACTIVE plan avoiding the route (a re-plan in the recorded run), checked in UI, API and timeline) |
| Valid backup promotion; invalid or stale backups | `test_failover_promotes_a_still_valid_backup`, `test_failover_rejects_a_backup_whose_stock_is_gone_and_replans`, `test_failover_rejects_a_backup_from_a_facility_that_lost_verification`, `test_F_failure_of_a_backup_only_route_invalidates_just_that_backup` | — |
| Multiple route failures | `test_concurrent_disruptions_of_two_routes_fail_over_consistently`; **new:** `test_successive_route_failures_promote_backups_then_replan_and_reservations_follow` | — |
| Unrelated failures change nothing | `test_D_unrelated_route_changes_nothing`, `test_E_route_of_another_emergency_does_not_touch_this_one`, `test_disruption_of_an_unrelated_route_does_not_touch_this_emergency` | — |
| Reservation migration and release | `test_reservation_takes_only_units_in_date_at_arrival_and_is_all_or_nothing`, `test_release_never_frees_another_emergencys_reservation`; **new:** the successive-failure test (units follow the plan at every step) | Clean-start `critical_workflow` (Docker and native) |
| ETA, history, explanation, notification | `test_full_route_is_stored_in_order`, `test_plan_round_supersedes_previous_live_rows_and_keeps_one_active` | e2e:`gap_live_pages` "decision transparency" |

### F. Shipments and cold chain — **IMPLEMENTED_AND_VERIFIED** (no physical sensor)

| Item | Tests | Browser / evidence |
|---|---|---|
| Lifecycle and invalid transitions | `test_phase_d_shipment.py` (27) | W (create, dispatch, delay, arrive, receive), e2e:`phase_j_journeys` |
| Concurrent transitions | `test_two_simultaneous_dispatches_exactly_one_succeeds`, `test_two_simultaneous_receives_exactly_one_inventory_transfer`, `test_dispatch_vs_cancel_race_on_a_created_shipment` | — |
| Reconciliation exactly once; return, redirect, quarantine | `test_phase_e_shipment_reconciliation.py` (15, incl. `test_concurrent_returns_restock_exactly_once`, `test_second_decision_is_409`) | W (reconciliation required, then recorded), e2e:`phase_e_domain_integrity` |
| Telemetry validation, duplicates, gaps, sensor failure, excursions | `test_telemetry_validation_rejects_future_and_valueless_readings`, `test_missing_data_is_a_data_gap_and_duplicates_are_idempotent`, `test_cold_chain_evaluation` | — |
| Spoiled, damaged, expired on arrival; no unsafe restock | `test_receipt_records_per_unit_outcomes`, `test_excursion_forces_quarantine_at_receipt`, `test_failed_shipment_cannot_be_restocked_against_bad_telemetry` | — |

### G. Donations, verification, certificates — **IMPLEMENTED_AND_VERIFIED**

| Item | Tests | Browser / evidence |
|---|---|---|
| Submission and authorised verification | `test_phase_e_donation_verification.py` (19) | W (donation → hospital verifies → certificate) |
| Rejection and correction | `test_double_verify_and_double_reject_are_409`, `test_corrections_are_each_audited_and_notified` | — |
| Document versioning and authorisation | `test_phase_e_document_versioning.py` (9), `test_phase20_secure_file_storage.py` (27) | W (documents: infected, wrong type and clean upload; admin review) |
| Certificate only after verification; revocation; protected download | `test_phase15_certificate_security.py` (11), `test_certificates.py`, `test_tampered_expired_and_cross_certificate_tokens_are_refused` | W (certificate download) |

### H. Administration — **IMPLEMENTED_AND_VERIFIED**

| Item | Tests | Browser / evidence |
|---|---|---|
| Identity and facility verification | `test_verifications.py` (19), `test_phase14_verification_security.py` (17) | W (admin approves), live failure injection (un-scanned document cannot be approved) |
| Route management and disruption | `test_concurrent_disrupt_requests_for_the_same_route_only_one_wins`, `test_graph_api.py` | **New:** e2e:`qa_failover_ui` (disrupt and recover in the UI) |
| Oversight of inventory and requests | `test_admin.py` (22), `test_admin_phase6.py` (17) | W (overview, users, facilities, shipments, donors) |
| Ranking override with an audited reason | `test_donor_distance_limit_below_at_and_above_and_admin_override`, `test_override_keeps_unverified_source_and_is_respected_on_revocation` | — |
| Audit chain and admin boundaries | `test_phase_e_audit_chain.py` (18), `test_hardening_audit_chain.py` (14), `test_gap_audit_security.py` | W (audit chain verified: no tampering), e2e:`phase_e_domain_integrity` |

## 2. Concurrency and invariants (brief, Phase 5)

All tests below use real PostgreSQL connections, real threads and barriers.

| Race | Tests | Result |
|---|---|---|
| 1. Two hospitals reserve the last unit | `test_race_a_two_hospitals_race_for_final_units`, `test_two_emergencies_cannot_reserve_the_same_last_units`, `test_concurrent_emergency_inventory_reservations_never_oversell` | No oversell |
| 2. Two requests compete for one donor | **new** `test_one_donor_accepting_two_requests_at_once_commits_to_exactly_one`, `…_two_emergencies_at_once_…` | **Defect D1** found (both accepted); fixed; now exactly one |
| 3. Allocation and cancellation overlap | `test_race_c_allocate_vs_cancel`, `test_dispatch_vs_cancel_race_on_a_created_shipment`, `test_release_versus_allocate_race_leaves_no_corrupted_state` | Consistent |
| 4. Route failure during backup promotion | `test_concurrent_disruptions_of_two_routes_fail_over_consistently`, `test_concurrent_replans_leave_exactly_one_active_pathway`, `test_concurrent_pathway_selection_for_same_emergency_yields_no_active_collision` | Exactly one ACTIVE plan |
| 5. Two workers, same deadline or expiry event | **new** `test_two_deadline_monitor_passes_at_once_warn_and_escalate_exactly_once`; `test_r3_admin_sweep_vs_hospital_reserve_and_allocate_no_deadlock_no_double_count` | Exactly once |
| 6. Shipment failure reconciled twice at once | `test_concurrent_returns_restock_exactly_once`, `test_r1_receive_vs_fail_on_arrived_shipment_has_exactly_one_outcome` | Exactly once |
| 7. Outbox event retried or duplicated | `test_concurrent_writers_get_gap_free_ordered_publication`, `test_redis_outage_keeps_events_and_republishes_in_order`; **new** `test_event_published_twice_reaches_a_live_stream_once` | Delivered once |
| Idempotency under concurrent duplicates | `test_concurrent_identical_idempotency_key_requests_create_exactly_one_emergency`, `test_twenty_concurrent_identical_requests_create_exactly_one_donation`, `test_concurrent_certificate_generation_for_same_donation_yields_exactly_one`, `test_same_email_registered_twice_concurrently_creates_one_account` | Exactly one |

**Cross-table invariants.** `app/services/invariant_check.py` defines **18 executable SQL invariant checks**, numbered as in `docs/LIFELINK_INVARIANTS.md`; the lower-numbered invariants there are enforced by database constraints and tests. They cover no oversell, status/quantity agreement, one ACTIVE plan per emergency, reconciliation of failed shipments, thaw clocks, pathway invalidation records, and more. `scripts/check_invariants.py` also verifies the audit chain.

* Checked by `test_phase_e_invariants.py` (each detector is proven by a deliberate violation).
* Checked at the end of the 35-step scenario `test_gap_e2e_scenario.py`.
* Run live after the browser suite (`python -m scripts.check_invariants`: all OK, FINAL_AUDIT_REPORT.md §4).

**Database constraints.** `CHECK 0 ≤ reserved ≤ units`, partial unique indexes (one ACTIVE pathway per emergency), foreign keys and append-only audit triggers are all in the migrations. Schema-versus-models equality is checked by `alembic check` and `test_alembic_head_schema_matches_orm_metadata`.

## 3. API operations: coverage in the final backend run

**Method.** The status of each operation comes from the route log of the final backend run (`evidence/final/routes_final.log`), as defined in PROJECT_INVENTORY.md §4.

**Result:**

* **154** of 160 operations were observed with both success and 4xx responses.
* **5** were observed with success responses only. Four are public: `GET /`, `/health`, `/health/live` and `/health/ready` (the last also observed returning 503 with Redis or PostgreSQL down, in live failure injection). The fifth is `GET /auth/session`, which answers every caller.
* **1** is `POST /admin/graph/resync`. It needs Neo4j, so it is BLOCKED_BY_ENVIRONMENT: its authorization (401/403) and its honest 503 are verified.
* **No operation is unexercised.**

**What else is verified.** Authorization, validation, persistence, state transitions, idempotency, error bodies and side effects are asserted by the tests named in §1, not by the status codes. `test_phase_h_security.py` additionally sweeps all 160 operations for authentication and Origin enforcement.

| # | Method | Path | Module | Auth | Roles | Origin check | Calls in final run | Status codes observed | Status |
|---|---|---|---|---|---|---|---|---|---|
| 1 | GET | `/admin/analytics/overview` | admin | session | ADMIN | — | 35 | 200×5, 401×4, 403×24, 422×2 | IMPLEMENTED_AND_VERIFIED |
| 2 | GET | `/admin/audit-log` | admin | session | ADMIN | — | 66 | 200×29, 400×1, 401×4, 403×32 | IMPLEMENTED_AND_VERIFIED |
| 3 | POST | `/admin/audit-log/checkpoint` | admin | session | ADMIN | yes | 29 | 200×1, 401×4, 403×23, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 4 | GET | `/admin/audit-log/head` | admin | session | ADMIN | — | 25 | 200×1, 401×4, 403×20 | IMPLEMENTED_AND_VERIFIED |
| 5 | POST | `/admin/audit-log/verify` | admin | session | ADMIN | yes | 46 | 200×11, 401×4, 403×28, 409×1, 422×2 | IMPLEMENTED_AND_VERIFIED |
| 6 | GET | `/admin/blood-requests` | admin | session | ADMIN | — | 45 | 200×15, 401×4, 403×21, 422×5 | IMPLEMENTED_AND_VERIFIED |
| 7 | POST | `/admin/blood-requests/{request_id}/transition` | admin | session | ADMIN | yes | 31 | 200×3, 400×2, 401×4, 403×21, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 8 | GET | `/admin/certificates` | admin | session | ADMIN | — | 35 | 200×10, 401×5, 403×20 | IMPLEMENTED_AND_VERIFIED |
| 9 | POST | `/admin/certificates/{certificate_id}/revoke` | admin | session | ADMIN | yes | 29 | 200×2, 401×4, 403×21, 404×1, 422×1 | IMPLEMENTED_AND_VERIFIED |
| 10 | GET | `/admin/donors` | admin | session | ADMIN | — | 34 | 200×10, 401×4, 403×20 | IMPLEMENTED_AND_VERIFIED |
| 11 | GET | `/admin/emergencies` | admin | session | ADMIN | — | 29 | 200×3, 401×4, 403×21, 422×1 | IMPLEMENTED_AND_VERIFIED |
| 12 | GET | `/admin/facilities` | admin | session | ADMIN | — | 37 | 200×12, 401×4, 403×20, 422×1 | IMPLEMENTED_AND_VERIFIED |
| 13 | GET | `/admin/facilities/{facility_id}` | admin | session | ADMIN | — | 40 | 200×15, 401×4, 403×20, 404×1 | IMPLEMENTED_AND_VERIFIED |
| 14 | POST | `/admin/facilities/{facility_id}/revoke-verification` | admin | session | ADMIN | yes | 26 | 200×1, 401×4, 403×20, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 15 | GET | `/admin/graph/pathway-log` | admin | session | ADMIN | — | 25 | 200×1, 401×4, 403×20 | IMPLEMENTED_AND_VERIFIED |
| 16 | POST | `/admin/graph/resync` | admin | session | ADMIN | yes | 32 | 401×4, 403×24, 503×4 | BLOCKED_BY_ENVIRONMENT (needs Neo4j; authorization and the honest 503 are verified) |
| 17 | GET | `/admin/system/health` | admin | session | ADMIN | — | 31 | 200×7, 401×4, 403×20 | IMPLEMENTED_AND_VERIFIED |
| 18 | GET | `/admin/system/metrics` | admin | session | ADMIN | — | 24 | 200×2, 401×5, 403×17 | IMPLEMENTED_AND_VERIFIED |
| 19 | GET | `/admin/transport-routes` | admin | session | ADMIN | — | 27 | 200×2, 401×4, 403×21 | IMPLEMENTED_AND_VERIFIED |
| 20 | POST | `/admin/transport-routes` | admin | session | ADMIN | yes | 50 | 201×18, 400×2, 401×4, 403×24, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 21 | POST | `/admin/transport-routes/{route_id}/disrupt` | admin | session | ADMIN | yes | 34 | 200×8, 401×4, 403×20, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 22 | POST | `/admin/transport-routes/{route_id}/recover` | admin | session | ADMIN | yes | 27 | 200×2, 401×4, 403×20, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 23 | GET | `/admin/users` | admin | session | ADMIN | — | 62 | 200×25, 401×4, 403×28, 422×5 | IMPLEMENTED_AND_VERIFIED |
| 24 | GET | `/admin/users/stats` | admin | session | ADMIN | — | 26 | 200×2, 401×4, 403×20 | IMPLEMENTED_AND_VERIFIED |
| 25 | POST | `/admin/users/{user_id}/reactivate` | admin | session | ADMIN | yes | 32 | 200×4, 401×4, 403×24 | IMPLEMENTED_AND_VERIFIED |
| 26 | PATCH | `/admin/users/{user_id}/role` | admin | session | ADMIN | yes | 31 | 200×2, 400×1, 401×4, 403×24 | IMPLEMENTED_AND_VERIFIED |
| 27 | POST | `/admin/users/{user_id}/suspend` | admin | session | ADMIN | yes | 40 | 200×9, 400×1, 401×4, 403×24, 422×2 | IMPLEMENTED_AND_VERIFIED |
| 28 | POST | `/auth/change-password` | auth | session | any signed-in | yes | 14 | 200×4, 401×8, 403×1, 429×1 | IMPLEMENTED_AND_VERIFIED |
| 29 | POST | `/auth/forgot-password` | auth | none | — | yes | 14 | 202×13, 403×1 | IMPLEMENTED_AND_VERIFIED |
| 30 | POST | `/auth/login` | auth | none | — | yes | 1809 | 200×1786, 401×15, 403×2, 429×6 | IMPLEMENTED_AND_VERIFIED |
| 31 | POST | `/auth/logout` | auth | none | — | yes | 41 | 204×39, 403×2 | IMPLEMENTED_AND_VERIFIED |
| 32 | GET | `/auth/me` | auth | session | any signed-in | yes | 86 | 200×61, 401×25 | IMPLEMENTED_AND_VERIFIED |
| 33 | POST | `/auth/register` | auth | none | — | yes | 89 | 201×61, 403×1, 409×15, 422×8, 429×3, 503×1 | IMPLEMENTED_AND_VERIFIED |
| 34 | POST | `/auth/reset-password` | auth | none | — | yes | 35 | 200×4, 400×13, 403×1, 429×17 | IMPLEMENTED_AND_VERIFIED |
| 35 | GET | `/auth/session` | auth | optional | — | yes | 3 | 200×3 | IMPLEMENTED_AND_VERIFIED (success path; no 4xx observed) |
| 36 | POST | `/auth/verify-email` | auth | none | — | yes | 30 | 200×17, 400×11, 403×1, 422×1 | IMPLEMENTED_AND_VERIFIED |
| 37 | POST | `/auth/verify-email/resend` | auth | none | — | yes | 6 | 202×5, 403×1 | IMPLEMENTED_AND_VERIFIED |
| 38 | GET | `/blood-requests` | blood_requests | session | any signed-in | — | 9 | 200×8, 401×1 | IMPLEMENTED_AND_VERIFIED |
| 39 | POST | `/blood-requests` | blood_requests | session | FAMILY, HOSPITAL, PATIENT | yes | 303 | 200×9, 201×273, 401×2, 403×10, 404×1, 409×3, 422×3, 429×2 | IMPLEMENTED_AND_VERIFIED |
| 40 | GET | `/blood-requests/{request_id}` | blood_requests | session | any signed-in | — | 60 | 200×44, 401×2, 404×12, 422×2 | IMPLEMENTED_AND_VERIFIED |
| 41 | POST | `/blood-requests/{request_id}/cancel` | blood_requests | session | any signed-in | yes | 27 | 200×13, 401×1, 403×1, 404×5, 409×6, 422×1 | IMPLEMENTED_AND_VERIFIED |
| 42 | POST | `/blood-requests/{request_id}/fulfill` | blood_requests | session | any signed-in | yes | 15 | 200×6, 401×1, 403×1, 404×4, 409×3 | IMPLEMENTED_AND_VERIFIED |
| 43 | POST | `/blood-requests/{request_id}/respond` | blood_requests | session | DONOR | yes | 68 | 200×48, 400×4, 401×1, 403×8, 404×1, 409×6 | IMPLEMENTED_AND_VERIFIED |
| 44 | GET | `/certificates` | certificates | session | DONOR | — | 15 | 200×4, 401×3, 403×8 | IMPLEMENTED_AND_VERIFIED |
| 45 | POST | `/certificates` | certificates | session | DONOR | yes | 76 | 201×56, 401×2, 403×9, 404×3, 409×5, 503×1 | IMPLEMENTED_AND_VERIFIED |
| 46 | GET | `/certificates/verify/{code}` | certificates | none | — | — | 9 | 200×5, 404×4 | IMPLEMENTED_AND_VERIFIED |
| 47 | GET | `/certificates/{certificate_id}` | certificates | session | any signed-in | — | 21 | 200×5, 401×3, 404×12, 422×1 | IMPLEMENTED_AND_VERIFIED |
| 48 | GET | `/certificates/{certificate_id}/download` | certificates | optional | — | — | 36 | 200×9, 401×24, 404×1, 422×1, 503×1 | IMPLEMENTED_AND_VERIFIED |
| 49 | GET | `/donations` | donation_review | session | ADMIN, HOSPITAL | — | 9 | 200×4, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 50 | GET | `/donations/{donation_id}` | donation_review | session | ADMIN, HOSPITAL | — | 11 | 200×2, 401×1, 403×8 | IMPLEMENTED_AND_VERIFIED |
| 51 | POST | `/donations/{donation_id}/reject` | donation_review | session | ADMIN, HOSPITAL | yes | 17 | 200×8, 401×1, 403×7, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 52 | POST | `/donations/{donation_id}/verify` | donation_review | session | ADMIN, HOSPITAL | yes | 75 | 200×62, 401×1, 403×9, 404×1, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 53 | GET | `/donors/me` | donors | session | DONOR | — | 22 | 200×13, 401×2, 403×7 | IMPLEMENTED_AND_VERIFIED |
| 54 | PUT | `/donors/me` | donors | session | DONOR | yes | 227 | 200×221, 401×1, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 55 | GET | `/donors/me/appointments` | donors | session | DONOR | — | 9 | 200×3, 401×1, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 56 | POST | `/donors/me/appointments` | donors | session | DONOR | yes | 31 | 201×20, 400×3, 401×1, 403×6, 404×1 | IMPLEMENTED_AND_VERIFIED |
| 57 | POST | `/donors/me/appointments/{appointment_id}/cancel` | donors | session | DONOR | yes | 14 | 200×3, 401×1, 403×5, 404×4, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 58 | PUT | `/donors/me/availability` | donors | session | DONOR | yes | 12 | 200×6, 401×1, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 59 | GET | `/donors/me/donations` | donors | session | DONOR | — | 10 | 200×5, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 60 | POST | `/donors/me/donations` | donors | session | DONOR | yes | 151 | 200×37, 201×96, 400×2, 401×2, 403×9, 404×1, 409×1, 422×3 | IMPLEMENTED_AND_VERIFIED |
| 61 | GET | `/donors/me/requests` | donors | session | DONOR | — | 10 | 200×5, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 62 | GET | `/donors/me/responses` | donors | session | DONOR | — | 16 | 200×9, 401×2, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 63 | GET | `/emergencies` | emergencies | session | any signed-in | — | 3 | 200×2, 401×1 | IMPLEMENTED_AND_VERIFIED |
| 64 | POST | `/emergencies` | emergencies | session | FAMILY, HOSPITAL, PATIENT | yes | 190 | 200×21, 201×152, 400×3, 401×1, 403×7, 422×4, 429×2 | IMPLEMENTED_AND_VERIFIED |
| 65 | GET | `/emergencies/active` | emergencies | session | DONOR, HOSPITAL | — | 9 | 200×3, 401×2, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 66 | GET | `/emergencies/{emergency_id}` | emergencies | session | any signed-in | — | 50 | 200×36, 401×1, 404×13 | IMPLEMENTED_AND_VERIFIED |
| 67 | POST | `/emergencies/{emergency_id}/acknowledge` | emergencies | session | ADMIN, DONOR, HOSPITAL | yes | 17 | 200×11, 401×1, 403×3, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 68 | POST | `/emergencies/{emergency_id}/assistance-found` | emergencies | session | any signed-in | yes | 8 | 200×3, 401×1, 403×1, 404×3 | IMPLEMENTED_AND_VERIFIED |
| 69 | POST | `/emergencies/{emergency_id}/cancel` | emergencies | session | any signed-in | yes | 17 | 200×7, 401×1, 403×1, 404×7, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 70 | GET | `/emergencies/{emergency_id}/match-candidates` | emergencies | session | any signed-in | — | 10 | 200×8, 401×1, 404×1 | IMPLEMENTED_AND_VERIFIED |
| 71 | GET | `/emergencies/{emergency_id}/match-strategies` | emergencies | session | any signed-in | — | 3 | 200×1, 401×1, 404×1 | IMPLEMENTED_AND_VERIFIED |
| 72 | POST | `/emergencies/{emergency_id}/overrides` | emergencies | session | ADMIN | yes | 8 | 201×1, 401×1, 403×6 | IMPLEMENTED_AND_VERIFIED |
| 73 | POST | `/emergencies/{emergency_id}/overrides/{override_id}/revoke` | emergencies | session | ADMIN | yes | 7 | 200×1, 401×1, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 74 | POST | `/emergencies/{emergency_id}/pathway` | emergencies | session | any signed-in | yes | 19 | 200×14, 401×1, 403×3, 404×1 | IMPLEMENTED_AND_VERIFIED |
| 75 | POST | `/emergencies/{emergency_id}/pathway/release` | emergencies | session | any signed-in | yes | 3 | 200×1, 401×1, 403×1 | IMPLEMENTED_AND_VERIFIED |
| 76 | POST | `/emergencies/{emergency_id}/pathway/reserve` | emergencies | session | any signed-in | yes | 3 | 200×1, 401×1, 403×1 | IMPLEMENTED_AND_VERIFIED |
| 77 | GET | `/emergencies/{emergency_id}/pathways` | emergencies | session | any signed-in | — | 3 | 200×2, 401×1 | IMPLEMENTED_AND_VERIFIED |
| 78 | POST | `/emergencies/{emergency_id}/resolve` | emergencies | session | any signed-in | yes | 18 | 200×8, 401×1, 403×1, 404×4, 409×4 | IMPLEMENTED_AND_VERIFIED |
| 79 | GET | `/emergencies/{emergency_id}/resource-pathways` | emergencies | session | any signed-in | — | 16 | 200×3, 401×1, 403×4, 404×3, 422×5 | IMPLEMENTED_AND_VERIFIED |
| 80 | POST | `/emergencies/{emergency_id}/respond` | emergencies | session | DONOR, HOSPITAL | yes | 42 | 200×29, 400×3, 401×1, 403×5, 404×1, 409×3 | IMPLEMENTED_AND_VERIFIED |
| 81 | GET | `/events/stream` | events | none | — | — | 18 | 200×16, 401×2 | IMPLEMENTED_AND_VERIFIED |
| 82 | GET | `/family/links` | family | session | any signed-in | — | 12 | 200×10, 401×2 | IMPLEMENTED_AND_VERIFIED |
| 83 | POST | `/family/links` | family | session | FAMILY | yes | 77 | 201×61, 401×1, 403×7, 404×4, 409×4 | IMPLEMENTED_AND_VERIFIED |
| 84 | POST | `/family/links/{link_id}/accept` | family | session | any signed-in | yes | 41 | 200×26, 401×1, 403×8, 404×1, 409×5 | IMPLEMENTED_AND_VERIFIED |
| 85 | POST | `/family/links/{link_id}/reject` | family | session | any signed-in | yes | 19 | 200×8, 401×1, 403×7, 404×1, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 86 | POST | `/family/links/{link_id}/revoke` | family | session | any signed-in | yes | 19 | 200×9, 401×1, 403×6, 404×1, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 87 | GET | `/forecasts` | forecasts | session | ADMIN, HOSPITAL | — | 5 | 200×1, 401×1, 403×3 | IMPLEMENTED_AND_VERIFIED |
| 88 | POST | `/forecasts` | forecasts | session | ADMIN, HOSPITAL | yes | 11 | 201×4, 401×1, 403×6 | IMPLEMENTED_AND_VERIFIED |
| 89 | GET | `/forecasts/{run_id}` | forecasts | session | ADMIN, HOSPITAL | — | 7 | 200×2, 401×1, 403×3, 404×1 | IMPLEMENTED_AND_VERIFIED |
| 90 | GET | `/health` | health | none | — | — | 8 | 200×8 | IMPLEMENTED_AND_VERIFIED |
| 91 | GET | `/health/live` | health | none | — | — | 5 | 200×5 | IMPLEMENTED_AND_VERIFIED |
| 92 | GET | `/health/ready` | health | none | — | — | 5 | 200×4, 503×1 | IMPLEMENTED_AND_VERIFIED |
| 93 | GET | `/hospitals/directory` | hospitals | session | any signed-in | — | 15 | 200×13, 401×2 | IMPLEMENTED_AND_VERIFIED |
| 94 | GET | `/hospitals/me` | hospitals | session | HOSPITAL | — | 127 | 200×120, 401×1, 403×6 | IMPLEMENTED_AND_VERIFIED |
| 95 | PUT | `/hospitals/me` | hospitals | session | HOSPITAL | yes | 426 | 200×420, 401×1, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 96 | GET | `/hospitals/me/activity` | hospitals | session | HOSPITAL | — | 6 | 200×1, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 97 | GET | `/hospitals/me/emergencies` | hospitals | session | HOSPITAL | — | 6 | 200×1, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 98 | GET | `/hospitals/me/inventory` | hospitals | session | HOSPITAL | — | 58 | 200×53, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 99 | PUT | `/hospitals/me/inventory` | hospitals | session | HOSPITAL | yes | 16 | 200×9, 401×1, 403×5, 422×1 | IMPLEMENTED_AND_VERIFIED |
| 100 | POST | `/hospitals/me/inventory/add` | hospitals | session | HOSPITAL | yes | 177 | 200×165, 401×1, 403×5, 409×4, 422×2 | IMPLEMENTED_AND_VERIFIED |
| 101 | POST | `/hospitals/me/inventory/allocate` | hospitals | session | HOSPITAL | yes | 27 | 200×16, 401×1, 403×5, 409×5 | IMPLEMENTED_AND_VERIFIED |
| 102 | GET | `/hospitals/me/inventory/components` | hospitals | session | HOSPITAL | — | 15 | 200×6, 401×1, 403×8 | IMPLEMENTED_AND_VERIFIED |
| 103 | POST | `/hospitals/me/inventory/expire` | hospitals | session | HOSPITAL | yes | 10 | 200×2, 401×1, 403×5, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 104 | GET | `/hospitals/me/inventory/log` | hospitals | session | HOSPITAL | — | 7 | 200×2, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 105 | POST | `/hospitals/me/inventory/reconcile` | hospitals | session | HOSPITAL | yes | 8 | 200×1, 401×1, 403×5, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 106 | POST | `/hospitals/me/inventory/release` | hospitals | session | HOSPITAL | yes | 10 | 200×3, 401×1, 403×5, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 107 | POST | `/hospitals/me/inventory/reserve` | hospitals | session | HOSPITAL | yes | 38 | 200×28, 401×1, 403×5, 409×4 | IMPLEMENTED_AND_VERIFIED |
| 108 | GET | `/hospitals/me/requests` | hospitals | session | HOSPITAL | — | 11 | 200×6, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 109 | POST | `/hospitals/me/requests/{request_id}/allocate` | hospitals | session | HOSPITAL | yes | 52 | 200×31, 400×1, 401×1, 403×5, 404×2, 409×11, 422×1 | IMPLEMENTED_AND_VERIFIED |
| 110 | GET | `/hospitals/me/requests/{request_id}/events` | hospitals | session | HOSPITAL | — | 9 | 200×1, 401×1, 403×4, 404×3 | IMPLEMENTED_AND_VERIFIED |
| 111 | POST | `/hospitals/me/requests/{request_id}/fulfill` | hospitals | session | HOSPITAL | yes | 17 | 200×6, 401×1, 403×5, 404×1, 409×4 | IMPLEMENTED_AND_VERIFIED |
| 112 | POST | `/hospitals/me/requests/{request_id}/respond` | hospitals | session | HOSPITAL | yes | 14 | 200×4, 401×1, 403×5, 404×1, 422×3 | IMPLEMENTED_AND_VERIFIED |
| 113 | GET | `/hospitals/me/resource-pathways` | hospitals | session | HOSPITAL | — | 20 | 200×6, 401×1, 403×8, 422×5 | IMPLEMENTED_AND_VERIFIED |
| 114 | GET | `/hospitals/me/routes` | hospitals | session | HOSPITAL | — | 7 | 200×2, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 115 | GET | `/hospitals/me/units` | hospitals | session | HOSPITAL | — | 9 | 200×4, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 116 | POST | `/hospitals/me/units/{unit_id}/discard` | hospitals | session | HOSPITAL | yes | 8 | 200×1, 401×1, 403×5, 409×1 | IMPLEMENTED_AND_VERIFIED |
| 117 | POST | `/hospitals/me/units/{unit_id}/quarantine` | hospitals | session | HOSPITAL | yes | 8 | 200×1, 401×1, 403×5, 404×1 | IMPLEMENTED_AND_VERIFIED |
| 118 | POST | `/hospitals/me/units/{unit_id}/release-quarantine` | hospitals | session | HOSPITAL | yes | 7 | 200×1, 401×1, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 119 | POST | `/hospitals/me/units/{unit_id}/thaw` | hospitals | session | HOSPITAL | yes | 9 | 200×1, 401×1, 403×5, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 120 | GET | `/` | main | none | — | — | 1 | 200×1 | IMPLEMENTED_AND_VERIFIED |
| 121 | GET | `/blood-requests/{request_id}/match-candidates` | matching | session | any signed-in | — | 8 | 200×5, 401×1, 404×2 | IMPLEMENTED_AND_VERIFIED |
| 122 | GET | `/blood-requests/{request_id}/match-strategies` | matching | session | any signed-in | — | 3 | 200×1, 401×1, 404×1 | IMPLEMENTED_AND_VERIFIED |
| 123 | GET | `/map/data` | matching | session | FAMILY, HOSPITAL, PATIENT | — | 8 | 200×4, 401×1, 403×3 | IMPLEMENTED_AND_VERIFIED |
| 124 | GET | `/matching/blood-compatibility` | matching | session | any signed-in | — | 3 | 200×1, 401×2 | IMPLEMENTED_AND_VERIFIED |
| 125 | GET | `/notifications` | notifications | session | any signed-in | — | 122 | 200×117, 401×1, 403×2, 422×2 | IMPLEMENTED_AND_VERIFIED |
| 126 | GET | `/notifications/preferences` | notifications | session | any signed-in | — | 7 | 200×5, 401×2 | IMPLEMENTED_AND_VERIFIED |
| 127 | PUT | `/notifications/preferences` | notifications | session | any signed-in | yes | 14 | 200×8, 401×2, 403×1, 422×3 | IMPLEMENTED_AND_VERIFIED |
| 128 | POST | `/notifications/read-all` | notifications | session | any signed-in | yes | 3 | 200×1, 401×1, 403×1 | IMPLEMENTED_AND_VERIFIED |
| 129 | GET | `/notifications/unread-count` | notifications | session | any signed-in | — | 6 | 200×5, 401×1 | IMPLEMENTED_AND_VERIFIED |
| 130 | POST | `/notifications/{notification_id}/read` | notifications | session | any signed-in | yes | 16 | 200×11, 401×1, 403×1, 404×3 | IMPLEMENTED_AND_VERIFIED |
| 131 | GET | `/pathways/config` | pathways | session | any signed-in | — | 2 | 200×1, 401×1 | IMPLEMENTED_AND_VERIFIED |
| 132 | POST | `/pathways/simulate` | pathways | session | ADMIN, HOSPITAL | yes | 9 | 200×2, 401×1, 403×5, 404×1 | IMPLEMENTED_AND_VERIFIED |
| 133 | GET | `/patients/managed` | patients | session | FAMILY | — | 18 | 200×12, 401×2, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 134 | GET | `/patients/me` | patients | session | FAMILY, PATIENT | — | 15 | 200×3, 401×4, 403×4, 503×4 | IMPLEMENTED_AND_VERIFIED |
| 135 | PUT | `/patients/me` | patients | session | FAMILY, PATIENT | yes | 8 | 200×2, 401×1, 403×4, 422×1 | IMPLEMENTED_AND_VERIFIED |
| 136 | GET | `/shipments` | shipments | session | ADMIN, HOSPITAL | — | 9 | 200×5, 401×1, 403×3 | IMPLEMENTED_AND_VERIFIED |
| 137 | POST | `/shipments` | shipments | session | ADMIN, HOSPITAL | yes | 90 | 200×1, 201×76, 400×2, 401×1, 403×5, 409×5 | IMPLEMENTED_AND_VERIFIED |
| 138 | GET | `/shipments/by-code/{shipment_code}` | shipments | session | ADMIN, HOSPITAL | — | 9 | 200×3, 401×1, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 139 | GET | `/shipments/units/{unit_code}` | shipments | session | ADMIN, HOSPITAL | — | 12 | 200×5, 401×1, 403×6 | IMPLEMENTED_AND_VERIFIED |
| 140 | GET | `/shipments/{shipment_id}` | shipments | session | ADMIN, HOSPITAL | — | 12 | 200×6, 401×1, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 141 | POST | `/shipments/{shipment_id}/arrive` | shipments | session | ADMIN, HOSPITAL | yes | 20 | 200×15, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 142 | POST | `/shipments/{shipment_id}/cancel` | shipments | session | ADMIN, HOSPITAL | yes | 13 | 200×4, 401×1, 403×5, 409×3 | IMPLEMENTED_AND_VERIFIED |
| 143 | POST | `/shipments/{shipment_id}/delay` | shipments | session | ADMIN, HOSPITAL | yes | 11 | 200×3, 401×1, 403×5, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 144 | POST | `/shipments/{shipment_id}/dispatch` | shipments | session | ADMIN, HOSPITAL | yes | 61 | 200×49, 401×1, 403×7, 409×4 | IMPLEMENTED_AND_VERIFIED |
| 145 | POST | `/shipments/{shipment_id}/fail` | shipments | session | ADMIN, HOSPITAL | yes | 29 | 200×21, 401×1, 403×5, 409×2 | IMPLEMENTED_AND_VERIFIED |
| 146 | POST | `/shipments/{shipment_id}/in-transit` | shipments | session | ADMIN, HOSPITAL | yes | 20 | 200×15, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 147 | POST | `/shipments/{shipment_id}/receive` | shipments | session | ADMIN, HOSPITAL | yes | 27 | 200×12, 401×1, 403×6, 409×8 | IMPLEMENTED_AND_VERIFIED |
| 148 | POST | `/shipments/{shipment_id}/reconcile` | shipments | session | ADMIN, HOSPITAL | yes | 32 | 200×11, 401×1, 403×8, 409×3, 422×9 | IMPLEMENTED_AND_VERIFIED |
| 149 | GET | `/shipments/{shipment_id}/telemetry` | shipments | session | ADMIN, HOSPITAL | — | 7 | 200×2, 401×1, 403×4 | IMPLEMENTED_AND_VERIFIED |
| 150 | POST | `/shipments/{shipment_id}/telemetry` | shipments | session | ADMIN, HOSPITAL | yes | 19 | 200×12, 401×1, 403×4, 422×2 | IMPLEMENTED_AND_VERIFIED |
| 151 | POST | `/shipments/{shipment_id}/telemetry/simulate` | shipments | session | ADMIN | yes | 8 | 200×1, 401×1, 403×6 | IMPLEMENTED_AND_VERIFIED |
| 152 | GET | `/shipments/{shipment_id}/timeline` | shipments | session | ADMIN, HOSPITAL | — | 10 | 200×4, 401×1, 403×5 | IMPLEMENTED_AND_VERIFIED |
| 153 | GET | `/admin/verifications` | verifications | session | ADMIN | — | 21 | 200×6, 401×2, 403×13 | IMPLEMENTED_AND_VERIFIED |
| 154 | GET | `/admin/verifications/{request_id}` | verifications | session | ADMIN | — | 24 | 200×5, 401×2, 403×10, 404×3, 422×4 | IMPLEMENTED_AND_VERIFIED |
| 155 | POST | `/admin/verifications/{request_id}/approve` | verifications | session | ADMIN | yes | 27 | 200×10, 401×2, 403×9, 409×6 | IMPLEMENTED_AND_VERIFIED |
| 156 | GET | `/admin/verifications/{request_id}/documents/{index}` | verifications | session | ADMIN | — | 41 | 200×11, 401×1, 403×14, 404×6, 409×6, 422×1, 503×2 | IMPLEMENTED_AND_VERIFIED |
| 157 | POST | `/admin/verifications/{request_id}/documents/{index}/rescan` | verifications | session | ADMIN | yes | 10 | 200×3, 401×1, 403×6 | IMPLEMENTED_AND_VERIFIED |
| 158 | POST | `/admin/verifications/{request_id}/reject` | verifications | session | ADMIN | yes | 26 | 200×11, 401×2, 403×8, 409×3, 422×2 | IMPLEMENTED_AND_VERIFIED |
| 159 | POST | `/verifications` | verifications | session | DONOR, HOSPITAL | yes | 131 | 201×90, 401×3, 403×8, 409×8, 422×19, 429×1, 503×2 | IMPLEMENTED_AND_VERIFIED |
| 160 | GET | `/verifications/me` | verifications | session | any signed-in | — | 15 | 200×13, 401×2 | IMPLEMENTED_AND_VERIFIED |

## 4. Frontend routes: crawl and browser coverage

**Crawl coverage.** `frontend/scripts/qa-crawl.mjs` signed in as an allowed role (or none for public pages) and opened every route at 375, 768 and 1280 px, making **186 page visits**. For each visit it recorded:

* console errors;
* failed requests;
* horizontal overflow;
* broken images;
* blank pages;
* axe-core serious and critical violations (at 1280 px).

**Authorization checks.** Each guarded route was then opened anonymously and as a wrong role: **75/75 were denied** (redirected to sign-in or 403, or shown Access Denied).

**Findings:**

* **Overflow, broken images (apart from map tiles), blank pages, axe:** 0 horizontal overflow at any width, 0 axe serious or critical violations, 0 blank pages.
* **Map tiles:** 106 console or request errors, all from OpenStreetMap tile hosts refused by the sandbox egress. The app shows its fallback banner (KNOWN_LIMITATIONS E5).
* **`/mathematical-model`:** for an anonymous visitor the page called an authenticated endpoint (401 and a console error). This was **defect D5**, fixed. The four public live pages were re-crawled after the fix with 0 errors (`evidence/frontend-routes/recheck_after_fixes/`), and those results replace the first crawl in the table.

**Route status.** A route marked "render, a11y, layout; no workflow spec" passed the crawl, but no Playwright spec navigates to it by URL. The pages it renders may still be exercised by clicking through from other pages.

| # | Route | Guard | Roles | E2E specs that open it | Crawl (375/768/1280): console errors · failed requests · max overflow · axe serious+critical (tile-host failures excluded) | Status |
|---|---|---|---|---|---|---|
| 1 | `/` | public | — | document_storage_security, phase23_security_hardening, phase26_accessibility, phase27_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 2 | `/login` | public | — | clean_start, container_frontend, gap_live_pages, phase23_security_hardening, phase_j_journeys, qa_evidence, qa_failover_ui | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 3 | `/register` | public | — | qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 4 | `/verify-email` | public | — | qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 5 | `/verify/:code` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 6 | `/forgot-password` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 7 | `/reset-password` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 8 | `/dev-index` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 9 | `/403` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 10 | `/session-expired` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 11 | `/500` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 12 | `/offline` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 13 | `/recommendation` | public | — | gap_live_pages | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 14 | `/decision-transparency` | public | — | gap_live_pages, phase27_accessibility, qa_failover_ui | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 15 | `/mathematical-model` | public | — | gap_live_pages, phase27_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 16 | `/graph-engine` | public | — | container_frontend, gap_live_pages | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 17 | `/simulation` | public | — | gap_live_pages, phase27_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 18 | `/research-mode` | public | — | gap_live_pages | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 19 | `/help` | public | — | phase26_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 20 | `/legal` | public | — | phase26_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 21 | `/legal/:section` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 22 | `/patient` | RequireAuth | PATIENT | phase21_error_loading_empty_offline, phase23_security_hardening, phase26_responsive_overflow, phase27_accessibility, phase31_1_domain_signals, phase_f_realtime, phase_j_journeys, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 23 | `/patient/find-donor` | RequireAuth | PATIENT | phase27_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 24 | `/patient/sos-tracking` | RequireAuth | PATIENT | phase27_accessibility, phase31_1_domain_signals | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 25 | `/patient/new-request` | RequireAuth | PATIENT | phase21_error_loading_empty_offline, phase27_accessibility, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 26 | `/patient/history` | RequireAuth | PATIENT | phase26_responsive_overflow, phase_f_realtime, phase_j_journeys, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 27 | `/family` | RequireAuth | FAMILY | family_link_notifications, phase26_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 28 | `/family/patients` | RequireAuth | FAMILY | family_link_notifications, phase26_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 29 | `/family/patients/:patientId` | RequireAuth | FAMILY | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 30 | `/donor` | RequireAuth | DONOR | document_storage_security, phase26_accessibility, phase26_donation_idempotency, phase26_responsive_overflow, phase27_accessibility, phase31_1_domain_signals, phase_e_domain_integrity, phase_j_journeys, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 31 | `/donor/mobile` | RequireAuth | DONOR | phase_j_journeys | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 32 | `/donor/eligibility` | RequireAuth | DONOR | qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 33 | `/donor/ready-console` | RequireAuth | DONOR | phase26_accessibility, phase27_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 34 | `/donor/milestones` | RequireAuth | DONOR | phase26_donation_idempotency, phase26_responsive_overflow, phase31_1_domain_signals, phase_e_domain_integrity, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 35 | `/donor/identity-verification` | RequireAuth | DONOR | document_storage_security, phase27_accessibility, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 36 | `/hospital` | RequireAuth | HOSPITAL | clean_start, container_frontend, document_storage_security, gap_live_pages, phase26_accessibility, phase27_accessibility, phase_e_domain_integrity, phase_g_integration, phase_j_journeys, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 37 | `/hospital/inventory` | RequireAuth | HOSPITAL | phase_g_integration, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 38 | `/hospital/donors` | RequireAuth | HOSPITAL | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 39 | `/hospital/requests/:requestId` | RequireAuth | HOSPITAL | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 40 | `/hospital/analytics-mobile` | RequireAuth | HOSPITAL | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 41 | `/hospital/units` | RequireAuth | HOSPITAL | clean_start, container_frontend, gap_live_pages | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 42 | `/hospital/resource-pathways` | RequireAuth | HOSPITAL | gap_live_pages | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 43 | `/hospital/shipments` | RequireAuth | HOSPITAL | gap_live_pages, phase_e_domain_integrity, phase_j_journeys, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 44 | `/hospital/verification` | RequireAuth | HOSPITAL | document_storage_security, phase27_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 45 | `/hospital/donation-reviews` | RequireAuth | HOSPITAL | phase_e_domain_integrity, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 46 | `/admin` | RequireAuth | ADMIN | admin_notification_surface, document_storage_security, gap_live_pages, phase23_security_hardening, phase26_accessibility, phase26_responsive_overflow, phase31_1_domain_signals, phase_e_domain_integrity, phase_g_integration, qa_evidence, qa_failover_ui | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 47 | `/admin/verifications` | RequireAuth | ADMIN | admin_notification_surface, document_storage_security, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 48 | `/admin/users` | RequireAuth | ADMIN | admin_notification_surface, phase23_security_hardening, phase26_accessibility, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 49 | `/admin/analytics` | RequireAuth | ADMIN | phase26_accessibility, phase31_1_domain_signals, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 50 | `/admin/transport-routes` | RequireAuth | ADMIN | gap_live_pages, phase26_responsive_overflow, qa_failover_ui | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 51 | `/admin/shipments` | RequireAuth | ADMIN | qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 52 | `/admin/facilities` | RequireAuth | ADMIN | admin_notification_surface, gap_live_pages, phase_g_integration, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 53 | `/admin/donors` | RequireAuth | ADMIN | phase26_accessibility, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 54 | `/admin/audit-feed` | RequireAuth | ADMIN | phase_e_domain_integrity, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 55 | `/admin/verification` | public | — | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 56 | `/analytics` | RequireAuth | ADMIN, HOSPITAL | gap_live_pages | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 57 | `/certificate` | RequireAuth | any signed-in | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 58 | `/certificate/:id` | RequireAuth | any signed-in | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 59 | `/matching/strategy-lab` | RequireAuth | PATIENT, FAMILY, HOSPITAL, ADMIN | phase27_accessibility | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 60 | `/profile` | RequireAuth | any signed-in | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 61 | `/settings` | RequireAuth | any signed-in | — | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec) |
| 62 | `/notifications` | RequireAuth | any signed-in | admin_verification_notifications, blood_request_notifications, offline_network_failure, phase21_error_loading_empty_offline, phase26_accessibility, qa_evidence | 3 visits: 0 · 0 · 0px · 0 | IMPLEMENTED_AND_VERIFIED |
| 63 | `*` | public | — | phase21_error_loading_empty_offline, phase25_error_pages_and_500 | not crawled | IMPLEMENTED_AND_VERIFIED |
