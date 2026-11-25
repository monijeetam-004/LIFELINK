# Neo4j setup

Neo4j is **optional**. PostgreSQL is authoritative for facilities, routes
and stock; Neo4j holds a projection (`Facility`, `ROUTE`, `HAS_STOCK`) that
the pathway planner may use for the path search.

* Path query: `graph_service._route_paths_query` — `UNWIND` the candidate
  sources, `OPTIONAL MATCH p = (src)-[:ROUTE*1..H]-(dest)` restricted to
  OPERATIONAL routes not in the excluded set, cheapest by the strategy's
  objective, returning the full ordered `route_ids` and `node_ids`.
* Trust rule: a graph answer is used only if every route in it is
  OPERATIONAL in PostgreSQL right now, and distances/times are always
  re-read from PostgreSQL. Otherwise (or if Neo4j is down) the relational
  hop-bounded search (`app/core/route_graph.py`) answers and the response
  says why (`pathway_source`, `degraded_reason`).

```bash
docker compose --profile graph up -d neo4j
# .env
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<from your secret store>
# project PostgreSQL into the graph
curl -X POST -b cookie.txt -H 'Origin: http://localhost:5173' localhost:8000/admin/graph/resync
python -m scripts.verify_neo4j            # end-to-end check against the live server
```

Tests:

* always: `tests/test_gap_neo4j.py` (query builder, parser, planner trust
  rule and fallback), `tests/test_graph_service_logic.py`;
* with a server: `NEO4J_TEST_URI=bolt://localhost:7687 NEO4J_TEST_USER=neo4j
  NEO4J_TEST_PASSWORD=... pytest tests/test_gap_neo4j.py` runs
  `test_real_neo4j_paths_equal_relational_paths` (graph and relational
  engines must choose the same routes on the same network).

**Status in the verification sandbox: BLOCKED.** No Neo4j server could be
obtained (Docker Hub, dist.neo4j.org, debian.neo4j.com and Maven Central are
all unreachable — `evidence/egress_probe.txt`). The real-server test was
skipped with that reason; nothing claims Neo4j was exercised.

**Final external validation, 2026-10-13: BLOCKED — REAL SERVER UNAVAILABLE.** Every acquisition channel was retried (`evidence/external/neo4j/neo4j_attempts.txt`):

* `docker pull neo4j:5.24` from Docker Hub, ECR Public, ghcr.io and quay.io: all 403;
* dist.neo4j.org, neo4j.com, debian.neo4j.com, Maven Central and its GCS mirror: unreachable;
* GitHub release assets: 404;
* building from source (tag `5.24.0` is visible over git) needs Maven, which is blocked.

To close it on a machine with registry access:

```bash
cd backend
docker compose --profile graph up -d neo4j
# backend/.env: NEO4J_URI=bolt://localhost:7687 (bolt://neo4j:7687 inside compose), NEO4J_USERNAME=neo4j, NEO4J_PASSWORD=<yours>
curl -X POST -b cookie.txt -H 'Origin: http://localhost:5173' localhost:8000/admin/graph/resync
python -m scripts.verify_neo4j
NEO4J_TEST_URI=bolt://localhost:7687 NEO4J_TEST_USER=neo4j NEO4J_TEST_PASSWORD=<yours> pytest tests/test_gap_neo4j.py -v
```

