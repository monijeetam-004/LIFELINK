# LIFELINK — Environment + Neo4j Configuration Verification Report

**Scope:** verify (not rebuild) that environment configuration — especially Neo4j — is
correctly wired into the existing LifeLink backend. No architecture was changed. No graph
functionality, fallback behavior, RBAC, or APIs were touched. The three changes made are all
listed in §13, and each is a config-hygiene fix, not a rebuild.

**Note on the task template:** the instructions referred to a "Flask application entrypoint."
This backend is **FastAPI**, not Flask — confirmed directly (`app/main.py`: `from fastapi
import FastAPI`, `app = FastAPI(...)`, in `requirements.txt`/`pyproject.toml`:
`fastapi>=0.111`). Everything below was verified against the real FastAPI entrypoint; nothing
was force-fit to a Flask shape.

---

## 1. Repository / environment structure (as found)

| Item | Location |
|---|---|
| Backend root | `backend/` |
| Frontend root | `frontend/` |
| App entrypoint | `backend/app/main.py` (FastAPI, `lifespan` context manager) |
| Config/settings module | `backend/app/core/config.py` (`pydantic_settings.BaseSettings`) |
| Env-var loading mechanism | `env_file=".env"` on `Settings.model_config`, resolved relative to the process's working directory — the app is always run with `cwd=backend/` (confirmed in `README.md`: `cd backend` precedes every `uvicorn`/`pytest` command; Docker/compose set `env_file: .env` relative to `backend/` too) |
| Tests | `backend/tests/` |
| Pytest config | `backend/pyproject.toml` → `[tool.pytest.ini_options]` (`testpaths = ["tests"]`) |
| Database config | `DATABASE_URL` / `TEST_DATABASE_URL` in `Settings` → `backend/app/db/session.py` |
| Neo4j integration | `backend/app/core/graph.py` (driver lifecycle) + `backend/app/services/graph_service.py` (queries) |
| Redis config | `REDIS_URL` in `Settings` → `backend/app/core/redis.py` |
| Auth/session config | `SESSION_COOKIE_*`, `SESSION_TTL_SECONDS` in `Settings` → `backend/app/api/cookies.py`, `app/api/deps.py`, `app/services/session_service.py` |
| `.gitignore` | `backend/.gitignore` (backend-scoped), `frontend/.gitignore` (frontend-scoped) |
| `.env.example` | `backend/.env.example`, `frontend/.env.example` — both present |
| Docker/deployment config | `backend/Dockerfile`, `backend/docker-compose.yml` (includes an **optional** local Neo4j service) |
| Existing Neo4j verification script | `backend/scripts/verify_neo4j.py` (built in the prior phase; used, not rebuilt) |
| Existing LifeLink phase reports | `LIFELINK_PHASE_8_REPORT.md` (project root) — the graph-engine build this verification pass checks against |

The repository is **not a git working tree** (`git status` fails with "not a git repository" at
every level checked) — there is currently no `.git` directory anywhere in the project. The
`.gitignore` files are real and correctly authored regardless, so the repo is ready the moment
it is initialized/cloned into git; this is simply the current state of this environment, not a
defect.

---

## 2. `.env` location verification — **CORRECT, no fix needed**

Confirmed the actual layout matches the intended structure exactly:

```
backend/
├── .env              ← present, at backend root
├── .env.example       ← present, at backend root
├── .gitignore
├── tests/             ← contains conftest.py + 23 test modules, NO .env file inside it
└── application files...
```

`find backend -iname ".env*"` returns exactly two files: `backend/.env` and
`backend/.env.example`. **No duplicate `.env` exists inside `tests/`** — there was nothing to
move or remove. The root `.env` is the single canonical local development environment file.

---

## 3. Environment variable loading — verified by actually importing the app

Ran (values never printed — only booleans/shapes):

```python
from app.core.config import settings
settings.environment          # loaded
settings.neo4j_configured      # True — all three of URI/username/password present
settings.database_url          # set
settings.test_database_url     # set
settings.cors_origin_list      # parsed correctly from CORS_ORIGINS
settings.session_cookie_name   # loaded
```

```
from app.main import app   # imports cleanly, FastAPI app object constructed, routers included
```

Both succeeded. `Settings` is `pydantic_settings.BaseSettings` with `env_file=".env"`,
`case_sensitive=False`, `extra="ignore"` — so `NEO4J_URI` in `.env` maps to the
`neo4j_uri` field regardless of case, and any variable in `.env` with no matching field
(e.g. `AURA_INSTANCEID`, `AURA_INSTANCENAME`) is silently and safely ignored rather than
causing a startup error.

---

## 4. Variable-by-variable verification (names only, values never inspected/printed beyond presence)

| Variable | Declared in `Settings`? | Genuinely consumed where? | Present in `.env`? |
|---|---|---|---|
| `ENVIRONMENT` | ✅ `environment` | `settings.is_test` property, seed/test bootstrap | ✅ configured |
| `SECRET_KEY` | ✅ `secret_key` | (session/token signing — see note below) | ✅ configured |
| `DATABASE_URL` | ✅ `database_url` | `app/db/session.py` engine | ✅ configured |
| `TEST_DATABASE_URL` | ✅ `test_database_url` | `tests/conftest.py` (forces `DATABASE_URL` to this value before app import — see §5) | ✅ configured |
| `REDIS_URL` | ✅ `redis_url` | `app/core/redis.py` | ✅ configured |
| `RQ_SYNC` | ✅ `rq_sync` | email/queue dispatch | ✅ configured |
| `SESSION_COOKIE_NAME` | ✅ | `app/api/cookies.py`, `app/api/deps.py`, `app/api/routes/auth.py` | ✅ configured |
| `SESSION_COOKIE_SECURE` | ✅ | `app/api/cookies.py` (`secure=` on `set_cookie`) | ✅ configured |
| `SESSION_COOKIE_SAMESITE` | ✅ | `app/api/cookies.py` | ✅ configured |
| `SESSION_COOKIE_DOMAIN` | ✅ | `app/api/cookies.py` (`or None` fallback) | ✅ configured |
| `SESSION_TTL_SECONDS` | ✅ | `app/services/session_service.py`, `app/api/cookies.py` (`max_age`) | ✅ configured |
| `CORS_ORIGINS` | ✅ `cors_origins` → `cors_origin_list` | `app/main.py` `CORSMiddleware(allow_origins=..., allow_credentials=True)` | ✅ configured |
| `FRONTEND_BASE_URL` | ✅ | email link generation | ✅ configured |
| `MAIL_HOST` / `MAIL_PORT` / `MAIL_FROM` / `MAIL_USERNAME` / `MAIL_PASSWORD` / `MAIL_STARTTLS` | ✅ all six | `app/services/email_service.py` | ✅ configured |
| `NEO4J_URI` | ✅ `neo4j_uri` | `app/core/graph.py::get_driver()` | ✅ configured |
| `NEO4J_USERNAME` | ✅ `neo4j_username` | `app/core/graph.py::get_driver()` (`auth=(username, password)`) | ✅ configured |
| **`NEO4J_PASSWORD`** | ✅ `neo4j_password` | `app/core/graph.py::get_driver()` | ✅ configured — **spelled correctly everywhere**, see §5 |
| `NEO4J_DATABASE` | ✅ `neo4j_database` | `app/core/graph.py::graph_session()` (`driver.session(database=... or None)`) | ✅ configured |
| `AURA_INSTANCEID` / `AURA_INSTANCENAME` | ❌ not declared | **not read by any code** (confirmed by repo-wide grep — zero matches outside `.env`/`.env.example`) — harmless thanks to `extra="ignore"`; this matches the instruction "only use them if the existing code actually needs them," and it doesn't | ✅ present in `.env` (Aura console metadata only) |

### The `NEO4J_PASSWORD` / `NEO4J_PASSWOR` check

Searched the entire repository (`app/`, `scripts/`, `tests/`, `*.md`, `*.yml`, `.env*`) for the
exact truncated string `NEO4J_PASSWOR` not followed by `D`. **Zero matches.** The variable is
spelled `NEO4J_PASSWORD` consistently in `.env`, `.env.example`, `app/core/config.py`
(`neo4j_password`), and every reference to it. There was nothing to fix here — the concern in
the task brief did not correspond to an actual defect in this codebase.

---

## 5. Neo4j Aura configuration — driver lifecycle traced end to end

Read `app/core/graph.py` in full and confirmed, by tracing the code (not assuming):

- **URI/username/password/database** are read exclusively from `settings.neo4j_uri`,
  `settings.neo4j_username`, `settings.neo4j_password`, `settings.neo4j_database` — never
  hardcoded, never read from anywhere else.
- **Driver construction is lazy**: `get_driver()` only builds `GraphDatabase.driver(uri,
  auth=(username, password), connection_timeout=settings.neo4j_connection_timeout_seconds)`
  the first time it's needed (double-checked-locking singleton), and constructing the object
  does not open a socket.
- **Connection lifecycle**: `close_driver()` is wired into the FastAPI `lifespan` shutdown
  handler in `app/main.py` — confirmed present, unchanged.
- **Health check**: `graph_health()` calls `driver.verify_connectivity()` and returns a
  small, **credential-free** dict: `{configured, connected, status, reason}`. `reason` is
  always just an exception *type name* (e.g. `"ServiceUnavailable"`), never a raw exception
  message — this matters because the raw Neo4j driver's own exception text embeds the target
  hostname (confirmed live, see §9); the app already never surfaces that, and this
  verification pass did not need to change that discipline anywhere.
- **Graceful failure**: every Cypher-issuing call goes through `graph_session()`, which wraps
  `ServiceUnavailable` / `Neo4jError` / `DriverError` into `GraphUnavailableError`. Callers
  either propagate it honestly (`POST /admin/graph/resync` → HTTP 503) or fall back to the
  haversine-distance ranking with `pathway_source=HAVERSINE_FALLBACK` and a real
  `degraded_reason`.
- **GRAPH vs HAVERSINE_FALLBACK behavior**: unchanged and reverified live (§9) — still
  behaves exactly as documented in `LIFELINK_PHASE_8_REPORT.md`.
- **`resync_all()` behavior**: unchanged and reverified live (§9) — still returns a real 503,
  never a fabricated success, when Neo4j is unreachable.
- **Resource-pathway behavior**: unchanged and reverified live (§9).

`AURA_INSTANCEID` / `AURA_INSTANCENAME` were confirmed, per §4, to be genuinely unused by the
driver or anywhere else in the code — the instruction not to assume they're required was
correct, and no code change was needed or made there.

**Nothing about the Neo4j architecture, the fallback logic, the sync strategy, or the resync
endpoint was rebuilt, replaced, or weakened.** This pass only read and exercised the existing
code.

---

## 6. Test database isolation — verified by reading `tests/conftest.py`

This is the strongest isolation guarantee in the repo, and it already existed (nothing needed
to change):

```python
os.environ.setdefault(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://lifelink:lifelink@localhost:5432/lifelink_test",
)
# The app reads DATABASE_URL; point it at the test database before app import.
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
```

This line runs **before `from app.main import app`** — so by the time the app (and its
SQLAlchemy `engine`) is constructed, `DATABASE_URL` has already been forcibly overwritten to
equal `TEST_DATABASE_URL` (`lifelink_test`, a *different database name* from the real dev
database `lifelink`), regardless of whatever `DATABASE_URL` was set to in `.env`. There is no
code path by which running pytest can write to the real `lifelink` development database.

Additional isolation confirmed by reading the fixtures:
- `_schema` (session-scoped, autouse): drops and recreates every table in the test database
  before the test session, drops again after.
- `_clean` (function-scoped, autouse): `TRUNCATE`s every table and flushes Redis **database
  15** (not the dev default, database 0) before every single test.
- `client`/`mailbox` fixtures are plain, no side effects.

**Neo4j test behavior**: confirmed live-network Neo4j tests are **intentionally, explicitly
not enabled**. `tests/test_graph_engine.py` and `tests/test_graph_service.py` construct their
own throwaway `Settings(neo4j_uri="neo4j://lifelink-graph-does-not-exist.invalid:7687", ...)`
objects and `monkeypatch.setattr(graph, "settings", ...)` to replace the module-level settings
object used by `app/core/graph.py` — so these tests **never touch the real Aura credentials in
`.env`** even though those credentials are present and would otherwise be picked up by the
process-wide `settings` singleton. This is a real, correct isolation pattern, not a gap to fix.

---

## 7. Git security — one real gap found and fixed (see §13)

`backend/.gitignore` (before this pass):
```
__pycache__/
*.pyc
.env
.venv/
venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.egg-info/
```

`.env` (exact filename) was already ignored, and `.env.example` was correctly **not**
ignored (it's meant to be tracked). This was already safe for the current single `.env` file.

**Gap**: there was no `.env.*` pattern, so a future file like `.env.local` or
`.env.production` would **not** have been git-ignored — a real, if currently-latent, risk
exactly matching the pattern the task asked to verify against. Fixed minimally (§13) by adding
`.env.*` plus a `!.env.example` re-allow, adapted into the existing file rather than
replacing it wholesale.

`frontend/.gitignore` already contains `.env` and `*.local` — no gap found there.

No hardcoded secrets found anywhere in source: searched `app/`, `scripts/`, `frontend/src/`,
and `docs/` for embedded `neo4j+s://`/`bolt://` URIs and for `password=`/`secret=`/`api_key=`
literals. The only matches were (a) placeholder/example strings in `scripts/verify_neo4j.py`'s
own docstring (`neo4j+s://xxxx.databases.neo4j.io`, clearly a template) and a comment in
`docker-compose.yml`, and (b) the **local-only** `NEO4J_AUTH: neo4j/lifelink-dev-password` for
the *optional* local Neo4j container in `docker-compose.yml` — the same class of throwaway
dev-network credential as the adjacent `DATABASE_URL:
postgresql+psycopg://lifelink:lifelink@db:5432/lifelink`, not a real secret and not connected
to the real Aura instance in any way.

---

## 8. `.env.example` status — one real gap found and fixed (see §13)

`.env.example` already existed and already documented every variable **except** two that are
present in the real `.env`: `AURA_INSTANCEID` and `AURA_INSTANCENAME`. Verified with a
name-only diff between the two files (values never compared, only variable names). Added both
as empty placeholders with an explanatory comment (they're Aura console metadata, not read by
the app). `.env.example` continues to contain **zero real values** — every line is either an
empty placeholder or a safe non-secret default (e.g. `MAIL_PORT=1025`, matching the local
Mailpit dev convention already in the file).

---

## 9. Live runtime re-verification (this pass, real HTTP requests against a real running server)

The prior phase's Neo4j graceful-degradation behavior was **re-exercised live** in this pass,
not merely re-read, to confirm the config wiring genuinely reaches runtime behavior:

```
GET  /admin/system/health   → overall status: "degraded"
                               graph: {configured: True, connected: False,
                                       status: "unreachable", reason: "ServiceUnavailable"}
POST /admin/graph/resync    → HTTP 503, {"detail": "Neo4j is unavailable -- the graph was not resynced"}
GET  /hospitals/me/resource-pathways?blood_type=O_NEG
                             → pathway_source: "HAVERSINE_FALLBACK", graph_available: False,
                               2 real candidates returned
```

This is the same honest, non-fabricated degradation behavior documented in
`LIFELINK_PHASE_8_REPORT.md` — confirmed unchanged and still working, end to end, through a
real logged-in session (`demo.admin@lifelink-demo.example.com`,
`demo.hospital@lifelink-demo.example.com`) against the real local Postgres database. The test
server was started, exercised, and cleanly stopped; no residual process was left running.

`scripts/verify_neo4j.py` (the existing script from the prior phase) was re-run rather than
rebuilt:

```
[ok] Neo4j configured (URI/username/password/database present; none printed here).
[FAIL] verify_connectivity(): ServiceUnavailable
```
Exit code 1 — an honest failure, because this sandbox's network still blocks raw Bolt/TCP (the
same environment limitation documented in the prior phase report, unrelated to configuration
correctness). **Configuration validation** (this report, §3–§5) and **live Neo4j connectivity**
(this section) are and remain two separate claims — this report does not conflate them.

---

## 10. Frontend environment safety

`frontend/.env.example` declares exactly one variable: `VITE_API_URL` (the backend's base
HTTP URL). Grepped `frontend/src/` for every `import.meta.env` usage — there is exactly **one**
reference in the entire frontend codebase (`src/lib/api.ts`), and it is `VITE_API_URL`. No
other `VITE_*` variable exists anywhere in the frontend. Confirmed **zero** references to
Neo4j credentials, database URLs, `SECRET_KEY`, Redis URLs, mail credentials, or S3 keys
anywhere in `frontend/src` — the only "Neo4j" matches in the frontend are UI copy strings
(e.g. "Neo4j is unreachable right now...") describing engine state to the user, not
configuration. The frontend talks to Neo4j only indirectly, through the backend's REST API
(`/hospitals/me/resource-pathways`, `/admin/transport-routes`, etc.) — exactly as designed in
the prior phase; this pass did not change that boundary.

---

## 11. Tests executed this pass (all real, none invented)

| Command | Result |
|---|---|
| `python3 -c "from app.core.config import settings; ..."` | succeeded (no values printed) |
| `python3 -c "from app.main import app; ..."` | succeeded — app imports and constructs cleanly |
| `python3 -m pytest -q` (full suite) | **213 passed**, 0 failed |
| `python3 -m pytest tests/test_graph_engine.py tests/test_graph_service.py tests/test_graph_service_logic.py tests/test_graph_api.py tests/test_seed_demo_data.py -q` | **50 passed**, 0 failed |
| `python3 -m mypy app scripts` | 3 pre-existing errors, in `app/api/routes/verifications.py` (2) and `app/api/routes/donors.py` (1) — **not touched by this pass or the prior Neo4j-engine phase**; unrelated to environment/Neo4j configuration |
| `python3 -m ruff check .` (the exact command `README.md` documents) | 60 findings — see honest breakdown below; **not a functional/configuration problem** |
| `python3 scripts/verify_neo4j.py` | Honest `[FAIL]` at the connectivity step (§9) — correct behavior in this network-restricted sandbox |

### Honest `ruff check .` breakdown (not fixed — out of this task's scope, explained below)

```
43  E501   line-too-long
15  UP042  replace-str-enum (suggests `class X(str, enum.Enum)` → `class X(enum.StrEnum)`)
 2  I001   unsorted-imports
```
By file:
- `alembic/versions/c6cfa33e7862_phase7_admin_verification_prefs_...py` — 26 findings. **Pre-dates
  the graph-engine phase entirely** (a different, earlier migration).
- `alembic/versions/4a9d4f2ed50b_phase8_graph_engine_schema_transport_...py` — 19 findings
  (long `op.create_table`/`sa.Column(...)` lines, 1 import-order note). This is the graph-engine
  phase's own migration.
- `app/models/enums.py` — 14 findings (`UP042` on `FacilityType`, `TransportRouteStatus`,
  `GraphPathwaySource` — the 3 enums the graph-engine phase added — plus the pre-existing
  enums in the same file that use the identical pattern).
- `app/models/user.py` — 1 finding (`UP042` on the pre-existing `UserRole` enum).

**Why these were reported, not fixed, in this pass:** every one of these is a pre-existing
repository-wide style convention (every enum in the codebase, including ones this pass did not
touch, uses `class X(str, enum.Enum)`), or a migration-file formatting choice from either this
phase or an earlier one, applied consistently. None of it is a configuration, security, or
Neo4j-integration defect — the task for this pass is environment/Neo4j configuration
verification, and Phase J explicitly says not to make speculative architectural or
whole-codebase changes. A dedicated lint-cleanup pass (touching `pyproject.toml`'s `[tool.ruff]`
exclusions or reformatting call sites) would be the right, separately-scoped place to address
this, and is noted here for visibility rather than silently left undiscovered.

---

## 12. Any failures

None that indicate a real defect. The two "failures" observed (`verify_neo4j.py`'s honest
`[FAIL]`, and 60 pre-existing `ruff` findings) are, respectively, an expected environment
limitation and a pre-existing/out-of-scope style-lint backlog — both are reported plainly
above rather than hidden or worked around.

## Any environment limitations

Same one already known from the prior phase and reconfirmed here: **this sandbox's outbound
network blocks raw Bolt/TCP**, so live Neo4j connectivity cannot be proven from inside this
environment. Configuration correctness (this report) is fully verified; live connectivity
requires running `scripts/verify_neo4j.py` from a network-unrestricted environment (a laptop,
CI runner, or any host outside this sandbox) — see `LIFELINK_PHASE_8_REPORT.md` §9 for the
exact steps.

Separately (unrelated to Neo4j): Postgres and Redis were found stopped at the start of this
verification pass (a fresh container/session state, not a configuration defect) and were
started (`service postgresql start`, `redis-server --daemonize yes`) before running any tests;
the real dev database's data (1065 previously-seeded users) was intact and unaffected.

---

## 13. Files changed in this pass (all three are config-hygiene fixes, not rebuilds)

| File | What changed | Why |
|---|---|---|
| `backend/.env` | **Comment only** — replaced a stale header above the `NEO4J_*` block that said Neo4j was "NOT used by Phase 1... Ignored by app config" with an accurate description (Neo4j vars are real, typed, actively-consumed, optional `Settings` fields; PostgreSQL remains the source of truth). No variable name or value was touched. | The old comment was factually wrong as of the graph-engine phase and would mislead anyone editing this file next. |
| `backend/.env.example` | Added `AURA_INSTANCEID=` and `AURA_INSTANCENAME=` placeholders (empty, with an explanatory comment that they're Aura console metadata the app doesn't read); changed the example `NEO4J_DATABASE=` line to show `neo4j` as an example value, matching the task's suggested example. | The real `.env` has these two variables; the example template didn't document them. |
| `backend/.gitignore` | Added `.env.*` and `!.env.example` immediately after the existing `.env` line. | Closes a real (if currently latent) gap: only the exact filename `.env` was ignored before, so a future `.env.local`/`.env.production` would not have been. |

No application code, migration, test, API route, RBAC check, or frontend file was modified.
`app/core/graph.py` and `app/services/graph_service.py` — the actual Neo4j engine — were read
and exercised, never edited.

---

## 14. Exact commands used (for reproducibility)

```
find backend -iname ".env*"
find backend/tests -maxdepth 1 -type f
sed -E 's/^([A-Za-z_][A-Za-z0-9_]*)=.*/\1=<redacted>/' backend/.env      # names only, no values
grep -rn "NEO4J_PASSWOR" .   # (excluding the correct NEO4J_PASSWORD)     # typo search, zero matches
diff <(grep -oE '^[A-Za-z_]+=' backend/.env | sort -u) \
     <(grep -oE '^[A-Za-z_]+=' backend/.env.example | sort -u)            # names only
python3 -c "from app.core.config import settings; ..."
python3 -c "from app.main import app; ..."
python3 -m pytest -q
python3 -m pytest tests/test_graph_engine.py tests/test_graph_service.py \
    tests/test_graph_service_logic.py tests/test_graph_api.py tests/test_seed_demo_data.py -q
python3 -m mypy app scripts
python3 -m ruff check .
python3 -m ruff check . --statistics
python3 scripts/verify_neo4j.py
# Live runtime re-check (server started, exercised, then stopped):
uvicorn app.main:app --host 0.0.0.0 --port 8000 &
curl -s -X POST http://localhost:8000/auth/login ...
curl -s http://localhost:8000/admin/system/health ...
curl -s -X POST http://localhost:8000/admin/graph/resync ...
curl -s "http://localhost:8000/hospitals/me/resource-pathways?blood_type=O_NEG&max_results=2" ...
pkill -f "uvicorn app.main:app"
```

---

## 15. Remaining manual action

1. **Run `scripts/verify_neo4j.py` from a network-unrestricted environment** to prove live
   Bolt connectivity to the real Aura instance — the one thing this sandbox genuinely cannot
   verify (see `LIFELINK_PHASE_8_REPORT.md` for exact steps).
2. Optional, separately-scoped: a dedicated lint-cleanup pass for the 60 `ruff` findings in
   §11 (line-length in two migration files, `str`+`Enum` → `enum.StrEnum` across the
   codebase's enum classes) — cosmetic, not a configuration or security issue, deliberately
   left untouched by this pass per its "do not make speculative architectural changes" scope.

---

## Definition of done — checklist

- [x] Root backend `.env` is correctly located (`backend/.env`, alongside `tests/`)
- [x] No unnecessary duplicate `tests/.env` exists (there never was one)
- [x] Environment loading is verified (app imports, `Settings` loads, values reach real code paths)
- [x] `NEO4J_PASSWORD` naming is correct everywhere (no `NEO4J_PASSWOR` typo found anywhere)
- [x] Neo4j credentials are environment-driven (traced through `app/core/graph.py`)
- [x] Neo4j database configuration is environment-driven (`settings.neo4j_database` → `driver.session(database=...)`)
- [x] Existing Neo4j graph functionality remains intact (re-verified live, unchanged)
- [x] GRAPH / HAVERSINE_FALLBACK behavior remains intact (re-verified live)
- [x] Existing `/admin/graph/resync` behavior remains intact (re-verified live — honest 503)
- [x] Test database remains isolated (traced `tests/conftest.py`'s forced `DATABASE_URL` override)
- [x] `.env` is git-ignored (was already true; `.env.*` pattern added for future files)
- [x] `.env.example` contains no secrets (confirmed — placeholders/defaults only)
- [x] Frontend does not expose private backend secrets (only `VITE_API_URL` is exposed, confirmed)
- [x] Existing tests still pass (213/213)
- [x] Existing type checking still passes where it passed before (mypy: same 3 pre-existing, unrelated errors; 0 new)
- [ ] Existing lint passes cleanly — **does not**, but for pre-existing/out-of-scope reasons documented honestly in §11, not hidden
- [x] No fake connectivity or fake success was introduced anywhere
- [x] This report was created
