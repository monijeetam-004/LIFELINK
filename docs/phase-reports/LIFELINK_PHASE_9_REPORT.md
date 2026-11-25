# LifeLink — Phase 9 Report: Production Blood Compatibility and Matching Engine

## 0. Scope and approach

Phase 9 asked for a centralized, deterministic blood compatibility engine; a
64-combination compatibility test matrix; an explainable, multi-factor
donor-matching engine ("do not use an unexplained magic score"); and a
"Research Mode" comparison across four named strategies (nearest, time-aware,
time + resilience, event-driven), with real algorithms behind the results.

As with every prior phase, the approach was: audit the existing code
end-to-end before writing anything, then implement for real, and say plainly
where the spec's assumptions didn't match what the codebase actually has data
for, rather than fabricating anything to make a checklist look complete.

## 1. Audit findings

**Compatibility logic.** The ABO/Rh compatibility matrix
(`_CAN_DONATE_TO`, `can_donate_to`, `compatible_donor_types`) already lived in
exactly one place on the backend: `app/models/enums.py`. Every backend call
site (`graph_service.py`, `notification_service.py`, `emergencies.py`,
`matching.py`, `blood_requests.py`, `donors.py`) already imported that same
implementation — there was no backend duplication to remove. `enums.py`
itself, though, is a 250-line grab-bag of ~15 unrelated domain enums, not "one
authoritative domain implementation" in the sense the spec means. The one
genuine duplication was on the **frontend**: `BloodCompatibilityChart.tsx`
had its own hand-transcribed copy of the same table (its own comment even
said it was "transcribed exactly" from the backend — an honest admission that
it could drift).

**Matching/ranking logic.** Two independent, undocumented, plain
lexicographic-tuple sorts already existed and differed from each other:
`matching.py::match_candidates` sorted by `(not accepted, distance-or-inf,
last_donation_date)`, while `emergencies.py::emergency_match_candidates`
sorted by `(not accepted, role != HOSPITAL)`. Neither used a numeric score
(good — that requirement was already met), but neither explained itself, and
existing tests (`tests/test_matching_and_map.py`,
`tests/test_emergency_network.py`) locked in the exact resulting order,
including one explicit assertion (`assert "score" not in donors[0]`) that a
raw score must never appear on a candidate.

**"Research Mode" / the four named strategies.** These names — nearest,
time-aware, time + resilience, event-driven — do **not** appear anywhere in
this codebase's real logic. They only exist as static labels inside
`ResearchMode.tsx`, a frozen, public, unauthenticated Stitch-export mockup
with entirely invented numbers (confirmed by reading it in full — 228 lines,
zero API calls, matching the project's own documented "kept as-is, do not
redesign" policy for `ResearchMode.tsx` / `MathematicalModel.tsx` /
`GraphDecisionEngine.tsx`; see `README.md` and the earlier, unrelated
`LIFELINK_PHASE_8_REPORT.md` — the Neo4j graph-engine phase, not the Phase 8
authz report — for the established precedent of building real capability as
a *new authenticated screen* rather than wiring live data into a frozen
public mock). So there was no real algorithm to "preserve" for these four
names; they had to be designed and implemented for real, in the open, as new
work.

## 2. Centralized compatibility engine

New module: **`backend/app/core/blood_compatibility.py`** — the one
authoritative implementation the spec asks for. It owns:

- `_CAN_DONATE_TO` — the same matrix as before (medically unchanged), now
  documented and isolated from the enums grab-bag.
- `can_donate_to`, `compatible_donor_types` — moved here verbatim.
- `compatible_recipient_types` — new: the donor-side inverse (no backend
  caller needed this direction before; the new
  `GET /matching/blood-compatibility` endpoint does).
- `UI_LABELS` / `to_ui_label` — the single canonical↔label mapping ("A+",
  "O-", etc.), matching the spec's required UI labels exactly.
- `normalize_blood_type` / `try_normalize_blood_type` — parses either a
  canonical name or a UI label, tolerant of case and whitespace; raises (or
  returns `None`) for anything else, covering the spec's "invalid blood
  group" / "missing blood group" / "normalization" test requirements.

`app/models/enums.py` no longer defines any of this — it now carries a
comment pointing to the new module instead, so a future accidental
re-duplication there would show up as an unused-import lint failure, not a
silent second implementation. All six backend call sites were updated to
import from `app.core.blood_compatibility` instead of `app.models.enums`.

New endpoint: **`GET /matching/blood-compatibility`** — serves the whole
matrix (per group: canonical name, UI label, `can_donate_to`,
`can_receive_from`) to any signed-in user. The frontend's
`BloodCompatibilityChart.tsx` now fetches this instead of hardcoding the
table, eliminating the one real duplication found. `BLOOD_LABEL` in
`frontend/src/lib/domain.ts` was also fixed to use plain hyphens ("A-")
instead of a Unicode minus sign ("A−"), matching the spec's literal
UI-label list; there is no frontend test suite in this project (confirmed —
`node_modules` aside, there isn't a single `*.test.*` file), so this was safe
to change directly.

**Documented, deliberate scope**: this is red-cell (whole-blood) compatibility
only. The system has never modeled donation components (platelets, plasma) —
that's a pre-existing, pre-Phase-9 product decision recorded in
`DonorProfile`'s own model comment — so "component" as a compatibility axis
is out of scope, not silently dropped.

## 3. 64-combination compatibility test matrix

New file: **`backend/tests/test_blood_compatibility_matrix_phase9.py`**
(29 tests). The ground-truth table is transcribed independently from
standard ABO/Rh transfusion rules (not copied from the implementation under
test), covering:

- All 64 donor × recipient combinations, parametrized individually.
- `compatible_donor_types` and `compatible_recipient_types` cross-checked
  against the same independent ground truth from both directions.
- The universal-donor (O-) and universal-recipient (AB+) edge cases, and
  self-compatibility for all 8 groups.
- UI representation: every canonical↔label mapping, both directions.
- Normalization: case/whitespace tolerance, missing blood group (raises),
  invalid blood group (raises, several malformed inputs), and the
  graceful-degradation variant (`try_normalize_blood_type`).
- The wire-level `GET /matching/blood-compatibility` endpoint, checked
  against the same ground truth, plus its auth requirement.

## 4. Explainable matching engine

New module: **`backend/app/core/matching_engine.py`** — a pure, DB-free
ranking function. The caller (a route) queries the database, filters to
already-compatible candidates, and builds one `MatchCandidateInput` per
candidate; the engine only sorts and explains. This split is what makes the
engine independently unit-testable without a database.

### What each of the spec's ten matching factors maps to, honestly

| # | Factor | Status |
|---|---|---|
| 1 | compatibility | Real — caller only ever passes pre-filtered compatible candidates |
| 2 | component | **Not modeled** — whole-blood-only system, pre-existing decision |
| 3 | donor availability | Real — `DonorProfile.is_available` |
| 4 | eligibility | Real — `app.core.eligibility.is_eligible` (56-day rule); `None` for a hospital responder |
| 5 | verification | Real — `DonorProfile.identity_verified`; `None` for a hospital responder (see known gap below) |
| 6 | location | Real — haversine distance where both sides have coordinates and the donor opted in |
| 7 | urgency | Real — the request/emergency's actual `Urgency`, carried through to every factor and explanation; documented as *not* varying sort order in this engine (no strategy here differentiates by urgency tier) |
| 8 | response likelihood/reliability | Approximated by `has_accepted` (real: has this candidate already said yes) — no historical response-rate model exists to do better |
| 9 | resilience | Approximated by `stale_availability`: whether `DonorProfile.updated_at` is older than 30 days — a real, computed proxy, not a true reliability score, and documented as such |
| 10 | operational constraints | **Not modeled** at donor-ranking level — facility-level operational constraints already have a separate, real engine (`graph_service.py`); ranking donor candidates for one request is a different question |

**Known gap, disclosed rather than hidden**: the emergency responder query
doesn't currently join `HospitalProfile.verified` for hospital responders, so
a hospital candidate's `verified` factor is `None` (not applicable) rather
than reflecting that real flag. Donor `identity_verified` is unaffected.

### The four strategies

`MatchStrategy`: `NEAREST`, `TIME_AWARE`, `TIME_RESILIENCE`, `EVENT_DRIVEN`.
These names come from the frozen `ResearchMode.tsx` mockup (see §1) but the
algorithms behind them are new, real, and specific to this codebase's actual
fields:

- **NEAREST** — accepted first, then closest by real distance, then earlier
  last-donation-date, tie-broken by a stable ref. This exactly reproduces the
  pre-Phase-9 default ordering, so no existing test's expectations changed.
- **TIME_AWARE** — accepted first, then anyone whose donation-eligibility
  rest period has already elapsed (or for whom eligibility doesn't apply)
  ranks ahead of anyone still resting, then distance.
- **TIME_RESILIENCE** — TIME_AWARE plus identity-verified candidates rank
  ahead of unverified ones, as a dependability signal, before falling back to
  distance.
- **EVENT_DRIVEN** — reacts to what has already happened on the live
  item: accepted first, then currently-available, then fresh (not stale)
  availability data, then distance.

Every sort key ends with the candidate's unique `ref` as a final
deterministic tie-breaker, so equal-in-every-other-way candidates still
produce one stable order.

Every ranked candidate carries `MatchFactors` (no `score` field — this is
enforced by an existing pre-Phase-9 test assertion,
`assert "score" not in donors[0]`, which still passes) and a generated
`explanation` string built only from that candidate's own real factors, in
the spirit of the spec's own example sentence.

### Wiring

- `GET /blood-requests/{id}/match-candidates` and
  `GET /emergencies/{id}/match-candidates` now run through the engine
  (default strategy `NEAREST`, selectable via a `?strategy=` query param),
  and every candidate now additionally carries `rank`, `factors`, and
  `explanation`. All pre-existing response fields (`donor_ref`, `donor_name`,
  `is_available`, `has_accepted`, `distance_km`, `last_donation_date`,
  `city`) are unchanged.
- New: `GET /blood-requests/{id}/match-strategies` and
  `GET /emergencies/{id}/match-strategies` — run all four strategies over the
  same real candidate pool at once, always anonymised, for side-by-side
  comparison.
- New authenticated frontend screen: **Strategy Lab**
  (`frontend/src/pages/shared/StrategyLab.tsx`, at `/matching/strategy-lab`,
  gated the same way as match-candidates itself). This is where the real
  four-strategy comparison actually lives — consistent with this project's
  established pattern (see `ResourcePathways.tsx` for the prior instance) of
  adding a new authenticated screen for new real capability rather than
  bolting live data onto a frozen public mock. `ResearchMode.tsx` and
  `MathematicalModel.tsx` are untouched, exactly as before.

One documented ordering change: the pre-Phase-9 emergency endpoint
tie-broke unaccepted candidates as "hospitals before donors"
(`c.role != HOSPITAL`); the engine's strategies don't carry that special
case (none of the four strategies were designed around a role-based
tie-break). No existing test asserted that ordering specifically — the one
test touching this endpoint (`test_match_candidates_factual_and_anonymised`)
filters by role and checks per-role facts, not cross-role order — so this
is a deliberate, documented simplification, not a silent regression.

## 5. Matching engine tests

New file: **`backend/tests/test_matching_engine_phase9.py`** (22 tests),
covering every item in the spec's TESTING list against the pure engine
(no database needed for most of these):

- Ranking determinism (repeated runs, shuffled input order, all 4
  strategies).
- Equal candidates — deterministic tie-break by ref.
- Unavailable donor — still ranked (never silently dropped by the engine
  itself), and EVENT_DRIVEN specifically ranks available ahead of not.
- Unverified donor — TIME_RESILIENCE ranks verified ahead; NEAREST/
  TIME_AWARE are unaffected by verification (proving the strategies are
  genuinely different algorithms, not the same sort renamed four times).
- Urgent request — `factors.urgency` genuinely reflects the request's real
  urgency for every `Urgency` value.
- No candidates — every strategy returns `[]` for empty input.
- Location missing — `distance_km`/`within_alert_radius` are `None`, and
  missing-location candidates always sort after known-distance ones.
- Stale availability — computed correctly from `updated_at`, and
  EVENT_DRIVEN ranks fresh data ahead of stale.
- One combined test constructing a candidate pool where NEAREST,
  TIME_AWARE, and TIME_RESILIENCE each pick a different #1 candidate.
- No `score` field anywhere, on the dataclass or in `factors`.
- API-level: an incompatible-blood-type donor never reaches either
  `/match-candidates` or the new `/match-strategies` endpoints (for both
  blood requests and emergencies); the default `/match-candidates` strategy
  is `NEAREST` and exposes `rank`/`factors`/`explanation` without a `score`;
  an explicit `?strategy=` query param is honored.

## 6. Verification performed

- **Backend**: full suite, 450 tests, 0 failures / 0 errors (verified via
  JUnit XML, `pytest -q --junitxml=...`). This includes every pre-existing
  matching/emergency/graph test file (`test_matching_and_map.py`,
  `test_emergency_network.py`, `test_donor_matching.py`,
  `test_emergencies_and_notifications.py`, the graph-service/API/engine
  files) plus the two new Phase 9 files (51 new tests total).
- **`ruff check app`**: 21 findings, all `UP042` ("class inherits from both
  `str` and `enum.Enum`, prefer `enum.StrEnum`") — 20 of these are
  pre-existing on classes this phase did not touch (`BloodType`, `Urgency`,
  `UserRole`, and 17 others already in the codebase before Phase 9); the
  21st is the same, pre-existing, codebase-wide convention applied
  consistently to the one new enum this phase adds (`MatchStrategy`). Four
  genuinely new `UP017` findings (`datetime.timezone.utc` → `datetime.UTC`)
  in the new `matching_engine.py` were fixed for real via `ruff --fix`.
- **`mypy app`**: the same pre-existing 3-error baseline as every prior
  phase (`app/api/routes/verifications.py` ×2, `app/api/routes/donors.py`
  ×1) — unchanged, nothing new introduced.
- **Frontend**: `tsc --noEmit` (typecheck), `eslint .` (lint), and
  `tsc -b && vite build` (production build) all clean.

## 7. Files changed

**New:**
- `backend/app/core/blood_compatibility.py`
- `backend/app/core/matching_engine.py`
- `backend/tests/test_blood_compatibility_matrix_phase9.py`
- `backend/tests/test_matching_engine_phase9.py`
- `frontend/src/pages/shared/StrategyLab.tsx`

**Backend, edited:**
- `app/models/enums.py` (compatibility logic removed, pointer comment added)
- `app/api/routes/matching.py` (engine wiring, new `/matching/blood-compatibility`
  and `/blood-requests/{id}/match-strategies` endpoints)
- `app/api/routes/emergencies.py` (engine wiring, new
  `/emergencies/{id}/match-strategies` endpoint)
- `app/api/routes/blood_requests.py`, `app/api/routes/donors.py`,
  `app/services/notification_service.py`, `app/services/graph_service.py`
  (import updates only — call sites unchanged)
- `app/schemas/matching.py` (new `BloodCompatibilityOut`,
  `MatchFactorsOut`, `StrategyComparisonOut` schemas; `MatchCandidate` gains
  `rank`/`factors`/`explanation`)
- `app/schemas/emergency.py` (`EmergencyMatchResponder` gains
  `rank`/`factors`/`explanation`)

**Frontend, edited:**
- `src/lib/domain.ts` (fixed `BLOOD_LABEL` hyphens, added
  `getBloodCompatibility`, matching-engine types, strategy-comparison API calls)
- `src/components/BloodCompatibilityChart.tsx` (now fetches the matrix
  instead of hardcoding it)
- `src/App.tsx` (new guarded `/matching/strategy-lab` route)

## 8. Honest limitations

- The spec's premise that an existing "nearest, time-aware, time +
  resilience, event-driven" comparison framework should be "preserved" did
  not match reality — no such framework existed anywhere as real logic.
  What's delivered instead is a genuine, from-scratch implementation of all
  four, built on this codebase's actual data, with the reasoning for each
  documented in code rather than invented to match a UI mockup's numbers.
- "Resilience" and "response likelihood/reliability" are real, computed
  proxies (`stale_availability`, `has_accepted`) — not a true historical
  reliability model, because this codebase has no per-donor response-history
  table to build one from.
- "Component" and facility-level "operational constraints" are explicitly
  out of scope for donor-candidate ranking, for the reasons in §4's table.
- A hospital responder's `verified` factor is `None` rather than reflecting
  `HospitalProfile.verified`, because the emergency responder query doesn't
  currently join that column — a real, disclosed gap rather than a
  fabricated `True`/`False`.
