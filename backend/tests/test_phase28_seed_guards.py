"""Phase 28 -- production-data-safety guards on the two standalone
scripts that write synthetic/fixed-password accounts (scripts/
seed_demo_data.py, scripts/e2e_seed.py).

Neither script is wired into app startup (a grep of app/ confirms nothing
there imports either module -- production boot cannot trigger this by
itself, which is the master prompt's actual "must NOT automatically seed"
requirement), but nothing previously stopped an operator from manually
running one against a real production DATABASE_URL by mistake. These tests
cover the fail-closed guard added to each script's main() -- mirroring the
existing RESTORE_ALLOW_PROD guard on scripts/db_restore.sh -- entirely via
monkeypatching (no subprocess, no real seeding), so they run in milliseconds
and never touch the real database.
"""
from __future__ import annotations

import pytest

import scripts.e2e_seed as e2e_seed
import scripts.seed_demo_data as seed_demo_data


def test_seed_demo_data_refuses_in_production_without_override(monkeypatch) -> None:
    monkeypatch.setattr(seed_demo_data.settings, "environment", "production")
    monkeypatch.delenv("SEED_DEMO_ALLOW_PROD", raising=False)
    called = {"run": False}
    monkeypatch.setattr(seed_demo_data, "run", lambda db: called.__setitem__("run", True))

    with pytest.raises(SystemExit) as excinfo:
        seed_demo_data.main()

    assert excinfo.value.code == 1
    assert called["run"] is False  # never touched the database


def test_seed_demo_data_allows_production_with_explicit_override(monkeypatch) -> None:
    monkeypatch.setattr(seed_demo_data.settings, "environment", "production")
    monkeypatch.setenv("SEED_DEMO_ALLOW_PROD", "yes")
    called = {"run": False}
    monkeypatch.setattr(seed_demo_data, "run", lambda db: called.__setitem__("run", True))

    seed_demo_data.main()  # must not raise

    assert called["run"] is True


def test_seed_demo_data_runs_normally_outside_production(monkeypatch) -> None:
    monkeypatch.setattr(seed_demo_data.settings, "environment", "development")
    monkeypatch.delenv("SEED_DEMO_ALLOW_PROD", raising=False)
    called = {"run": False}
    monkeypatch.setattr(seed_demo_data, "run", lambda db: called.__setitem__("run", True))

    seed_demo_data.main()  # must not raise

    assert called["run"] is True


def test_e2e_seed_refuses_in_production_without_override(monkeypatch) -> None:
    monkeypatch.setattr(e2e_seed.settings, "environment", "production")
    monkeypatch.delenv("SEED_DEMO_ALLOW_PROD", raising=False)

    class _BoomSession:
        def execute(self, *a, **k):
            raise AssertionError("must not touch the database when refused")

    monkeypatch.setattr(e2e_seed, "SessionLocal", lambda: _BoomSession())

    with pytest.raises(SystemExit) as excinfo:
        e2e_seed.main()

    assert excinfo.value.code == 1
