"""Neo4j driver lifecycle for the graph/resource-pathway engine.

This module is the ONLY place a Neo4j `Driver` is constructed. Everything
above it (app/services/graph_service.py, the admin/emergency/hospital
routes) goes through `graph_session()` or `graph_health()` and must treat
`GraphUnavailableError` as an expected, honest outcome -- never as a bug to
route around by fabricating a graph result.

Design constraints (see LIFELINK global engineering rules):
  * Neo4j is entirely optional. NEO4J_URI/USERNAME/PASSWORD/DATABASE come
    from environment configuration (app/core/config.py) and may be unset --
    that is a normal, supported state (`graph_enabled()` is False), not an
    error condition.
  * Credentials are never logged, never included in an exception message
    surfaced to a caller, and never returned from any function here. Only
    `type(exc).__name__` (an exception class name) is ever propagated.
  * Driver construction is lazy and non-blocking, same as
    app/core/redis.py's module-level client -- constructing a Driver object
    does not open a socket. Only `verify_connectivity()` / an actual query
    does, which is where a real unreachable-Neo4j failure surfaces.
  * Nothing here ever silently returns a fake/empty success. Every failure
    path raises GraphUnavailableError (or, in graph_health(), returns an
    explicit connected=False status) so callers can give an honest answer.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from neo4j import Driver, GraphDatabase, Session
from neo4j.exceptions import DriverError, Neo4jError, ServiceUnavailable

from app.core import metrics
from app.core.config import settings

logger = logging.getLogger("lifelink.graph")

_driver: Driver | None = None
_driver_lock = threading.Lock()


class GraphUnavailableError(RuntimeError):
    """Raised whenever the graph engine cannot honestly answer a request.

    Callers MUST catch this and take an explicitly-labelled non-graph
    fallback (see GraphPathwaySource in app/models/enums.py) rather than
    returning a result that looks like it came from a real graph
    traversal. The message never contains connection credentials -- only a
    short, generic reason and/or an exception class name.
    """


def graph_enabled() -> bool:
    """Whether Neo4j is configured at all (URI + username + password)."""
    return settings.neo4j_configured


def get_driver() -> Driver:
    """Return the process-wide lazy Neo4j driver singleton.

    Raises GraphUnavailableError if Neo4j is not configured, or if the
    driver object itself fails to construct (e.g. a malformed URI scheme).
    This does NOT prove Neo4j is reachable -- driver construction never
    opens a network connection; use graph_health() or run a query inside
    graph_session() to actually test reachability.
    """
    global _driver
    if not graph_enabled():
        raise GraphUnavailableError(
            "Neo4j is not configured (NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD unset)"
        )
    if _driver is None:
        with _driver_lock:
            if _driver is None:
                # graph_enabled() (== settings.neo4j_configured) already
                # guarantees these three are non-None; asserting it here
                # (rather than trusting that implicitly) keeps this honest
                # for a type checker and for any future refactor.
                uri, username, password = (
                    settings.neo4j_uri,
                    settings.neo4j_username,
                    settings.neo4j_password,
                )
                assert uri is not None and username is not None and password is not None
                try:
                    _driver = GraphDatabase.driver(
                        uri,
                        auth=(username, password),
                        connection_timeout=settings.neo4j_connection_timeout_seconds,
                    )
                except Exception as exc:
                    raise GraphUnavailableError(
                        f"Neo4j driver construction failed: {type(exc).__name__}"
                    ) from exc
    return _driver


def close_driver() -> None:
    """Close the driver, if one was ever constructed.

    Called on app shutdown (app/main.py lifespan) and in test teardown so
    a test run never leaks open sockets/threads across test cases.
    """
    global _driver
    with _driver_lock:
        if _driver is not None:
            try:
                _driver.close()
            except Exception:  # best-effort close; never raise on shutdown
                logger.warning("Neo4j driver close raised during shutdown", exc_info=True)
            finally:
                _driver = None


@contextmanager
def graph_session() -> Iterator[Session]:
    """Yield a real Neo4j session scoped to the configured database.

    This is the single chokepoint every Cypher-issuing call in
    app/services/graph_service.py goes through. It never fabricates a
    session or swallows a connectivity failure: on any problem opening the
    session or (via the caller re-raising a driver/Neo4j error inside the
    `with` block) executing a query against it, GraphUnavailableError is
    raised so the caller can take its honest fallback path.
    """
    if not graph_enabled():
        raise GraphUnavailableError("Neo4j is not configured")
    driver = get_driver()
    try:
        session = driver.session(database=settings.neo4j_database or None)
    except Exception as exc:
        raise GraphUnavailableError(f"Neo4j session open failed: {type(exc).__name__}") from exc
    try:
        yield session
    except (ServiceUnavailable, Neo4jError, DriverError) as exc:
        logger.warning("Neo4j operation failed: %s", type(exc).__name__)
        metrics.inc_neo4j_failure()
        raise GraphUnavailableError(f"Neo4j operation failed: {type(exc).__name__}") from exc
    finally:
        session.close()


def graph_health() -> dict[str, Any]:
    """Real connectivity probe used by GET /admin/system/health's `graph` field.

    Never raises. Always returns a small, credential-free dict describing
    the honest current state: not configured / configured-but-unreachable
    / connected.
    """
    if not graph_enabled():
        return {"configured": False, "connected": False, "status": "disabled"}
    try:
        driver = get_driver()
        driver.verify_connectivity()
    except GraphUnavailableError as exc:
        metrics.inc_neo4j_failure()
        return {"configured": True, "connected": False, "status": "unreachable", "reason": str(exc)}
    except Exception as exc:  # verify_connectivity raises driver errors directly, not ours
        logger.warning("Neo4j verify_connectivity failed: %s", type(exc).__name__)
        metrics.inc_neo4j_failure()
        return {
            "configured": True,
            "connected": False,
            "status": "unreachable",
            "reason": type(exc).__name__,
        }
    return {"configured": True, "connected": True, "status": "ok"}
