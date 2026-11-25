# LifeLink Phase 31 — Domain Correctness, Core Logic & Research-Grade System Audit

## Methodology

Per the master prompt's Section 0 ("DO NOT START CODING IMMEDIATELY"), this phase began with a full repository inspection before any file was modified: direct reading of `backend/app/models/*.py`, the routes for blood requests, emergencies, verifications, certificates, and admin; the lifecycle modules (`app.core.request_lifecycle`, `app.core.emergency_lifecycle` and their DB-touching service counterparts); `docs/DATABASE_MAP.md` (found to be stale — it only documents Phase 0-6 schema and explicitly labels everything since as "outline only," confirming the master prompt's own warning not to trust old docs for either "exists" or "missing" claims). Seven parallel read-only investigation agents were then dispatched, each scoped to a specific question set from the master prompt's sections 3-33, with explicit file:line-citation requirements. Their reports were synthesized, and the most safety/implementation-critical claims were independently re-verified by direct source reading rather than trusted at face value — specifically: the donor double-booking gap (confirmed by reading `respond_to_request` end-to-end — no such check existed), the missing emergency-accept eligibility gate (confirmed by grep — `is_eligible` was imported into `emergencies.py` but only ever read for match-candidate display, never as an accept-time gate, unlike the structurally identical `blood_requests.py`), and the emergency-deadline display-only status (confirmed by tracing every use of `Emergency.deadline` across the codebase — only `_slack_seconds()` read it, purely for output).

Gaps were then classified per Section 34's A-J scheme, prioritized per Section 34's stated order (domain correctness > data integrity > security > concurrency > explainability > research validity > operational realism > UI polish), and a bounded set of justified fixes was implemented, tested (unit + concurrency + authorization, using real PostgreSQL/threads, never sleep-based synchronization), and verified through a full regression run — not merely proposed.

## Findings, grouped by master-prompt section

Each finding: **Classification** (A-J) · **Status** (FIXED / VALID LIMITATION / NOT APPLICABLE / NOT FIXED — none in this phase were left as "not fixed" without a stated reason).

### Section 3 — Blood unit lifecycle

**Finding**: No per-unit `BloodUnit` entity exists; inventory is aggregate counts (`HospitalInventory.units`/`reserved_units`) with a full append-only mutation log (`HospitalInventoryLog`, recording every ADD/RESERVE/ALLOCATE/RELEASE/EXPIRE/RECONCILE with pre/post snapshots). Every mutation is row-locked and DB-CHECK-constrained non-negative.

**Classification**: B (missing domain logic relative to the master prompt's suggested model) — but evaluated and **not implemented**.

**Status**: VALID LIMITATION. Reasoning: the existing aggregate model already provides real transactional integrity (locking, non-negative constraints, a fully replayable audit log) — the specific invariants the master prompt worries about ("unavailable unit can't be allocated," "reserved unit can't double-allocate") are already true at the aggregate level, verified by `tests/test_concurrency.py`'s real-thread reservation race tests. Inventing a `BloodUnit` row-per-unit model would require a real collection/barcode/expiry pipeline behind it to mean anything; without one, per-unit rows would just be a second, unverifiable source of truth alongside the real aggregate counts — the kind of complexity Section 34 explicitly warns against adding "merely because it sounds impressive."

### Section 4 — Blood component model

**Finding**: Whole Blood / RBC / Plasma / Platelets are not distinguished anywhere — every request, inventory row, and donation is `BloodType`-only.

**Classification**: F (research/domain-completeness gap) intersecting Section 35 (medical/regulatory safety).

**Status**: VALID LIMITATION, explicitly per Section 35's instruction: component-specific compatibility rules are a real clinical fact this codebase has no authoritative, versioned source for. Inventing one (even as a "configurable" placeholder with fabricated defaults) would risk being mistaken for real medical guidance. Documented here rather than implemented, exactly as Section 35 requires ("if authoritative sources are not already in the repository, DO NOT GUESS").

### Section 5 — Blood compatibility engine

**Finding**: Hardcoded, direction-aware, correctly bidirectional ABO/Rh compatibility (`app.core.blood_compatibility`), verified across all 64 combinations. One real gap: hospital-inventory allocation (`inventory_service.py`'s allocate path) does not itself re-check compatibility — it trusts the caller (the matching/request-fulfillment flow) to have already selected a compatible donor/unit before calling allocate.

**Classification**: B (missing defense-in-depth), not A (no evidence any real caller currently allocates incompatible inventory — every call site was traced and each supplies an already-compatibility-filtered blood type).

**Status**: VALID LIMITATION. Adding a redundant compatibility check inside `inventory_service.allocate` was considered but not implemented this phase: it would be genuine defense-in-depth, but every current call site is already provably safe by construction, and adding an unreachable-in-practice check without a failing test to justify it would violate Section 34's "no unnecessary code" guidance. Flagged here for a future phase if a new caller is ever added that doesn't pre-filter.

### Section 6 — Donor eligibility vs. matching

**Finding**: Eligibility (`is_eligible`, the 56-day interval) and matching (`rank_candidates`) are conceptually and structurally separate — confirmed by direct code read. **Real gap found and fixed**: `emergencies.py::respond_to_emergency` imported `is_eligible` and used it only for match-candidate display, never as an accept-time gate, unlike the structurally identical `blood_requests.py::respond_to_request`, which already gated ACCEPT on it. A recently-donated donor could accept an emergency SOS despite being ineligible to actually donate again.

**Classification**: A (real bug — an inconsistency between two structurally parallel code paths, not a deliberate design difference).

**Status**: **FIXED**. `respond_to_emergency` now raises the same 400 with the same eligible-on date the blood-request path already gave. Test: `tests/test_phase31_domain_fixes.py::test_recently_donated_donor_cannot_accept_emergency` / `test_eligible_donor_can_still_accept_emergency`.

**Second finding**: `notify_matching_donors` alerted every compatible-blood-type donor regardless of eligibility — an ineligible donor was still notified for a request they'd be blocked from accepting.

**Classification**: B (missing domain logic — wasted donor attention and a false sense of reachable supply for the recipient, not a security issue).

**Status**: **FIXED**. `notify_matching_donors` now skips donors who are not currently `is_eligible`. Test: `test_ineligible_donor_is_not_alerted_for_a_new_matching_request`.

### Section 7 — Matching engine architecture

**Finding**: A real staged pipeline exists (`app.core.matching_engine`): hard-constraint filtering (compatibility, availability) → feasibility (distance/radius) → ranking via strategy-specific tuple sort keys → explanation string. No arbitrary weighted "trust score" exists anywhere — confirmed by reading `MatchFactorsOut`'s own docstring, which documents this as a deliberate design decision.

**Classification**: NOT APPLICABLE — the architecture the master prompt describes as a goal is already the actual architecture.

**Status**: NOT FIXED because nothing needed fixing; re-verified this phase, not re-implemented.

### Section 8 — Decision transparency

**Finding**: `MatchFactorsOut` and each candidate's `explanation` string expose real factual inputs (compatibility, availability, distance, has-accepted, urgency-fit) — verified these correspond to fields actually computed and used in ranking, not decorative. `DecisionTransparency.tsx` (frontend) is an honestly-labeled conceptual/demo page (carries the existing "CONCEPTUAL ILLUSTRATION" banner convention), distinct from the real, backend-wired match-candidate UI.

**Classification**: NOT APPLICABLE.

**Status**: NOT FIXED because nothing needed fixing; the labeling convention was spot-checked, not re-implemented.

### Section 9 — Emergency escalation logic

**Finding**: `Emergency.deadline` was write-only from an operational standpoint — the only reader anywhere in the codebase was `_slack_seconds()`, purely for a display value. A missed deadline had zero consequence: no flag, no event, no notification, nothing queryable.

**Classification**: B (missing domain logic — explicitly named by the master prompt: "a deadline should have meaningful consequences").

**Status**: **FIXED**, carefully bounded per Section 9's own caution against "dangerous automatic medical decisions": a new nullable `Emergency.deadline_exceeded_at` column, set exactly once by `emergency_lifecycle.flag_deadline_exceeded_if_needed` (mirroring the existing lazy-on-read `expire_if_stale` pattern — there is no scheduler anywhere in this codebase) the first time any read or mutating route notices the deadline has passed on a still-open emergency. Writes an `EmergencyEvent`, an `AuditLog` entry, and a best-effort notification to the creator. **Deliberately never changes `Emergency.status`** — a missed deadline does not mean the emergency is resolved, less urgent, or auto-anything; the system surfaces the fact for a human to act on, exactly the decision-support boundary Section 9 requires. Migration: `d5e6f7a8b9c0_phase31_emergency_deadline_exceeded.py`, verified upgrade→downgrade→upgrade against the real dev database. Tests: 5 new tests in `tests/test_phase31_domain_fixes.py` covering flagged-once, never-flagged-early (future/no deadline), status-unchanged, notification-sent, and triggered-from-a-mutating-route-too.

### Section 10 — Blood request lifecycle

**Finding**: States are `PENDING → MATCHING → FULFILLED`, `CANCELLED`/`EXPIRED` from any open state (`app.core.request_lifecycle`). Verified by direct code read of `fulfill_request`: a multi-unit request does **not** auto-become FULFILLED on one donor's acceptance — fulfillment is an explicit action crediting specific donations. Quantity invariants (matched ≤ requested is enforced by construction — donations are individually logged and summed, never assumed) hold. One real gap: there is no dedicated `PARTIALLY_FULFILLED` status — a request with some but not all units covered stays `MATCHING`, and "how many units are covered so far" must be derived from linked `Donation` rows rather than read off a single field.

**Classification**: H (documentation/completeness gap, not a correctness bug — nothing incorrectly reports fulfillment status).

**Status**: VALID LIMITATION. Adding a redundant `PARTIALLY_FULFILLED` status was considered and rejected: it would require either denormalizing a "units covered so far" counter (a second source of truth that could drift from the real `Donation` sum) or computing it live everywhere the status is displayed (a larger frontend/API contract change than this phase's evidence justified). Documented in `docs/LIFELINK_PHASE_31_DOMAIN_MODEL.md` Section 7 rather than implemented.

### Section 11 — Donor response lifecycle

**Finding (real gap)**: Nothing prevented the same donor from holding simultaneous `ACCEPTED` responses across multiple open blood requests, or across a blood request and an emergency, or across two emergencies. Each table only enforced uniqueness *within itself* (`uq_donor_response_pair` / `uq_emergency_response_pair`).

**Classification**: A (real bug — a physical donor cannot actually be in two places; an "ACCEPTED" commitment the donor can't fulfill creates a false sense of secured supply for whichever request they don't show up for).

**Status**: **FIXED**. New module `app.services.donor_commitment.find_conflicting_commitment`, called from both accept paths, checks both tables for any other `ACCEPTED` response on a still-open request/emergency for the same donor — excluding the resource currently being acted on (so idempotent re-accept isn't blocked) and excluding HOSPITAL responders on emergencies (an institution, not a single body, legitimately coordinates several concurrent emergencies). Application-level (a genuinely cross-table invariant no single DB constraint can express), enforced inside the same transaction as the accept itself. Tests: 6 new tests covering same-table, cross-table, hospital-exemption, guard-releases-after-cancel, and idempotent-replay-not-blocked.

**Other Section 11 questions investigated**: Can an accepted donor be assigned incompatible/expired work? No — compatibility and eligibility are both re-checked at accept time (the latter now consistently, per Section 6's fix above), and inventory allocation only ever operates on currently-available stock (row-locked). Can a donor withdraw? Yes — `DECLINED`/re-response is supported and re-opens the slot. What happens on fulfillment-before-response or accept-after-expiry? Both routes re-check `status in OPEN_STATUSES` (post-lazy-expiry) before accepting a response, so an already-fulfilled or expired request correctly rejects a late accept with 404/409 rather than silently accepting it.

### Section 12 — Hospital confirmation

**Finding**: `HospitalRequestResponse`/hospital-side emergency response is a genuinely separate entity and workflow from the algorithm's match suggestion — a hospital must explicitly acknowledge/prepare/dispatch/allocate/fulfill; the algorithm never marks a real-world event completed on its own. One disclosed limitation, not new to this phase: donation logging itself (`POST /donors/me/donations`) is donor-self-attested with no hospital-confirmation step — this is why certificate revocation (Section 20 below) exists, as the actual remedy for a self-attested donation later found false.

**Classification**: NOT APPLICABLE for the core finding (already correct); the donation-logging trust boundary is documented as a VALID LIMITATION (see Section 20 and the domain model doc's Donation entry).

**Status**: NOT FIXED because the core separation was already correct; the disclosed limitation is addressed indirectly by the Section 20 fix (revocation), not by adding a hospital-confirmation gate to donation logging itself, which would be a larger workflow change than this phase's evidence justified (no report of this being exploited, and the honor-system nature is already surfaced honestly in the UI/docs rather than hidden).

### Section 13 — Inventory consistency

**Finding**: Every mutation (add/reserve/release/allocate/dispatch-adjacent/expire/discard/reconcile) goes through `inventory_service.py`, which row-locks before mutating, validates quantities, writes an audit log entry with pre/post snapshots, and is called only from routes that themselves enforce role/ownership authorization.

**Classification**: NOT APPLICABLE — already correct.

**Status**: NOT FIXED because nothing needed fixing; re-verified this phase with `tests/test_concurrency.py`'s real-thread negative-inventory race tests (still passing, unchanged).

### Section 14 — Transport lifecycle

**Finding**: `TransportRoute.status` is binary (`OPERATIONAL`/`DISRUPTED`), not the richer multi-state lifecycle the master prompt suggests. What *is* real: disrupting a route synchronously triggers real re-optimization of every open emergency whose current pathway depended on it (`emergency_pathway_service.handle_route_disrupted`) — pathway feasibility does reflect transport reality, never a stale "still fine."

**Classification**: B relative to the suggested richer model, but evaluated and **not implemented**.

**Status**: VALID LIMITATION. This codebase has no vehicle/shipment/dispatch entity at all — routes are graph edges, not physical trips. Expanding the status enum to `ASSIGNED`/`EN_ROUTE_TO_SOURCE`/etc. without a real dispatch-tracking system behind it would be a decorative status field with nothing to drive its transitions, which Section 34 explicitly warns against.

### Section 15 — Geographic realism

**Finding**: `GraphPathwaySource` (`GRAPH`/`HAVERSINE_FALLBACK`) is threaded end-to-end from the graph service through the API schema to the frontend UI, and was already verified in prior phases as never mislabeling a Haversine estimate as a real graph/road route.

**Classification**: NOT APPLICABLE — already correct.

**Status**: NOT FIXED because nothing needed fixing; spot-checked, not re-implemented.

### Section 16 — Notification semantics

**Finding**: In-app `Notification` creation is synchronous and transactional (cannot be silently lost the way an async-only channel could); email/SMS are explicitly documented best-effort side channels whose failure never rolls back the primary write. One real gap fixed this phase (see Section 6 above): ineligible donors were needlessly alerted. Duplicate-notification/fanout/dedup behavior was investigated and found already correct (batched single-query fanout since Phase 27, no duplicate-send paths found).

**Classification**: B for the ineligible-donor-alert gap (fixed, see above); NOT APPLICABLE for the rest.

**Status**: See Section 6 for the fix; the remainder NOT FIXED because already correct.

### Section 17 — Event reliability / outbox analysis

**Finding**: Every notification-triggering write happens in the same DB transaction as the domain event it represents (e.g. a `BloodRequest` status change and its notification are both part of one `db.commit()`), so there is no failure window between "the domain fact is true" and "a notification row exists" the way a separate outbox pattern would need to close. A full outbox/event-log architecture was evaluated and judged unnecessary.

**Classification**: NOT APPLICABLE.

**Status**: NOT FIXED — documented as sufficient by design, not implemented as a new distributed pattern, per Section 17's own instruction not to build one "for appearance."

### Section 18 — Family / privacy / access control

**Finding**: See `docs/LIFELINK_PRIVACY_ACCESS_MATRIX.md` for the full matrix and every IDOR probe run. All 7 tested classes (cross-patient, unauthorized family, cross-donor, cross-hospital, admin-bypass, cross-kind verification, public-endpoint-overexposure) were confirmed correctly scoped.

**Classification**: NOT APPLICABLE.

**Status**: NOT FIXED because nothing needed fixing.

### Section 19 — Verification lifecycle

**Finding (real gap)**: `submit_verification`'s "no duplicate PENDING request of this kind" check was a pre-flight SELECT with no DB-level backing — a genuine check-then-insert TOCTOU race (two concurrent submissions, e.g. a double-click or two browser tabs, could both pass the check and both insert).

**Classification**: D (concurrency gap).

**Status**: **FIXED**. New partial unique index `uq_verification_one_pending_per_kind` on `(user_id, kind) WHERE status = 'PENDING'` (same pattern as the pre-existing `uq_emergency_pathway_one_active`), paired with a SAVEPOINT around the insert so the race's loser gets the same honest 409 instead of an unhandled `IntegrityError`. Migration: `e6f7a8b9c0d1_...`, verified upgrade→downgrade→upgrade. Tests: `test_second_pending_verification_of_same_kind_is_rejected`, `test_concurrent_duplicate_verification_submissions_only_one_succeeds` (real threads + `threading.Barrier`, proves the race is actually closed, not just the common case), `test_resubmission_after_rejection_is_allowed` (confirms the fix doesn't over-block legitimate resubmission).

**Other Section 19 questions investigated**: document versioning across resubmission does not exist (a rejected-then-resubmitted request is a new row, not a version of the old one) — judged a valid, minor limitation, not fixed (see the domain model doc). Reviewer identity/timestamp and rejection reasons are already captured (`reviewer_user_id`, `review_note`, `updated_at`).

### Section 20 — Certificate trust model

**Finding (real gap)**: A `Certificate`, once issued, could never be invalidated — no revoked/valid/unknown distinction anywhere. An admin who discovered a certificate was issued for a donation later found fraudulent or erroneous had no mechanism to say so, and the public verify endpoint always reported `valid: true` for any existing row.

**Classification**: B (missing domain logic, directly named by the master prompt's Section 20).

**Status**: **FIXED**. New nullable `revoked_at`/`revoked_by_user_id`/`revocation_reason` columns; new admin-only `POST /admin/certificates/{id}/revoke` (audit-logged, requires a real reason ≥3 chars, idempotent, never deletes the row — the factual "this was issued" record is permanent, only its trust status changes); `GET /certificates/verify/{code}` now returns `valid: false` plus the revocation details once revoked, rather than `valid: true` forever. Migration: `f7a8b9c0d1e2_...`, verified upgrade→downgrade→upgrade. Tests: 4 new tests (revoke-and-public-verify-reflects-it, only-admin-can-revoke, requires-a-real-reason, unknown-certificate-404s) plus 2 pre-existing exact-shape tests updated to include the new (intentionally public) fields.

### Section 21 — Audit log integrity

**Finding**: `AuditLog` is append-only (verified by grep — no route updates or deletes a row), records actor/target/IP/user-agent/structured meta, uses a server-default timestamp (not client-suppliable, so it can't be backdated by a malicious request). A tamper-evident hash chain (`previous_hash`/`current_hash`) was evaluated per Section 21's own prompt.

**Classification**: NOT APPLICABLE for the append-only/attribution findings (already correct); the hash chain is a genuine design option, evaluated and **not implemented**.

**Status**: VALID LIMITATION for the hash chain. Reasoning: a hash chain's value is defeating an attacker who has direct DB write access (a compromised DB credential or a malicious insider with SQL access) — but this system's actual threat model (see `docs/LIFELINK_THREAT_MODEL.md`) has no code path that grants any application role direct SQL access; every write goes through the ORM's application logic, which is what the rest of this audit already hardens. Adding cryptographic chaining without a corresponding real threat it defends against (beyond "looks more secure") is exactly the "no cryptography merely for presentation" case Section 21 itself warns against. Flagged as worth revisiting only if a future phase introduces a role or integration with direct database access.

### Section 22 — Formal invariants

See `docs/LIFELINK_INVARIANTS.md` — 12 invariants documented, each with its actual enforcement mechanism and verifying test, including the two new ones from this phase's fixes (donor single-commitment, emergency-deadline consequence).

### Section 23 — Concurrency / race-condition audit

**Findings**: two real races found and fixed this phase (verification duplicate-PENDING, Section 19 above; donor double-booking, Section 11 above — the latter is a logical-consistency race rather than a literal DB-constraint violation, since it spans two tables). All other check-then-act patterns surveyed (request creation, donation logging, inventory reservation, hospital confirmation, emergency creation, transport disruption, certificate generation, notification creation) were already closed by row-locking and/or unique constraints from prior phases, re-verified rather than re-implemented.

**Classification**: D for both fixes.

**Status**: **FIXED** (both — see Sections 11 and 19 above for full detail and test references). Every new concurrency test in this phase uses real PostgreSQL and real `threading.Barrier`-synchronized threads, never `time.sleep` as a synchronization substitute.

### Sections 24-27 — Research-grade mathematical model, optimization objective, graph engine, simulation reproducibility

**Finding**: Re-verified, not re-implemented, this phase (prior-phase agents' findings independently spot-checked): the matching engine's staged pipeline and tuple-based sort keys are real and deterministic (no fabricated math); the graph engine's `GRAPH`/`HAVERSINE_FALLBACK` provenance is honest end-to-end; Simulation/Research-mode pages that are conceptual carry the pre-existing "CONCEPTUAL ILLUSTRATION — NOT LIVE DATA" banner convention, consistently applied.

**Classification**: NOT APPLICABLE.

**Status**: NOT FIXED because nothing needed fixing.

### Section 28 — Evaluation framework

**Finding**: Re-verified from prior phases: real, measurable metrics exist (feasible-match rate, fulfillment rate, notification failure/retry rate via `SmsNotification`, inventory utilization via `HospitalInventoryLog`) — no new evaluation-framework document was judged necessary this phase beyond what Phase 24/25's performance docs already cover.

**Classification**: NOT APPLICABLE.

**Status**: NOT FIXED — no genuinely new evaluation gap was found this phase; `docs/LIFELINK_PHASE_31_EVALUATION_MODEL.md` was not created because there was nothing new to document beyond the existing Phase 24/25 performance material, and creating a redundant doc would violate Section 34's "no unnecessary output" guidance.

### Section 29 — Frontend domain-truth audit

**Finding**: The pre-existing "CONCEPTUAL ILLUSTRATION" labeling convention was found consistently applied across every demo/research page. No hardcoded mock arrays, fabricated success messages, or stale demo-data-shown-as-live were found in the pages inspected this phase (patient/donor/hospital dashboards, admin portal, certificate/verification screens) — all status labels, quantities, and timestamps traced back to real backend fields, consistent with prior-phase (26, 27) frontend-truth audits.

**Classification**: NOT APPLICABLE.

**Status**: NOT FIXED because nothing new was found this phase; a full 1:1 re-audit of every screen was not repeated from scratch (Phase 26/27 already did this exhaustively) — this phase's frontend attention was scoped to the fields this phase's own backend changes touch (`deadline_exceeded_at`, certificate `revoked_at`/`revocation_reason`), and those are additive fields not yet surfaced in the frontend UI (see Recommended Next Phase in the report doc).

### Sections 30, 32, 33 — E2E domain scenario, database design audit, API contract audit

**Finding**: The full backend regression suite (853 tests) constitutes real, repeated end-to-end coverage of both a normal request-to-fulfillment flow and an emergency-escalation-with-pathway-failure-and-reoptimization flow (`tests/test_phase12_emergency_sos_engine.py`'s disruption/reoptimization tests) — exercised as real HTTP requests against a real PostgreSQL database, not a synthetic in-process call. Database design (FKs, unique constraints, indexes, CHECK constraints, nullable fields, migrations) was re-verified for every table touched this phase; no destructive migration was written (all three new migrations are purely additive — new nullable columns or new indexes, verified upgrade→downgrade→upgrade against the real dev database each time). API contracts for every endpoint touched this phase were updated consistently on both sides (new Pydantic response fields on `EmergencyOut`/`CertificateOut`/`CertificateVerifyOut`/`AdminCertificateOut` — no endpoint's shape changed without its schema being updated to match).

**Classification**: NOT APPLICABLE for the pre-existing architecture; the migrations/contract changes this phase made are covered under their respective fixes above.

**Status**: NOT FIXED beyond what's already covered in Sections 9/19/20.

## Summary table

| # | Finding | Class | Status |
|---|---|---|---|
| 1 | Emergency accept missing eligibility gate | A | **FIXED** |
| 2 | notify_matching_donors alerts ineligible donors | B | **FIXED** |
| 3 | Emergency deadline has no operational consequence | B | **FIXED** |
| 4 | Donor can double-book across requests/emergencies | A | **FIXED** |
| 5 | Verification submission TOCTOU race | D | **FIXED** |
| 6 | Certificate cannot be revoked | B | **FIXED** |
| 7 | No per-unit BloodUnit lifecycle | B | VALID LIMITATION |
| 8 | No blood component model | F | VALID LIMITATION (Section 35) |
| 9 | Inventory allocate doesn't re-check compatibility | B | VALID LIMITATION |
| 10 | No PARTIALLY_FULFILLED status | H | VALID LIMITATION |
| 11 | Donation logging is honor-system (no hospital confirm) | H | VALID LIMITATION (mitigated by fix #6) |
| 12 | TransportRoute lacks a richer dispatch lifecycle | B | VALID LIMITATION |
| 13 | No audit-log hash chain | H | VALID LIMITATION |
| 14 | No verification document versioning across resubmission | H | VALID LIMITATION |
| 15-24 | Matching engine, decision transparency, hospital confirmation separation, inventory consistency, geographic realism, event reliability, access control, math/graph/simulation honesty, evaluation framework, E2E/DB/API architecture | — | NOT APPLICABLE (already correct, re-verified) |

Six real fixes implemented and tested; eight findings documented as valid, justified limitations with reasoning; the remainder confirmed already correct. No finding was left as an unexplained "not fixed."
