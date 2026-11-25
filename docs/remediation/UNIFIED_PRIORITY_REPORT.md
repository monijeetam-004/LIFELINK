# Unified Donor Priority Score: implementation report (2026-10-31)

> **Release gates, 2026-11-03:** see `docs/release/RELEASE_GATES_2026-11-03.md`. Decision: **NOT READY**, because of pending clinical and privacy approvals, blocked real integrations and no independent audit anchoring. That round fixed three privacy defects (P1–P3) and added a leakage-free evaluation, superseding §F's caveat-only result.
>
> **Privacy remediation round 2, 2026-11-03:** round 1's re-check fix was incomplete (the reason was still in the request/emergency event history, readable by patients, family and other donors), and the travel-time estimate and exact score let patients and family recover finer distances. Fixed and tested by role against the real API; see `docs/release/PRIVACY_SECURITY_REVIEW.md` §3b. Decision unchanged: **NOT READY**. The latent evaluation has a further limitation, below (§F, "Round 2 limitation").

The model itself is described in [`docs/UNIFIED_DONOR_PRIORITY.md`](../UNIFIED_DONOR_PRIORITY.md). The evidence is in `docs/QA/evidence/unified-priority-2026-10-31/`.

## A. Repository state

* **Branch and start point.** The branch is `finalize/audit-remediation-2026-10-31`. The work started from `365fe04` (the expected stable commit, verified), with a clean working tree.
* **Commits:**
  * `ecf5c72`: model, strategy selection, contact snapshots, UI;
  * `2c80172`: history exclusion, comparison, browser spec, documentation, paper;
  * then the commit that adds this report (branch head; the tests ran at `2c80172`, and later commits change documentation and evidence only).
* **Migration.** `a7b8c9d0e1f2_unified_donor_priority` is additive. `donor_contact_attempts` gains three nullable columns, `ranking_strategy`, `scoring_model` and `score_snapshot`. Existing rows stay NULL; nothing was back-filled. It is the single head after `f9a0b1c2d3e4`.

**Files changed (excluding evidence):**

| Area | Files |
|---|---|
| Model | `backend/app/core/unified_priority.py` (new) |
| Service | `backend/app/services/donor_shortlist_service.py` |
| API | `backend/app/api/routes/shortlists.py`, `backend/app/schemas/shortlist.py` |
| Configuration and storage | `backend/app/core/config.py`, `backend/.env.example`, `backend/app/models/donor_contact.py` |
| Evaluation | `backend/scripts/evaluate_ranking_strategies.py` (new) |
| Backend tests | `backend/tests/test_unified_priority.py` (new), `backend/tests/test_donor_shortlist.py` |
| Frontend | `frontend/src/components/DonorShortlistPanel.tsx` (+ test), `frontend/src/lib/gapApi.ts`, `frontend/e2e/remediation_shortlist_demo.spec.ts` |
| Documentation | `docs/UNIFIED_DONOR_PRIORITY.md` (new), `docs/API_MAP.md`, `docs/remediation/*`, the paper (`main.tex`, PDF, Overleaf zip) |

**Baseline before any change.** Run at `365fe04`:

```
pytest tests/test_donor_matching.py tests/test_donor_shortlist.py tests/test_matching_and_map.py tests/test_matching_engine_phase9.py tests/test_audit_d4_distance_policy.py
```

Result: **63 passed** (`baseline_targeted.log`).

## B. Existing strategies

These live in `app/core/matching_engine.py` and are **unchanged**.

| Strategy | Key |
|---|---|
| `NEAREST` | not accepted → distance → last-donation date → id |
| `TIME_AWARE` | not accepted → not eligible → distance → id |
| `TIME_RESILIENCE` | not accepted → not eligible → verification → distance → id |
| `EVENT_DRIVEN` | not accepted → not available → stale profile → distance → id |

**Evidence that they still work:**

* `test_matching_engine_strategy_keys_are_unchanged`: each strategy's documented order on a fixed five-donor fixture.
* `test_the_four_strategies_order_exactly_as_the_matching_engine`: the shortlist order equals `rank_candidates`.
* The existing `test_donor_matching.py`, `test_matching_engine_phase9.py` and `test_matching_and_map.py` all pass in the full run.
* The ranking-view endpoints `match-candidates` and `match-strategies` are untouched.

**One unavoidable difference in the shortlist only.** Hard filters run before any ordering, so "not eligible" and "not available" donors are excluded instead of ranked last. This is a safety boundary. The strategies' semantics did not change, and the ranking view still shows those donors.

## C. Unified scoring model

**Formula.**

```
P = 100 × ( Σ_observed w_i·s_i + 0.5 · Σ_missing w_j )
```

* `P` is in the range 0–100.
* `coverage` = Σ_observed w_i.
* If coverage is below 0.30, the donor is **not scored** and is ordered by `TIME_RESILIENCE` after all scored donors.

**Weights** (provisional engineering judgement; set through `PRIORITY_WEIGHT_*`, validated at start-up):

| Factor | Weight | Source |
|---|---:|---|
| availability | 0.25 | Accepted this request; otherwise `availability_confirmed_at` |
| response | 0.15 | Answers ÷ invitations on **other** requests |
| acceptance | 0.10 | Accepted ÷ answered on other requests |
| distance | 0.20 | Shared coordinates and the distance policy |
| activity | 0.05 | Last response or recorded donation |
| freshness | 0.10 | `location_updated_at` |
| verification | 0.15 | `identity_verified` and facility-verified donations |

The full factor inventory, the reasons for departing from the brief's 25/20/20/10/10/15 split, and the factors deliberately **not** scored (latency, no-shows, fulfilment, sign-ins, days since donation) are in the model document, §3–4.

**Missing data.** A missing factor contributes 0, plus a neutral 0.5 baseline shown as its own line. Weights are not renormalised. The score carries its evidence coverage, an evidence level and a status per factor. Unknown availability has status `UNCONFIRMED`, never "available".

**Double counting.**

* Travel time is a filter only.
* Acceptance is conditional on answering.
* An answer to the current request counts as availability only. `_history` now excludes the current request or emergency; this was found and fixed in this round, see the test `test_answering_this_request_is_not_counted_again_as_history`.
* Staleness counts in freshness only.
* Eligibility and the availability flag are filters, not scores.

**Ties.** Score, then scored before unscored, then known distance, then donor id.

**Versioning and audit.**

* The model tag is `UNIFIED_PRIORITY/1.0.0#<12-hex config fingerprint>`.
* Every contact row stores the strategy, the tag and a factor snapshot. The snapshot has no names, dates, coordinates or text.
* The audit events `shortlist.contacted` (count, strategy, tag) and `shortlist.confirmation` are recorded at the decision boundary, not on page views.

## D. Application integration

**API.** Additive and backward compatible: existing fields are kept, and `score` is still on a 0–1 scale.

* `?strategy=` on the shortlist GET and on `contact-next`. An unknown value returns 422.
* A `priority` block per candidate; `strategy`, `scoring_model` and `explanation_level` on the shortlist.
* `ranking_strategy` and `scoring_model` on each contact.
* `GET /shortlist/policy` adds the model, version and fingerprint. Weights are shown only to hospital and admin users.

**UI** (`DonorShortlistPanel`):

* a **Ranking** selector: unified (default) or one of the four strategies;
* "Priority is a ranking measure (0–100), not a probability of donation";
* per candidate: the score or "not scored (too little data)", the evidence level, the availability status, distance, travel estimate and next action;
* **Why this rank**: a factor table whose points add up to the score;
* per contacted donor: the strategy and the score at the time of contact.

**States.** The score never changes a state. Eligible, ranked, contacted, awaiting, accepted, confirmed (coordinator, after re-check), donation verified, and units reserved or secured remain separate. The shortage, "nobody eligible" and "nobody answered" are reported separately.

**Privacy.**

* Patients and family members get summaries without counts or dates.
* Donors and unrelated users get 404, which is audited.
* Phone numbers, medical notes and coordinates are never returned.
* The server computes everything; clients send only a strategy name.

## E. Testing

Every check below was run on 2026-10-31.

Environment: the 2-vCPU sandbox, PostgreSQL 16, Redis 7, clamd, and the official MinIO and Mailpit binaries. Evidence folder: `docs/QA/evidence/unified-priority-2026-10-31/`.

| Check | Command | Commit | Result | Evidence |
|---|---|---|---|---|
| Baseline (before changes) | 5 matching and shortlist test files (§A) | `365fe04` | 63 passed | `baseline_targeted.log` |
| Backend, full | `CLAMD_TEST_REQUIRED=1 S3_TEST_*=<MinIO> SMTP_TEST_*=<Mailpit> QA_ROUTE_LOG=… .venv/bin/python -m pytest -p no:cacheprovider -o addopts= -q -rfEs --junitxml=…` | `2c80172` | **1,471 passed, 1 skipped (live Neo4j), 0 failed** (37 min) | `backend_final.log`, `backend_junit_final.xml`, `backend_final.meta` |
| API coverage | route log of that run | `2c80172` | All 169 operations called, each with a 2xx except `POST /admin/graph/resync` (no Neo4j) | `routes_final.log` |
| Ruff, migrations | `ruff check .`; `alembic heads` | `2c80172` | clean; single head `a7b8c9d0e1f2`. Schema = models, checked by `test_alembic_head_schema_matches_orm_metadata` in the full run | — |
| Vitest | `npx vitest run` | `2c80172` | **181 / 181** (23 files) | `vitest_final.log` |
| tsc, ESLint, build | `npx tsc --noEmit`; `npx eslint .`; `npm run build` | `2c80172` | exit 0, exit 0, built | `tsc.txt`, `eslint.txt`, `build.txt` |
| Playwright, CI-identical | `docs/QA/tools/e2e_ci_harness.sh <out>` | `2c80172` | **107 passed, 7 skipped, 0 failed.** Skipped: 2 container/clean-start-only specs and the 5 demo-stack specs. Invariants after E2E: OK | `playwright_ci_harness.log`, `playwright/` |
| Browser, demo stack | `DEMO_FRONTEND_URL=… DEMO_RESULTS=… npx playwright test e2e/remediation_shortlist_demo.spec.ts` | `ecf5c72` + the history fix | **5 / 5**: scenarios A–D, plus the breakdown test (table sums to the score). Axe 0 violations and no overflow at 375/768/1280 | `shortlist_ui_spec.txt`, `screenshots/` |
| Demo seed and walkthrough (default = unified) | `DEMO_GUIDE.md` §1 and §4 | same | Seed: 400 synthetic donors, invariants 21/21 OK. Walkthrough: A satisfied 3/3; B partial 2/3 (1 short); C no eligible; D #1 timed out and #2 confirmed; E 80 km donor got 0 alerts; F/G secured re-plan and idempotent hand-off. Shortlist GET median 127 ms over 8 calls (observation, not a benchmark) | `demo_seed.txt`, `demo_walkthrough.txt`, `demo_results.json` |
| Offline comparison | `python -m scripts.evaluate_ranking_strategies --out …` | same | §F | `ranking_comparison.json`, `ranking_comparison.txt` |

**Not run:**

* the clean starts (native and container), last run at `003e19e`, before this feature;
* failure injection, performance and load benchmarks: historical;
* the real Neo4j, Gmail, MSG91 and geocoder: unavailable here.

The tests written during development failed first and were then fixed in code or in the test fixture. The fixture fixes were for a wrong setup, not a weakened assertion. The full runs above are the first runs on the final code.

**Scenario coverage (brief §14).**

| # | Scenario | Test |
|---|---|---|
| A | The four strategies | `test_the_four_strategies_order_exactly_as_the_matching_engine`, `test_matching_engine_strategy_keys_are_unchanged`, the existing matching suites, and the vitest "offers the four original strategies…" |
| B, C | Controlled fixture, available donors | `test_controlled_fixture_selects_the_expected_candidate` |
| D | Unknown availability | `test_unknown_availability_is_missing_never_confirmed` |
| E, P | High score but no answer; a response changes state but secures nothing | `test_high_scoring_non_responder_is_never_counted_as_confirmed`, `test_response_lifecycle_escalation_and_confirmation`, walkthrough D |
| F | Ineligible donor excluded | `test_ineligible_donor_is_excluded_whatever_its_evidence` |
| G | Beyond the distance limit | `test_audit_d4_distance_policy.py` (9 tests), walkthrough E (80 km donor: 0 alerts) |
| H | Missing data | `test_missing_data_policy_neutral_baseline_and_coverage`, `test_unscored_candidates_follow_the_scored_ones`, vitest "never shows a made-up number…" |
| I | Stale location | `test_stale_location_is_surfaced_in_freshness_only` |
| J | Double counting | `test_no_double_counting_between_correlated_factors`, `test_answering_this_request_is_not_counted_again_as_history` |
| K | Ties | `test_equal_scores_break_ties_deterministically` |
| L | Invalid configuration | `test_invalid_configuration_is_rejected` |
| M | Explanation reconciles | `test_explanations_reconcile_with_the_score`; browser test "unified priority breakdown reconciles…" (sums the rendered table) |
| N | Versioning | `test_model_version_and_fingerprint_trace_weight_changes`, `test_strategy_is_selectable_validated_and_recorded_on_contacts` |
| O | Concurrency | `test_concurrent_contact_requests_never_duplicate_contacts`; the existing reservation, hand-off and commitment race tests |
| Q | Reservation and failover recovery | `test_audit_c15_c21_c20_fulfillment.py` (unchanged) |
| R, S | Escalation and shortage | `test_timeout_marks_unanswered_and_escalates`, vitest "separates found … still-needed", walkthrough B, browser B |
| T | Roles | `test_explanation_detail_depends_on_role_and_policy_hides_weights`, `test_access_control_and_privacy` |
| U | Demo scenarios | walkthrough A–G and browser A–D, re-run under the unified default |

## F. Evaluation

**Method.** `scripts/evaluate_ranking_strategies.py` on the demo database:

* **Dataset:** SYNTHETIC. The seed-1337 network plus the seed-20261031 shortlist donors.
* **Requests:** 80, one URGENT request for 2 donors per seed city × recipient group (O+, A+, B+, AB+), at a verified hospital.
* **Eligible candidates per request:** median 29.5, minimum 12, maximum 55.
* **Read-only:** everything runs in one rolled-back transaction.

Contacting was replayed in the shortlist's own batch size. Results are in `ranking_comparison.json` and `ranking_comparison.txt`.

| Measured | Unified | Nearest | Time-aware | Time+resilience | Event-driven |
|---|---:|---:|---:|---:|---:|
| Eligibility violations in returned rankings | 0 | 0 | 0 | 0 | 0 |
| Persona outcomes: demand met (of 80) | 68 | 68 | 68 | 68 | 68 |
| Persona outcomes: median contacts when met | 4 | 12 | 12 | 12 | 12 |
| Persona outcomes: median batches to first acceptance | 1 | 2 | 2 | 2 | 2 |
| Persona outcomes: accepted per contact | 0.195 | 0.117 | 0.120 | 0.124 | 0.120 |
| Independent outcomes (p = 0.35): every metric | identical across modes | | | | |
| Median `evaluate()` time, ms (2 vCPUs, about 1,400 donors) | 116 | 123 | 119 | 116 | 126 |

**Coverage and sensitivity.** Median evidence coverage was 0.4, and no candidate fell below the unscored threshold. With the response and acceptance weights set to 0, the first contact batch was the same set in 48 of 80 requests.

**Interpretation.**

* **Persona outcomes are biased by construction.** The seed generated each donor's response history from the same persona that decides its simulated answer. A model that reads history must therefore do better on these outcomes. The result shows that the evidence reaches the ranking, and nothing about real donors.
* **The independent control behaved as expected.** Its outcomes do not depend on who is contacted, so every mode gives the same numbers, as they must. Any difference would have pointed at a bug.
* **No probability was trained, calibrated or claimed.** There is no real outcome data.

**Round 2 limitation of the latent model (2026-11-03, documented, code unchanged).** `latent_order` in `scripts/evaluate_ranking_strategies.py` resamples only the invited / answered / accepted **counts**. Every other input is taken as stored, including the last-response date and the last recorded donation that drive the `activity` factor. A donor whose resampled history has no answers can therefore still score as "responded recently", and a donor with sampled answers can have no last-response date. Some synthetic profiles are internally inconsistent, and the effect on the comparison is not separated out. The proportionate fix is to resample (or blank) the last-response date together with the counts and re-run; that was not done in round 2, which was limited to privacy. Either way the result is a synthetic illustration: **no real-world effectiveness is claimed.**

## G. Outstanding release gates

1. Clinical and operational review of the hard filters, the interval policy (120 days for everyone, because sex is not recorded), every weight and every threshold.
2. Real, permitted response and fulfilment data, before any weight is tuned or any calibration is attempted.
3. Delivery receipts from e-mail and SMS providers, before response latency could be used fairly. Gmail and MSG91 are still untested against the real services.
4. A privacy review of the response-history aggregates stored in contact snapshots, and of retention.
5. Deferrals other than the interval are not modelled.
6. The comparison is synthetic only, and the latent model keeps stored last-response dates while resampling the counts (§F). Performance was measured only on the 2-vCPU sandbox.
7. The earlier open items still apply: Neo4j, the geocoder, independent anchoring of the audit log, and historical performance figures.

## H. Final status

**PASS WITH DOCUMENTED LIMITATIONS.**

**What is implemented and verified:**

* all four strategies remain functional and selectable;
* the unified mode is separately named and versioned;
* hard eligibility precedes ranking;
* states remain distinct;
* missing data is visible;
* explanations reconcile with the score;
* the full backend, frontend and browser suites pass on the final code.

**What is not established:** the weights are provisional, and the comparison is synthetic and biased by construction. Nothing here shows real-world performance, and clinical review is still required (§G).
