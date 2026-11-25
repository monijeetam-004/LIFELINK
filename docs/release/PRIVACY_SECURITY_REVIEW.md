# Privacy and security review: donor shortlist and unified priority

**Status: engineering review done (two rounds). Formal privacy approval is PENDING.** No privacy officer has reviewed this.

* **Prepared:** 2026-11-03, against HEAD `2cfe39a` plus the uncommitted changes of the release-gate round ("round 1") and the privacy remediation round ("round 2", §3b).
* **What it records:** the data flows, the checks actually executed, the defects found and fixed in each round, and the decisions an authorised reviewer still has to make.
* **Round 2 correction of round 1.** Round 1 reported the re-check-reason defect as fixed. It was not: the reason was still written into the request/emergency **event history**, which patients, family members, other donors and facilities read (§3b, R2-1). Round 1 also said distances had been returned "at full float precision"; in fact `haversine_km` already rounded them to 0.1 km for everyone, so round 1's real change was the whole-km figure for patients and family (§3b, R2-5).

## 1. Data flow

| Data | Source (table/field) | Computed in | Stored? | Returned to |
|---|---|---|---|---|
| Invitations, answers, acceptances on **other** requests | `notifications` (types `*.new`, `*.shortlist_invite`), `donor_responses`, `emergency_responses` | `donor_shortlist_service._history` (current request excluded) | **Only derived values:** the `response` and `acceptance` factor values in `donor_contact_attempts.score_snapshot`, written when a donor is contacted | **FULL (hospital/admin):** counts in the factor text, e.g. "answered 1 of 1". **SUMMARY (patient/family):** a sentence only |
| Last response or recorded donation time | the same tables, plus `donations.created_at` | `_history` | The `activity` factor value in the snapshot | FULL: "N day(s) ago". SUMMARY: a sentence only |
| Availability confirmation time | `donor_profiles.availability_confirmed_at` | `unified_priority._availability` | The factor value in the snapshot | FULL: "confirmed N days ago". SUMMARY: a status label |
| Location and its age | `donor_profiles.latitude/longitude` (only if `share_location`), `location_updated_at` | `distance_policy.assess` (`haversine_km`, already rounded to 0.1 km) | Never stored by the shortlist; distance is not in the snapshot | **Before round 1:** 0.1 km to every viewer (round 1 wrongly said "full precision"). **Now:** 0.1 km (FULL) or whole km, at least 1 (SUMMARY). Coordinates are never returned |
| Travel-time estimate | derived: distance ÷ 45 km/h | `donor_shortlist_service` | No | **Before round 2:** whole minutes from the 0.1-km distance to every viewer, i.e. the distance to 0.75 km, finer than the whole-km figure shown to patients and family. **Now:** FULL from the 0.1-km distance; SUMMARY from the **whole-km** distance, so it adds no precision |
| Identity verification and verified donations | `donor_profiles.identity_verified`, `donations.verification_status` | `unified_priority` | The factor value in the snapshot | FULL: text. SUMMARY: a sentence |
| Donor name | `users.full_name` | `shortlists.build_out` | No | The request's **managers** only: requester, patient, linked family, admin. This is the same rule as the earlier ranking view |
| Re-check failure reason | `donor_contact_attempts.status_reason` | `donor_shortlist_service.confirm` | Yes, in the contact row while the status is `ELIGIBILITY_RECHECK_REQUIRED` (cleared when the contact leaves that status, round 2); and in the `shortlist.confirmation` audit meta (round 2) | **Before round 1:** to every manager on the shortlist. **Round 1:** hidden on the shortlist for SUMMARY, **but still in the event history** (below). **Round 2:** shortlist FULL only (SUMMARY: "the eligibility re-check did not pass", chosen by status); event history generic; audit log admin-only |
| Request/emergency **event history** (`blood_request_events`, `emergency_events`) | `donor_shortlist_service._subject_event` | — | Yes | Read via `GET /blood-requests/{id}`, `GET /emergencies/{id}`, `GET /hospitals/me/requests/{id}/events` and `GET /hospitals/me/activity` by: requester, patient, linked family, donors who answered (requests) or answered/are candidates (emergencies), facilities that can see it, admins. **Before round 2:** the confirmation note carried the donor ref, the status **and the re-check reason**. **Now:** "A contacted donor could not be confirmed for coordination; a coordinator must follow up (details are visible to facility staff)." or "A contacted donor was confirmed for coordination." No donor ref, no reason |
| Score, coverage, model tag | computed | `unified_priority.compute` | `score_at_contact`, `scoring_model`, `score_snapshot` | FULL: exact score, baseline, `score_at_contact`. **SUMMARY since round 2:** score and `score_at_contact` in **10-point bands**, `missing_data_baseline` null (§3b, R2-3). Ordering always uses the exact score. `score_snapshot` is **never** returned by any endpoint |
| Weights and thresholds | configuration | — | No | **Before this round:** `/shortlist/policy` hid them, but every candidate's `priority.factors[].weight`, `value`, `contribution` and `components` exposed them, and the history values, to patients and family. **Now:** null or empty for SUMMARY viewers |

**What the snapshot contains** (`unified_priority.PriorityScore.snapshot`): the model tag, the score, the coverage, the availability flag, and seven factor values in [0, 1]. It holds no names, dates, coordinates, free text or medical data.

**Audit events** (`audit_service.record`):

| Event | Meta stored |
|---|---|
| `shortlist.contacted` | count, strategy, model tag |
| `shortlist.confirmation` | contact id, result; **since round 2** also the re-check `reason` when the result is `ELIGIBILITY_RECHECK_REQUIRED` (admin-readable only: `GET /admin/audit-log`, 403 for every other role, tested) |
| `shortlist.access_denied` | the actor's role |
| `demo.shortlist_simulated` | counts |

No donor identifiers beyond the contact id, and no history, are written to audit meta. The re-check reason is a fixed phrase from `confirm()` ("no longer marked available", "donation interval not elapsed", "you already accepted another open blood request/emergency"), never free text from a user.

**Logs.** The shortlist service, routes and demo simulator contain **no** logging calls (checked with `grep`). Error responses are the standard ones:

* 404 when the subject is not found or the caller has no access;
* 403 when the caller is not a manager;
* 409 for a state conflict;
* 422 for an invalid strategy (Pydantic enum).

None of these echo donor data.

## 2. Access control: enforced on the server

| Check | Test (run in the full suite) |
|---|---|
| Donors and unrelated users get 404 on another request's shortlist; another facility sees the list without names | `test_donor_shortlist.py::test_access_control_and_privacy`. The denial is also written as a `shortlist.access_denied` audit event (`shortlists.py` `_load`); no test asserts the event |
| Hospital and admin users get FULL detail; patients get SUMMARY; `/shortlist/policy` weights only for hospital and admin | `test_unified_priority.py::test_explanation_detail_depends_on_role_and_policy_hides_weights` |
| **New:** SUMMARY responses carry no weights, factor values, contributions or components; distances are whole km; re-check reasons are generic. FULL keeps the detail at 0.1 km | `test_unified_priority.py::test_summary_viewers_get_no_weights_history_values_precise_distance_or_reasons` |
| **New:** the UI renders SUMMARY explanations without points | `DonorShortlistPanel.test.tsx` ("renders a SUMMARY explanation without weights or points") |
| Management actions (contact, confirm) are refused with 403 for non-managers | `test_access_control_and_privacy` |
| The simulator is off by default, admin-only, and acts on synthetic accounts only; SMS is refused for the synthetic domain | `test_simulator_is_off_by_default_admin_only_and_synthetic_only` |
| Clients cannot submit scores: the API accepts only a strategy name | API schema (no score input field) |
| **Round 2:** the event history of a blood request and of an emergency carries no re-check reason and no donor ref, for patient, linked family, the failed donor, other responding donors, a mere candidate donor, a hospital and an admin (each must get 200); hospital/admin keep the reason on the shortlist; the admin audit log keeps it; the audit log is 403 for every other role | `test_shortlist_privacy_round2.py::test_failed_recheck_reason_is_not_in_emergency_events_for_any_role`, `::test_failed_recheck_reason_is_not_in_blood_request_events_for_any_role` |
| **Round 2:** a re-check reason does not survive a later status change (withdrawal) | `::test_a_recheck_reason_does_not_survive_a_later_status_change` |
| **Round 2:** on an SOS with coordinates, exact distance, travel time, score band, baseline and `score_at_contact` per role, with the distance required to be present | `::test_distance_travel_time_and_score_precision_by_role_on_an_sos` |
| **Round 2:** shortlist read/contact/confirm refused to a donor, an unrelated patient and an unlinked family member (404), and contact/confirm refused to a non-managing facility (403); refused calls change nothing | `::test_shortlist_endpoints_refuse_donors_outsiders_and_non_managers` |
| **Round 2:** patient-facing reason chosen by status, for every `DonorContactStatus` × every reason the code writes × with/without `[sim-wait]` | `::test_reason_shown_depends_on_status_for_every_status_and_reason`, `::test_marker_alone_is_not_a_reason` |
| **Round 2:** UI labels a banded score "about N/100" and shows no baseline line; FULL unchanged | `DonorShortlistPanel.test.tsx` (2 tests) |

## 3. Defects found and fixed in this round

| # | Defect | Fix | Files |
|---|---|---|---|
| P1 | Patients and family received the model weights and each donor's factor values (derived response and acceptance history) through `priority.factors[]` and `components`. The weights were "hidden" only on the policy endpoint | Null or empty for SUMMARY viewers. The schema fields became optional, which is additive and backward compatible | `backend/app/api/routes/shortlists.py`, `backend/app/schemas/shortlist.py`, `frontend/src/lib/gapApi.ts`, `frontend/src/components/DonorShortlistPanel.tsx` |
| P2 | `distance_km` was shown to patients and family at 0.1 km. Repeated requests from known locations could narrow down where a donor lives. *(Round 1 described this as "full float precision"; that was wrong, see R2-5.)* | Whole km (minimum 1) for SUMMARY; 0.1 km kept for FULL; explanation text coarsened to match | `shortlists.py` |
| P3 | A failed confirmation reason (for example a recent donation, or another commitment) was shown to patients and family **on the shortlist** | A generic text for SUMMARY on the shortlist; the detail stays for FULL. **Incomplete:** the same reason was still in the event history, see R2-1 | `shortlists.py` |

P1 and P2 have regression tests (§2). P3 was **not** closed by round 1; it is closed by round 2 (R2-1, R2-4).

## 3b. Defects found and fixed in round 2 (privacy remediation)

| # | Severity | Defect (confirmed by a probe against the real API before the fix) | Fix | Test that proves it closed |
|---|---|---|---|---|
| R2-1 | HIGH | `confirm()` wrote `Donor <ref>: ELIGIBILITY_RECHECK_REQUIRED (<reason>)` into the event history. A patient saw "(donation interval not elapsed)" on `GET /blood-requests/{id}` and `GET /emergencies/{id}`; **another donor** saw it on `GET /emergencies/{id}` | Generic notes without donor ref or reason (both outcomes). The reason is kept on the contact row (FULL viewers) and added to the staff-only `shortlist.confirmation` audit meta | `test_failed_recheck_reason_is_not_in_emergency_events_for_any_role`, `test_failed_recheck_reason_is_not_in_blood_request_events_for_any_role` |
| R2-2 | MEDIUM | `travel_minutes_estimate` (distance ÷ 45 km/h, whole minutes) was computed from the 0.1-km distance for everyone. Probe: patient saw 4.0 km and 5 min (≈ 3.75 km), admin saw 3.5 km | SUMMARY travel time is computed from the whole-km figure | `test_distance_travel_time_and_score_precision_by_role_on_an_sos` (Mid donor: staff 3.4 km / 5 min, patient and family 3 km / 4 min) |
| R2-3 | MEDIUM | The exact score (0.01 points) and `missing_data_baseline` were shown to SUMMARY viewers. With the published weights and the factor statuses, the distance term (0.4 points per km) can be backed out, defeating whole-km rounding, and parts of the history term | SUMMARY: `priority.score`, `score` and `score_at_contact` in 10-point bands; `missing_data_baseline` null. No weight or algorithm change; ordering uses the exact score | same test (band, baseline, `score_at_contact` asserted per role) |
| R2-4 | LOW | Reason hiding depended on wording: a fixed allowlist of "generic" phrases. A reason with the simulator's `[sim-wait]` suffix no longer matched and was relabelled "the eligibility re-check did not pass" although no re-check happened | Label chosen by **status**: only `ELIGIBILITY_RECHECK_REQUIRED` gets the re-check label. Operational reasons are shown as written; `[sim-wait]` is stripped for everyone; a simulated refusal gets its own generic sentence; any other text under any other status fails closed to "details are visible to facility staff". A re-check reason is also **cleared** when the contact leaves that status (it used to survive into WITHDRAWN/ACCEPTED, where a status rule would not hide it) | `test_reason_shown_depends_on_status_for_every_status_and_reason` (11 statuses × 11 reasons × 2), `test_marker_alone_is_not_a_reason`, `test_a_recheck_reason_does_not_survive_a_later_status_change` |
| R2-5 | (docs) | Round 1's documents said distances had been returned at full float precision and that P3 was fixed | Corrected here, in `RELEASE_GATES_2026-11-03.md` and `UNIFIED_DONOR_PRIORITY.md` | — |

**What an authorised reader still sees** (round-2 evidence `docs/QA/evidence/privacy-round2-2026-11-03/role_payloads.json`, test database, synthetic accounts):

| Role | Event note after a failed and a successful confirmation | Shortlist | Contact reason | Distance / travel / score |
|---|---|---|---|---|
| Patient, linked family | generic (both) | 200, SUMMARY | "the eligibility re-check did not pass" | 3 km / 4 min / 60 (band) |
| Failed donor, confirmed donor, candidate donor | generic (both) | 404 | — | — |
| Hospital (same city, not managing) | generic (both) | 200, FULL, no names | "donation interval not elapsed" | 3.4 km / 5 min / 56.14 |
| Admin | generic (both) | 200, FULL | "donation interval not elapsed"; audit meta `reason` | 3.4 km / 5 min / 56.14 |

**Score and history inference (P5): analysis and decision status.**

* *Risk.* A SUMMARY viewer still receives, per candidate: the rank, `evidence_level`, `evidence_coverage`, `availability_status` and each factor's `status` (OBSERVED / COARSE / MISSING). The `response`, `acceptance` and `activity` statuses reveal **whether** a donor has a usable answer history on *other* requests, and `availability_status` whether they confirmed availability recently. Before round 2 the exact score and the baseline added the numbers needed to estimate the factor values; that part is closed (R2-3).
* *Residual after banding.* A band is coarsening, not anonymisation: two donors either side of a band edge are still distinguished, and the rank always reveals the relative order. Ranking is the product's purpose, so the order itself is accepted.
* *Recommended proportionate mitigation (NOT implemented; pending decision).* For SUMMARY viewers, return only `evidence_level` and drop the per-factor `status` values and the exact `evidence_coverage` (or bucket coverage into the same four levels). Keep `availability_status` only for `ACCEPTED_THIS_REQUEST`, which concerns this request. This changes what the patient UI explains, so it needs the product owner and the privacy officer; the published requirement is "role-specific minimal explanations", which does not settle whether "has history: yes/no" is minimal. The scoring algorithm and weights are unchanged and were not touched.


## 4. Retention and deletion

**Current behaviour:**

* **Contact rows** (`donor_contact_attempts`) are kept indefinitely. There is no purge job.
* **Deletion cascade:** deleting a request, an emergency or a donor's user row cascades to that donor's contact rows, including the snapshot. Hard-deleting a user who is referenced by sealed audit entries is refused, as already tested.
* **Live updates:** realtime events are retained for 72 hours, as before.

**Decisions pending** (owner: privacy officer / DPO, with the product owner):

1. A retention period for contact rows and score snapshots, for example the request's lifetime plus N months. Then a purge or anonymisation job.
2. Whether donor names should be visible to requesting patients and family, or only to facility staff. Today the rule matches the earlier ranking view's "manager" rule.
3. Whether storing derived response-history values per contact is proportionate, or whether only the score and coverage should be kept.
3a. **(Round 2)** Whether SUMMARY viewers keep the per-factor evidence statuses, the exact evidence coverage and the availability status (P5 recommendation above).
3b. **(Round 2)** Whether the 10-point SUMMARY score band is the right granularity, or whether SUMMARY should show only the evidence level and the rank.
4. A lawful basis and notice to donors that response history influences contact order.
5. A data-protection impact assessment, if required under India's DPDP Act 2023. **Engineering cannot determine applicability.**

## 5. Residual risks

* **Hospital and admin users see factor-level history** (counts and ages) for every eligible donor on any request they can view. This is broad for facility users who do not manage the request, and is a reviewer decision.
* **The rule depends on correct role assignment.** It is role-based and does not check purpose.
* **Same-city matching** combined with whole-km distances still reveals approximate proximity.
* **Ranks and score bands** still reveal relative order and, at band edges, small differences (P5).
* **Facility users who do not manage a request** read the full re-check reason and factor detail on any request or emergency they can see; for emergencies that is every hospital while the emergency is open. Narrowing this to the managing facility is a reviewer decision.
* **Contact rows written before round 2** may still carry a re-check reason under a later status. They are not migrated; the SUMMARY output fails closed for them ("details are visible to facility staff"), and FULL viewers still see the text.
* **Event notes written before round 2** keep their old text, including any re-check reason. No data migration was run (no production data touched). An operator with authorisation could rewrite `shortlist_confirmation` notes; this is listed for the reviewer, not done.

**Approval record:** none. The gate stays **PENDING APPROVAL**.
