"""Migration integrity (final verification section 9.1).

Guards against the failure seen on a developer machine:

    Can't locate revision identified by '0007_phase7b'

`0007_phase7b` has never existed in this repository. A database stamped with
it came from a Docker volume created by another checkout or project. These
tests pin down:

* the repository's chain is one linear history: one base, one head, no
  dangling down_revision, no duplicate ids, and every file reachable;
* the phantom revision is not referenced anywhere;
* against REAL scratch PostgreSQL databases, using the real `alembic`
  CLI in a subprocess and `scripts/migration_doctor.py`:
  - fresh database: EMPTY, then upgrade head, then AT_HEAD, matching the models;
  - existing database at an older revision, with data: BEHIND, then upgrade
    head; the data is preserved;
  - stale stamp `0007_phase7b`: `alembic upgrade head` fails with exactly
    the reported error; the doctor reports UNKNOWN_REVISION (exit 2) and
    writes nothing; the documented non-destructive recovery
    (`alembic stamp --purge head`, allowed only when the schema matches the
    models) works and keeps the data;
  - foreign schema with the same stamp: the doctor refuses to recommend
    stamping;
  - tables without alembic_version: UNVERSIONED (exit 3);
* compose pins the project name (so volumes are `lifelink_*`) and runs the
  doctor before `alembic upgrade head`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

import psycopg

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from scripts import migration_doctor  # noqa: E402

PHANTOM = "0007_phase7b"


def _versions() -> list[Path]:
    return sorted((BACKEND / "alembic" / "versions").glob("*.py"))


# --------------------------------------------------------------------------- #
# Static: the repository graph
# --------------------------------------------------------------------------- #


def test_repository_chain_is_single_linear_history() -> None:
    g = migration_doctor.repo_graph()
    assert g.problems == []
    assert len(g.heads) == 1 and len(g.bases) == 1
    assert g.bases == ["0001_initial"]
    # every file is on the one chain, base -> head, exactly once
    assert len(g.order) == len(_versions()) == len(set(g.order))
    assert g.order[0] == "0001_initial" and g.order[-1] == g.heads[0]


def test_alembic_cli_reports_one_head_and_no_branches() -> None:
    env = {**os.environ}
    heads = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    branches = subprocess.run(
        [sys.executable, "-m", "alembic", "branches"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert heads.returncode == 0 and branches.returncode == 0, heads.stderr + branches.stderr
    head_lines = [ln for ln in heads.stdout.splitlines() if ln.strip()]
    assert len(head_lines) == 1 and head_lines[0].endswith("(head)")
    assert branches.stdout.strip() == ""


def test_phantom_revision_is_not_in_or_referenced_by_the_repository() -> None:
    g = migration_doctor.repo_graph()
    assert PHANTOM not in g.revisions
    for f in _versions():
        assert PHANTOM not in f.read_text(), f.name
    for f in (BACKEND / "app").rglob("*.py"):
        assert PHANTOM not in f.read_text(), f


def test_compose_pins_project_name_and_runs_doctor_before_migrating() -> None:
    text = (BACKEND / "docker-compose.yml").read_text()
    assert "\nname: lifelink\n" in text
    doctor = text.index("python -m scripts.migration_doctor &&")
    assert doctor < text.index("alembic upgrade head &&", doctor)


# --------------------------------------------------------------------------- #
# Real PostgreSQL scratch databases
# --------------------------------------------------------------------------- #


def _admin_url() -> str:
    base = os.environ["TEST_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    return base.rsplit("/", 1)[0] + "/postgres"


@contextmanager
def scratch_db():
    name = f"lifelink_migint_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(_admin_url(), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    sa_url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + f"/{name}"
    try:
        yield sa_url, sa_url.replace("postgresql+psycopg://", "postgresql://")
    finally:
        with psycopg.connect(_admin_url(), autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{name}' AND pid <> pg_backend_pid()"
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


def _alembic(sa_url: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": sa_url},
        capture_output=True,
        text=True,
        timeout=180,
    )


def _doctor_cli(sa_url: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "scripts.migration_doctor", "--database-url", sa_url],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _audit_marker(pg_url: str) -> int:
    with psycopg.connect(pg_url) as conn:
        return conn.execute(
            "SELECT count(*) FROM audit_logs WHERE action = 'migration.integrity.marker'"
        ).fetchone()[0]


def test_fresh_existing_stale_and_recovered_databases() -> None:
    g = migration_doctor.repo_graph()
    head = g.heads[0]
    older = g.order[-3]  # an existing database two migrations behind
    with scratch_db() as (sa_url, pg_url):
        # -- fresh -------------------------------------------------------
        d = migration_doctor.diagnose(sa_url)
        assert d.state == "EMPTY" and d.exit_code == 0 and d.pending == len(g.order)

        # -- existing, behind, holding data ----------------------------
        r = _alembic(sa_url, "upgrade", older)
        assert r.returncode == 0, r.stderr
        with psycopg.connect(pg_url) as conn:
            conn.execute(
                "INSERT INTO audit_logs (id, action, created_at) "
                "VALUES (gen_random_uuid(), 'migration.integrity.marker', now())"
            )
        d = migration_doctor.diagnose(sa_url)
        assert d.state == "BEHIND" and d.pending == 2 and d.db_revisions == [older]
        r = _alembic(sa_url, "upgrade", "head")
        assert r.returncode == 0, r.stderr
        d = migration_doctor.diagnose(sa_url)
        assert d.state == "AT_HEAD" and d.db_revisions == [head]
        assert d.schema_vs_models["matches_current_models"] is True
        assert _audit_marker(pg_url) == 1
        assert _alembic(sa_url, "check").returncode == 0

        # -- stale stamp: reproduce the reported failure -----------------
        with psycopg.connect(pg_url) as conn:
            conn.execute("UPDATE alembic_version SET version_num = %s", (PHANTOM,))
        r = _alembic(sa_url, "upgrade", "head")
        assert r.returncode != 0
        assert f"Can't locate revision identified by '{PHANTOM}'" in (r.stderr + r.stdout)

        cli = _doctor_cli(sa_url)
        assert cli.returncode == 2, cli.stdout + cli.stderr
        assert "UNKNOWN_REVISION" in cli.stdout and PHANTOM in cli.stdout
        assert "alembic stamp head" in cli.stdout  # schema matches -> stamping is safe
        with psycopg.connect(pg_url) as conn:  # the doctor wrote nothing
            assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == PHANTOM
        assert _audit_marker(pg_url) == 1

        # -- documented non-destructive recovery --------------------------
        r = _alembic(sa_url, "stamp", "--purge", "head")
        assert r.returncode == 0, r.stderr
        r = _alembic(sa_url, "upgrade", "head")
        assert r.returncode == 0, r.stderr
        assert migration_doctor.diagnose(sa_url).state == "AT_HEAD"
        assert _audit_marker(pg_url) == 1  # data kept


def test_foreign_schema_with_phantom_stamp_is_never_declared_safe_to_stamp() -> None:
    with scratch_db() as (sa_url, pg_url):
        with psycopg.connect(pg_url) as conn:
            conn.execute("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
            conn.execute("INSERT INTO alembic_version VALUES (%s)", (PHANTOM,))
            conn.execute("CREATE TABLE widgets (id int PRIMARY KEY)")
        d = migration_doctor.diagnose(sa_url)
        assert d.state == "UNKNOWN_REVISION" and d.exit_code == 2
        assert d.looks_like_lifelink is False
        assert d.schema_vs_models["matches_current_models"] is False
        steps = " ".join(d.next_steps)
        assert "Stamping would be unsafe" in steps and "pg_dump" in steps
        assert "DESTRUCTIVE" in steps


def test_tables_without_alembic_version_are_unversioned() -> None:
    with scratch_db() as (sa_url, pg_url):
        with psycopg.connect(pg_url) as conn:
            conn.execute("CREATE TABLE users (id int PRIMARY KEY)")
        d = migration_doctor.diagnose(sa_url)
        assert d.state == "UNVERSIONED" and d.exit_code == 3
