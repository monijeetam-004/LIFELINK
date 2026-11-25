# LifeLink: final remediation report (2026-10-31)

* **Scope:** this round answers the independent paper-versus-code audit of 2026-10-31, which checked HEAD `cf7ee37`, tag `qa-final-2026-10-20`. It also implements the requested confidence-based donor shortlist.
* **Branch:** `finalize/audit-remediation-2026-10-31`, from `cf7ee37`.
* **Final commit:** the head of the branch, i.e. the commit that adds this report. Code was last changed in `003e19e`, which is the commit the final backend run tested.
* **Companion documents:**
  * [TRACEABILITY_MATRIX.md](TRACEABILITY_MATRIX.md): every one of the audit's 116 claims, with its status;
  * [DEMO_GUIDE.md](DEMO_GUIDE.md): commands and a click-by-click demonstration;
  * [../AUDIT_CHECKPOINTS.md](../AUDIT_CHECKPOINTS.md): checkpoint setup, rotation and limits.

## 1. Outcome in one paragraph

**Code fixes.** The audit raised four contradictions, and all four are resolved:

* **D4:** blood-request alerts now apply the distance limit.
* **E4:** the paper's tamper-evidence claim is now accurate, and the container stack enforces checkpoints.
* **G8:** the 12-check count is corrected.
* **A11:** Fig. 1 now says 63 routes.

The three incomplete fulfilment workflows are implemented and tested:

* **C15:** a failover cannot leave a plan that looks secured but holds no units;
* **C21:** a reserved plan is handed to its shipments without being released;
* **C20:** a failed linked shipment re-plans its emergency.

The Analytics screen shows both forecast baselines (F9). A stale Neo4j projection is detected (A8). The donation interval is now a cited, configurable Indian policy, with clinical review still pending (D3). The confidence shortlist is built end to end, from service and API to UI, a synthetic dataset and the demo.

**Test runs.** The backend, Vitest and Playwright suites, and the native clean start, were re-run on the remediated code: 

* **backend:** 1,452 passed, 1 skipped (live Neo4j), 0 failed;
* **Vitest:** 178 / 178;
* **Playwright:** 106 passed, 6 skipped. One flaky test failed on a browser-protocol error and passed on re-run;
* **native clean start:** 11 / 12 (Neo4j blocked);
* **container clean start:** 11 / 12 (Neo4j blocked).

The first container run found a real defect introduced by this round: the worker could not start with the required checkpoint key. It was fixed and re-verified. All 169 API operations were called in the backend run.

**Not done.** These cannot be closed here:

* **Real-service checks:** Neo4j, Gmail, MSG91 and a live geocoder are still untested against their real services.
* **Clinical validation:** the eligibility policy and the shortlist weights have not been clinically validated.
* **Audit anchoring:** the checkpoint log is still local to the deployment (no external anchor).
* **Historical measurements only:** performance, load and failure injection were not re-measured.

**The project is therefore not "fully complete"; §10 lists what remains.**

## 2. What changed

**Commits** (on top of `cf7ee37`):

| Commit | Content |
|---|---|
| `022adba` | D4 shared distance policy; cited eligibility policy (D3); migration `f9a0b1c2d3e4` |
| `0d6c880` | C15 secured failover; C21 plan-to-shipment hand-off; C20 re-plan on a linked shipment's failure |
| `04f1a6b` | Confidence shortlist (service, API, contact lifecycle, timeouts, demo simulator); E4 checkpoint operations |
| `9fb1cf1` | Frontend: shortlist panel, hand-off and "securing" state, both forecast baselines (F9) |
| `5fc9813` | Synthetic shortlist dataset (seed 20261031, 400 donors), API walkthrough, SMS guard for synthetic accounts |
| `70ef821` | A8: graph answers are cross-checked against the PostgreSQL optimum |
| `960e749`, `014cfca`, `57bac84`, `003e19e`, then the documentation commit | These cover:<ul><li>test-setup fixes after the full run;</li><li>the walkthrough's presenter mode;</li><li>the corrected "donors required" wording;</li><li>the seeded verifier (invariant #31);</li><li>the shortlist browser spec;</li><li>the SOS City field and the location hints;</li><li>the E2E setups;</li><li>the emergency shortlist test;</li><li>the worker checkpoint-key fix;</li><li>the harnesses;</li><li>then the paper, documents and evidence.</li></ul> |

**New backend modules:**

* `app/core/distance_policy.py`
* `app/services/donor_shortlist_service.py`
* `app/services/pathway_handoff_service.py`
* `app/services/demo_simulator.py`
* `app/api/routes/shortlists.py`
* `app/models/donor_contact.py`
* `app/schemas/shortlist.py`

**Schema:** one migration, `f9a0b1c2d3e4`:

* a new `donor_contact_attempts` table;
* donor location and availability-confirmation timestamps;
* the pathway recovery state;
* links from a shipment to its emergency and pathway;
* a `TRANSFER` reservation-ledger action.

**New API operations (9; 160 → 169):**

* `GET /shortlist/policy`
* `GET` and `POST …/shortlist`, `…/shortlist/contact-next` and `…/shortlist/contacts/{id}/confirm`, each for blood requests and for emergencies
* `POST /demo/shortlist/{subject}/{id}/simulate` (DEMO_MODE, admin, synthetic donors only)
* `POST /emergencies/{id}/pathway/handoff`
* `POST /admin/audit-log/checkpoint`

**Frontend:**

* the SOS form gained a **City** field; both the SOS and blood-request forms now say that without a location no donor is alerted (D4 made this true);
* `components/DonorShortlistPanel.tsx`, mounted in the patient/family request drawer, the emergency drawer and the hospital request page;
* the hand-off button and reservation state on `/decision-transparency`;
* both baselines on `/analytics`.

**Existing tests that had to change.** Some existing tests changed because their scenario relied on the defects that were fixed. Almost all of these changes are to setup only:

* Nationwide alerts for requests with no location (D4). The donor and the request now share a city, and each change carries an "Audit D4" comment.
* The hard-coded 56-day interval (D3). The appointment test now reads the configured interval.

The backend files changed this way:

* `test_donor_appointments`, `test_emergencies_and_notifications`, `test_emergency_network`, `test_phase18_notifications`, `test_phase_f_realtime`, `test_qa_concurrency`, `test_sms`;
* earlier in the round: `test_alert_watcher`, `test_gap_features_api`, `test_phase27_notification_scaling`, `test_phase31_domain_fixes`, `test_donor_matching`.

The Playwright files changed this way: `e2e/setup.ts` (the E2E donor now has a city), and the blood-request, SOS and live-bell specs, which now give their request a city.

**Exceptions.** In two places an assertion was changed on purpose, because it encoded the defect being fixed:

* `test_alert_watcher.py` asserted that a donor about 6,000 km away, with no personal radius, **was** alerted for a blood request. It now asserts that the donor is **not** alerted. A new assertion checks that an unlocated donor in another city is not alerted either.
* `test_donor_matching.py` expected `eligible_on` = last donation + 56 days. It now expects last donation + the configured policy interval.

Both changes carry a comment. No assertion was weakened and no test was deleted. The first full backend run on the remediated code had 11 failures, all of this kind. That log is kept as `backend_run1_before_test_setup_fixes.log`. The first Playwright run had 3 failures of the same kind, kept as `playwright_run1_3_failed_alert_scope.log`.

## 3. The "existing five-factor credibility score"

**It does not exist in this repository.** On 2026-10-31 the code, the paper, every document and the full git history (`git log --all`) were searched for a credibility score, "five-factor" or "5-factor", and nothing was found.

What does exist is the matching engine's per-candidate factor set, `app/core/matching_engine.py`, `MatchFactors`:

* compatible;
* available;
* donation-interval eligible;
* identity verified;
* distance and within the alert radius;
* has accepted;
* days since the last donation;
* stale availability;
* urgency.

These factors are ranked by **four lexicographic strategies**, which have no weights. The strategies were **not changed**. As the brief instructs, no five factors were invented. The shortlist is a new, explicitly named policy, `CONFIDENCE_SHORTLIST`. It reuses the same stored signals and adds response history, explicit availability confirmations and location age. The paper says this.

## 4. Final donor-shortlist logic

> **Superseded later on 2026-10-31.** The ordering score described in this section (`CONFIDENCE_SHORTLIST`) was replaced by the Unified Donor Priority Score (`UNIFIED_PRIORITY` 1.0.0). The hard filters, contact lifecycle and counts are unchanged. See `docs/UNIFIED_DONOR_PRIORITY.md` and `docs/remediation/UNIFIED_PRIORITY_REPORT.md`.

**1. Hard eligibility.** A failed check excludes the donor, whatever the score. The exclusion counter for each check is in brackets:

* group compatible with the requested **component**, and an active account. Both are applied in the query; the survivors are reported as `compatible_pool`;
* availability state AVAILABLE (`unavailable`);
* donation interval elapsed under the active policy (`donation_interval_not_elapsed`);
* no other active accepted commitment (`committed_elsewhere`);
* inside the shared distance policy (`beyond_distance_limit`, `location_unknown`, `no_request_location`, `outside_alert_radius`);
* travel feasibility: when a deadline exists and a distance was measured, a labelled straight-line estimate at 45 km/h must arrive before the deadline (`travel_exceeds_deadline`).

Donors already contacted for this request are never invited twice. The exclusion counts are returned with every shortlist.

**2. Score.** It orders the eligible candidates only. These are provisional engineering weights, set in `SHORTLIST_WEIGHT_*` and normalised to sum to 1:

| Feature | Default weight | Definition |
|---|---|---|
| verification *v* | 0.35 | 0.6 if identity is verified, plus up to 0.4 for facility-verified donations |
| reliability *r* | 0.20 | Answered / invited. With fewer than `SHORTLIST_MIN_RESPONSE_SAMPLES` (3) invitations, it is shrunk towards 0.5, so **no history = 0.5, never "unreliable"** |
| activity *a* | 0.15 | Recency, within `SHORTLIST_ACTIVITY_WINDOW_DAYS` (30), of an explicit availability confirmation, a response or a recorded donation. **A sign-in is not activity** |
| distance δ | 0.20 | 1 − d / limit for a measured distance; 0.5 for "same city, distance unknown" |
| freshness *f* | 0.10 | Age of the shared location (stale after `DONOR_LOCATION_STALE_DAYS`, 90) |

*S* = 0.35*v* + 0.20*r* + 0.15*a* + 0.20δ + 0.10*f*. Ties break by donor id. Each candidate carries its factor values and a short explanation. No medical notes, phone numbers or coordinates are exposed, only a rounded distance and its basis.

**3. Contact.**

* Donors are contacted in bounded batches: `SHORTLIST_CONTACT_MULTIPLIER` (2) × donors still needed, capped at `SHORTLIST_MAX_BATCH` (10).
* Contact is idempotent and deduplicated. Donors who already received the open alert are tracked, not notified again.
* Unanswered invitations become TIMED_OUT after `SHORTLIST_RESPONSE_TIMEOUT_MINUTES`: 30, or 10 for CRITICAL. The deadline monitor applies the timeouts.
* With `SHORTLIST_AUTO_ESCALATE`, the next ranked donors are contacted automatically.
* `DONOR_ALERT_MODE` selects between:
  * `broadcast` (default): every in-scope eligible donor gets the open alert, as before, and the shortlist tracks and invites the top few;
  * `shortlist`: only ranked batches are contacted.

**4. Response states** (`DonorContactStatus`):

* QUEUED
* AWAITING_RESPONSE
* NOTIFICATION_FAILED
* ACCEPTED
* DECLINED
* UNAVAILABLE ("not now")
* TIMED_OUT
* ELIGIBILITY_RECHECK_REQUIRED
* CONFIRMED_FOR_COORDINATION
* WITHDRAWN

An accepted donor becomes CONFIRMED_FOR_COORDINATION only when the request owner confirms and the eligibility and commitment checks pass again. In-app notifications are tracked as sent or read. **Delivery to a phone or mailbox is never claimed.**

**5. Counts returned separately:**

* eligible candidates;
* contacted;
* awaiting response;
* accepted;
* confirmed;
* declined;
* timed out;
* units already fulfilled (`units_fulfilled`), and units reserved from inventory (`units_reserved_inventory`);
* still needed.

The resulting states are NOT_STARTED, AWAITING, PARTIAL, SATISFIED, NO_ELIGIBLE and CLOSED. The text never uses "guaranteed" or "available" for a scored donor.

## 5. Eligibility policy (D3)

| Policy | Interval | Source |
|---|---|---|
| `IN_DCR_2020` (**default**) | 120 days for every donor | Drugs and Cosmetics (Second Amendment) Rules, 2020, G.S.R. 166(E), 11 March 2020, Schedule F Part XII-B, H(4): "once in three months (90 days) for males and four months (120 days) for females". |
| `US_21CFR630` | 56 days | 21 CFR 630.15(a)(1)(i), "no more than once in 8 weeks". This is the value LifeLink used before. |

How the default is applied:

* LifeLink does not record donor sex, and the policy never infers it. The stricter 120 days therefore applies to everyone.
* This never makes an ineligible donor eligible. It can keep an eligible male donor waiting up to 30 extra days.
* The clause was read from a published copy of the gazette notification. It needs **clinical and legal review before any real use.**
* Deferrals other than the interval are not modelled, and free-text medical notes are never parsed.
* The check is a pre-screen. It does not replace the blood centre's screening, crossmatching, antibody screening or TTI testing.

## 6. Synthetic data and seed/reset commands

* **Base (unchanged):** `scripts/seed_demo_data.py`, seed 1337: 1,000 donors plus 1 demo donor, 61 facilities in 20 cities, 85 routes. The E1–E4 experiments use this data, and it was not modified.
* **New:** `scripts/seed_shortlist_demo.py`, seed 20261031: **400 synthetic donors** (default `--donors 400`).
  * **Marking:** e-mail `sim-<persona>-<key>@synthetic.lifelink.example`, names prefixed `[SYNTHETIC]`, no phone number.
  * **Scenario clusters:** four, A to D, around fictional "Demopur" hospitals.
  * **Other donors:** the rest are spread over the 20 seed cities. They vary in group, availability, interval state (consistent with the active policy), shared/unshared and fresh/stale location, verification, verified donations, confirmations, and response history (good, poor or unknown).
  * **Idempotent:** keyed by e-mail; it refuses to run in production.
  * **Measured on the demo DB, 2026-10-31 (`demo_seed.txt`):**
  * scenario clusters A 11, B 20, C 7, D 2 (40 donors);
  * population 360 donors: 112 with a stale location, 127 with no location, 72 inside the donation interval, 43 unavailable, 174 identity-verified;
  * response history: 60 expired historical requests with 429 responses;
  * **total 400.**
  * A second run changed nothing (still 400). All 20 database invariants and the audit chain passed.
* **Seed:**
  ```bash
  alembic upgrade head
  python -m scripts.seed_demo_data
  python -m scripts.seed_shortlist_demo
  ```
* **Reset:** re-create **only** the dedicated `lifelink_demo` database, then seed again (DEMO_GUIDE.md §1).

## 7. Tests that were actually run

**Environment.**

* Sandbox: 2 vCPUs, 8 GB RAM.
* Services: PostgreSQL 16, Redis 7.0.15, clamd (ClamAV 1.5.4), official MinIO and Mailpit binaries as native processes.
* Runtimes: Python 3.11.17 and Node 22.22.
* Docker: locally built base images, because the sandbox cannot pull from registries.
* Evidence folder: `docs/QA/evidence/remediation-2026-10-31/`. Everything below is a run executed on 2026-10-31.

| Check | Exact command | Commit | Result | Evidence |
|---|---|---|---|---|
| Backend, full (final) | `CLAMD_TEST_REQUIRED=1 S3_TEST_*=<real MinIO> SMTP_TEST_*=<real Mailpit> QA_ROUTE_LOG=… .venv/bin/python -m pytest -p no:cacheprovider -o addopts= -q -rfEs --junitxml=…` (`docs/QA/REPRODUCE_TESTS.md` §1) | `003e19e` | **1,452 passed, 1 skipped (live Neo4j), 0 failed**, 40 min 18 s. Route log: all 169 operations called, each with a 2xx except `POST /admin/graph/resync` (no Neo4j) | `backend_final.log`, `backend_junit_final.xml`, `backend_final.meta` |
| Backend, full (earlier runs) | same | `70ef821` (run 1); `960e749` (run 2) | Run 1: 11 failed, 1,439 passed, 1 skipped. All 11 were test setups relying on the fixed defects (§2). Run 2: **1,450 passed, 1 skipped, 0 failed** | `backend_run1_before_test_setup_fixes.log`, `backend_run2_960e749/` |
| API operation coverage | route log of the backend run | `960e749` | All 169 operations called. Four were seen only with error statuses: `POST /admin/graph/resync` (no Neo4j), `GET /shortlist/policy`, and the emergency shortlist contact-next and confirm. A success-path test was added for the last three | `backend_run2_960e749/routes_final.log` |
| Vitest | `npx vitest run` | `003e19e` | **178 / 178** (23 files) | `vitest_final.log` |
| TypeScript, ESLint, build | `npx tsc --noEmit`; `npx eslint .`; `npm run build` | same | exit 0, exit 0, built | `tsc.txt`, `eslint.txt`, `build.txt` |
| Ruff | `.venv/bin/ruff check .` | final | All checks passed | — |
| Playwright, CI-identical | `docs/QA/tools/e2e_ci_harness.sh <out>` (fresh `lifelink_e2e`, RQ_SYNC, real clamd, no SMTP, no Neo4j) | `57bac84` | **106 passed, 1 failed, 6 skipped** (113). The failure was a browser-protocol error ("No resource with given identifier found") in `phase26_donation_idempotency.spec.ts:95`. That test passed in runs 1 and 2, and its spec passed **3 / 3** when re-run alone on a fresh harness. **Skipped:** 2 container/clean-start-only specs (passed in the container run) and the 4 demo-stack specs (passed in the demo run). Invariants after E2E: OK | `playwright_ci_harness.log`, `playwright/`, `playwright_rerun_phase26.log` |
| Playwright, earlier runs | same | `014cfca` | Run 1: 3 failed. All three were alert specs whose requests had no city (D4); fixed by giving the requests a city. Run 2: 1 failed, from a race in `qa_evidence.spec.ts`; the facility list was not awaited, which is now fixed | `playwright_run1_3_failed_alert_scope.log`, `playwright_run2_qa_evidence_race.log` |
| Shortlist browser check | `DEMO_FRONTEND_URL=… DEMO_RESULTS=… npx playwright test e2e/remediation_shortlist_demo.spec.ts` | `960e749` + wording fix | **4 / 4.** Each scenario checked at 3 widths with axe: 0 violations, no overflow | `shortlist_ui_spec.txt`, `screenshots/` |
| Native clean start (real MinIO, Mailpit, clamd) | `native_app_check.sh` (fresh `lifelink_ext` DB, API, worker, seed, built frontend, `clean_start_check`) | `70ef821` + paper edits | **12 checks: 11 PASS, 1 BLOCKED (Neo4j), 0 FAIL.** `critical_workflow` included the new hand-off | `native_clean_start_run.log`, `native_check_result.json` |
| Container clean start (final) | `docs/final-verification/external_validation/scripts/docker_clean_start.sh <out>` (fresh source copy, fresh volumes, image build, compose up, checks inside the API container, 2 browser specs against nginx) | `57bac84` + the compose fix, committed unchanged as `003e19e` right after the run | **12 checks: 11 PASS, 1 BLOCKED (Neo4j), 0 FAIL.** Both browser specs passed. The API reports audit checkpoints `enabled: true, required: true, log_verifies: true`. Worker and deadline monitor healthy | `docker/docker_clean_start_run.log`, `docker/docker_check_result.json` |
| Container clean start (earlier runs) | same | `57bac84` | **Run 1: 10 PASS, 2 FAIL, 1 BLOCKED.** The worker exited because it inherited `AUDIT_CHECKPOINT_KEY` with no path. This was a **real defect introduced by this revision, found and fixed** (`003e19e`, plus a test). **Run 2:** the harness's tear-down failed because compose needs the key; the stack never became healthy. Harness fixed | `docker_run1_worker_failed/`, `docker_run2_teardown_blocked/` |
| Demo seed and walkthrough | DEMO_GUIDE.md §1 and §4 | `960e749` + wording fix | Seed: 400 synthetic donors, idempotent, invariants OK. A first seed run showed invariant #31 failing (168 verified donations without a verifier); the seed was fixed and re-run from an empty database. Walkthrough: scenarios A–G as in §9 | `demo_seed.txt`, `demo_seed_run1_invariant31_fail.txt`, `demo_walkthrough.txt`, `demo_results.json` |

**Not run, and why:**

| Check | Reason |
|---|---|
| Real Neo4j test (`NEO4J_TEST_URI`) | No Neo4j server is obtainable here |
| Gmail, MSG91, live geocoder | No credentials or network access |
| Failure injection | Not re-run: it stops and restarts every shared service. The 2026-10-20 run (27/27) is historical |
| Performance and load benchmarks | Not re-run. The figures are historical and labelled |
| E1–E4 experiments | Not re-run. The figures are historical and labelled |

## 8. Paper

* **Source:** `docs/paper/LifeLink_Paper_Overleaf/main.tex` and `references.bib` (47 entries, all cited).
* **Build:** `pdflatex → bibtex → pdflatex ×2`. The PDF was regenerated from that source into `docs/paper/LifeLink_Research_Paper.pdf`, and the Overleaf zip was rebuilt. Build status: clean. There are no LaTeX or BibTeX warnings (no undefined references, no overfull boxes). The PDF has 16 pages (previously 14), and the zip compiles on its own.

**What was corrected:**

| # | Paper claim | Now says |
|---|---|---|
| 1 | Fig. 1 "62 routes" | "63 routes (62 pages + catch-all)". The figure source (TikZ) was redrawn with separate RQ worker and deadline monitor boxes; the monitor's direct PostgreSQL link with SKIP LOCKED; MSG91, the geocoder, nginx, the Neo4j resync and the checkpoint log. Mailpit is marked tested and Gmail pending |
| 2 | "18 routers expose 160 operations" | 169 = 168 in 19 routers + `GET /` |
| 3 | Container clean start "10/11" | See §7 (12 checks) |
| 4 | Test counts 1,397/170/106 | The 2026-10-31 counts, dated |
| 5 | Donors beyond 50 km "neither alerted nor ranked" | True now for blood requests and SOS alike. The paper describes the shared policy, same-city handling and no-location handling |
| 6 | "Any rewrite breaks the chain or its checkpoints" | The chain alone detects edits, deletions and reordering. Recomputation and truncation are detected only with checkpoints, which are required in the container stack. The log is local; independent anchoring is future work |
| 7 | Failover "all in one transaction" | A backup is promoted only together with its reservation, otherwise the next backup or a fresh round, otherwise `RECOVERY_REQUIRED` (Algorithm 1 updated) |
| 8 | Plan → shipment hand-off | The integrated hand-off is described |
| 9 | Shipment failure | A failed linked shipment re-plans its emergency; unlinked shipments are reconciled only |
| 10 | Forecast UI | Both baselines, each with its own verdict |
| 11 | Red-cell 42 days [Lifeblood] | AABB COI and the ARC Compendium |
| 12 | Donor interval (56 days, uncited) | Indian policy, G.S.R. 166(E), 120 days applied to everyone. The US value is configurable. Clinical review is pending |
| 13 | Performance and load figures | Labelled as measured on 2026-10-06/07 on earlier code |
| 14 | Failure injection "not repeated" | The 2026-10-20 27/27 run on real MinIO and Mailpit, dated; the earlier 23/23 stand-in round is named |
| 15 | E2 demo-hospital attribution | Worded as interpretation |
| 16 | Architecture omissions | See row 1 |
| 17 | Synthetic data | The seed-1337 network and the separate seed-20261031 shortlist data are both stated as synthetic |
| 18 | External integrations | Neo4j, Gmail, MSG91 and the geocoder are listed as pending |
| 19 | New shortlist | Section III-D2, results in Table "Shortlist scenarios", limitations |
| 20 | Clinical limits | Strengthened: interval, deferrals, TTI, provisional weights, synthetic answers only |
| — | E3 | Stated as run under the 56-day rule (2026-10-13) |

## 9. Demonstration

See [DEMO_GUIDE.md](DEMO_GUIDE.md). The scripted walkthrough was run against the final code; its output is `docs/QA/evidence/remediation-2026-10-31/demo_walkthrough.txt` (with `demo_results.json`); the browser check of scenarios A–D is `shortlist_ui_spec.txt` plus `screenshots/`.

| Scenario | Need | Eligible | Contacted | Confirmed | Declined | Timed out | Still short | State |
|---|---|---|---|---|---|---|---|---|
| A: enough donors | 3 | 6 | 6 | 3 | 3 | 0 | 0 | SATISFIED |
| B: many eligible, two accept | 3 | 15 | 15 | 2 | 7 | 6 | 1 | PARTIAL (escalate to blood banks) |
| C: nobody eligible | 1 | 0 | 0 | 0 | 0 | 0 | 1 | NO_ELIGIBLE, with reasons |
| D: #1-ranked donor silent | 1 | 2 | 2 | 1 (#2) | 0 | 1 (#1) | 0 | SATISFIED |

Every request was for A+ whole blood. In each scenario 908 donors nationwide had a compatible group; the hard filters removed the rest.

* **E (distance limit):** 414 compatible donors were excluded as beyond the limit, and the 80 km donor received 0 notifications.
* **F (route failure on a reserved plan):** the plan had no backup, so a fresh round was planned and secured: version 2, 22 units reserved. The plan was never shown as secured without units.
* **G (hand-off):** the reservation went `HANDED_OFF` to shipment `SHP-…` (22 units). A repeated hand-off returned the same shipments.
* **Browser check:** for scenarios A–D, the hospital request page showed the same states, the DEMO MODE banner and the SYNTHETIC / simulated labels. There were 0 axe violations and no overflow at 375, 768 and 1280 px.

**Found by this check and fixed:** the panel's footer read "donors still needed 3" even after 3 donors were confirmed. That number is the total required, so it now reads "donors required"; the API summary was fixed the same way.

## 10. Remaining items, by risk

**High.** These cannot be closed in this environment, or need people outside the project:

1. **Clinical validation.**
   * The donation-interval policy (120 days for everyone, because sex is not recorded), the compatibility and shelf-life tables and the post-thaw window are cited simplifications. None has been reviewed by a clinician or a regulatory expert.
   * The shortlist weights are provisional and were tuned on nothing.
   * Deferrals other than the interval are not modelled.
2. **Real external services are untested:**
   * a Neo4j server, including the new stale-projection cross-check, which has only been tested with a replaced driver;
   * Gmail SMTP;
   * MSG91 SMS;
   * a live geocoder.

   The adapters, readiness reporting and test procedures exist (`docs/final-verification/NEO4J_SETUP.md`, `SMS_SETUP.md`, `docs/LIFELINK_EMAIL.md`).
3. **The audit checkpoints have no independent anchor.**
   * The checkpoint log is on the deployment's own volume. Anyone who controls the host can read the key and rewrite both the database and the log.
   * Native and development deployments keep checkpoints opt-in; readiness warns when they are off.

**Medium:**

4. **Not re-measured on the remediated code:**
   * performance and load (2026-10-06/07);
   * failure injection (2026-10-20);
   * graceful shutdown timing (2026-10-20);
   * E1–E4 (2026-10-13; E3 under the old 56-day rule).

   E2 and E3 store only aggregates. Shortlist latency was observed only on a contended machine (median 181 ms over 8 calls), not benchmarked.
5. **PII at rest.** Donor phone numbers and the free-text `medical_conditions` field are not encrypted at field level. This was deliberately not added without a key-management and migration design (§6.4 of the brief). Shortlist responses expose neither.
6. **Notification delivery.** Only in-app notifications are tracked, as "sent" or "read". There are no delivery receipts from e-mail or SMS providers, and nothing claims delivery.
7. **Demo-simulator button.** The web app shows it only to an administrator viewing a request drawer, and the admin screens do not open that drawer. In practice the simulator is used through the API or the walkthrough script.
8. **Browser coverage gaps.** The hand-off button is verified through the API (walkthrough, clean start), not in a browser spec. The shortlist panel in the patient, family and emergency drawers is covered by component tests only.

**Low:**

9. **Unlinked shipments.** Shipments created by hand (not through the hand-off) still have reconciliation only.
10. **Fig. 7/8 capability ratings** of external products are author judgement.
11. **Container stack images.** It runs on locally built base images; the official Docker Hub images were never pulled in this sandbox.
12. **`ruff format`.** It would reformat pre-existing files. This is not a CI gate and was not changed.
