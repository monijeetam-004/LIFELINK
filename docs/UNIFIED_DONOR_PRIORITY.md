# Unified Donor Priority Score (`UNIFIED_PRIORITY` 1.0.0)

* **Added:** 2026-10-31.
* **Code:**
  * `backend/app/core/unified_priority.py`: the model (pure, DB-free);
  * `backend/app/services/donor_shortlist_service.py`: eligibility, evidence loading, ordering;
  * `backend/app/api/routes/shortlists.py`: API, roles, audit.
* **UI:** `frontend/src/components/DonorShortlistPanel.tsx`.
* **Tests:** `backend/tests/test_unified_priority.py`, `test_donor_shortlist.py`, `test_donor_matching.py`, and `DonorShortlistPanel.test.tsx`.

> **What the score is, and is not.** It is a *ranking* measure for donors who have already passed every hard eligibility check. "92 / 100" means the donor ranks highly under the configured, provisional criteria. It is **not** any of these:
>
> * a probability of donation;
> * a guarantee of availability or supply;
> * clinically validated.
>
> No probabilistic model was trained. LifeLink has no real donor outcome data, and synthetic outcomes cannot calibrate one. Every weight is **provisional engineering judgement**, and needs clinical and operational review before real use.

## 1. What existed before

### 1.1 No "five-factor credibility score"

**There was no five-factor credibility score.** On 2026-10-31 the code, the paper, the documents and the full git history were searched for one, and none was found. So none was reconstructed.

Before this model, the shortlist's own order came from a provisional five-weight score, `CONFIDENCE_SHORTLIST`. It was added on the same day and never released. `UNIFIED_PRIORITY` supersedes it.

Contact rows made before the change have a NULL `ranking_strategy` and `scoring_model`. Nothing was back-filled with a guess.

### 1.2 The four lexicographic strategies (unchanged)

These live in `backend/app/core/matching_engine.py`. Each is a sort key: the first field decides, and later fields only break ties. False sorts before True.

| Strategy | Sort key | Inputs | Missing data |
|---|---|---|---|
| `NEAREST` | not accepted this request → distance → last donation date (ISO string; none = "" sorts first) → id | `has_accepted`, haversine distance, `last_donation_date` | unknown distance = +∞ (last) |
| `TIME_AWARE` | not accepted → not yet eligible → distance → id | + `is_eligible` (donation interval) | `eligible=None` treated as eligible |
| `TIME_RESILIENCE` | not accepted → not yet eligible → verification (verified < n/a < not verified) → distance → id | + `identity_verified` | — |
| `EVENT_DRIVEN` | not accepted → not available → stale availability (profile `updated_at` older than 30 days) → distance → id | + `is_available`, profile `updated_at` | `updated_at` unknown = not stale |

**Where they are used:**

* the ranking view, `GET /blood-requests/{id}/match-candidates?strategy=` and `…/match-strategies` (unchanged);
* the emergency responder view (unchanged);
* the shortlist, which can now be ordered by any of them (`?strategy=NEAREST` etc.).

In the shortlist, the strategies run through the **unchanged** `matching_engine.rank_candidates` function, with the same inputs the ranking view uses. Their semantics and tie-breaks are exactly theirs. Tests pin each strategy's documented order on a fixed fixture, and check that the shortlist order equals `rank_candidates` for each strategy.

**Interaction with the hard filters.** The shortlist applies hard filters before ranking: interval elapsed, available, and within the distance policy. So inside the shortlist, a strategy's "not yet eligible" or "not available" key is always False. This is a **safety change of input, not of semantics**: those donors are excluded instead of ranked last. The ranking view is unaffected and still shows them, ranked last.

## 2. Hard eligibility comes first (unchanged, reused)

This is `donor_shortlist_service.evaluate`. A donor who fails any check below is excluded, and counted by reason. **No score can re-admit them.**

| Check | Exclusion counter |
|---|---|
| Group compatible with the requested component | (query; the survivors are `compatible_pool`) |
| Active account; not the requester | (query) |
| Availability state `AVAILABLE` | `unavailable` |
| Donation interval elapsed under the cited jurisdiction policy (`app/core/eligibility.py`; default `IN_DCR_2020`, 120 days for every donor) | `donation_interval_not_elapsed` |
| No other active accepted commitment | `committed_elsewhere` |
| Shared distance policy (`app/core/distance_policy.py`): measured ≤ 50 km (inclusive), or same city when there are no coordinates. Unknown location = not near. A request without a location alerts nobody | `beyond_distance_limit`, `location_unknown`, `no_request_location`, `outside_alert_radius` |
| Travel feasibility: with a deadline and a measured distance, the straight-line estimate at 45 km/h must arrive before the deadline | `travel_exceeds_deadline` |

**An exclusion is not a low score.** An excluded donor is never shown as a candidate. A low score only orders an eligible donor later.

**Re-checks.**

* Eligibility is re-checked when a donor accepts, and again when a coordinator confirms. Confirmation fails into `ELIGIBILITY_RECHECK_REQUIRED` if the donor no longer qualifies.
* The donor-commitment row lock, from QA defect D1, prevents two simultaneous acceptances.

Deferrals other than the interval are **not modelled**: `DonorProfile` has no structured deferral field, and free-text medical notes are never parsed. Clinical screening at the blood centre remains authoritative.

## 3. Factor inventory

| Factor | Existing field or source | Data quality | At runtime | In unified score | Missing-data behaviour |
|---|---|---|---|---|---|
| Blood group / component compatibility | `DonorProfile.blood_type`, `app/core/compatibility` | Domain rule, cited | yes | **hard filter** | Not a candidate |
| Donation interval | `DonorProfile.last_donation_date`, `app/core/eligibility` | Self-reported date, or a verified donation | yes | **hard filter** (TIME_AWARE key) | No date = eligible (no donation on record) |
| Availability flag | `DonorProfile.availability_state` / `is_available` | Donor-set | yes | **hard filter** (EVENT_DRIVEN key) | — |
| Accepted this request | `DonorResponse` / `EmergencyResponse` ACCEPTED | Observed event | yes | `availability` = 1.0 (all strategies' first key) | — |
| Explicit availability confirmation | `DonorProfile.availability_confirmed_at` (set only by the availability endpoint) | Observed, dated | yes | `availability` | MISSING → "unknown", never "available" |
| Profile `updated_at` (EVENT_DRIVEN staleness) | `DonorProfile.updated_at` | **Weak**: any profile edit moves it | yes | not scored; it is EVENT_DRIVEN's own key | — |
| Distance | haversine of shared coordinates; `distance_policy` | Observed if shared | yes | `distance` (NEAREST, TIME_AWARE, TIME_RESILIENCE, EVENT_DRIVEN key) | Same city = 0.5 (COARSE); unknown = MISSING |
| Travel time | distance ÷ 45 km/h (labelled assumption) | Derived from distance | yes | **hard filter only** (deadline) | — |
| Location freshness | `DonorProfile.location_updated_at` | Observed, dated | yes | `freshness` | MISSING if no dated, shared location |
| Identity verification | `DonorProfile.identity_verified` | Admin-verified | yes | `verification` (TIME_RESILIENCE key) | Always observed (False = 0) |
| Facility-verified donations | `Donation.verification_status = VERIFIED` | Hospital-verified | yes | `verification` | 0 = none |
| Invitations received | `Notification` of type `blood_request.new` / `emergency.new` | Observed; delivery is **not** known | yes | `response` (denominator) | 0 → MISSING ("unknown, not unreliable") |
| Invitations answered | `DonorResponse` / `EmergencyResponse` | Observed | yes | `response` | — |
| Accepted when answering | same, `status = ACCEPTED` | Observed | yes | `acceptance` | Never answered → MISSING |
| Last response or recorded donation | response timestamps, `Donation.created_at` | Observed | yes | `activity` | MISSING |
| Days since last donation | `last_donation_date` | as above | yes | **not scored** (NEAREST tie-break only) | — |
| Response latency | `responded_at` − invitation time | Notification creation ≠ delivery time | partly | **not scored**: latency without delivery receipts would penalise donors for provider delays | — |
| Cancellation / no-show | — | **No reliable field** (withdrawals are rare and not attributable) | no | **not scored** | — |
| Completed donation after acceptance | `Donation` with `request_id` | Sparse and often self-reported | partly | **not scored** separately; verified donations already count in `verification` | — |
| Sign-ins / portal visits | sessions | Not evidence of willingness | yes | **never scored** | — |
| Urgency | request/emergency | Domain | yes | explanation and timeouts only (as in the matching engine) | — |

## 4. Formula and weights

```
score = 100 × ( Σ_observed  w_i · s_i   +   Σ_missing  w_j · 0.5 )      range 0 … 100
coverage = Σ_observed w_i                                               range 0 … 1
```

* Each `s_i` is in [0, 1].
* The weights are validated: finite, ≥ 0, a positive sum, and exactly the seven factor names. They are then normalised to sum to 1.
* Invalid configuration makes `Settings()` fail at start-up. It is **never** silently corrected.

| Factor | Default weight | Brief's proposal | Why it differs |
|---|---:|---:|---|
| availability (explicit willingness) | **0.25** | 25 % | — |
| response (answered ÷ invited) | **0.15** | 20 % "response reliability" | The brief's 20 % and its 15 % "acceptance and fulfilment" overlap. Split as 15 % answering + 10 % accepting (conditional), and fulfilment is not scored (no reliable field) |
| acceptance (accepted ÷ answered) | **0.10** | 15 % | (see above) |
| distance (1 − d / limit) | **0.20** | 20 % | Travel time is the same evidence, so it is a filter only |
| activity (response or recorded donation) | **0.05** | 10 % "portal activity" | Portal activity is not willingness. Only answers and donations count, and these overlap with `response`, hence the smaller weight |
| freshness (location age) | **0.10** | 10 % | Profile edits are not freshness evidence; only the location timestamp is |
| verification (identity + verified donations) | **0.15** | — (missing from the proposal) | An existing factor: TIME_RESILIENCE's key. The audit had to keep every relevant existing factor |

**Configuration.** The weights are set with `PRIORITY_WEIGHT_*`. `PRIORITY_MIN_COVERAGE` (0.30) sets the coverage below which the score is "unavailable". `SHORTLIST_MIN_RESPONSE_SAMPLES` (3) sets the shrinkage of small samples. `DONOR_LOCATION_STALE_DAYS` (90) and `SHORTLIST_ACTIVITY_WINDOW_DAYS` (30) are also used.

**Factor values:**

| Factor | Value |
|---|---|
| availability | Accepted this request = 1.0. Explicitly confirmed ≤ 7 days ago = 1.0, falling linearly to 0.6 at 30 days. Older (stale) = 0.3. Never confirmed = MISSING |
| response, acceptance | (k + 0.5·m) / (n + m), m = 3: small samples are shrunk towards neutral. A single "no" is not "unreliable" |
| distance | measured: 1 − d / 50; same city: 0.5 |
| activity | ≤ 7 days = 1.0, linear to 0.1 at 30 days |
| freshness | ≤ 30 days = 1.0; ≤ 90 days = 0.7; older = 0.2 |
| verification | 0.6 × identity verified + 0.4 × min(verified donations, 3) / 3 |

## 5. Missing data and uncertainty

* **Missing factors.** A missing factor contributes **nothing** of its own. Its weight is filled with the neutral 0.5, shown as its own "missing-data baseline" line. A donor with no evidence therefore drifts to 50, never towards "highly reliable".
* **Why not renormalise.** Renormalising over the observed factors only was rejected: it would let one good signal outrank well-documented donors.
* **Reported with every score:**
  * `coverage` (the share of weight observed);
  * `evidence_level`: HIGH ≥ 0.8, MEDIUM ≥ 0.5, LOW ≥ min, otherwise INSUFFICIENT;
  * a status for each factor: OBSERVED, COARSE or MISSING.
* **Fallback.** Below `PRIORITY_MIN_COVERAGE` the score is **unavailable**: it is shown as "not scored" and not as a number. Those candidates are ordered after all scored ones, by the `TIME_RESILIENCE` strategy.
* **Unknown availability.** It is never shown as available: the status is `UNCONFIRMED`.

## 6. Double counting

| Overlap | Treatment |
|---|---|
| distance ↔ travel time | Same evidence. Travel is a deadline filter only |
| response ↔ acceptance | Acceptance is *conditional on answering*, so one answer is rewarded once |
| response ↔ latency | Latency is not scored (no delivery receipts) |
| availability confirmation ↔ activity | A confirmation counts in `availability` only |
| accepted this request ↔ availability ↔ response | Counts in `availability` only. The current request is excluded from the history used for `response` and `acceptance` |
| stale location ↔ distance | Staleness lowers `freshness` only |
| eligibility / availability flag ↔ score | Hard filters; never scored again |
| verified donations ↔ completed fulfilment | Only verified donations count, once, in `verification` |

Tests in `test_unified_priority.py::test_no_double_counting_between_correlated_factors` check these.

## 7. Ties, determinism and versioning

* **Order:** score (desc), then scored before unscored, then known distance (asc; unknown last), then donor id. The order is deterministic for one data snapshot and configuration.
* **Version tags.** `MODEL_VERSION` is `1.0.0`. `PriorityConfig.fingerprint()` is a SHA-256 over the model, version, normalised weights and parameters, cut to 12 hexadecimal characters. Together they give the tag `UNIFIED_PRIORITY/1.0.0#<fingerprint>`.
* **Changing a weight changes the fingerprint.** This is tested.
* **Stored on each contact row** (migration `a7b8c9d0e1f2`):
  * `ranking_strategy`;
  * `scoring_model`, the tag;
  * `score_snapshot`: score, coverage, availability, and each factor's value. There are no names, dates, coordinates or free text.
* **Audit.** It happens at the decision boundary, not on every page view. `shortlist.contacted` records the count, strategy and model tag. `shortlist.confirmation` and the existing response events record the rest. The audit log is hash-chained, and its tamper evidence has the limits described in `docs/AUDIT_CHECKPOINTS.md`.

## 8. API, roles and privacy

* **Choosing the order.**
  * `GET /blood-requests/{id}/shortlist?strategy=` (and the emergency equivalent) orders the shortlist by `UNIFIED_PRIORITY` (default) or one of the four strategies.
  * `POST …/contact-next?strategy=` contacts the next batch in that order.
  * An unknown strategy returns **422**.
  * Automatic escalation keeps the strategy of the last batch.
* **Who sees what.**
  * Hospital and admin users who may view the request get the full factor detail.
  * Patients and family members get **minimal summaries**: no counts, dates or other donors' histories. Since 2026-11-03 they also get no weights, factor values or contributions (fields are null, and `components` is empty), and only a generic text for a failed re-check.
  * **Since privacy round 2 (2026-11-03)** patients and family also see the priority score (and the score at contact) in **10-point bands**, labelled "about N/100", and no missing-data baseline. Ranking still uses the exact score; weights and the algorithm are unchanged.
  * Phone numbers, medical notes and coordinates are never returned.
  * **Distances are rounded:** to 0.1 km for hospital and admin users, and to whole kilometres (at least 1) for everyone else. *Correction (round 2):* an earlier version of this paragraph said distances had been returned at full precision before 2026-11-03. They had not: `haversine_km` already rounded to 0.1 km for every viewer; the 2026-11-03 change was the whole-km figure for patients and family.
  * **Travel time is a separate figure** (distance ÷ 45 km/h, whole minutes). For hospital and admin users it comes from the 0.1-km distance; for patients and family it is computed from the **whole-km** distance, so it cannot be used to recover a finer distance (round 2).
  * **Re-check reasons** are shown only to hospital and admin users and are kept in the admin-only audit entry `shortlist.confirmation`. The request/emergency event history, which patients, family, donors and facilities read, says only that a confirmation did or did not go through. For patients and family the label "the eligibility re-check did not pass" is chosen by the contact status `ELIGIBILITY_RECHECK_REQUIRED`, not by the wording.
  * See `docs/release/PRIVACY_SECURITY_REVIEW.md` (round 1 §3, round 2 §3b) for the defects, tests and the pending decision on per-factor evidence statuses.
* **Configuration visibility.** `GET /shortlist/policy` shows the weights only to hospital and admin users.
* **No client-supplied scores.** Every value is computed on the server. The API takes nothing but the strategy name.

## 9. State model (unchanged)

The model runs through these states:

* **Eligible**, then **Ranked**: neither state is stored.
* **Contacted**: QUEUED, then AWAITING_RESPONSE.
* **Answered**: ACCEPTED, DECLINED, UNAVAILABLE ("not now"), TIMED_OUT, NOTIFICATION_FAILED or WITHDRAWN.
* **CONFIRMED_FOR_COORDINATION**: set by the request owner after the re-check.
* **Donation verified**: set by the hospital.

Units are reserved and secured through the separate inventory, pathway and hand-off workflow.

The score **never** changes a state. "Still needed" is the requirement minus the confirmed donors (minus units fulfilled or reserved from inventory). Two other outcomes are reported, and kept apart: nobody eligible (`NO_ELIGIBLE`, with the reasons), and nobody answered (timeouts plus escalation to blood banks).

## 10. Evaluation

See `docs/QA/evidence/unified-priority-2026-10-31/ranking_comparison.json`. It was produced by `backend/scripts/evaluate_ranking_strategies.py`, which runs read-only inside one rolled-back transaction. The results are summarised in `docs/remediation/UNIFIED_PRIORITY_REPORT.md`.

**The comparison is synthetic.** The seeded response history and the simulated answers come from the same personas. On those outcomes, a history-using model is favoured **by construction**. The independent-outcome control is there to show this.

## 11. Release gates (open)

The current status of every gate is in `docs/release/RELEASE_GATES_2026-11-03.md`. The clinical and privacy review packs are in `docs/release/`.


* **Clinical and operational review** of:
  * the hard filters;
  * the interval policy (120 days for everyone, because sex is not recorded);
  * every weight and threshold.
* **Real response data.** Weights or any calibration need it. None exists.
* **Delivery receipts** (e-mail and SMS) are needed before response latency could be used fairly.
* **Privacy review** of storing response-history aggregates in contact snapshots.
* **Deferrals** other than the interval are not modelled.
