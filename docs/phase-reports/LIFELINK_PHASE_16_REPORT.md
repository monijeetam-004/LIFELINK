# LifeLink Phase 16 — Neo4j Graph Decision Engine & Resource Pathway Hardening

## 1. Executive summary

This phase was an audit-first hardening pass over LifeLink's existing Neo4j
graph engine and resource-pathway architecture, not a rebuild. The repository
already contained a mature, single, well-engineered graph integration — built
in Phase 12 and re-audited in Phase 0's independent audit — consisting of a
driver-lifecycle module (`app/core/graph.py`), a graph-service layer
(`app/services/graph_service.py`), a versioned `EmergencyPathway` model with
automatic disruption-triggered re-optimization
(`app/services/emergency_pathway_service.py`), and admin/emergency/hospital
routes wiring all of it into the real, live emergency flow. Section 1 of the
spec's own instruction — "first understand what already exists" — was taken
literally: nothing was rebuilt, no second graph architecture was created, and
`matching_engine.py`/`inventory_service.py` were left untouched.

The audit found the architecture to be honest and sound in almost every
dimension the spec asks about: parameterized Cypher everywhere (no injection
surface), an honest `GRAPH`/`HAVERSINE_FALLBACK` labeling discipline, a
resync endpoint that fails with a real 503 rather than faking success,
privacy-by-design graph schema (no per-donor nodes at all), and Postgres
remaining the unambiguous authority for inventory, authorization, and
verification state. Three genuine, non-trivial gaps were found and fixed:

1. **A real concurrency bug** in `emergency_pathway_service.create_pathway_version`
   — a check-then-insert race on the "one ACTIVE pathway per emergency"
   invariant that could throw an unhandled `IntegrityError` under concurrent
   pathway selection/re-optimization. Fixed with a row lock, the same pattern
   Phase 15 used for the certificate-issuance race.
2. **Unbounded `max_results` query parameters** on both resource-pathway
   endpoints, inconsistent with the codebase's own more careful
   admin-pathway-log pattern. Fixed by bounding both to 1-50.
3. **Six frontend "research/demo" pages** wired into real, public,
   unauthenticated routes, presenting entirely fabricated data with zero
   labeling distinguishing them from live production decisions. Fixed with a
   clear, visible "CONCEPTUAL ILLUSTRATION — NOT LIVE DATA" banner on each,
   without otherwise redesigning them.

One suspected gap — the graph's `CAN_DONATE_TO` relationship never being
traversed by pathway search — was investigated and correctly ruled out as
intentional, consistent architecture (see §11).

**Real Neo4j connectivity could not be verified live in this sandbox** — its
network blocks raw Bolt/TCP, confirmed directly in this session by re-running
the project's own `scripts/verify_neo4j.py` (see §39). This is an
environmental limitation, not a code defect: every hermetic test that could
be run, was run, and the honest-fallback code paths this restriction
exercises are exactly the ones the spec cares most about.

**Baseline: 586 passed, 0 failed, 0 errors. Final: 597 passed, 0 failed, 0
errors** (586 baseline + 11 new Phase 16 tests). No regression.

**Status: COMPLETE WITH DOCUMENTED LIMITATIONS** (see §50) — the graph engine
and emergency/pathway architecture are sound and trustworthy; the only
limitation is this sandbox's inability to reach live Neo4j/Aura over Bolt,
which is honestly reported rather than hidden or faked.

## 2. Source-of-truth architecture

PostgreSQL is authoritative for every piece of state that matters: users,
donors, hospitals, blood banks, hospital inventory, blood requests,
emergencies, transport routes, verification status, donations, and
certificates. Neo4j holds a **derived** resource-pathway graph rebuilt from
Postgres (`resync_all()`) plus best-effort incremental syncs
(`sync_facility`/`sync_inventory`/`sync_route`) at real mutation points. This
was not imposed from a template — it is exactly what the existing code does,
confirmed by reading every sync call site: nothing writes to Postgres from a
Neo4j read, and no frontend surface treats Neo4j as an independent authority
for anything security-sensitive. The one place Neo4j-derived data reaches a
decision is resource-pathway *discovery* (which facilities to consider, in
what order) — never allocation, authorization, or verification, all of which
stay in Postgres/the application layer (see §12-14, §22).

## 3. Neo4j connection management

`app.core.config.Settings` exposes `neo4j_uri` / `neo4j_username` /
`neo4j_password` / `neo4j_database` / `neo4j_connection_timeout_seconds`
(default 5.0s) as `str | None`, plus a `neo4j_configured` property
(`bool(uri and username and password)`) — the same "optional, honest degrade"
pattern already used for `sms_provider_configured`. Real Aura credentials are
present in `backend/.env` (previously flagged in `README.md` as unrotated —
see §42) and loaded via `pydantic_settings.BaseSettings`. No env var was
renamed. Credentials are read only from backend process env; nowhere in the
codebase are they written to a response body, a log line, or a frontend
bundle — confirmed by grep across `app/` and by the new
`test_resync_endpoint_never_exposes_cypher_or_credentials_on_failure` test,
which asserts the failure body contains none of `neo4j+s://`, `bolt://`,
`password`, or `NEO4J_PASSWORD`.

## 4. Driver lifecycle

`app/core/graph.py` implements a thread-locked, lazy, process-wide singleton
`Driver` via `GraphDatabase.driver()` — not a per-request driver. Every Cypher
call goes through the single `graph_session()` context manager, which is the
one chokepoint that catches `ServiceUnavailable` / `Neo4jError` / `DriverError`
and re-raises as the module's own `GraphUnavailableError`, so no raw driver
exception or session leak ever reaches a route handler. `close_driver()`
exists for clean shutdown/test-teardown, and `graph_health()` never raises —
it always returns a `{configured, connected, status, reason?}` shape. This
was already correct; no changes were made to `graph.py` in this phase.

## 5. Graph schema (as it actually exists)

Documented in `graph_service.py`'s own module docstring and confirmed
accurate by reading every Cypher statement in the file:

- `(:Facility {id, name, facility_type, city, latitude, longitude, verified})`
  — source: `HospitalProfile`; derived/mirrored, Postgres authoritative.
- `(:BloodType {code})` — a fixed, static reference node per blood type.
- `(:City {name})` — derived from facility city strings.
- `(:BloodType)-[:CAN_DONATE_TO]->(:BloodType)` — mirrors the authoritative
  `can_donate_to()` compatibility matrix; present but not currently queried
  by pathway search (§11).
- `(:Facility)-[:HAS_STOCK {units}]->(:BloodType)` — derived from
  `HospitalInventory`; Postgres remains the authoritative ledger (§14).
- `(:Facility)-[:ROUTE {route_id, distance_km, transit_minutes, status,
  disrupted_reason}]->(:Facility)` — derived from `TransportRoute`; directed
  on creation, matched undirected for traversal.
- `(:Facility)-[:LOCATED_IN]->(:City)`.
- `(:City)-[:HAS_AVAILABLE_DONORS {count}]->(:BloodType)` — a deliberate,
  privacy-preserving **aggregate-only** edge; there are no per-donor nodes
  anywhere in the schema (see §23).

No invented node labels or relationships were added this phase.

## 6. Constraints & indexes

`ensure_constraints()` (idempotent, safe to call repeatedly, also re-run
inside `resync_all()`) already declares: `facility_id_unique`
(`Facility.id`), `blood_type_code_unique` (`BloodType.code`),
`city_name_unique` (`City.name`), `facility_city_idx` (`Facility.city`),
`route_id_idx` (`ROUTE.route_id`), `route_status_idx` (`ROUTE.status`). Every
identifier the sync/traversal paths actually filter or match on is covered.
No missing constraint/index was found; none were added.

## 7. Data synchronization

Postgres changes reach Neo4j through exactly one mechanism: explicit,
best-effort calls at real mutation points — `sync_facility()` on
hospital-profile create/update, `sync_inventory()` on inventory update,
`sync_route()` on transport-route create/disrupt/recover — plus a full
wipe-and-rebuild (`resync_all()`) for the explicit admin resync operation.
There is no second, competing sync mechanism (no background job, no Celery
task, no ORM event hook duplicating this). The three incremental sync
functions never raise (they return `True`/`False`), so a Neo4j hiccup never
rolls back or fails the primary Postgres write — confirmed by
`test_graph_service.py::test_sync_facility_returns_false` /
`test_sync_inventory_returns_false` / `test_sync_route_returns_false`.

## 8. Resync endpoint

`POST /admin/graph/resync` requires admin auth (`_admin` dependency, 403 for
every other role — `test_graph_resync_forbidden_for_non_admin`), never
returns or logs credentials, and — critically — **raises a real 503** via
`GraphUnavailableError` when Neo4j is unreachable rather than reporting a
fabricated success (`test_graph_resync_reports_failure_honestly_when_neo4j_unreachable`,
re-confirmed this phase with the added credential-leak check in
`test_phase16_graph_engine_security.py`). Success and failure are both
audited (`admin.graph_resync_succeeded` / `admin.graph_resync_failed`).

## 9. Graph↔Postgres consistency

Consistency is enforced structurally rather than checked by a separate
reconciliation job: every pathway read goes through
`find_resource_pathways()`, which either queries live Neo4j or explicitly
falls back to Postgres-only haversine ranking — there is no third path where
a stale graph silently answers as if it were current. A genuinely stale
graph (e.g. after a Postgres write with a failed incremental sync) is
resolved by the admin resync operation, which does a full wipe + rebuild
(never a partial merge that could leave orphaned nodes). The degraded/stale
scenario is exactly the fallback scenario already tested end-to-end in
`test_graph_api.py` (Neo4j is genuinely unreachable in this sandbox, so every
existing pathway test already exercises the real degradation path, not a
mock).

## 10. Emergency resource-pathway flow (full audit)

The full flow — emergency created → blood type known → candidate resources
searched via `find_resource_pathways()` → distance/hops/disruption-filtered
candidates ranked → best pathway persisted as `EmergencyPathway` v1 → later
re-optimized on disruption or manual reselection — was traced end to end
through `emergencies.py`, `emergency_pathway_service.py`, and
`graph_service.py`. Authorization is enforced before any graph-derived data
is returned: `_can_view()` gates whether the caller may see the emergency at
all, and a second, separate check requires the hospital caller to own
`origin_facility_id` (or be admin) before the pathway search runs at all —
the graph engine never bypasses this. Blood compatibility, facility
verification-adjacent state, and transport availability are all checked
before a candidate can appear (§11-15).

## 11. Blood compatibility

There is exactly one authoritative compatibility implementation,
`app/core/blood_compatibility.py::can_donate_to()` (a Phase 9 requirement),
consumed consistently by `graph_service.resync_all()` (building the
`CAN_DONATE_TO` graph edges), `emergencies.py` and `blood_requests.py`
(donor-eligibility screening). Investigated in depth: does the pathway
search ever substitute a different blood type for a patient's actual need?
No — `_PATHWAY_QUERY` matches facility stock by **exact** blood-type code
only (`HAS_STOCK {units}]->(:BloodType {code: $blood_type})`), confirmed by
`grep -n "CAN_DONATE_TO" app/services/graph_service.py` showing the
relationship is created in `resync_all()` but never referenced in any
`MATCH` clause. This is **intentional, correct, pre-existing architecture**,
not a defect: `Emergency.blood_type` / `BloodRequest.blood_type` represents
the exact unit type a patient needs, and hospital-inventory
search/reservation/allocation always operates on that exact type;
`can_donate_to()` is used elsewhere only to screen *donor* eligibility to
give blood for a request, never to let the graph substitute a different
held-stock type "because it's compatible and closer." An incompatible
resource genuinely cannot be selected by proximity alone, because
compatibility isn't the axis the pathway query varies on at all — it's fixed
to the emergency's exact required type from the start.

## 12. Donor resource integration

The graph schema deliberately has **no per-donor nodes** — donor presence is
represented only as an aggregate `(:City)-[:HAS_AVAILABLE_DONORS
{count}]->(:BloodType)` edge (§23). Donor selection for an emergency/blood
request is not touched by this phase's graph work at all — it continues to
run entirely through the pre-existing, unmodified `matching_engine.py`,
which independently enforces compatibility, eligibility, availability,
verification, and location. The boundary is clean: the graph engine answers
"which *facilities* can supply blood and how to reach them"; `matching_engine.py`
answers "which *donors* should be asked." Neither duplicates the other.

## 13. Hospital / blood-bank resources

Facility identity, verification state, and location all come from the same
`HospitalProfile` row Postgres already treats as authoritative — the graph's
`Facility` node is a mirror, not an independent source. Nothing in
`_PATHWAY_QUERY` or the route handlers grants a facility eligibility merely
for appearing in Neo4j; an ineligible-under-backend-rules facility (e.g.
unverified, where that matters elsewhere in the app) is not specially
privileged by the graph layer, because the graph layer never performs
authorization — only discovery.

## 14. Inventory integration (Phase 12's transactional layer, untouched)

Neo4j's `HAS_STOCK` edge is a **derived, informational mirror only**.
Postgres (`inventory_service.py`, with its existing row-locking via
`with_for_update()`) remains the sole authority for available quantity,
reservation, allocation, release, and expiry — nothing in this phase changed
that. The new `test_selecting_a_pathway_never_reserves_or_alters_inventory`
test proves this directly: selecting (or repeatedly re-selecting) a resource
pathway three times in a row for the same emergency never changes
`HospitalInventory.units` at all — only an explicit hospital "accept" (a
separate, pre-existing code path) reserves inventory. Graph pathway
selection cannot cause allocation exceeding actual Postgres inventory,
because it never allocates anything.

## 15. Transport graph

Transport nodes/routes/distance/transit-time/status/disruption/recovery are
modeled as `(:Facility)-[:ROUTE {...}]->(:Facility)`, kept in sync via
`sync_route()` at create/disrupt/recover. `_PATHWAY_QUERY` filters
`ALL(r IN relationships(p) WHERE r.status = 'OPERATIONAL')`, so a disrupted
edge is structurally excluded from every graph-sourced pathway result — a
disrupted route can never appear as usable. The haversine fallback
independently excludes a destination whose direct Postgres `TransportRoute`
is `DISRUPTED` via `_direct_route_disrupted()`, so the same guarantee holds
even when Neo4j itself is unreachable.

## 16. Disruption → re-optimization (deep audit)

This is the architecture's centerpiece and it works exactly as the spec
demands, already, pre-Phase-16: `admin.py::disrupt_transport_route` flips a
route to `DISRUPTED`, computes a before/after impact probe, and calls
`emergency_pathway_service.handle_route_disrupted()`, which finds every
still-open emergency whose *current* ACTIVE pathway actually used that
route's origin or destination facility, and re-optimizes each one for real —
by calling the unmodified `find_resource_pathways()` again, never fabricating
an alternative. Both `transport_disrupted` and `pathway_reoptimized`
`EmergencyEvent` audit rows are written, and a real
`notification_service.notify(..., type="emergency.pathway_reoptimized", ...)`
fires. `still_reachable=False` is reported honestly when no alternative
exists (`test_transport_disruption_reoptimizes_affected_emergency_pathway`,
pre-existing Phase 12 test, re-verified in this phase's regression run). The
full sequence — pathway A active on T1 → T1 disrupted → impact measured →
alternative discovered (or honestly not) → new version active → patient
notified — is exercised end to end by
`test_full_scenario_create_through_disruption_to_resolution` in
`test_phase12_emergency_sos_engine.py`, which this phase re-ran clean.

## 17. Pathway versioning

`EmergencyPathway` carries `version`, `status`
(`EmergencyPathwayStatus.ACTIVE`/`SUPERSEDED`), `source`, `selected_facility_id`
(nullable — an honest "no viable pathway" outcome), `distance_km`,
`transit_minutes`, `hops`, and `superseded_at`/`superseded_reason`. A partial
unique index, `uq_emergency_pathway_one_active`
(`postgresql_where="status = 'ACTIVE'"`), enforces at most one ACTIVE row per
emergency at the database level — not just in application logic.
Re-optimization always creates a new version rather than overwriting; every
historical version remains inspectable via `GET /emergencies/{id}` (its
`pathway_history` field), subject to the same `_can_view()` authorization as
everything else about that emergency.

**A real concurrency bug was found here and fixed.** Before this phase,
`create_pathway_version` read the current ACTIVE pathway and inserted a new
ACTIVE row with no row lock. Two near-simultaneous callers for the same
emergency (a hospital's manual re-selection racing an admin's
disruption-triggered re-optimization, or simply a double-click) could both
pass the read, both insert a new ACTIVE row, and the loser would hit
`uq_emergency_pathway_one_active` as an unhandled `IntegrityError` — the same
class of bug Phase 15 found and fixed for certificate issuance. Fixed by
locking the `Emergency` row (`db.get(Emergency, emergency.id,
with_for_update=True)`) at the top of `create_pathway_version`, serializing
concurrent callers so the second one correctly sees and supersedes the
first's result rather than colliding with it. Proven by the new
`test_concurrent_pathway_selection_for_same_emergency_yields_no_active_collision`
test (two threads, a `threading.Barrier`, both must return 200 and the
emergency must end with exactly one ACTIVE version) — this test would have
intermittently 500'd before the fix.

## 18. Pathway ranking

The actual, existing algorithm: candidates are ranked purely by minimum
total `distance_km` along a path that (a) has stock of the exact required
blood type at the destination, and (b) contains no `DISRUPTED` edge,
within a bounded hop count (§19). There is no separate "strategy" selector,
no urgency/deadline weighting inside the ranking query itself, and no
reliability/history-based scoring — this phase did not invent a new scoring
function, because the spec explicitly forbids inventing one "unless
genuinely required," and none of the audited gaps required it. Slack/deadline
(§35) is surfaced separately, alongside the ranked pathway, not blended into
its score.

## 19. Dijkstra / shortest path

Neo4j Community/Aura Free ships no APOC/GDS plugin, so there is no
`gds.shortestPath`/`apoc.algo.dijkstra` call anywhere in this codebase — and
the code is honest about this (`graph_service.py`'s own docstring calls it a
"deliberate Community/Aura-Free-compatible alternative"). The actual
algorithm is a bounded-depth (`<=6` hops, `_MAX_PATH_HOPS`) variable-length
Cypher path enumeration, picking the minimum-total-`distance_km` path per
destination via an `ORDER BY ... collect(...)[0]` pattern. This is a real,
deterministic minimum-cost search within the hop bound — not a general,
unbounded Dijkstra, and this report does not claim it is one. Existing tests
(`test_graph_service.py`, `test_graph_service_logic.py`) already cover
simple/multiple-candidate/no-path/disconnected/disrupted-edge scenarios via
mocked Neo4j results; no infinite-loop or invalid-cost behavior was found,
and none is possible given the fixed hop bound and `LIMIT` clause.

## 20. Haversine fallback

Every pathway result carries an explicit `GraphPathwaySource.GRAPH` or
`.HAVERSINE_FALLBACK` label — `find_resource_pathways()` tries the graph
first (only when `graph_enabled()`), catches `GraphUnavailableError`, and
falls back to `_haversine_fallback()`, which independently re-applies the
disrupted-route exclusion (§15) and the exact-blood-type filter (§11) so
security/filtering guarantees hold identically in both modes. The frontend
`ResourcePathwayOut`/`EmergencyPathwayOut` schemas expose `pathway_source`
and `graph_available` explicitly — nothing displays a fallback result as
graph-derived. This is, in fact, the *only* mode this sandbox can exercise
live (§39), and every existing pathway test already runs through it for
real, not as a simulation.

## 21. Failure modes

`graph_session()` is the single chokepoint catching
`ServiceUnavailable`/`Neo4jError`/`DriverError` and re-raising as
`GraphUnavailableError` — confirmed to cover connection-refused,
auth-failure, and query-timeout shaped errors uniformly (they all arrive as
driver exceptions of these types). `find_resource_pathways()` and every
incremental sync function catch this and degrade rather than 500;
`resync_all()` is the one deliberate exception, surfacing as a real 503. No
code path catches an exception and reports success anyway — confirmed by
reading every `except` clause touching `GraphUnavailableError` in the graph
call chain.

## 22. Graph security (authorization)

Every graph-derived response passes through the same application-layer
authorization as any other resource in this codebase before being returned:
`_can_view()` for emergency visibility, explicit facility-ownership checks
for hospital callers, admin-role checks for admin-only operations. Neo4j
itself has no concept of "who is allowed to see this" — the application
never assumes graph visibility equals authorization. This phase's new IDOR
sweep (`test_unrelated_patient_cannot_view_another_patients_emergency_pathways`,
`test_family_without_accepted_link_cannot_view_emergency_pathways`) adds the
patient/family angle to the hospital/donor/admin coverage
`test_graph_api.py` already had; both new tests pass (404, matching the
existing "don't reveal existence to a non-viewer" pattern used everywhere
else in this API).

## 23. Public data privacy

The graph schema structurally cannot expose donor phone/email/address/
medical info, because there are no per-donor nodes at all — only an
aggregate city+blood-type donor count. `ResourcePathwayOut`/`PathwayCandidateOut`
expose only `facility_id`, `facility_name`, `city`, `units`, `distance_km`,
`transit_minutes`, `hops` — confirmed field-by-field by the new
`test_resource_pathway_response_has_no_pii_or_graph_internals` test, which
also asserts none of the response's keys match a Neo4j-internal-id or
credential shape. No Neo4j internal element IDs are ever serialized into any
API response — every query returns application-defined properties only.

## 24. Graph query safety (Cypher injection)

Every Cypher statement in `graph_service.py` uses parameterized queries
(`$blood_type`, `$origin_facility_id`, `$max_results`, etc.) — no
string-concatenated user input was found anywhere in the file. No endpoint
accepts arbitrary Cypher from a client. This phase adds a concrete
API-boundary test proving the practical consequence: FastAPI/Pydantic type
validation (`BloodType` as a closed enum, `origin_facility_id`/`emergency_id`
as UUID-typed params) rejects a Cypher-injection-shaped string
(`"O_NEG'}) DETACH DELETE n //"`, `"'; MATCH (n) DETACH DELETE n; --"`) with a
plain 422 *before* it can ever reach a Cypher parameter
(`test_malicious_blood_type_and_facility_id_are_rejected_before_reaching_graph_layer`).

## 25. Graph API contract

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/hospitals/me/resource-pathways` | HOSPITAL | `blood_type` required, `max_results` 1-50 (Phase 16 fix) |
| GET | `/emergencies/{id}/resource-pathways` | owning HOSPITAL or ADMIN, gated by `_can_view` | `origin_facility_id` required, `max_results` 1-50 (Phase 16 fix) |
| POST | `/emergencies/{id}/pathway` | owning HOSPITAL or ADMIN | creates/re-selects a version; row-locked (Phase 16 fix) |
| GET | `/admin/transport-routes` | ADMIN | list |
| POST | `/admin/transport-routes` | ADMIN | auto-computes distance/transit estimate if omitted |
| POST | `/admin/transport-routes/{id}/disrupt` | ADMIN | triggers re-optimization (§16) |
| POST | `/admin/transport-routes/{id}/recover` | ADMIN | never re-optimizes (documented, intentional) |
| POST | `/admin/graph/resync` | ADMIN | 503 honestly on failure |
| GET | `/admin/graph/pathway-log` | ADMIN | bounded `limit` 1-200 |

No endpoint exposes raw Neo4j implementation detail (element IDs, Cypher
text, driver internals) to any caller, admin included.

## 26. Frontend `GraphDecisionEngine.tsx`

Confirmed (full-file read) to be a verbatim, 100% hardcoded Google-Stitch
export with zero backend integration, wired into a real public,
unauthenticated route (`/graph-engine`). No fabricated result was "fixed" in
place, because doing so would mean building a second, competing graph UI
against a page explicitly marked "do not redesign" in its own header
comment — instead, per the spec's own allowance ("if intentionally a
research/demo page, preserve it, but clearly distinguish..."), a fixed,
high-contrast **"CONCEPTUAL ILLUSTRATION — NOT LIVE DATA"** banner was added
as the first rendered element, with no other markup touched.

## 27. Decision transparency (`DecisionTransparency.tsx`)

Same finding and same fix as §26 — this file is also a verbatim Stitch
export with zero backend wiring, now carrying the same disclaimer banner.
The **real** decision-transparency data users actually need (why a pathway
was selected: compatibility — implicit via exact-type matching, §11 —
distance, transit time, hops, disruption exclusion, pathway version, and
fallback status) is already honestly exposed through
`ResourcePathwayOut`/`EmergencyPathwayOut` on the real pathway endpoints
(§25); this static page was never the mechanism for that and is now clearly
labeled as such.

## 28. Mathematical model (`MathematicalModel.tsx`)

Same finding, same fix. The page's set-theory/pipeline framing (`U -> C -> A
-> E -> L`) does not correspond to any backend module or function in this
codebase — it is a conceptual explainer, not a description of executed code,
and is now labeled that way rather than left to be mistaken for one.

## 29. Research Mode (`ResearchMode.tsx`)

Same finding, same fix — and the most egregious of the six: fabricated
telemetry ("14,209" nodes scanned, "0.9998" accuracy, "0.042λ" drift) and
out-of-domain organ-transplant/HLA-mismatch language with nothing to do with
blood donation. No real `/research/compare` backend endpoint exists in this
codebase (confirmed by route/grep search); this page never called one. It is
not wired to production emergency state and cannot affect it, since it makes
no API calls at all — now clearly labeled as conceptual.

Two additional pages were found during this phase's frontend sweep that the
spec's named list didn't call out by name but which are the same shape:
**`SimulationMode.tsx`** (`/simulation`) and **`DonorRecommendation.tsx`**
(`/recommendation`) — both verbatim Stitch exports, zero API calls, wired
into real public routes, no prior disclaimer. Both received the identical
banner fix for consistency (§40).

## 30. Graph health

`GET /admin/system/health`'s `graph` field is populated directly from
`graph_health()` and distinguishes `disabled` (not configured — a normal,
expected state) from `unreachable` (configured but a real connection
failure) from `ok`. `configured=False` deliberately does not flip overall
system status to `degraded`; `connected=False` while `configured=True` does.
The admin frontend (`AdminOverview.tsx`) already renders this honestly —
neutral color for `disabled`, error color otherwise — confirmed by reading
its render logic directly; no fake "healthy" state was found anywhere. A new
test, `test_admin_health_never_reports_graph_ok_when_configured_but_unreachable`,
locks this behavior in at the API level with a monkeypatched unreachable
state.

## 31. Resync correctness / idempotency

`resync_all()` does a full `MATCH (n) DETACH DELETE n` wipe followed by a
complete rebuild from current Postgres state, using `MERGE` (not `CREATE`)
for every node and relationship — repeated resyncs cannot duplicate nodes,
because a wipe-then-`MERGE`-rebuild is idempotent by construction: the graph
after resync #2 is identical to the graph after resync #1, given unchanged
Postgres state. This is structurally different from (and safer than) an
incremental merge-without-wipe strategy, which is why duplicate-detection
concerns (§32) are mostly moot for the full resync path — they matter for
the incremental `sync_*` functions instead, which is where §32 focuses.

## 32. Graph duplicates (incremental sync)

`sync_facility()`/`sync_inventory()`/`sync_route()` all use `MERGE` on the
appropriate unique key (`Facility.id`, the stock relationship's facility+
blood-type pair, `ROUTE.route_id`) — confirmed by reading
`_merge_facility_tx`/`_merge_stock_tx`/`_merge_route_tx` in
`graph_service.py`, and directly tested by
`test_graph_service_logic.py::test_merge_facility_tx_sends_expected_params`
et al. Repeated sync of the same entity cannot create a duplicate node,
because `MERGE` matches on the declared unique constraint (§6) rather than
blindly inserting. No `CREATE` was found or introduced in any sync path.

## 33. Transaction boundaries

Postgres and Neo4j are never in one atomic transaction, and the code does
not pretend otherwise. The incremental sync functions are called *after* the
Postgres write has already succeeded (and, for facility/inventory/route
mutations, only as a best-effort side effect that cannot roll back the
Postgres commit) — so the documented, honest recovery story is: Postgres is
always right; a failed incremental sync leaves Neo4j stale until the next
successful incremental sync or the next full admin resync. No distributed
ACID claim exists anywhere in the code or its comments.

## 34. Performance

`_PATHWAY_QUERY` is bounded to `<=6` hops (`_MAX_PATH_HOPS`) and always
carries a `LIMIT $max_results` — this phase's fix (§ below, "unbounded
`max_results`") closes the one place a user-controlled input could have
inflated that limit or produced a negative/invalid parameter. With
`facility_city_idx`/`route_id_idx`/`route_status_idx` in place (§6), the
traversal is index-assisted rather than a full scan. No N+1 pattern was
found — each pathway request issues exactly one Cypher query (or one
haversine computation) per call, not one per candidate.

## 35. Pathway deadline / slack

`Emergency.slack_seconds` (`app/api/routes/emergencies.py::_slack_seconds()`)
computes `(deadline - utcnow()).total_seconds()` on every read and is
**never clamped to zero** — a negative value (an overdue emergency) is
returned honestly, confirmed by
`test_slack_is_negative_for_a_past_deadline_and_never_clamped` (pre-existing,
re-verified this phase). No automatic escalation was invented — the spec
explicitly forbids inventing one without existing support, and none was
found.

## 36. Inventory + graph race conditions

This is structurally impossible to trigger as a *graph* race, because the
graph is never consulted for allocation at all (§14) — inventory races are
purely a Postgres-locking concern, already solved by Phase 10/12's
`with_for_update()` row locking in `inventory_service.py`, unmodified this
phase. `test_selecting_a_pathway_never_reserves_or_alters_inventory` proves
the graph/pathway layer's non-involvement directly: three pathway selections
in a row leave `HospitalInventory.units` completely untouched.

## 37. Transport disruption race conditions

`handle_route_disrupted()` iterates open emergencies whose ACTIVE pathway
touches the disrupted route's facilities and re-optimizes each — but, as
noted in §17, the *pathway-creation* side of this had no row lock before
this phase's fix. That fix (Emergency-row locking in
`create_pathway_version`) directly closes this race too: an admin's
disruption-triggered re-optimization and a hospital's concurrent manual
reselection for the same emergency now serialize correctly instead of racing
to insert two ACTIVE rows. The final persisted state is always internally
consistent — exactly one ACTIVE pathway, with the DB-level partial unique
index as a second line of defense even if application logic ever regressed.

## 38. Test suite

`tests/test_phase16_graph_engine_security.py` (new, 11 tests, all passing):
concurrency (1), input-validation bounds (2), Cypher-injection-shaped input
rejection (2, covering both pathway endpoints and the resync-failure body),
IDOR (2), privacy (1), inventory boundary (1), fallback honesty (1), graph
health (1). This deliberately does not re-test what
`test_graph_api.py`/`test_graph_engine.py`/`test_graph_service.py`/
`test_graph_service_logic.py`/`test_phase12_emergency_sos_engine.py` already
covered thoroughly (see each file's own module docstring for what it
covers) — Phase 16's file exists to close the specific gaps this phase's
audit found, not to pad a test count.

## 39. Real Neo4j test policy

**REAL NEO4J TESTS: not run / blocked (environmental network restriction).**
This cloud sandbox blocks raw Bolt/TCP protocol. Confirmed directly in this
session by running `PYTHONPATH=. python3 scripts/verify_neo4j.py` from the
backend directory: `[ok] Neo4j configured...` followed by `[FAIL]
verify_connectivity(): ServiceUnavailable`, exit code 1. This is consistent
with a prior phase's independent `LIFELINK_ENV_NEO4J_VERIFICATION_REPORT.md`,
which documents the identical limitation. No test in this phase claims live
Neo4j verification; every test is either a real end-to-end HTTP-API test
that legitimately exercises the honest haversine-fallback path (since that
*is* what real, unreachable Neo4j produces here), or an explicit
monkeypatch-based simulation clearly labeled as such in its own docstring.
This is not treated as a code failure, per the spec's own instruction, since
the code's behavior under this exact condition (honest 503/fallback,
credential-safe errors) is precisely what's being verified.

## 40. No fake graph data

Searched the full frontend and backend for hardcoded nodes/edges, fake graph
responses, static pathway arrays, placeholder distances, fake optimization
scores, fake transport states, fake graph health, and mock emergency
pathways. Every production code path (`graph_service.py`, the admin/
emergency/hospital routes, `AdminOverview.tsx`, `hospital/ResourcePathways.tsx`,
`admin/TransportRoutes.tsx`) uses real data end to end. Six frontend pages
were found to be legitimate, pre-existing, explicitly-marked research/demo
pages with fabricated display data and no backend calls at all:
`GraphDecisionEngine.tsx`, `DecisionTransparency.tsx`, `MathematicalModel.tsx`,
`ResearchMode.tsx`, `SimulationMode.tsx`, `DonorRecommendation.tsx` — all six
now carry a clear, visible disclaimer banner (§26-29) rather than being
removed, honoring their own "do not redesign" header comments while closing
the labeling gap the spec calls out. No test fixture or research-simulation
data was altered — those are legitimate and out of scope for this fix.

## 41. Frontend tests

`npx tsc --noEmit`: clean, zero errors. `npm run lint` (ESLint): clean, zero
warnings or errors. `npm run build`: succeeds (`vite build`, 2166 modules
transformed, ~6s). Playwright is not present in this repository (no
`playwright.config.*`, no `.spec.ts` files, no `playwright` devDependency) —
consistent with every prior phase's finding; there is no E2E suite to run.

## 42. Documentation consistency

Searched every top-level `.md` doc for claims about Neo4j/graph
engine/Dijkstra/matching/optimization/fallback/emergency pathways. Found one
genuinely stale, materially misleading claim: `README.md`'s status table
listed "Neo4j graph engine, pathway versioning, transport-failure resilience
simulation, `/research/compare`" as **"Not built"**, and an entire "What this
build explicitly did not do" section stated the graph engine "was never
wired to a real backend graph system, in this build or before it." Both
statements were written before Phase 12 built the graph engine for real, and
were never updated afterward — a textbook instance of exactly the stale
"graph engine not implemented" claim the spec asks to find. **Fixed**: added
a clear doc-staleness note pointing to the current, accurate
`LIFELINK_PHASE_0_AUDIT.md` §7 and this report, and corrected the specific
table row and section header inline (struck through, not deleted, to
preserve the historical record) rather than rewriting the whole README. No
other doc (`docs/`, other phase reports) needed changes — `docs/` is already
explicitly flagged by the README itself as historical/superseded, and every
other phase report's graph-related claims were checked against the current
code and found accurate.

## 43. Security non-negotiables — compliance checklist

| Non-negotiable | Status |
|---|---|
| Expose Neo4j credentials | Never found; verified by test |
| Expose raw Cypher errors | Never found — `GraphUnavailableError` sanitizes |
| Accept arbitrary Cypher | Never found — fully parameterized |
| Trust graph data for authorization | Never found — app-layer auth gates every response |
| Bypass Postgres transactional inventory | Never found; verified by new test |
| Expose donor private info | Structurally impossible — no per-donor nodes |
| Allow graph IDOR | Closed — new patient/family tests added |
| Return fake graph health | Never found; verified by test |
| Claim graph success when unavailable | Never found — honest 503/fallback everywhere |
| Fabricate pathways/scores | Never found in production paths |
| Silently switch to fallback | Never found — `pathway_source` always labeled |
| Create duplicate graph/matching/inventory architecture | Not done this phase |
| Bypass existing authorization/verification | Not done this phase |
| Let frontend decide authorization | Never found |
| Weaken security tests | Not done — only additions |

## 44. Architectural non-negotiables — preserved

Existing Postgres architecture, `matching_engine.py`, `inventory_service.py`,
the emergency lifecycle, `EmergencyPathway`, `graph.py`/`graph_service.py`,
the audit service, the notification service, Phase 13 family authorization,
Phase 14 verification authorization, and Phase 15 certificate architecture
were all read and confirmed unmodified except where explicitly noted in
§17/§25 (the row-lock fix and the `max_results` bound fix — both narrow,
additive, and justified by a real bug/gap found during audit, not a
redesign).

## 45. Future improvements

- Move pathway ranking from the bounded 6-hop plain-Cypher enumeration to
  `gds.shortestPath`/`apoc.algo.dijkstra` if the facility network grows
  meaningfully beyond today's scale (documented, non-urgent — carried over
  from the Phase 0 audit's own recommendation, still accurate).
- Rotate the real Neo4j Aura credential sitting in `backend/.env`, flagged
  as unrotated since before this phase (§3, §42) — an operational task, not
  a code change.
- Consider a lightweight periodic reconciliation check (compare a sample of
  Postgres facility/route state against the graph) to detect silent
  incremental-sync drift between admin resyncs, rather than relying solely
  on an admin remembering to resync.
- If a genuine live-Neo4j CI environment becomes available, add a
  non-hermetic test tier that runs against real Aura, gated behind an env
  flag, to complement (not replace) the hermetic suite this phase relied on.

---

## 46. Final realistic scenario (verification, not simulation)

The complete scenario the spec asks for — patient creates an emergency →
blood requirement → compatible resources discovered → hospital pathway
selected via the graph engine → transport disrupted → impact measured →
original pathway superseded → alternative selected → new version active →
patient notified → transport recovered → system consistent — is not a new
build for this phase; it already exists as a real, passing, end-to-end test
against the live Postgres test database:
`test_phase12_emergency_sos_engine.py::test_full_scenario_create_through_disruption_to_resolution`.
This phase re-ran it (along with the entire suite) and confirms it still
passes with zero modification needed. No step in that scenario is faked or
simulated; every step is a real HTTP call against the real API. The one
piece of that scenario this sandbox genuinely cannot verify live is a real
Neo4j round-trip during pathway discovery — the scenario instead (honestly)
exercises the haversine-fallback path, which is the actual, current behavior
of this deployment in this environment, not a workaround invented for this
report.

## 47. Commands executed (representative)

```
# Infra
sudo -u postgres pg_ctlcluster 16 main start
redis-server --daemonize yes

# Neo4j live-connectivity re-verification (environmental limitation confirmed)
cd backend && PYTHONPATH=. python3 scripts/verify_neo4j.py   # -> ServiceUnavailable, exit 1

# Focused graph test re-run after max_results fix
pytest -q tests/test_graph_api.py tests/test_graph_service.py \
  tests/test_graph_engine.py tests/test_graph_service_logic.py   # 47 passed

# New Phase 16 test file
pytest --tb=short -p no:warnings tests/test_phase16_graph_engine_security.py   # 11 passed

# Full regression (baseline)
pytest --tb=short -p no:warnings   # 586 passed (before new file existed)

# Full regression (final)
pytest --tb=short -p no:warnings   # 597 passed, 0 failed, 0 errors

# Lint / types
ruff check app/services/emergency_pathway_service.py app/api/routes/emergencies.py \
  app/api/routes/hospitals.py tests/test_phase16_graph_engine_security.py   # clean
mypy app/services/emergency_pathway_service.py app/api/routes/hospitals.py \
  app/api/routes/emergencies.py   # no issues

# Frontend
npx tsc --noEmit     # clean
npm run lint         # clean
npm run build        # succeeds
```

## 48. Migration policy

No Postgres migration was created or needed this phase. `uq_emergency_pathway_one_active`
and every `EmergencyPathway` column already existed from Phase 12. Neo4j
constraints/indexes (§6) are managed entirely by `ensure_constraints()`, not
Alembic, and were already correct — no changes were made there either.

---

# 49. Final regression

**BASELINE (frozen, start of Phase 16): 586 passed, 0 failed, 0 errors.**
**NEW TESTS (Phase 16): 11 passed, 0 failed, 0 errors.**
**FINAL (full suite, after all Phase 16 changes): 597 passed, 0 failed, 0 errors.**
**FAILURES: 0. ERRORS: 0. No regression against the frozen baseline.**

# 50. Final status report

```
STATUS: COMPLETE WITH DOCUMENTED LIMITATIONS

BASELINE: 586 passed, 0 failed, 0 errors
FINAL: 597 passed, 0 failed, 0 errors
NEW TESTS: 11 (tests/test_phase16_graph_engine_security.py)
FAILURES: 0
ERRORS: 0

GRAPH ARCHITECTURE: single, pre-existing (Phase 12), no second architecture created
SOURCE OF TRUTH: PostgreSQL authoritative for all entities; Neo4j derived-only
NEO4J: configured (real Aura credentials in backend/.env); unreachable from this
  sandbox (Bolt/TCP blocked) -- confirmed via scripts/verify_neo4j.py
GRAPH SCHEMA: Facility/BloodType/City nodes; CAN_DONATE_TO/HAS_STOCK/ROUTE/
  LOCATED_IN/HAS_AVAILABLE_DONORS relationships; documented in §5, unchanged
CONSTRAINTS: 3 uniqueness constraints, all present and correct (§6)
INDEXES: 3 indexes (facility_city, route_id, route_status), all present (§6)
SYNCHRONIZATION: best-effort incremental sync at mutation points + full
  wipe-rebuild resync; single mechanism, no duplication (§7)
RESYNC: admin-only, honest 503 on failure, never fakes success (§8, §31)
GRAPH HEALTH: HEALTHY("ok")/DEGRADED("unreachable")/DISABLED honestly
  distinguished; never reports "ok" when unreachable (§30)
EMERGENCY PATHWAYS: full audit trail from creation to versioned selection to
  disruption-triggered re-optimization, real and working (§10, §16-17)
COMPATIBILITY: single authoritative engine (blood_compatibility.py); exact-type
  pathway matching confirmed intentional, not a gap (§11)
INVENTORY BOUNDARY: Neo4j never allocation-authoritative; verified by new
  concurrency-safe test (§14, §36)
TRANSPORT: disruption structurally excluded from both graph and fallback
  pathway results (§15)
DISRUPTION: triggers real re-optimization via handle_route_disrupted, audited
  and notified (§16)
RE-OPTIMIZATION: never fabricated -- always re-runs find_resource_pathways (§16)
PATHWAY VERSIONING: DB-level partial unique index enforces one ACTIVE row;
  concurrency race found and fixed this phase (§17, §37)
RANKING: minimum-distance path within a 6-hop bound; documented honestly,
  no invented scoring function (§18)
DIJKSTRA: not a general Dijkstra -- a bounded-hop minimum-distance Cypher
  enumeration, honestly documented as such, not claimed otherwise (§19)
HAVERSINE FALLBACK: always explicitly labeled (GraphPathwaySource), same
  security/filtering guarantees as the graph path (§20)
AUTHORIZATION: enforced at the application layer before any graph-derived
  data is returned; graph never bypasses it (§22)
IDOR: closed -- existing hospital/donor/admin coverage plus new
  patient/family tests this phase (§22, §38)
PRIVACY: no per-donor nodes; response schemas carry no PII/Neo4j internals,
  verified by new test (§23, §38)
CYPHER SECURITY: fully parameterized; malicious input rejected by schema
  validation before reaching Cypher, verified by new test (§24, §38)
CONCURRENCY: one real race found (pathway versioning) and fixed with row
  locking; inventory race structurally impossible via the graph (§17, §36-37)
PERFORMANCE: bounded hop count + LIMIT + indexes; max_results now bounded
  1-50 (real gap found and fixed this phase) (§34)
FRONTEND: 6 static research/demo pages found wired into public routes with
  fabricated data and no prior disclaimer; all 6 now carry a clear
  "CONCEPTUAL ILLUSTRATION -- NOT LIVE DATA" banner (§26-29, §40)
DECISION TRANSPARENCY: real factors already exposed via the live pathway
  endpoints; the static demo page is now clearly labeled as non-live (§27)
MATHEMATICAL MODEL: confirmed conceptual, not a description of executed
  code; now labeled as such (§28)
RESEARCH MODE: confirmed 100% fabricated telemetry with zero backend wiring;
  now labeled as such; does not affect production emergency state (§29)
REAL NEO4J TEST: not run / blocked (environmental network restriction,
  confirmed directly this session -- see §39)
DATABASE MIGRATIONS: none created; none required
TYPESCRIPT: clean (tsc --noEmit)
ESLINT: clean (npm run lint)
BUILD: succeeds (npm run build)
E2E: no Playwright suite present in this repository (unchanged from prior
  phases)
FILES CREATED:
  - backend/tests/test_phase16_graph_engine_security.py
  - LIFELINK_PHASE_16_REPORT.md
FILES MODIFIED:
  - backend/app/api/routes/emergencies.py (max_results bound + docstring)
  - backend/app/api/routes/hospitals.py (max_results bound + Query import + docstring)
  - backend/app/services/emergency_pathway_service.py (row lock for the
    pathway-versioning concurrency fix)
  - frontend/src/pages/GraphDecisionEngine.tsx (disclaimer banner)
  - frontend/src/pages/DecisionTransparency.tsx (disclaimer banner)
  - frontend/src/pages/MathematicalModel.tsx (disclaimer banner)
  - frontend/src/pages/ResearchMode.tsx (disclaimer banner)
  - frontend/src/pages/SimulationMode.tsx (disclaimer banner)
  - frontend/src/pages/DonorRecommendation.tsx (disclaimer banner)
  - README.md (doc-staleness correction, §42)
KNOWN LIMITATIONS:
  - Live Neo4j/Aura connectivity cannot be verified from this sandbox
    (network blocks Bolt/TCP). All graph-path behavior is verified via
    mocked/monkeypatched Neo4j interactions plus the real, live
    haversine-fallback path (which this environment genuinely exercises,
    not a stand-in for it).
  - Pathway ranking is a bounded 6-hop Cypher enumeration, not a general
    Dijkstra/GDS shortest-path -- an intentional, documented limitation of
    Neo4j Community/Aura Free, unchanged from before this phase.
COMMANDS EXECUTED: see §47
```
