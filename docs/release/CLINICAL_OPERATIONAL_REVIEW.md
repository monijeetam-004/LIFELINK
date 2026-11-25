# Clinical and operational review pack: donor eligibility, shortlist and unified priority

**Status: PENDING APPROVAL.** This document prepares the review. It does **not** record any approval.

* **Prepared:** 2026-11-03, against branch `finalize/audit-remediation-2026-10-31` (HEAD `2cfe39a` plus this round's uncommitted changes). Line numbers refer to that checkout.
* **What it contains:** the rules as they are actually coded, their documented rationale, the risks, and the questions only a qualified owner can answer.
* **What it does not do:** no rule here was changed, approved or "medically validated" by engineering.

**Roles named below:**

* **Transfusion Medicine Officer (TMO):** a qualified clinician responsible for blood-donor selection at the blood centre.
* **Blood Centre Operations Lead (BCOL).**
* **Regulatory / Legal (REG):** for the Drugs and Cosmetics Rules.
* **Product Owner (PO).**

## 1. Rules as implemented

| # | Rule | Code | Documented rationale | Risks | Approver and evidence needed |
|---|---|---|---|---|---|
| R1 | **Donation interval: 120 days for every donor** (policy `IN_DCR_2020`, the default). `US_21CFR630` (56 days) is selectable | `backend/app/core/eligibility.py:44-78` (policies), `:97` `is_eligible`; config `DONOR_ELIGIBILITY_POLICY` | Drugs and Cosmetics (Second Amendment) Rules 2020, G.S.R. 166(E), Sch. F Pt XII-B, H(4): 90 days for males, 120 for females. Sex is not recorded and is never inferred, so the stricter value applies to all | **Over-restrictive** for donors who would qualify at 90 days: they are excluded for up to 30 extra days, which shrinks supply. **Source risk:** the clause was read from a published copy of the gazette notification, not the official e-Gazette PDF. Apheresis and plateletpheresis intervals (48 h; 12 weeks) are **not** modelled. Only the last donation date is used, and it may be self-reported | **TMO + REG:** confirm the current applicable rule and edition. Decide whether to collect sex (with consent and a privacy review) or accept 120 days for all. Decide whether apheresis donors need a separate rule |
| R2 | **Blood group / component compatibility** | `app/core/compatibility.py:47` `compatible_source_types`; `app/core/blood_compatibility.py:81` `can_donate_to` (used at response time); `app/core/component_compatibility.py:85` | Cited tables (UTMB, AABB COI, ARC Compendium), pinned by fingerprint tests | **Consistent checks (verified):** the shortlist checks by **component**. A donor's answer to an SOS is checked with the same component table (`emergencies.py:129` `_donor_compatible`). A blood request is always whole blood and is checked with `can_donate_to`. **A donor cannot give plasma or platelets:** a whole-blood donor can be shortlisted for a plasma or platelet emergency, because donors have no component capability (for example, apheresis eligibility). No crossmatch, antibody screen, irradiation or CMV status. **Unverified for India:** compatibility sources are US/Australian | **TMO:** approve the tables for the deployment's jurisdiction, and confirm that a whole-blood donor accepting a component request is acceptable |
| R3 | **Active account, not the requester** | `donor_shortlist_service.py:357-358` | A requester cannot be their own donor candidate; deactivated accounts never appear | A family member who is also a donor is not excluded (only the requester is) | **PO:** confirm that relatives are allowed as donors |
| R4 | **Availability: state must be AVAILABLE** | `donor_shortlist_service.py:381` | Donor-set flag. `NOT_NOW` and `UNAVAILABLE` both exclude | A stale "available" flag still admits the donor. It is only scored lower (`availability` factor; profile staleness) | **BCOL:** acceptable staleness before a donor must re-confirm |
| R5 | **No other active accepted commitment** | `donor_shortlist_service.py:387`; `app/services/donor_commitment.py:38` and `:67` (row lock); response routes `blood_requests.py:542`, `emergencies.py:1531` | Prevents one donor being counted for two patients. The race was fixed in QA defect D1, with tests | A donor who accepted elsewhere but will not attend still blocks other requests until that request closes | **BCOL:** when a commitment should lapse |
| R6 | **Distance: measured ≤ 50 km (inclusive); same city if no coordinates; unknown location = not near; a request with no location alerts nobody** | `app/core/distance_policy.py:94` `assess`, `:140` `alert_skip_reason`; config `MAX_DONOR_DISTANCE_KM=50`, `DONOR_ALERT_ALLOW_UNSCOPED=false`, `DONOR_LOCATION_STALE_DAYS=90` | Audit finding D4: one policy for all alert, rank and shortlist paths, and no silent nationwide broadcast | **Straight-line distance:** 50 km can be well over 50 km by road, or impassable. **Coarse city match:** "same city" admits donors anywhere in a large city. **Request location:** a request **without** a city alerts no donor; the forms now say so. **Override:** an admin override (audited, with a reason) lifts the limit | **BCOL:** the right radius per region and urgency, whether same-city is acceptable, and who may override |
| R7 | **Travel feasibility against the deadline**: straight-line ÷ 45 km/h must arrive before the deadline | `donor_shortlist_service.py:85` (`ASSUMED_TRAVEL_KMPH`), `:407` | The same labelled assumption as the pathway planner | **Optimistic in cities:** 45 km/h ignores traffic. Blood-request deadlines are end-of-day `needed_by`, so the check rarely bites. No time is allowed for donor screening or collection | **BCOL:** realistic speed assumptions and collection lead time |
| R8 | **Acceptance re-check:** at the donor's answer (compatibility, available, interval, commitment) and at coordinator confirmation (available, interval, commitment). A failed confirmation becomes `ELIGIBILITY_RECHECK_REQUIRED` | `blood_requests.py:542-590`; `emergencies.py:1531-1570`; `donor_shortlist_service.py:667` `confirm` | Fails closed | **Confirmation re-check:** it does not re-check compatibility, because the group cannot change. **Not modelled at all:** deferrals, age, weight, haemoglobin and questionnaire answers, and there are no structured fields for them. `date_of_birth` and `weight_kg` exist on profiles but are **not** used | **TMO:** what minimum pre-screen the app must enforce before "confirmed for coordination", and how deferrals reach the system |
| R9 | **State separation:** ranked → contacted → awaiting → accepted → confirmed for coordination → (hospital) donation verified. Unit reservation and securing is a separate inventory/pathway workflow | `app/models/enums.py:80` (`DonorContactStatus`); `donor_shortlist_service.py` (`contact_next_batch`, `record_response`, `confirm`) | Ranking never changes a state. Tested by `test_ranking_is_read_only_and_never_changes_any_state` (added in this round) | **Misreading "confirmed":** staff might read "confirmed for coordination" as "will donate". The UI says it still needs screening | **BCOL:** confirm the wording, and who may confirm (today: the request's managers, which includes patients and family) |
| R10 | **Unified priority score orders eligible donors only.** It is provisional, configurable and not a probability | `app/core/unified_priority.py`; `docs/UNIFIED_DONOR_PRIORITY.md` | Engineering judgement; synthetic evaluation only | **Ranking bias:** the order may systematically favour verified, nearby, frequently-responding donors (fairness and burden on frequent donors) | **TMO + BCOL + PO:** accept or adjust the weights before real use; define the monitoring once real data exist |

## 2. Questions that need a qualified owner

1. **(TMO, REG)** Is G.S.R. 166(E) H(4) still the applicable donation-interval rule? Is applying 120 days to everyone acceptable until sex is collected?
2. **(TMO)** Which pre-screen criteria must the app enforce before "confirmed for coordination"? Candidates are age 18–65, weight ≥ 45 kg, haemoglobin ≥ 12.5 g/dL, and deferral categories. Or is this left entirely to the blood centre?
3. **(TMO)** A donor who accepts a plasma or platelet emergency would need apheresis or separation at the blood centre. Should such emergencies invite donors at all, or only blood banks?
4. **(BCOL)** What radius and travel assumptions apply per region and urgency? Is "same city, distance unknown" acceptable for alerting?
5. **(BCOL)** How long may an "available" flag stand without re-confirmation? When should an accepted-but-unfulfilled commitment lapse?
6. **(BCOL, PO)** Who may confirm a donor for coordination? Today it is the request's managers, including the requesting patient or family.
7. **(TMO, BCOL, PO)** Are the unified-priority weights acceptable for a pilot? Which outcome measures will be monitored (contacts per secured donor, burden per donor, demand met, equity across areas)?

## 3. Evidence available to reviewers

| Evidence | Location |
|---|---|
| Rule tests | `backend/tests/test_audit_d4_distance_policy.py`, `test_donor_shortlist.py`, `test_unified_priority.py`, `test_qa_round_races.py` |
| Synthetic scenarios and comparison | `docs/QA/evidence/release-gates-2026-11-03/ranking_comparison.json`. Synthetic; see the caveat in that file |
| Clinical simplifications already disclosed | `docs/QA/CLINICAL_ASSUMPTIONS_AND_LIMITATIONS.md` |

**Approval record:** none. The gate stays **PENDING APPROVAL** until a named TMO, BCOL and REG reviewer signs the items above.
