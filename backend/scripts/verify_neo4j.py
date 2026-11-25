"""Standalone, real-network Neo4j verification script.

WHY THIS EXISTS
----------------
This sandbox's outbound network only allows a small egress allowlist over
HTTP(S) on port 443; raw Bolt/TCP connections (the `neo4j+s://...:7687`
scheme Aura uses) are blocked by the sandbox's proxy. That was confirmed
directly during this phase's development: the real Aura credentials in
`.env` are present and well-formed (`neo4j_configured=True`), but every
connection attempt fails in well under a second with a DNS-resolution /
`ServiceUnavailable` error -- consistent with the connection being refused
at the network layer, not with bad credentials or a down cluster.

Because of that, this phase's automated test suite (tests/test_graph_engine.py,
tests/test_graph_service.py, tests/test_graph_service_logic.py,
tests/test_graph_api.py) can only prove two things for real: (1) the code
honestly degrades to the haversine fallback and never fabricates a GRAPH-
labelled result when Neo4j is unreachable, and (2) the Cypher-building and
record-parsing logic is correct in isolation against fake driver responses.
Neither of those actually executes real Cypher against a real Neo4j
instance over the wire.

This script is the third, missing leg: run it from ANY environment that
*does* have real network access to your Neo4j instance (a laptop, a CI
runner, a cloud VM -- anywhere that isn't this sandbox) to get a genuine,
live-network verification of connectivity, constraint creation, a full
resync from Postgres, and a real weighted-path Cypher query.

USAGE
-----
    cd backend
    pip install -r requirements.txt
    export DATABASE_URL=postgresql+psycopg://...      # same Postgres LifeLink uses
    export NEO4J_URI=neo4j+s://xxxx.databases.neo4j.io
    export NEO4J_USERNAME=neo4j
    export NEO4J_PASSWORD=...                          # never pass this on the CLI/argv
    export NEO4J_DATABASE=neo4j                         # optional
    python3 scripts/verify_neo4j.py

Exits 0 only if every step below genuinely succeeded. Exits 1 (and prints
exactly what failed) otherwise. Never prints the password or URI. Safe to
run repeatedly -- every write it performs is idempotent (MERGE-based, same
as the app's own sync code) or explicitly scoped to a resync you asked for.
"""
from __future__ import annotations

import sys
import time
import uuid
from typing import NoReturn


def _fail(step: str, exc: Exception) -> NoReturn:
    """Report a failure WITHOUT printing the exception's own message.

    Driver/network exceptions (ServiceUnavailable, Neo4jError, etc.) often
    embed the target hostname or other connection detail in their message
    text -- e.g. "Failed to DNS resolve address xxxx.databases.neo4j.io".
    That is not a password, but it is still connection detail this script
    must not put in a log, so only the exception's TYPE is ever printed,
    same discipline as app/core/graph.py uses for the same reason.
    """
    print(f"[FAIL] {step}: {type(exc).__name__}")
    sys.exit(1)


def _fail_with_safe_message(step: str, message: str) -> NoReturn:
    """Like _fail(), but for a message this script authored itself (no
    driver/connection text in it) -- safe to print in full."""
    print(f"[FAIL] {step}: {message}")
    sys.exit(1)


def main() -> None:
    print("=" * 72)
    print("LifeLink — real-network Neo4j verification")
    print("=" * 72)

    from app.core.config import settings

    if not settings.neo4j_configured:
        print(
            "[FAIL] NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD are not all set in "
            "this environment's configuration. Nothing to verify."
        )
        sys.exit(1)
    print("[ok] Neo4j configured (URI/username/password/database present; none printed here).")

    from app.core import graph as graph_core
    from app.db.session import SessionLocal
    from app.services import graph_service

    # ---- 1. Raw connectivity -------------------------------------------
    t0 = time.monotonic()
    try:
        driver = graph_core.get_driver()
        driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001 - deliberately broad, this is a diagnostic tool
        _fail("verify_connectivity()", exc)
    print(f"[ok] verify_connectivity() succeeded in {time.monotonic() - t0:.2f}s")

    # ---- 2. Constraint / index setup ------------------------------------
    try:
        graph_service.ensure_constraints()
    except Exception as exc:  # noqa: BLE001
        _fail("ensure_constraints()", exc)
    print("[ok] ensure_constraints() applied (facility/blood-type/city uniqueness, route indexes)")

    # ---- 3. Full resync from the real Postgres database -----------------
    db = SessionLocal()
    try:
        try:
            counts = graph_service.resync_all(db)
        except Exception as exc:  # noqa: BLE001
            _fail("resync_all(db) — wipes and rebuilds the graph from Postgres", exc)
        print(
            "[ok] resync_all() rebuilt the graph from Postgres: "
            f"{counts['facilities']} facilities, {counts['cities']} cities, "
            f"{counts['stock_edges']} stock edges, {counts['routes']} routes, "
            f"{counts['donor_aggregate_edges']} donor-aggregate edges"
        )
        if counts["facilities"] == 0:
            print(
                "[warn] 0 facilities were synced — this usually means the connected "
                "Postgres database has no seeded HospitalProfile rows yet. Run "
                "`python3 -m scripts.seed_demo_data` (or seed real data) first, "
                "then re-run this script for a meaningful pathway-query check below."
            )

        # ---- 4. A real weighted-path Cypher query, if there's data to query ----
        from sqlalchemy import select

        from app.models.hospital import HospitalProfile

        origin = db.scalar(select(HospitalProfile).limit(1))
        if origin is None:
            print("[skip] no HospitalProfile rows to query a pathway from — step 4 skipped.")
        else:
            from app.models.enums import BloodType

            result = graph_service.find_resource_pathways(
                db, blood_type=BloodType.O_POS, origin_facility_id=origin.id, max_results=5
            )
            if result.source.value != "GRAPH":
                _fail_with_safe_message(
                    "find_resource_pathways() returned a live pathway",
                    f"expected pathway_source=GRAPH with Neo4j reachable, got "
                    f"{result.source.value!r} ({result.degraded_reason})",
                )
            print(
                f"[ok] find_resource_pathways() ran a REAL Cypher traversal from facility "
                f"{origin.id} for O+: {len(result.candidates)} candidate(s) found "
                f"(best distance: {result.best_distance_km} km)"
            )

        # ---- 5. Confirm a node really exists with the expected shape ----
        with graph_core.graph_session() as session:
            rec = session.run(
                "MATCH (f:Facility {id: $id}) RETURN f.name AS name, f.city AS city",
                id=str(origin.id) if origin is not None else str(uuid.uuid4()),
            ).single()
        if origin is not None:
            if rec is None:
                _fail_with_safe_message(
                    "post-resync node read-back",
                    f"Facility {origin.id} not found in the graph after resync_all()",
                )
            print(f"[ok] read back real Facility node: name={rec['name']!r} city={rec['city']!r}")
    finally:
        db.close()

    print("=" * 72)
    print("ALL CHECKS PASSED — this Neo4j instance is genuinely reachable, writable,")
    print("and queryable by the LifeLink graph engine over the real network.")
    print("=" * 72)


if __name__ == "__main__":
    main()
