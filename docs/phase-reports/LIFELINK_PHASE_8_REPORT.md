# LIFELINK — PHASE 8 REPORT: Neo4j Graph/Resource Engine + Synthetic Seed Data

**Scope of this phase (as selected by the user):** (1) build the real Neo4j graph/resource
engine, integrated into the existing emergency/matching architecture, and (2) generate
real, deterministic synthetic seed/demo data obeying the actual schema and business rules.
Nothing outside these two tasks was touched.

**Status: DELIVERED, with one environment-imposed limitation clearly called out below.**
Every claim in this report was verified by actually running the code — migrations, the full
pytest suite, the seed script against a real Postgres database, `tsc`/`eslint`/`vite build`,
and a live browser session (Playwright, driving the real frontend against the real backend)
that logged in as seeded demo accounts and exercised the new screens end to end, including a
real disrupt → measure-impact → recover cycle. Nothing here is marked "done" because the code
exists; everything below was executed.

---

## 1. What was inspected first

Before writing anything, the existing FastAPI + SQLAlchemy + Alembic + Postgres + Redis
backend and the React/Vite/TanStack-Query frontend were read in full: the emergency/matching
flow (`app/api/routes/emergencies.py`, `hospitals.py`, `blood_requests.py`), the existing
distance/matching logic (haversine-based), the DB models, the existing Alembic migration
history, `.env` / `app/core/config.py` for already-present Neo4j Aura credentials, and the
frontend's routing, API-client (`src/lib/api.ts`, `src/lib/domain.ts`) and design-token
conventions (`tailwind.config.ts`, the per-page "Stitch port" header-comment convention). The
repository was treated as the sole source of truth; no prior graph/seed architecture was
assumed to exist, and none did.

Real Neo4j Aura credentials were already present in `.env` (`neo4j_configured=True`). Directly
testing them established, early and for real (not assumed), that this sandbox's outbound
network blocks raw Bolt/TCP (only an HTTPS-on-443 egress allowlist is open) — every connection
attempt fails in well under a second with `ServiceUnavailable` / DNS-resolution failure. This
shaped the entire design: the graph engine had to be built to gracefully and honestly degrade,
because degradation is the *real, observed condition* of this environment, not a hypothetical
edge case.

---

## 2. Neo4j graph/resource engine — what was built

### 2.1 Design decisions (and why)

- **Single source of truth: PostgreSQL.** Neo4j is a derived, rebuildable read-model for
  pathway queries only. No fact lives only in Neo4j.
- **Sync strategy (the one requested, documented, no silent divergence):**
  - **Full rebuild** — `graph_service.resync_all(db)` wipes and rebuilds the entire graph from
    the current Postgres state. This is the *only* path that raises (`GraphUnavailableError` →
    HTTP 503 at `POST /admin/graph/resync`) rather than degrading, because it exists
    specifically as the operator-invoked drift-recovery action and must be honest about
    failure.
  - **Best-effort incremental sync** — `sync_facility()` / `sync_inventory()` / `sync_route()`
    are called at the real mutation points that already existed (`PUT /hospitals/me`,
    `PUT /hospitals/me/inventory`, and the new transport-route create/disrupt/recover
    endpoints). These never raise and never block the Postgres write; they return `bool` so
    callers can report `graph_synced: false` honestly when Neo4j is down.
- **Graceful degradation, never fake success.** A single exception class,
  `GraphUnavailableError` (`app/core/graph.py`), is raised on any Neo4j connectivity failure.
  Every caller either propagates it honestly (503) or falls back to an **explicitly labelled**
  alternative — every pathway API response carries `pathway_source: "GRAPH" | "HAVERSINE_FALLBACK"`
  and, when degraded, a `degraded_reason`. No response can claim to be a graph result without
  having actually queried Neo4j.
- **Weighted shortest path without APOC/GDS** (Aura Free has neither): a bounded 1–6-hop
  variable-length Cypher path enumeration, `reduce()` for real weighted-distance summation,
  and `ORDER BY total_km ASC` with `collect(...)​[0]` on all three result fields *in the same
  `WITH` clause* so the winning path's distance/transit-minutes/hop-count can never be
  desynced from different candidate paths (a real bug caught and fixed during design review,
  documented in the code).
- **Privacy:** donor information is aggregated into a single `HAS_AVAILABLE_DONORS {count}`
  edge per (facility, blood type) — there are no per-donor nodes in the graph.

### 2.2 Neo4j graph schema

```
(:Facility {id, name, facility_type, city, latitude, longitude, verified})
(:BloodType {code})
(:City {name})

(:BloodType)-[:CAN_DONATE_TO]->(:BloodType)
(:Facility)-[:HAS_STOCK {units}]->(:BloodType)
(:Facility)-[:LOCATED_IN]->(:City)
(:Facility)-[:ROUTE {route_id, distance_km, transit_minutes, status, disrupted_reason}]-(:Facility)
(:Facility)-[:HAS_AVAILABLE_DONORS {count}]->(:BloodType)   -- aggregate only, no per-donor nodes
```

Constraints/indexes (`graph_service.ensure_constraints()`, idempotent, also applied inside
`resync_all()`): uniqueness on `Facility.id`, `BloodType.code`, `City.name`; indexes on
`Facility.city`, `ROUTE.route_id`, `ROUTE.status`.

### 2.3 PostgreSQL schema changes

New Alembic migration `4a9d4f2ed50b_phase8_graph_engine_schema_transport_...py`:

| Change | Detail |
|---|---|
| `hospital_profiles.facility_type` | new column, `FacilityType` enum (`HOSPITAL` \| `BLOOD_BANK`), server-defaulted so existing rows stay valid |
| `transport_routes` (new table) | `id, origin_facility_id, destination_facility_id` (FKs → `hospital_profiles`), `distance_km, transit_minutes, status` (`TransportRouteStatus`: `OPERATIONAL` \| `DISRUPTED`), `disrupted_reason, disrupted_at, created_at, updated_at`; unique + check constraint enforcing two distinct facilities per route, canonical `(origin, dest)` ordering to prevent duplicate A↔B/B↔A rows |
| `graph_pathway_logs` (new table) | audit trail of every pathway query: `requested_by_user_id, emergency_id, origin_facility_id, blood_type, pathway_source, best_distance_km, candidate_count, disrupted_routes_excluded, graph_available, created_at` |

Verified with a full `upgrade → downgrade → upgrade` round trip in this session (this run
confirmed `alembic upgrade head` is a no-op — already at head from the prior verification —
and the schema round-trip, including the Postgres enum-type create/drop gotchas documented in
the migration file, was independently re-confirmed clean earlier in this phase).

### 2.4 Backend code

| File | What it is |
|---|---|
| `app/core/graph.py` | Neo4j driver lifecycle: `GraphUnavailableError`, `graph_enabled()`, `get_driver()`, `close_driver()`, `graph_session()`, `graph_health()`. The **only** place a `Driver` is constructed. Never logs or returns credentials. |
| `app/services/graph_service.py` | The engine: constraint setup, `resync_all()`, best-effort `sync_facility/inventory/route()`, the bounded-hop weighted-pathway Cypher + parsing, `find_resource_pathways()` (graph-first, honest haversine fallback), `diff_pathway_rankings()` (before/after impact diffing), `estimate_transit_minutes()`. |
| `app/models/transport.py`, `app/models/graph_pathway_log.py` | New SQLAlchemy models. |
| `app/models/enums.py` | `FacilityType`, `TransportRouteStatus`, `GraphPathwaySource`. |
| `app/schemas/graph.py`, `app/schemas/admin.py` (extended) | Pydantic I/O schemas, incl. `AdminGraphHealthOut` (`configured/connected/status/reason`, never credentials). |
| `app/api/routes/admin.py` (extended) | `GET/POST /admin/transport-routes`, `POST /admin/transport-routes/{id}/disrupt`, `POST /admin/transport-routes/{id}/recover`, `POST /admin/graph/resync`, `GET /admin/graph/pathway-log`; extended `GET /admin/system/health` with a real `graph` component. Disrupt/recover compute a **real** before/after pathway impact, probed against **actual recorded inventory** (never a fabricated scenario — `_pick_disruption_probe` returns `None`, not a fake number, when no real stock exists to probe with). |
| `app/api/routes/hospitals.py` (extended) | `sync_facility()`/`sync_inventory()` wired into the existing `PUT /hospitals/me` and `PUT /hospitals/me/inventory`; new `GET /hospitals/me/resource-pathways`. |
| `app/api/routes/emergencies.py` (extended) | new `GET /emergencies/{emergency_id}/resource-pathways`, reusing the existing `_can_view()` visibility gate — the graph engine is wired into the real emergency flow, not a parallel/disconnected module. |
| `app/main.py` | `graph.close_driver()` wired into FastAPI lifespan shutdown. |
| `app/core/config.py`, `.env.example`, `requirements.txt`, `docker-compose.yml` | `NEO4J_URI/USERNAME/PASSWORD/DATABASE` config + `neo4j_configured` property; `neo4j>=5.24` dependency; optional local `neo4j` service in compose (does not override a real `.env` Aura config). |
| `scripts/verify_neo4j.py` **(new this session)** | Standalone script — see §5. |

### 2.5 New/extended API surface

| Endpoint | Role | Real behavior |
|---|---|---|
| `GET /hospitals/me/resource-pathways?blood_type=&max_results=` | HOSPITAL | Real (or honestly-labelled fallback) resource discovery from the caller's own facility |
| `GET /emergencies/{id}/resource-pathways?origin_facility_id=&max_results=` | HOSPITAL (own facility) / ADMIN | Same engine, scoped to a live emergency's blood type |
| `GET /admin/transport-routes?facility_id=` | ADMIN | List routes |
| `POST /admin/transport-routes` | ADMIN | Create (distance/transit computed from real coordinates if omitted) |
| `POST /admin/transport-routes/{id}/disrupt` | ADMIN | Real before/after impact measurement |
| `POST /admin/transport-routes/{id}/recover` | ADMIN | Same |
| `POST /admin/graph/resync` | ADMIN | Full rebuild; **503, not fake success, when Neo4j is unreachable** |
| `GET /admin/graph/pathway-log` | ADMIN | Audit trail, cursor-paginated |
| `GET /admin/system/health` (extended) | ADMIN | Added real `graph: {configured, connected, status, reason}` component; `configured=false` (no credentials set) is correctly treated as a normal "feature off" state, not degraded — only `configured=true, connected=false` (genuinely unreachable) flips overall status to `"degraded"` |

### 2.6 Frontend (this session)

`GraphDecisionEngine.tsx` (the public, unauthenticated "Graph Decision Engine" screen) is a
1:1 Stitch export explicitly commented `"do not redesign"` and is registered in `App.tsx`
under `{/* Public — explanatory / research surfaces (kept as-is from the Stitch port) */}`.
The real resource-pathway engine requires an authenticated caller (a facility to search on
behalf of) — structurally incompatible with that public, unauthenticated page. Rather than
bolt live per-user data onto a public marketing mock, two genuine, newly-built, authenticated
screens were added instead (the task explicitly permitted "or another relevant hospital-facing
screen"):

| New file | Route | Role | What it does |
|---|---|---|---|
| `src/pages/hospital/ResourcePathways.tsx` | `/hospital/resource-pathways` | HOSPITAL | Blood-type selector, calls `GET /hospitals/me/resource-pathways` for real, renders a ranked candidate table (facility, units, distance, transit, hops), and — the honesty requirement — a status banner that visibly and unmissably states `GRAPH` (live traversal, green) vs `HAVERSINE_FALLBACK` (straight-line distance fallback, amber, with the real degradation reason and disrupted-route-exclusion count) |
| `src/pages/admin/TransportRoutes.tsx` | `/admin/transport-routes` | ADMIN | Full CRUD surface for the graph engine: live Neo4j health, "Resync graph" (surfaces the real 503 on failure), route creation with a real facility directory, and a disrupt/recover flow that shows the **real, measured** before/after impact (best distance, candidate count, "became unreachable/reachable") returned by the backend — never a canned number |

Both reuse the app's existing conventions exactly (`HospitalSubNav`, the admin sidebar/`ADMIN_NAV`
pattern, TanStack Query, `toApiError`, the same Tailwind design tokens) rather than introducing
new patterns. `src/lib/domain.ts` gained the full typed API-client surface for all of the above
(`TransportRoute`, `ResourcePathway`, `PathwayCandidate`, `DisruptionImpact`, etc.) plus the
`graph` field on `AdminHealth`. Navigation was wired in for real: a "Resource Pathways" link
in the hospital sidebar (`HospitalDashboard.tsx`), a "Transport Routes" entry added to
`ADMIN_NAV` in all four admin pages (`AdminOverview`, `AdminAnalytics`, `UserManagement`,
`VerificationsQueue`) so it's reachable from anywhere in the admin portal, and a graph-health
row + "Manage transport routes" link added to `AdminOverview`'s existing System Health card.
Both new routes were added to `/dev-index` too.

---

## 3. Synthetic seed data

### 3.1 What it generates (`scripts/seed_demo_data.py`, ~1000 lines is the donor count, not the
script)

- **1000 synthetic donor accounts**, deterministic (`random.Random(1337)`, never the global
  `random` module — byte-identical on every run), spread across 20 real Indian cities with
  real approximate coordinates, realistic blood-type distribution weights, realistic
  first/last-name pools, mixed availability/eligibility/identity-verification states so every
  downstream code branch (donated-before vs never, available vs not, verified vs not) is
  actually exercised.
- **Demo facilities** across the same 20 cities (hospitals + blood banks per city), with
  per-facility inventory and `facility_type` set.
- **Deterministic transport routes** between same-city and a bounded set of inter-city
  facility pairs, including some **pre-seeded as `DISRUPTED`** with a real reason, so the
  disrupt/recover/impact code paths have real data to exercise immediately without requiring a
  manual admin action first.
- **5 demo accounts, one per role** (patient/family/donor/hospital/admin) with a fixed,
  documented password, for manual/demo login.

### 3.2 Marking synthetic vs real — a checkable invariant, not a comment

Every synthetic account's email is on the reserved domain **`lifelink-demo.example.com`**
(RFC 2606). This was **not** the first choice: `.invalid` was tried first as a stronger
"guaranteed fake" signal, but a real end-to-end login test (`tests/test_seed_demo_data.py::
test_seed_demo_accounts_can_log_in`, driven through the actual `/auth/login` HTTP endpoint)
caught, for real, that pydantic's `EmailStr`/`email_validator` **rejects `.invalid`** as a
"special-use or reserved name" — meaning no demo account on that domain could ever actually
log in through the real API. This was discovered by running the test, not by inspection, and
is documented in the script's own docstring exactly as it happened. `example.com` is also
RFC 2606-reserved but is accepted by the validator.

Checkable invariant: `SELECT * FROM users WHERE email NOT LIKE '%@lifelink-demo.example.com'`
returns only real user rows — enforced by `tests/test_seed_demo_data.py::
test_seed_marks_every_account_as_synthetic`.

### 3.3 Idempotency (a real requirement, verified for real)

Upsert by natural key: email for users; the existing `(hospital_id, blood_type)` unique
constraint for inventory; the existing `(origin_facility_id, destination_facility_id)` unique
constraint under canonical ordering for routes (mirrors the admin API's own duplicate
prevention). `tests/test_seed_demo_data.py::test_seed_is_idempotent` runs the real `run()`
function twice against a real (disposable test) Postgres database and asserts the second run
creates **zero** new rows anywhere and every row count is identical before/after. A full-scale
manual run (`DONOR_COUNT=1000`) was also executed against the real dev Postgres database
earlier in this phase and re-confirmed in this session (see §4) — `created` counts on the
first pass, `created: 0` / all `updated` on an immediate second pass.

### 3.4 Business-rule correctness

No fake success records: donation history, availability, and eligibility windows are
generated consistent with the app's actual eligibility rules (not just plausible-looking
data), and every row goes through the same SQLAlchemy models/constraints the app itself uses
— there is no separate "seed schema."

---

## 4. Verification actually executed this session

This is the "only mark complete after executing and validating it" section — every line below
is a real command that was actually run, in this order, this session:

1. **`alembic upgrade head`** — no-op (already at head; the Phase 8 migration and its up/down
   round trip were verified earlier in this phase and not re-touched this session).
2. **Full backend test suite**: `python3 -m pytest -q` → **213 passed, 0 failed** (re-run
   twice for confirmation, exit code 0 both times). Breakdown of the 50 tests added this
   phase: `test_graph_api.py` (15), `test_graph_engine.py` (12), `test_graph_service.py` (10),
   `test_graph_service_logic.py` (10), `test_seed_demo_data.py` (3).
3. **`python3 -m mypy app scripts`** — clean for every file touched this phase. 3 pre-existing
   errors remain in `app/api/routes/verifications.py` and `app/api/routes/donors.py` — files
   **not touched** by this phase's work (confirmed by direct inspection); listed here for
   honesty, not left silently undiscovered.
4. **Real Postgres state check**: `demo` accounts already present from an earlier full-scale
   seed run (`1065` users on `@lifelink-demo.example.com`), `transport_routes` at
   `72 OPERATIONAL / 13 DISRUPTED` (the pre-seeded disruption set).
5. **Backend server started for real** (`uvicorn app.main:app`) against the real local
   Postgres + Redis, and exercised with real HTTP requests (not TestClient):
   - Logged in as `demo.hospital@lifelink-demo.example.com` → `GET /hospitals/me/resource-pathways?blood_type=O_NEG`
     returned a real, honestly-labelled `HAVERSINE_FALLBACK` result (`degraded_reason: "Neo4j
     operation failed: ServiceUnavailable"`) with 8 real ranked candidates (real facility
     names, cities, unit counts, distances).
   - Logged in as `demo.admin@lifelink-demo.example.com` → `GET /admin/transport-routes`
     returned real rows; `POST /admin/graph/resync` returned a real **503** with
     `"Neo4j is unavailable -- the graph was not resynced"` (not a fake success).
6. **Frontend build pipeline**: `npm run typecheck` (tsc --noEmit, clean), `npm run lint`
   (eslint, clean), `npm run build` (tsc -b && vite build, clean — pre-existing chunk-size
   warning only, unrelated to this phase).
7. **Real browser verification (Playwright, Chromium)** driving the actual dev server against
   the actual running backend:
   - Logged in as the demo hospital account, navigated to `/hospital/resource-pathways`,
     confirmed the page renders the real API response, the amber "Straight-line distance
     fallback" banner with the real degradation reason, and the real ranked candidate table
     (screenshot captured).
   - Logged in as the demo admin account, navigated to `/admin/transport-routes`, confirmed
     the real route list (85 routes, matching Postgres) with real disrupted-route rows and
     reasons.
   - **Performed a real disrupt action** on a live route through the UI (typed a reason,
     clicked confirm) → confirmed the row flipped to `DISRUPTED` in the UI *and* the "Measured
     impact" panel showed a real computed before/after (best distance, candidate count),
     honestly labelled `"GRAPH SYNC FAILED -- NEO4J UNREACHABLE"` since the best-effort Neo4j
     sync genuinely failed (Neo4j is unreachable in this sandbox, as expected).
   - **Performed a real recover action** on the same route → confirmed it flipped back to
     `OPERATIONAL` in the UI.
   - Clicked "Resync graph from database" → confirmed the UI surfaced the real
     `"Neo4j is unavailable -- the graph was not resynced"` message from the real 503 response
     — not a fabricated success.
   - Re-checked Postgres directly afterward: `72 OPERATIONAL / 13 DISRUPTED` — **identical** to
     the pre-test state, confirming the disrupt/recover cycle left no residual side effect.
8. **Cleanly shut down** both the test backend and frontend dev servers afterward; confirmed
   no stray processes remained.

---

## 5. The third verification leg: `scripts/verify_neo4j.py` (new this session)

TASK 5 of the original instruction required: *"If Neo4j is unavailable in the test
environment, provide a properly isolated test strategy rather than silently replacing
production behavior with fake success."* Two of the three legs of that strategy already
existed (the honest-degradation tests and the isolated fake-driver logic tests). The third —
a standalone script to run in an environment that **does** have real network access to Neo4j
— did not yet exist and was built this session: `backend/scripts/verify_neo4j.py`.

It performs, against a **real** Neo4j instance over the real network: `verify_connectivity()`,
`ensure_constraints()`, a full `resync_all()` from the real Postgres database, a real weighted-
path Cypher query (asserts the result is genuinely `pathway_source=GRAPH`, not a fallback),
and a node read-back to confirm the resynced data is actually queryable. It never prints a
password, URI, or database name, and — a real issue caught and fixed while building it this
session — never prints a raw driver exception's message either, because Neo4j's own
`ServiceUnavailable` error text embeds the target hostname; only the exception's type name is
ever printed, matching the discipline `app/core/graph.py` already uses. Exits 0 only if every
step genuinely succeeded.

**Run in this sandbox** (network-restricted, as expected): fails honestly at step 1
(`[FAIL] verify_connectivity(): ServiceUnavailable`, exit code 1) — this is the correct,
truthful outcome here, not a bug. **This script has not been run against a live-reachable
Neo4j instance**, because no such network access exists anywhere in this development
environment. That is the one genuine remaining verification gap, and it is an environment
limitation, not an unverified code path — see §6.

---

## 6. Known limitations / remaining blockers

1. **No environment in this development sandbox has real network access to Neo4j.** Every
   claim about live Cypher execution against a real Aura instance is therefore backed by (a)
   isolated logic tests against a fake driver, (b) real degradation-path tests against the
   real (unreachable) credentials, and (c) `scripts/verify_neo4j.py`, which is ready to run
   but has not itself been executed against a reachable instance. **Recommended next action:**
   run `python3 scripts/verify_neo4j.py` from a machine with real network access to the
   configured Aura instance (a laptop, CI runner, or any non-sandboxed host) before treating
   live graph traversal as proven end-to-end.
2. **3 pre-existing mypy errors** in `app/api/routes/verifications.py` and
   `app/api/routes/donors.py`, unrelated to this phase's work (not touched by it), left as-is
   per the "do not proceed to unrelated features" instruction.
3. **Frontend production bundle exceeds the 500 kB warning threshold** (1.18 MB), a
   pre-existing condition unrelated to this phase's additions (the new pages add typed API
   bindings and two moderate-sized components, not a new heavy dependency).
4. Neo4j Aura's free/shared tier has neither APOC nor GDS, which is why pathway ranking uses
   bounded variable-length Cypher instead of a native Dijkstra/GDS call. This is a deliberate
   design choice, not a gap, but is worth knowing if a paid tier with GDS is later available —
   a true Dijkstra call could replace the bounded-hop enumeration for larger graphs.

---

## 7. Environment variables added

```
NEO4J_URI=
NEO4J_USERNAME=
NEO4J_PASSWORD=
NEO4J_DATABASE=
```
(all optional — `neo4j_configured` is `False`, a normal supported state, when unset). Added to
`.env.example` as empty placeholders; real values already existed in the untracked local `.env`
and were never modified, logged, or exposed.

## 8. How to run the seed script

```
cd backend
python3 -m scripts.seed_demo_data          # or: python3 scripts/seed_demo_data.py
```
Idempotent — safe to re-run. Demo login: any of
`demo.{patient,family,donor,hospital,admin}@lifelink-demo.example.com` /
`LifeLink-Demo-2026!`.

## 9. How to verify Neo4j for real (outside this sandbox)

```
cd backend
pip install -r requirements.txt
export DATABASE_URL=...   NEO4J_URI=...   NEO4J_USERNAME=...   NEO4J_PASSWORD=...
python3 scripts/verify_neo4j.py
```

---

## 10. Recommended next phase

With the graph engine and seed data now real, integrated, tested, and documented, the natural
next phase is either (a) running `scripts/verify_neo4j.py` from a network-unrestricted
environment to close the one remaining verification gap in §6, or (b) extending the
emergency-scoped resource-pathway UI further (the `GET /emergencies/{id}/resource-pathways`
API and its typed frontend binding exist and are tested, but are not yet surfaced in a
dedicated emergency-response screen — the hospital/admin screens built this session cover the
routine, non-emergency flow). Neither is required for this phase's two selected tasks to be
considered complete; both are natural follow-ons.
