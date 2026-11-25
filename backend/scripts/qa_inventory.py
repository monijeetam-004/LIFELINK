"""Generate the QA inventory from the real source (not from documentation).

    python -m scripts.qa_inventory OUT_DIR [--routes-log FILE ...] [--e2e-dir DIR]

Writes OUT_DIR/inventory.json with:

* every API operation registered on the FastAPI app: method, path, module,
  handler, authentication (none / optional / session), the roles of a
  ``require_roles`` gate, and whether the ``Origin`` check applies;
* every frontend route in frontend/src/App.tsx with the roles of its
  ``RequireAuth`` guard and the e2e specs that visit it;
* every SQLAlchemy table, its columns, CHECK / UNIQUE constraints and
  foreign keys;
* the background workers and scheduled jobs found in app/workers;
* when ``--routes-log`` files are given (QA_ROUTE_LOG output of a test run),
  how often each operation was called and which status codes it returned.

Nothing here executes a request; it only inspects the code and logs.
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from fastapi.routing import APIRoute
from sqlalchemy import CheckConstraint, UniqueConstraint

ROOT = Path(__file__).resolve().parents[2]


def _walk_dependencies(dependant, seen=None):
    seen = seen if seen is not None else []
    for dep in dependant.dependencies:
        if dep.call is not None:
            seen.append(dep.call)
        _walk_dependencies(dep, seen)
    return seen


def _roles_of(call) -> list[str]:
    try:
        cv = inspect.getclosurevars(call)
    except TypeError:
        return []
    allowed = cv.nonlocals.get("allowed")
    if not allowed:
        return []
    return sorted(getattr(r, "value", str(r)) for r in allowed)


def api_operations() -> list[dict]:
    from app.main import app

    def _iter_routes(routes):
        # FastAPI >= 0.140 wraps included routers; their effective route
        # contexts carry the final path, methods, endpoint and dependant.
        for r in routes:
            if isinstance(r, APIRoute):
                yield r
            elif hasattr(r, "effective_route_contexts"):
                yield from r.effective_route_contexts()

    ops = []
    for route in _iter_routes(app.routes):
        if not getattr(route, "methods", None) or not hasattr(route, "dependant"):
            continue
        calls = _walk_dependencies(route.dependant)
        names = {getattr(c, "__name__", "") for c in calls}
        roles: list[str] = []
        for c in calls:
            roles += _roles_of(c)
        if "get_current_user" in names or roles:
            auth = "session"
        elif "get_optional_user" in names:
            auth = "optional"
        else:
            auth = "none"
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            ops.append(
                {
                    "method": method,
                    "path": route.path,
                    "module": route.endpoint.__module__.rsplit(".", 1)[-1],
                    "handler": route.endpoint.__name__,
                    "auth": auth,
                    "roles": sorted(set(roles)),
                    "origin_check": "verify_origin" in names,
                }
            )
    ops.sort(key=lambda o: (o["module"], o["path"], o["method"]))
    return ops


def frontend_routes(e2e_dir: Path) -> list[dict]:
    app_tsx = (ROOT / "frontend/src/App.tsx").read_text()
    routes = []
    chunks = re.split(r"<Route\b", app_tsx)[1:]
    for chunk in chunks:
        m = re.match(r'\s+path="([^"]+)"', chunk)
        if not m:
            continue
        body = chunk
        guard = re.search(r"<RequireAuth(?:\s+roles=\{\[([^\]]*)\]\})?", body)
        roles = re.findall(r'"([A-Z_]+)"', guard.group(1)) if guard and guard.group(1) else []
        routes.append({"path": m.group(1), "guarded": bool(guard), "roles": roles})
    # which e2e specs navigate to each route (string match on goto/URL literals)
    specs = {p.name: p.read_text() for p in sorted(e2e_dir.glob("*.spec.ts"))}
    for r in routes:
        if r["path"] == "*":
            r["e2e_specs"] = [n for n, t in specs.items() if "does-not-exist" in t or "/nope" in t]
            continue
        pat = re.escape(r["path"]).replace(r"/:", "/[^/]+").replace("\\:", ":")
        pat = re.sub(r":[a-zA-Z]+", "[^\"'`/?]+", pat)
        rx = re.compile(r"[\"'`](?:\$\{[A-Z_]+\})?" + pat + r"(?:[?\"'`/])")
        r["e2e_specs"] = [n for n, t in specs.items() if rx.search(t)]
    return routes


def tables() -> list[dict]:
    import app.models  # noqa: F401
    from app.db.base import Base  # noqa: F401  (registers every model)

    out = []
    for t in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
        out.append(
            {
                "table": t.name,
                "columns": len(t.columns),
                "foreign_keys": sorted(
                    {f"{fk.parent.name}->{fk.column.table.name}" for fk in t.foreign_keys}
                ),
                "checks": sorted(
                    str(c.name) for c in t.constraints if isinstance(c, CheckConstraint)
                ),
                "uniques": sorted(
                    str(c.name) for c in t.constraints if isinstance(c, UniqueConstraint)
                ),
                "indexes": len(t.indexes),
            }
        )
    return out


def workers() -> list[dict]:
    out = []
    for p in sorted((ROOT / "backend/app/workers").glob("*.py")):
        src = p.read_text()
        funcs = re.findall(r"^def ([a-z_]+)\(", src, re.M)
        out.append({"module": f"app.workers.{p.stem}", "functions": funcs})
    return out


def route_log_stats(files: list[Path]) -> dict:
    """QA_ROUTE_LOG files are JSON lists of {method, path, status, count}."""
    calls: Counter = Counter()
    statuses: dict[str, Counter] = defaultdict(Counter)
    for f in files:
        for row in json.loads(f.read_text()):
            key = f"{row['method']} {row['path']}"
            calls[key] += row["count"]
            statuses[key][str(row["status"])] += row["count"]
    return {k: {"calls": calls[k], "statuses": dict(statuses[k])} for k in calls}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--routes-log", nargs="*", default=[])
    ap.add_argument("--e2e-dir", default=str(ROOT / "frontend/e2e"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    data = {
        "api_operations": api_operations(),
        "frontend_routes": frontend_routes(Path(a.e2e_dir)),
        "tables": tables(),
        "workers": workers(),
        "route_log": route_log_stats([Path(p) for p in a.routes_log]),
    }
    (out / "inventory.json").write_text(json.dumps(data, indent=1, default=str))
    counts = {k: len(v) for k, v in data.items()}
    print(" ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
