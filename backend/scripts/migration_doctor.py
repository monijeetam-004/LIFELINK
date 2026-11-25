"""Read-only diagnosis of a database's Alembic migration state.

    python -m scripts.migration_doctor            # human-readable report
    python -m scripts.migration_doctor --json     # machine-readable

Run it BEFORE `alembic upgrade head` (docker-compose's api service does).
It never writes to the database. It answers the questions an operator
needs when a migration fails, for example with

    Can't locate revision identified by '0007_phase7b'

That error means the database's `alembic_version` table names a revision
that is not in this repository's `alembic/versions/`. Typical cause in
development: a Docker named volume left behind by ANOTHER checkout or
project. Compose names volumes `<project>_pgdata`, and the project name used
to default to the directory name. Before the compose file set
`name: lifelink`, every project whose compose file sat in a folder called
`backend/` shared the volume `backend_pgdata`.

States (exit code in brackets):

* EMPTY [0]: no tables and no alembic_version. `alembic upgrade head` will
  build the whole schema.
* BEHIND [0]: a known revision, with N migrations pending. Upgrade normally.
* AT_HEAD [0]: nothing to do.
* UNKNOWN_REVISION [2]: alembic_version names a revision this repository
  does not contain. `alembic upgrade head` WILL FAIL. The report compares
  the live schema with the current models so the operator can choose a
  recovery (see docs/final-verification/INFRASTRUCTURE_TEST.md, "Migration
  integrity").
* UNVERSIONED [3]: tables exist but there is no alembic_version. This is
  not a database Alembic created, or its history was removed.
* MULTIPLE_DB_HEADS [4]: alembic_version holds more than one row, but the
  repository has a single linear chain.
* REPO_INVALID [5]: the repository itself has more than one head, a missing
  down_revision or a duplicate revision id.

Nothing here deletes, stamps or modifies anything. Those are deliberate
operator decisions, documented with their data-loss consequences.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BACKEND = Path(__file__).resolve().parent.parent

EXIT = {
    "EMPTY": 0,
    "BEHIND": 0,
    "AT_HEAD": 0,
    "UNKNOWN_REVISION": 2,
    "UNVERSIONED": 3,
    "MULTIPLE_DB_HEADS": 4,
    "REPO_INVALID": 5,
}


@dataclass
class RepoGraph:
    revisions: list[str]
    heads: list[str]
    bases: list[str]
    problems: list[str]
    order: list[str]  # base -> head


@dataclass
class Diagnosis:
    state: str
    database: str
    db_revisions: list[str]
    repo_head: str | None
    pending: int | None = None
    tables: int = 0
    looks_like_lifelink: bool = False
    schema_vs_models: dict = field(default_factory=dict)
    explanation: str = ""
    next_steps: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return EXIT[self.state]


def repo_graph() -> RepoGraph:
    """The repository's migration graph, from Alembic's own script loader,
    plus independent checks on the raw files (duplicate ids, dangling
    down_revisions) that Alembic would only report as an exception."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    problems: list[str] = []
    seen: dict[str, str] = {}
    downs: dict[str, tuple] = {}
    import ast

    for f in sorted((BACKEND / "alembic" / "versions").glob("*.py")):
        tree = ast.parse(f.read_text())
        vals = {}
        for node in tree.body:
            target = None
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                target, value = node.targets[0].id, node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                target, value = node.target.id, node.value
            if target in ("revision", "down_revision"):
                vals[target] = ast.literal_eval(value)
        rev = vals.get("revision")
        if rev is None:
            problems.append(f"{f.name}: no revision id")
            continue
        if rev in seen:
            problems.append(f"duplicate revision id {rev!r} in {seen[rev]} and {f.name}")
        seen[rev] = f.name
        d = vals.get("down_revision")
        downs[rev] = tuple(d) if isinstance(d, (list, tuple)) else ((d,) if d else ())
    for rev, ds in downs.items():
        for d in ds:
            if d not in downs:
                problems.append(
                    f"{seen[rev]}: down_revision {d!r} does not exist in the repository"
                )
    script = ScriptDirectory.from_config(cfg)
    heads = list(script.get_heads())
    bases = list(script.get_bases())
    if len(heads) != 1:
        problems.append(f"expected exactly one head, found {heads}")
    if len(bases) != 1:
        problems.append(f"expected exactly one base, found {bases}")
    order = [s.revision for s in script.walk_revisions()][::-1]
    if set(order) != set(downs):
        problems.append(f"revisions not reachable from the head: {sorted(set(downs) - set(order))}")
    return RepoGraph(
        revisions=sorted(downs), heads=heads, bases=bases, problems=problems, order=order
    )


def _schema_vs_models(conn) -> dict:
    from sqlalchemy import inspect

    from app import models  # noqa: F401
    from app.db.base import Base

    insp = inspect(conn)
    live = {
        t: {c["name"] for c in insp.get_columns(t)}
        for t in insp.get_table_names()
        if t != "alembic_version"
    }
    orm = {t.name: {c.name for c in t.columns} for t in Base.metadata.sorted_tables}
    cols = {
        t: {"db_only": sorted(live[t] - orm[t]), "models_only": sorted(orm[t] - live[t])}
        for t in set(live) & set(orm)
        if live[t] != orm[t]
    }
    return {
        "matches_current_models": not (set(live) ^ set(orm)) and not cols,
        "tables_missing": sorted(set(orm) - set(live)),
        "tables_extra": sorted(set(live) - set(orm)),
        "column_differences": cols,
    }


def diagnose(url: str | None = None) -> Diagnosis:
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.engine import make_url

    if url is None:
        from app.core.config import settings

        url = settings.database_url
    g = repo_graph()
    head = g.heads[0] if len(g.heads) == 1 else None
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            tables = list(inspect(conn).get_table_names())
            has_version = "alembic_version" in tables
            db_revs = (
                list(conn.execute(text("SELECT version_num FROM alembic_version")).scalars())
                if has_version
                else []
            )
            user_tables = [t for t in tables if t != "alembic_version"]
            d = Diagnosis(
                state="AT_HEAD",
                database=make_url(url).render_as_string(hide_password=True),
                db_revisions=db_revs,
                repo_head=head,
                tables=len(user_tables),
                looks_like_lifelink={"users", "audit_logs", "hospital_profiles"} <= set(tables),
            )
            if g.problems:
                d.state = "REPO_INVALID"
                d.explanation = "; ".join(g.problems)
                d.next_steps = ["Fix the migration files; no database action is safe until then."]
                return d
            if not db_revs and not user_tables:
                d.state = "EMPTY"
                d.pending = len(g.order)
                d.explanation = "Fresh database."
                d.next_steps = ["alembic upgrade head"]
                return d
            if user_tables:
                d.schema_vs_models = _schema_vs_models(conn)
            if not db_revs:
                d.state = "UNVERSIONED"
                d.explanation = (
                    "Tables exist but there is no alembic_version row: this schema was "
                    "not created by this repository's migrations (or its history was "
                    "removed)."
                )
                d.next_steps = _recovery_steps(d)
                return d
            if len(db_revs) > 1:
                d.state = "MULTIPLE_DB_HEADS"
                d.explanation = (
                    f"alembic_version has {len(db_revs)} rows; the repository is linear."
                )
                d.next_steps = _recovery_steps(d)
                return d
            rev = db_revs[0]
            if rev not in g.revisions:
                d.state = "UNKNOWN_REVISION"
                d.explanation = (
                    f"The database is stamped {rev!r}, which does not exist in this "
                    f"repository's alembic/versions ({len(g.revisions)} revisions, head "
                    f"{head!r}). `alembic upgrade head` cannot work out which migrations "
                    "to apply and fails with "
                    f"\"Can't locate revision identified by '{rev}'\". In development "
                    "this almost always means the PostgreSQL volume was created by a "
                    "different checkout or project."
                )
                d.next_steps = _recovery_steps(d)
                return d
            idx = g.order.index(rev)
            d.pending = len(g.order) - 1 - idx
            if d.pending:
                d.state = "BEHIND"
                d.explanation = f"Known revision {rev!r}; {d.pending} migration(s) pending."
                d.next_steps = ["(production: take a backup first) alembic upgrade head"]
            else:
                d.state = "AT_HEAD"
                d.explanation = "Database is at the repository head."
            return d
    finally:
        engine.dispose()


def _recovery_steps(d: Diagnosis) -> list[str]:
    steps = [
        "1. Do NOT delete the volume yet. Back it up: "
        "docker compose exec db pg_dump -U lifelink -Fc lifelink > lifelink-before-recovery.dump",
    ]
    sv = d.schema_vs_models
    if d.looks_like_lifelink and sv.get("matches_current_models"):
        steps.append(
            "2. The live schema matches the CURRENT models exactly (tables and "
            "columns), so the data can be kept: `alembic stamp head` records the "
            "correct revision without changing any table. Then run `alembic upgrade "
            "head` (a no-op) and `alembic check`."
        )
    else:
        steps.append(
            "2. The live schema does NOT match the current models "
            f"(missing tables: {len(sv.get('tables_missing', []))}, extra tables: "
            f"{len(sv.get('tables_extra', []))}, tables with column differences: "
            f"{len(sv.get('column_differences', {}))}). Stamping would be unsafe: it would claim "
            "migrations ran that did not. Keep the dump and either restore the data "
            "you need into a fresh database, or point this checkout at a NEW volume "
            "(compose project `lifelink` already uses `lifelink_pgdata`) and leave the "
            "old volume untouched."
        )
    steps.append(
        "3. DESTRUCTIVE development reset (only if the data is disposable, after step 1): "
        "docker compose down && docker volume rm <the stale volume> && docker compose up -d. "
        "This permanently deletes every row in that volume."
    )
    return steps


def main() -> None:
    ap = argparse.ArgumentParser(description="Diagnose the database's Alembic state (read-only).")
    ap.add_argument("--database-url", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    import logging

    logging.disable(logging.WARNING)
    try:
        d = diagnose(args.database_url)
    except Exception as exc:  # unreachable database etc.: say so, never guess
        print(
            f"migration_doctor: cannot inspect the database: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        sys.exit(6)
    if args.json:
        print(json.dumps(asdict(d), indent=1, default=str))
    else:
        print(f"migration state : {d.state}")
        print(f"database        : {d.database}")
        print(f"db revision(s)  : {d.db_revisions or '-'}")
        print(f"repository head : {d.repo_head}")
        if d.pending is not None:
            print(f"pending         : {d.pending}")
        kind = "yes" if d.looks_like_lifelink else "no"
        print(f"tables          : {d.tables} (LifeLink schema: {kind})")
        if d.schema_vs_models:
            print(f"schema vs models: {json.dumps(d.schema_vs_models)}")
        print(f"explanation     : {d.explanation}")
        for s in d.next_steps:
            print(f"  {s}")
    sys.exit(d.exit_code)


if __name__ == "__main__":
    main()
