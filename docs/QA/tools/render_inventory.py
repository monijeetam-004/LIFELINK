"""Render the generated parts of the QA inventory as Markdown tables.

    python render_inventory.py INVENTORY_JSON CRAWL_RESULTS_JSON OUT_MD

INVENTORY_JSON comes from `python -m scripts.qa_inventory` (with the route
log of the final backend run); CRAWL_RESULTS_JSON from
frontend/scripts/qa-crawl.mjs (optional: pass "-" to skip). The output is
included verbatim in docs/QA/PROJECT_INVENTORY.md and FEATURE_TEST_MATRIX.md.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path


def api_table(inv: dict) -> list[str]:
    log = inv.get("route_log", {})
    rows = [
        "| # | Method | Path | Module | Auth | Roles | Origin check | Calls in final run | Status codes observed | Status |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, o in enumerate(inv["api_operations"], 1):
        key = f"{o['method']} {o['path']}"
        st = log.get(key)
        codes = ", ".join(f"{k}×{v}" for k, v in sorted(st["statuses"].items())) if st else "—"
        ok2xx = st and any(k.startswith("2") for k in st["statuses"])
        neg = st and any(k[0] == "4" for k in st["statuses"])
        if not st:
            status = "IMPLEMENTED_BUT_UNVERIFIED"
        elif ok2xx and (neg or o["auth"] == "none"):
            status = "IMPLEMENTED_AND_VERIFIED"
        elif ok2xx:
            status = "IMPLEMENTED_AND_VERIFIED (success path; no 4xx observed)"
        else:
            status = "IMPLEMENTED_BUT_PARTIAL (no 2xx observed)"
        rows.append(
            f"| {i} | {o['method']} | `{o['path']}` | {o['module']} | {o['auth']} | "
            f"{', '.join(o['roles']) or ('any signed-in' if o['auth'] == 'session' else '—')} | "
            f"{'yes' if o['origin_check'] else '—'} | {st['calls'] if st else 0} | {codes} | {status} |"
        )
    return rows


TILE = re.compile(r"tile\.openstreetmap|ERR_TUNNEL|ERR_PROXY|ERR_NAME")


def _clean(xs: list[str]) -> list[str]:
    """Drop failures caused by the sandbox egress block on map tiles."""
    return [x for x in xs if not TILE.search(x)]


def route_table(inv: dict, crawl: dict | list | None) -> list[str]:
    visits_all = crawl["results"] if isinstance(crawl, dict) else (crawl or [])
    by_route: dict[str, list[dict]] = defaultdict(list)
    for r in visits_all:
        r = {**r, "consoleErrors": _clean(r["consoleErrors"]), "failed": _clean(r["failed"])}
        by_route[r["route"]].append(r)
    rows = [
        "| # | Route | Guard | Roles | E2E specs that open it | Crawl (375/768/1280): console errors · failed requests · max overflow · axe serious+critical (tile-host failures excluded) | Status |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(inv["frontend_routes"], 1):
        visits = by_route.get(r["path"], [])
        if visits:
            errs = sum(len(v["consoleErrors"]) for v in visits)
            failed = sum(len(v["failed"]) for v in visits)
            over = max(v["overflowPx"] for v in visits)
            axe = sum(len(v["axe"] or []) for v in visits)
            crawl_cell = f"{len(visits)} visits: {errs} · {failed} · {over}px · {axe}"
        else:
            crawl_cell = "not crawled"
        specs = ", ".join(s.replace(".spec.ts", "") for s in r["e2e_specs"]) or "—"
        if r["path"] == "*":
            status = "IMPLEMENTED_AND_VERIFIED" if r["e2e_specs"] else "IMPLEMENTED_BUT_UNVERIFIED"
        elif visits and (r["e2e_specs"] or True):
            clean = all(
                not v["consoleErrors"] and not v["failed"] and v["overflowPx"] == 0
                and not (v["axe"] or [])
                for v in visits
            )
            if clean and r["e2e_specs"]:
                status = "IMPLEMENTED_AND_VERIFIED"
            elif clean:
                status = "IMPLEMENTED_AND_VERIFIED (render, a11y, layout; no workflow spec)"
            else:
                status = "IMPLEMENTED_BUT_PARTIAL (see crawl findings)"
        else:
            status = "IMPLEMENTED_AND_VERIFIED" if r["e2e_specs"] else "IMPLEMENTED_BUT_UNVERIFIED"
        rows.append(
            f"| {i} | `{r['path']}` | {'RequireAuth' if r['guarded'] else 'public'} | "
            f"{', '.join(r['roles']) or ('any signed-in' if r['guarded'] else '—')} | {specs} | "
            f"{crawl_cell} | {status} |"
        )
    return rows


def table_table(inv: dict) -> list[str]:
    rows = ["| Table | Columns | Indexes | CHECK constraints | UNIQUE constraints | Foreign keys |",
            "|---|---|---|---|---|---|"]
    for t in inv["tables"]:
        rows.append(
            f"| `{t['table']}` | {t['columns']} | {t['indexes']} | {len(t['checks'])} | "
            f"{len(t['uniques'])} | {', '.join(t['foreign_keys']) or '—'} |"
        )
    return rows


def main() -> int:
    inv = json.loads(Path(sys.argv[1]).read_text())
    crawl = None if sys.argv[2] == "-" else json.loads(Path(sys.argv[2]).read_text())
    out = []
    out += ["## API operations (generated)", ""] + api_table(inv) + [""]
    out += ["## Frontend routes (generated)", ""] + route_table(inv, crawl) + [""]
    out += ["## Database tables (generated)", ""] + table_table(inv) + [""]
    Path(sys.argv[3]).write_text("\n".join(out) + "\n")
    print(f"rendered {len(inv['api_operations'])} operations, {len(inv['frontend_routes'])} routes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
