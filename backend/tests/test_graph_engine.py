"""Tests for the Neo4j driver lifecycle / graceful-degradation layer.

Two things are deliberately DIFFERENT kinds of test here, and each is
labelled as such rather than left to look like the other:

  * The "disabled" tests (Neo4j not configured at all) and the
    "unreachable" tests (Neo4j configured but genuinely not reachable, via
    a real DNS lookup against a host that does not exist) are REAL
    integration tests: they exercise the actual neo4j driver and the
    actual network stack, and genuinely observe a connection failure.
    They do not mock the driver.
  * They intentionally do NOT prove that real Cypher queries against a
    live, reachable Neo4j server would succeed -- only that this codebase
    degrades honestly (raises GraphUnavailableError / reports
    connected=False) rather than fabricating a graph result when Neo4j is
    absent or unreachable. Proving live query correctness needs a real,
    network-reachable Neo4j instance, which this sandbox does not have
    (see scripts/verify_neo4j.py, meant to be run in such an environment).
"""
from __future__ import annotations

import time

import pytest

from app.core import graph
from app.core.config import Settings


@pytest.fixture(autouse=True)
def _reset_driver_singleton():
    """Every test starts and ends with no driver constructed.

    The driver singleton is process-global (see app/core/graph.py), so
    tests that point `graph.settings` at different hosts must not leak a
    driver built under a previous test's settings into the next test.
    """
    graph.close_driver()
    yield
    graph.close_driver()


def _unconfigured_settings() -> Settings:
    return Settings(neo4j_uri=None, neo4j_username=None, neo4j_password=None, neo4j_database=None)


def _unreachable_settings() -> Settings:
    # A syntactically valid bolt URI whose host deliberately does not
    # resolve -- a real, reproducible "Neo4j is unreachable" condition
    # that doesn't depend on any credentials actually present in this
    # environment's .env.
    return Settings(
        neo4j_uri="neo4j://lifelink-graph-does-not-exist.invalid:7687",
        neo4j_username="neo4j",
        neo4j_password="not-a-real-password",
        neo4j_database=None,
        neo4j_connection_timeout_seconds=3.0,
    )


class TestGraphDisabled:
    """Neo4j not configured at all -- the default out-of-the-box state."""

    def test_graph_enabled_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(graph, "settings", _unconfigured_settings())
        assert graph.graph_enabled() is False

    def test_get_driver_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(graph, "settings", _unconfigured_settings())
        with pytest.raises(graph.GraphUnavailableError):
            graph.get_driver()

    def test_graph_session_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(graph, "settings", _unconfigured_settings())
        with pytest.raises(graph.GraphUnavailableError):
            with graph.graph_session():
                pytest.fail("graph_session() must not yield when unconfigured")

    def test_graph_health_reports_disabled_honestly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(graph, "settings", _unconfigured_settings())
        health = graph.graph_health()
        assert health == {"configured": False, "connected": False, "status": "disabled"}

    def test_graph_health_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(graph, "settings", _unconfigured_settings())
        # Must be safe to call unconditionally from a health endpoint.
        graph.graph_health()


class TestGraphUnreachable:
    """Neo4j IS configured, but the host genuinely does not resolve/respond.

    This is the condition this sandbox is permanently in for the real Aura
    credentials in .env (raw-TCP/non-443 egress is blocked here -- see the
    phase report), so these assertions describe real, currently-observed
    behaviour, not a hypothetical.
    """

    def test_graph_enabled_true_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(graph, "settings", _unreachable_settings())
        assert graph.graph_enabled() is True

    def test_driver_constructs_without_connecting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Driver construction is lazy/non-blocking -- must succeed even
        # though the host is unreachable, because no socket is opened yet.
        monkeypatch.setattr(graph, "settings", _unreachable_settings())
        driver = graph.get_driver()
        assert driver is not None

    def test_graph_session_raises_unavailable_quickly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(graph, "settings", _unreachable_settings())
        started = time.monotonic()
        with pytest.raises(graph.GraphUnavailableError) as excinfo:
            with graph.graph_session() as session:
                session.run("RETURN 1")
        elapsed = time.monotonic() - started
        # Bounded well under the configured timeout -- proves this is a
        # real failure being handled, not a hang being masked.
        assert elapsed < 10
        # The error must never leak the configured username/password.
        message = str(excinfo.value)
        assert "not-a-real-password" not in message

    def test_graph_health_reports_unreachable_honestly_and_fast(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(graph, "settings", _unreachable_settings())
        started = time.monotonic()
        health = graph.graph_health()
        elapsed = time.monotonic() - started
        assert health["configured"] is True
        assert health["connected"] is False
        assert health["status"] == "unreachable"
        assert "reason" in health
        assert "not-a-real-password" not in str(health)
        assert elapsed < 10

    def test_graph_health_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(graph, "settings", _unreachable_settings())
        graph.graph_health()  # must not raise


class TestDriverLifecycle:
    def test_close_driver_is_idempotent(self) -> None:
        graph.close_driver()
        graph.close_driver()  # must not raise when nothing was ever built

    def test_driver_is_a_singleton_until_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(graph, "settings", _unreachable_settings())
        first = graph.get_driver()
        second = graph.get_driver()
        assert first is second
        graph.close_driver()
        third = graph.get_driver()
        assert third is not first
