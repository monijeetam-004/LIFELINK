# Clinical assumptions and limitations

**Scope.** This file audits every clinically meaningful rule in the code, records where each rule comes from, names the test that stops it from being changed by accident, and lists what the software does **not** model.

**Who reviewed what.** The audit was done by a software engineer, not a transfusion professional. **No rule here has been approved by a qualified clinician.** Every row marked *Review required* must be checked by a qualified transfusion professional before any real use.

> LifeLink is a research prototype. It must not be used to make real transfusion, donor-selection or blood-issue decisions. Passing software tests says nothing about patient safety.

## 1. Rules implemented, with sources and protecting tests

| # | Rule | Value in code | Where | Source recorded in the code | Protected by | Status |
|---|---|---|---|---|---|---|
| C1 | Red-cell ABO/Rh compatibility (8 × 8 matrix) | Standard matrix: O− universal donor, AB+ universal recipient | `app/core/blood_compatibility.py` | Standard red-cell compatibility. The module cites it as the standard matrix | `tests/test_blood_compatibility_matrix_phase9.py` (all 64 pairs, both directions, API equals matrix) | Implemented and tested. *Review required:* institution-specific substitution policy, e.g. Rh-positive to Rh-negative recipients in emergencies |
| C2 | Plasma compatibility (inverse ABO, Rh ignored) | Donor plasma → recipients: O → O; A → O, A; B → O, B; AB → all | `app/core/component_compatibility.py` | UTMB Blood Bank, "Blood Component ABO Compatibility Chart" (fetched 2026-10-05; no version stamp) | `tests/test_phase_e_clinical_sources.py::PINNED_COMPAT_FINGERPRINT`, `tests/test_phase35_blood_component.py` | Implemented and tested. *Review required:* the source is a US hospital chart, not an Indian guideline; confirm for the intended jurisdiction |
| C3 | Platelet compatibility | Always accepted; the identical type is preferred | `component_compatibility.py` | UTMB chart | Same fingerprint test | Implemented and tested. *Review required:* the Rh(D) policy for platelets (anti-D prophylaxis) is not modelled |
| C4 | Whole-blood shelf life | 35 days at 1–6 °C | `app/core/clinical_sources.py` | AABB Circular of Information (CPDA-1 figure; the cited range is 21–35 days) | `test_shelf_life_figures_are_pinned`, `test_ruleset_fingerprint_is_pinned` | Implemented and tested. *Review required:* the figure depends on the anticoagulant actually used |
| C5 | Red-cell shelf life | 42 days at 1–6 °C | `clinical_sources.py` | AABB COI; ARC Compendium v.04 (Jan 2021) | Same | Implemented and tested. Assumes additive solution (AS-1/AS-3); 21 days without it |
| C6 | Platelet shelf life | 5 days at 20–24 °C with agitation | `clinical_sources.py` | ARC Compendium v.04 | Same | Implemented and tested. Some services allow 7 days with bacterial controls; the shorter value is used |
| C7 | Frozen-plasma shelf life | 365 days at ≤ −25 °C | `clinical_sources.py` | Australian Red Cross Lifeblood, FFP product information | Same | Implemented and tested |
| C8 | Post-thaw plasma window | 24 h at 1–6 °C; the earlier of the two expiries applies | `clinical_sources.THAWED_PLASMA`, `inventory_service.thaw_plasma_unit` | AABB COI | `test_plasma_post_thaw_window_is_cited_and_enforced`, `test_gap_features_api::test_plasma_thaw_lifecycle` | Implemented and tested |
| C9 | Cold-chain ranges for shipments | Taken from the storage condition of each component (C4–C7) | `cold_chain_service`, `clinical_sources.temperature_range_c` | Same as C4–C7 | `test_cold_chain_evaluation`, `test_excursion_forces_quarantine_at_receipt` | Implemented and tested. *Review required:* transport standards may allow different ranges than storage standards. The code says it does not model a separate transport standard |
| C10 | Donation interval | **56 days** after the last whole-blood donation | `app/core/eligibility.py` (`DONATION_INTERVAL_DAYS`) | **None cited in the code.** The comment calls it the "standard minimum interval" | `tests/test_donor_matching.py`, `tests/test_donor_appointments.py` (both assert 56 days) | **Review required: likely wrong for the intended setting.** 56 days is the US (FDA/AABB) interval. Indian donor-selection guidance (NBTC, 2017) is commonly reported as 90 days for men and 120 days for women ([overview](https://en.wikipedia.org/wiki/Blood_donation_in_India), [NBTC guideline copy](https://www.slideshare.net/slideshow/blood-donor-selection-guidelines-2017/182369567)). The seed data and the paper are set in India. **Not changed in this round:** changing a clinical rule needs a qualified decision and a cited source, not an engineer's guess (audit brief, rule 9) |
| C11 | Planner handling time | 30 min added to every delivery | `app/services/pathway_planner.py` / settings | Assumption (stated in the paper) | `tests/test_gap_pathway_engine.py` | Operational assumption, not clinical |
| C12 | Deadline-risk margin | Arrival within 15 min of the deadline is `DEADLINE_RISK` | `pathway_planner` | Assumption | `test_deadline_classification_boundaries` | Operational assumption |
| C13 | Straight-line speed for off-network estimates | 45 km/h | `pathway_planner.ASSUMED_ROAD_SPEED_KMPH` | Assumption | `test_off_network_source_gets_a_labelled_straight_line_estimate` | Operational assumption, labelled in the UI |
| C14 | Donor distance limit | 50 km by default; admin override audited | `matching_engine`, settings | Product policy | `test_donor_distance_limit_below_at_and_above_and_admin_override` | Product policy, not a clinical rule |
| C15 | Stale donor availability | 30 days since the last profile update | `matching_engine.STALE_AVAILABILITY_DAYS` | Product policy | matching-engine tests | Product policy |
| C16 | Allocation order | Earliest expiry first (FEFO) | `inventory_service` | Standard inventory practice | Phase 34 lifecycle tests | Implemented and tested |

**How accidental changes are caught.** Every cited table is fingerprinted with SHA-256 (`ruleset_fingerprint()`, `compatibility_fingerprint()`), and the fingerprints are pinned by tests. Editing any number, source or note fails the suite until the pin is updated in the same change. That makes every edit to a clinical rule a visible, deliberate act. It does **not** check the rule against an authority, and anyone who edits the code can also update the pin.

**Not protected the same way.** The donation interval (C10) has no citation, no ruleset version and no fingerprint. It is protected only by two tests that assert the value 56.

## 2. Clinical processes that are not modelled

| Area | What is missing | Consequence |
|---|---|---|
| Pre-transfusion testing | No crossmatch (immediate-spin or full), no antibody screening, no antibody identification, no patient sample history | "Compatible" means ABO/Rh/component compatible only. A real issue decision needs a crossmatch or electronic-issue criteria |
| Special requirements | No irradiation, CMV-negative, leucodepletion, washed products, HLA/HPA matching, or phenotype matching (Kell, Rh subgroups) | Patients needing these products cannot be represented |
| Donor selection | Only the donation interval (C10) is enforced. There are no checks on age, weight (the `weight_kg` field is stored but not used in any rule), haemoglobin, blood pressure, deferral conditions, travel or medication deferrals, or sex-specific intervals | A donor marked "eligible" by LifeLink is not medically cleared to donate |
| Donation testing | No TTI (transfusion-transmitted infection) screening results, no release workflow from a quarantine pending tests | Units are treated as usable when added to stock |
| Component processing | No separation of whole blood into components, no volume or dose per unit, no paediatric splits, no pooled-platelet dose | Quantities are counted in "units" only |
| Massive transfusion | No protocol (fixed ratios of RBC:plasma:platelets) | Emergencies request one component at a time |
| Bedside and haemovigilance | No bedside patient–unit check, no transfusion reaction reporting, no traceability to the recipient after issue | Traceability ends at "received / allocated" |
| Cold-chain devices | Readings arrive through the API; no real logger or sensor was connected, and demo readings are labelled SIMULATED | The cold-chain rules are tested with synthetic readings only |
| Jurisdiction | Sources are US (AABB, ARC, UTMB) and Australian (Lifeblood); the seed data are Indian | Indian regulations (Drugs and Cosmetics Rules, NBTC guidelines) were not checked against the implemented rules |

## 3. Items requiring review by a qualified transfusion professional

1. **The donation interval (C10).** Decide on the interval for the target jurisdiction, including any sex-specific intervals. Then cite it, version it and fingerprint it like C4–C8.
2. **Plasma compatibility (C2).** Confirm the table, and the emergency-plasma policy, for the intended setting.
3. **Platelet Rh policy (C3)**, including anti-D prophylaxis.
4. **Shelf lives (C4–C7).** Confirm them against the anticoagulant, additive solution and storage actually used by the participating blood banks.
5. **Transport temperature ranges (C9)**, as distinct from storage ranges.
6. **Emergency substitution policy.** Decide whether and when the planner may substitute groups (for example O-negative rationing, or Rh-positive to Rh-negative males) under deadline pressure.
7. **Wording in the UI.** Labels such as "compatible", "eligible" and "VIABLE" must not be read as clinical clearance. The UI should state this wherever a decision is shown.

## 4. Statement of system boundaries

LifeLink coordinates logistics:

* it finds where compatible stock is;
* it reserves units;
* it plans and re-plans deliveries;
* it tracks shipments and their temperature;
* it records an auditable history.

It does **not** decide whether a patient should be transfused, which product a patient needs, or whether a donor is medically fit. All results in this project, including the QA results in this folder, are **technical** results on **synthetic** data.
