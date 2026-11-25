# LIFELINK — Phase 29: CI/CD + Staging Environment + Release Pipeline Hardening — Audit

Continues directly after Phase 28 (production environment/database/deployment hardening — final status GO-WITH-LIMITATIONS). This document is the full narrative and evidence; `LIFELINK_PHASE_29_CI_CD_STAGING_REPORT.md` is the classification and final gate.

## 0. Repository inspection, before any change

This session's container had been recycled since Phase 28 ended (PostgreSQL and Redis were both stopped; project files on disk were unaffected). Before writing anything, the actual repository state was inspected directly rather than assumed:

- **Git**: `git status`/`git config --list`/`git remote -v` at the project root confirmed this was **not a git repository at all** — no `.git` directory existed anywhere in the tree, only the sandbox's own global git config (GitHub-oriented: `url."https://github.com/".insteadOf=git@github.com:`, git-lfs filters, SSH commit signing). Every prior phase's work (0 through 28) existed only as files on disk with no version history.
- **CI config**: a full-tree search for `*.yml`/`*.yaml` (excluding `node_modules`) found exactly one file, `backend/docker-compose.yml` (a local dev/E2E compose file, explicitly documented in its own header comment as dev-only). No `.github/workflows/`, no `.gitlab-ci.yml`, no CI configuration of any platform existed anywhere.
- **Docker**: `backend/Dockerfile` and `backend/.dockerignore` were re-read and confirmed unchanged from Phase 28's end state (non-root user, `HEALTHCHECK` against `/health/live`, production entrypoint `app.asgi:application`).
- **Test config**: `backend/pyproject.toml`'s `[tool.pytest.ini_options]` (`addopts = "-q"`, `testpaths = ["tests"]`), `backend/requirements.txt` (runtime deps only, loose `>=` pins, no lock file) vs `backend/requirements-dev.txt` (`pytest`, `httpx`, `ruff`, `mypy` — a separate file, not baked into `requirements.txt`), `frontend/package-lock.json` (frontend already had deterministic installs via `npm ci`; no lock file existed for the backend).
- **Env config**: `backend/.env.example` fully re-read for every configuration surface (database, Redis, session, CORS, mail, registration limits, object storage, certificates, Neo4j, SMS).
- **Frontend build config**: `frontend/package.json` scripts (`dev`/`build`/`typecheck`/`lint`/`preview`), `frontend/playwright.config.ts` (hardcoded a sandbox-only Chromium path — see §9).
- **Backend startup / migration config**: `backend/scripts/run_production.sh`, `backend/alembic.ini`, `backend/alembic/versions/` (19 revisions, single head, confirmed via `alembic heads`).
- **Health/readiness endpoints**: `app/api/routes/health.py` re-read (`/health`, `/health/live`, `/health/ready` — Phase 28 additions, unchanged).
- **Smoke tests**: `backend/scripts/production_smoke_test.py` re-read in full (its own 12-item infra-level check list, `--self-boot` and `--base-url` modes).
- **Documentation**: all `LIFELINK_PHASE_*.md` files at the project root (45+ files) enumerated; Phase 28's audit/report read in full for the exact baseline this phase inherits (801 backend tests, 65 Playwright, TypeScript/ESLint/build clean, GO-WITH-LIMITATIONS).
- **Package scripts / dependency files**: `backend/requirements.txt`, `backend/requirements-dev.txt`, `frontend/package.json`/`package-lock.json` all read in full.

**Conclusion:** CI/CD was **absent**, not merely unconfigured for this platform — there was no git history to attach CI to in the first place. Per the master prompt's own instruction ("if the repository has no remote information, create the CI configuration in a platform-neutral, well-documented manner and explain what remains to be connected"), a real local git repository was initialized as necessary groundwork (§1), and GitHub Actions was chosen as the CI platform (§2) — this is implementation from a blank slate, not an improvement to something pre-existing, and is documented as such throughout rather than overstated.

## 1. Establishing version control

`git init` + `git branch -m main`, then three foundational commits before any CI/CD work began:

1. **Initial commit** ("LifeLink through Phase 28", 487 files) — every prior phase's work captured as the starting snapshot. Real `.env` files, backup files, build artifacts, `node_modules/`, and `dump.rdb` were verified excluded via a new root-level `.gitignore` (added alongside the pre-existing `backend/.gitignore` and `frontend/.gitignore` — git honors nested `.gitignore` files automatically) before staging, checked with multiple `git status --short` / `git ls-files` passes.
2. **Untrack Playwright test-results and TypeScript build info** — `frontend/test-results/.last-run.json` and `frontend/tsconfig.tsbuildinfo` were swept up by the initial `git add -A` because `frontend/.gitignore` didn't yet exclude them; fixed by extending that `.gitignore` and `git rm -r --cached`.
3. **Untrack `backend/.local-storage`** (found later, during E2E CI verification in §9 — 91 files, synthetic Phase 20 test-upload PDFs) — same class of gap, same fix (extend `backend/.gitignore`, untrack). This is the local-disk object-storage fallback's real write target (`app/core/storage.py`), never source.

No push, no remote, no deployment is claimed anywhere in this phase — only the prerequisite version-control state a CI/CD configuration needs to be meaningful at all. **A real `origin` remote must be added by the repository's owner before any of this phase's CI configuration can actually run** — see §17 for the exact remaining steps.

## 2. CI platform choice

No git remote and no existing CI config of any kind meant the master prompt's "use the repository's natural platform based on its actual remote/project configuration" could not be answered from remote info alone. Resolved using the sandbox's own global git configuration as the deciding signal: `url.https://github.com/.insteadof=git@github.com:` / `ssh://git@github.com/` rewrites, git-lfs filters, and SSH commit-signing setup all point at GitHub as this environment's own natural/expected git host.

**GitHub Actions was chosen because this is the natural platform given this sandbox's own git tooling defaults — not because any actual GitHub remote exists for this project.** A real remote must be added before any of `.github/workflows/ci.yml` can run for real.

## 3. CI pipeline (`.github/workflows/ci.yml`)

Eight jobs, every one a real hard gate — no `|| true`, no `continue-on-error`, nothing informational-only:

| Job | What it does |
|---|---|
| `secret-scan` | `detect-secrets scan --baseline .secrets.baseline` — fails only on a finding genuinely new since the committed baseline |
| `backend` | Install deps → `python -m compileall` (syntax/import validation) → full pytest suite against real PostgreSQL 16 + Redis 7 service containers → production-config tests re-run as a named, separately-visible step |
| `migrations` | Its own fresh PostgreSQL service → `alembic upgrade head` → schema sanity check → `alembic downgrade -3` / `upgrade head` round-trip → single-head check |
| `frontend` | `npm ci` → `tsc --noEmit` → `eslint .` → `npm run build`, build artifact uploaded |
| `dependency-audit` | `pip-audit` (hard gate) + `npm audit --audit-level=high` (gated at high/critical only — see §8) |
| `docker` | Builds this repo's own Dockerfile, applies migrations against a real Postgres service, starts the container, polls its **actual `HEALTHCHECK` command** via `docker inspect`, verifies `/health/ready` over the network, verifies clean shutdown on `docker stop` |
| `e2e` | Full Playwright suite against a real backend + real built frontend — see §9 |
| `ci-summary` | A single required status check (for branch protection, §16) that fails if any of the above failed |

`backend`/`frontend` gate `e2e` (`needs:`) so a ~10-minute browser suite doesn't run against units already known to be broken. Validated with `action-validator` (GitHub's own workflow JSON schema) — 0 errors — and with `yaml.safe_load` for basic syntax sanity.

## 4. Test database / Redis isolation

Every job with a database gets its own **fresh, ephemeral** `postgres:16` service container (matching the app's supported version — `psycopg[binary]>=3.1`, tested against 16 throughout this project) with its own name (`lifelink_test` / `lifelink_migrate` / `lifelink_e2e` / `lifelink` for the Docker job) — never the developer's local database, never a shared database across jobs. Same for `redis:7` service containers. This mirrors `backend/tests/conftest.py`'s own existing pattern (`TEST_DATABASE_URL`, Redis db 15, both explicitly documented as disposable) rather than inventing a new one.

## 5. Migration validation

A dedicated `migrations` job (not folded into `backend`, per the master prompt's explicit instruction): fresh Postgres → `alembic upgrade head` → a real schema-introspection check (`users` table present) → `alembic downgrade -3` / `upgrade head` round-trip (the same round-trip depth Phase 28's own migration smoke test used against real dev data) → `alembic heads | wc -l` must equal exactly 1 (catches a branching/duplicate revision — "the kind of missing-revision problem encountered earlier in the project," per the master prompt's own callout) → `alembic history --verbose` (fails loudly on a broken chain).

Locally reproduced end-to-end against a fresh `lifelink_e2e` database: all 19 revisions applied cleanly, single head confirmed.

## 6. Production-configuration CI

Rather than reinventing checks, the `backend` job re-runs `tests/test_phase28_production_config.py` and `tests/test_phase28_seed_guards.py` as their own named step — these already assert every item the master prompt's §6 lists (default/weak `SECRET_KEY` rejected, debug disabled, secure cookies enforced, unrestricted CORS rejected, dev/test seed blocked in production). No real secrets appear anywhere in `ci.yml` — every `SECRET_KEY` value used is an explicitly-labeled placeholder string (`ci-test-secret-key-not-for-production-use`, etc.), never a GitHub secret masquerading as a real credential (there is nothing to protect yet — no real deployment exists).

## 7. Secret management audit

`detect-secrets` 1.5.0 (pip-installed; PyPI reachable from this sandbox) scanned all 487 tracked files: **82 findings across 43 files**, every one individually verified by reading the flagged source line — none a real credential:

- `backend/docker-compose.yml`: documented local dev Postgres/MinIO/Neo4j credentials (`lifelink:lifelink@localhost`, `lifelink-secret`, `lifelink-dev-password`), used nowhere outside this sandbox, already flagged in that file's own header comment as dev-only.
- `backend/app/core/config.py`: the fail-closed default `SECRET_KEY` placeholder that production startup explicitly *rejects* (`_validate_secret_key`).
- `backend/scripts/phase27_loadtest.py`: a test fixture password string.
- `frontend/src/pages/shared/NotificationsCenter.tsx`: an incidental `"password"`-type notification-category keyword match, not a secret.
- Remaining findings: Alembic revision-ID hex strings (high entropy by construction, never credentials).

A `.secrets.baseline` file was generated (`detect-secrets scan --all-files $(git ls-files)`) and committed — the standard workflow: `ci.yml`'s `secret-scan` job runs `detect-secrets scan --baseline .secrets.baseline` on every future run, which fails only on a finding not already accounted for here. **No real credential was found anywhere in this repository's tracked files or `.gitignore`/`.dockerignore` configuration** (both were re-verified: `.env`/`.env.*` excluded except `.env.example`, `backups/`/`*.dump` excluded, and now `.local-storage/` too). Git history was not separately audited beyond this scan since the entire history is one phase old (this session's own initial commit) — there is no pre-existing history to have leaked something into.

## 8. Dependency security audit

**Python** (`pip-audit` 2.10.1 against `backend/requirements.txt`, 17 direct dependencies): **0 known vulnerabilities.** Hard-gated in CI (`dependency-audit` job).

**Node** (`npm audit` against `frontend/package-lock.json`): found 1 high-severity (`js-yaml` CPU-DoS, GHSA-2883-xcg3-v3hh, a transitive dev dependency of `eslint` via `@eslint/eslintrc`, never shipped to the production bundle) and 2 moderate (`react-router`/`react-router-dom`, CVE-2025-68470-adjacent open-redirect and a deserialization advisory). The high-severity finding was fixed with `npm audit fix` (non-force — a safe bump within `eslint`'s own dependency range), verified with a full `typecheck`/`lint`/`build` pass afterward (all clean, same pre-existing >500kB chunk-size warning, unrelated). The 2 moderate findings were **deliberately not force-fixed**: the real fix requires `react-router-dom` `6.30.4` → `7.18.4`, a major version with a different routing API (confirmed via `npm audit fix --force`'s own dry-run output), which the master prompt's explicit "do not upgrade merely to obtain a cleaner report" / "any dependency upgrade must be tested against the complete suite" instructions rule out as a same-phase change. `ci.yml`'s `dependency-audit` job is gated at `--audit-level=high` for exactly this reason: the disclosed, deferred moderate findings never block CI, but any new high/critical finding would. Recommended as a Phase 30 item (§20).

## 9. Playwright in CI — and two real, pre-existing bugs it caught

`ci.yml`'s `e2e` job installs Playwright's own Chromium (`npx playwright install --with-deps chromium`) rather than the sandbox's pinned browser path, starts the real backend (`uvicorn app.main:app`) and real built frontend (`vite preview`) as background processes, and **polls `/health/ready` and `/` in a loop** (no arbitrary sleep) before running the suite.

Two genuine, pre-existing bugs surfaced while building and verifying this job locally — both fixed, neither a Phase 29 regression:

1. **`playwright.config.ts` hardcoded a sandbox-only Chromium path** (`/opt/pw-browsers/chromium`), which would not exist on a GitHub Actions runner. Fixed: `executablePath` is now only set when that path exists on disk (`existsSync`), falling through to Playwright's own default resolution otherwise. Verified: sandbox behavior unchanged (the path still exists here); the same file now also works unmodified in CI. Also added an HTML reporter and screenshot/video-on-failure, gated on `process.env.CI`, for the artifact-upload step.
2. **`axe-core` was never declared as a dependency** — two accessibility spec files (`phase26_accessibility.spec.ts`, `phase27_accessibility.spec.ts`) read `node_modules/axe-core/axe.min.js` at module-load time, but `axe-core` had only ever been manually installed for a temporary Phase 26 script (`a11y_audit.mjs`, since deleted) that never persisted the dependency into `package.json`. On a fresh `npm ci` — exactly what CI does, and what this sandbox's own `node_modules` reset to after this session's container was recycled — `npx playwright test --list` threw at collection time and reported **"0 tests in 0 files" for the entire suite**, not just the two accessibility files (a single spec file throwing at import time aborts collection of everything else). Fixed by adding `axe-core` as a proper `devDependency`; `--list` now reports the expected 65 tests in 18 files again.

**A third, environment-configuration issue was found and fixed while validating the `e2e` job design itself** (reproduced live against the real backend + real built frontend, not assumed):

3. **CORS origin mismatch.** Several `e2e/*.spec.ts` files' own `APIRequestContext` calls hardcode `Origin: http://localhost:5173` (this app's `CORS_ORIGINS` *default*, from `.env.example`) to simulate a legitimate frontend origin for direct API calls, while Playwright's real browser-driven requests carry the actual serving origin (`vite preview`'s `:4173`). With only `:4173` allowed, every `APIRequestContext`-based test in `admin_notification_surface.spec.ts` and `phase23_security_hardening.spec.ts` failed its first API call with a 403 "Origin not allowed" (`app/api/deps.py:verify_origin`). Fixed: `ci.yml`'s `e2e` job sets `CORS_ORIGINS: "http://localhost:5173,http://localhost:4173"` (both).
4. **Missing demo/fixture data.** `phase26_responsive_overflow.spec.ts` explicitly asserts against the admin transport-routes table's real ~85 rows (documented in that spec file's own docstring — a real defect it originally reproduced needed real row volume). A bare `scripts/e2e_seed.py` run (5 fixture users, no facilities/routes) left that table empty, and the test timed out waiting for a row that never appeared. Fixed: the `e2e` job also runs `scripts/seed_demo_data.py` (idempotent, ~110s) before the suite.
5. **Stale local storage backend.** Without an explicit override, `S3_ENDPOINT_URL` was read from this sandbox's real `backend/.env` (pointing at a MinIO instance not running in this session), causing document-upload/certificate tests to fail against an unreachable endpoint. A real CI checkout has no `.env` file at all (gitignored, never committed), so this wouldn't occur there — but `ci.yml` sets `S3_ENDPOINT_URL`/`S3_ACCESS_KEY`/`S3_SECRET_KEY` (and `NEO4J_URI`/`USERNAME`/`PASSWORD`) explicitly empty anyway, so the job's behavior doesn't silently depend on an absence.

**Verified end-to-end, locally, with all five fixes applied and a freshly-flushed Redis** (to remove this session's own accumulated rate-limit counters — see §18's own finding about exactly this kind of accumulation): **65/65 Playwright tests passed.** A single earlier full-suite run (before a Redis flush between repeated manual verification passes) showed 64/65, with the one failure (`blood_request_idempotency.spec.ts`'s rapid-double-click test) passing cleanly in isolation — this is the exact same transient full-suite-timing flake Phase 28's own report already disclosed for this specific test, not a new regression.

Failure artifacts (Playwright HTML report, traces, screenshots, videos, backend/frontend stdout logs) are uploaded only `if: failure()`. Per §10's explicit exclusion list, nothing here ever uploads `.env`, secrets, credentials, database dumps, OTPs, or session cookies — confirmed by inspection of exactly what `actions/upload-artifact` is pointed at (Playwright's own report/trace directories and two plain stdout log files this app never writes credentials into, per Phase 28's own logging secret-leak sweep).

## 10. Test artifacts

Covered inline in §9 (Playwright) and §3 (frontend build artifact). No test-produced artifact anywhere in this pipeline contains a secret, credential, database dump, OTP, or session cookie by construction — the app itself never logs credentials (Phase 28), and the artifact paths uploaded are limited to Playwright's own report/trace output and two stdout log files.

## 11-13. Staging environment: architecture, configuration, data strategy

**No real staging infrastructure, cloud provider, server, domain, or credentials exist for this project** — consistent with Phase 28's own finding that this sandbox has a narrow outbound-network allowlist (PyPI/npm reachable; Docker Hub registry pulls return `403 Forbidden`; no `gh` CLI installed; no external cloud API reachable). Per the master prompt's explicit §13/§22 instruction, this phase does **not invent** a cloud provider, server, domain, or deployment success. What follows is the designed, documented configuration — implemented in code and scripts where that's possible without a real target, and marked **NOT-LIVE-VERIFIED** for the parts that need one.

**Design — a staging environment is a separate deployment of the exact same application** (same Dockerfile, same `app.asgi:application` entrypoint, same Alembic migrations), pointed at entirely separate infrastructure:

| Concern | Staging value | Never |
|---|---|---|
| Database | A dedicated `DATABASE_URL` (its own PostgreSQL instance/database — e.g. `lifelink_staging`), migrated the same way as production (`alembic upgrade head`, a separate deploy step — see §17) | Production PostgreSQL, ever, under any circumstance |
| Redis | A dedicated `REDIS_URL` instance | Production Redis |
| Object storage | A dedicated bucket/credentials (or the local-disk fallback, for a lighter-weight staging tier) | Production's S3/MinIO bucket or credentials |
| Secrets | A separate `SECRET_KEY` (still fail-closed-validated — Phase 28's validators don't distinguish staging from production by name, only `ENVIRONMENT=production`'s own checks; a staging deploy should still set `ENVIRONMENT=production` to get the same fail-closed guarantees, pointed at staging's own infra) | Production's `SECRET_KEY`, ever |
| Frontend origin / CORS | Staging's own frontend URL (e.g. `https://staging.lifelink.example.com`) in `CORS_ORIGINS`/`FRONTEND_BASE_URL` | Production's origin, and never a wildcard |
| Session cookies | `SESSION_COOKIE_SECURE=true` (staging should be served over real HTTPS too, for the same reasons Phase 28's audit gives for production) | — |
| Email | Either a staging-only Mailpit-equivalent, or a real SMTP account explicitly provisioned for staging (never production's Gmail sender account) | Production's Gmail credentials |

**Staging data**: per the master prompt's explicit §12 instruction, this phase does not copy real user personal data into staging and does not create fake data that could be mistaken for real production users. `scripts/seed_demo_data.py` already produces exactly this shape of data — its own accounts are under the clearly-synthetic `*.lifelink-demo.example.com` domain (Phase 27's own convention) and its 1000 synthetic donors/facilities are randomly generated, not derived from real records. The production seed guard Phase 28 added (`SEED_DEMO_ALLOW_PROD` fail-closed check) applies identically in staging: a staging deployment that sets `ENVIRONMENT=production` (recommended, above) gets the same protection against accidentally seeding demo data into what should be a real environment, and a staging-only seed mechanism (a deploy step that explicitly runs `scripts/seed_demo_data.py` against staging's own database, never production's) is the intended path.

## 14. Staging smoke test

`backend/scripts/staging_smoke_test.py` (new) implements the full 18-item checklist: frontend loads, backend responds, liveness, readiness, registration, email-verification path, login, protected route, RBAC, blood request creation, donor workflow, hospital workflow, family workflow, admin workflow, notification center, error handling, logout, frontend production build. It reuses `production_smoke_test.py`'s existing infra-level checks (rather than duplicating them) and adds the role-workflow checks that script doesn't cover.

Run against a `--self-boot` local instance (this sandbox's own PostgreSQL/Redis, no external staging target exists): **18/18 checks passed** — genuinely exercised over real HTTP against the real running app, not simulated. Two real bugs in the script itself were found and fixed while verifying it (documented in its own commit message and code comments): a reused-cookie client that made the "unauthenticated" check accidentally test "wrong role" instead, and a `--self-boot` code path that silently ignored `--frontend-url`.

Per the master prompt's explicit §14 instruction, this never requires real Gmail — the email-verification check works by confirming the resend-verification endpoint is wired and responds cleanly, not by reading a real inbox. Classified **LOCAL-VERIFIED**. Running the same script with `--base-url`/`--frontend-url` against a real staging deployment, once one exists, would make it **STAGING-VERIFIED** — no code change needed.

## 15. Release artifact

**Backend**: `requirements.txt` has loose `>=` pins (no lock file — the project's existing convention, unchanged this phase per "do not introduce unnecessary build complexity" and "do not modify unrelated functionality"; `pip-audit`'s clean result (§8) means this isn't currently a liveness risk, though a `pip-compile`-style lock file remains a reasonable Phase 30 recommendation for fully deterministic installs). `backend/Dockerfile` produces the deployable image; `ci.yml`'s `docker` job builds it in CI on every run as a build-correctness gate.

**Frontend**: `package-lock.json` already gives deterministic installs (`npm ci`, exact versions); `npm run build` (`tsc -b && vite build`) is deterministic given the same lockfile and Node version — verified by running it 3 separate times this phase (once per E2E verification attempt) and confirming output is bit-identical in asset naming (Vite's content-hash-based filenames) and produces the same chunk-size warning each time.

## 16. Docker/container pipeline

Phase 28's Dockerfile was re-audited, unchanged: non-root (`appuser`, uid 1000), correct `WORKDIR /app`, correct ASGI entrypoint (`app.asgi:application`), `HEALTHCHECK` against `/health/live` using only the stdlib (no extra package installed just for this), no secrets baked in (`.dockerignore` excludes `.env`/`.env.*`/`backups/`), minimal packages (`build-essential libpq-dev`, removed from apt lists after install), predictable single-process startup.

`ci.yml`'s `docker` job builds the image in CI, applies migrations against a real Postgres service via `docker run --rm ... alembic upgrade head`, starts the container, and **polls the container's own actual `HEALTHCHECK` status** (`docker inspect --format='{{.State.Health.Status}}'`) rather than re-implementing a separate check — proving the exact directive shipped in the image works, not a proxy for it. Also verifies `/health/ready` reachable over the network and a clean exit on `docker stop` (graceful SIGTERM shutdown).

**This sandbox's own `docker build` remains blocked** — `dockerd` itself starts successfully this phase (a change from Phase 28, where the daemon's own availability wasn't separately distinguished from registry access), but `docker pull python:3.12-slim` returns `403 Forbidden` from `registry-1.docker.io` (confirmed via the agent proxy's own diagnostic tooling: "The destination host is not allowed by your organization's egress policy for this session. Do not retry or route around it — report the blocked host," which this phase followed — no alternate registry or workaround was attempted). **This refines Phase 28's finding with more precise evidence: the restriction is at the container-registry level, not Docker daemon availability.** A GitHub Actions-hosted runner has ordinary internet access including Docker Hub by default, so `ci.yml`'s `docker` job is expected to run successfully there without modification — classified **NOT-LIVE-VERIFIED** here, for the reason stated, not invented.

## 17. Database migration during deployment

Documented order: **migration, then application start, as a separate, explicit step** — never a side effect of application boot. This repo's dev `docker-compose.yml` runs `alembic upgrade head && uvicorn ...` as its container `command` (fine for a disposable dev database, explicitly flagged in that file's own header comment as not appropriate for production), but the Dockerfile's own production `CMD` does **not** run migrations automatically — matching Phase 28's own deployment checklist, which already established the separate-step pattern this phase's `ci.yml` `migrations`/`docker` jobs follow (`alembic upgrade head` run explicitly, as its own step, before the application starts).

No destructive migration operation is placed in any deployment script or CI job — `alembic downgrade` only ever runs in the dedicated `migrations` CI job's own round-trip check, against that job's own disposable database, never against anything resembling production.

## 18. RQ worker deployment — and the stale-job finding, addressed for real

**Worker**: `python -m app.workers.worker` (`app/workers/worker.py`), listening on the `emails` and `sms` named queues, `with_scheduler=True`. Graceful shutdown: RQ's `Worker.work()` handles `SIGTERM`/`SIGINT` by finishing the in-flight job before exiting — no custom signal handling needed or added.

**Phase 28 found 148,965 stale queued email jobs, accumulated with no automatic bound.** This phase was explicitly instructed not to ignore this. What was actually done, in order:

1. **Independently reproduced the exact same class of problem**, live, on this session's own Redis instance: 8,893 queued email jobs found in `rq:queue:emails`, and — checked directly with `redis-cli ttl` on a sample job hash — **`ttl == -1` (never expires)**. This confirmed the root cause precisely: `email_service._enqueue()` called `Queue.enqueue()` with no `ttl`/`result_ttl`/`failure_ttl` at all, so a job's Redis hash never expires in any state (queued, succeeded, or failed).
2. **Fixed the root cause for every future job**: three new `Settings` fields (`email_job_queue_ttl_seconds=86400`, `email_job_result_ttl_seconds=3600`, `email_job_failure_ttl_seconds=604800`), passed to every `enqueue()` call. The 24-hour queue TTL specifically matches this app's own `EMAIL_VERIFICATION_TTL_SECONDS` default — an email job this stale is chasing a token that has likely already expired, so bounding it this way is consistent with existing app semantics, not an arbitrary new cutoff.
3. **Added queue-depth visibility**: `app/workers/queue.py:email_queue_health()`, wired into `GET /admin/system/health` as a new `rq` component (`queued`/`failed`/`started`/`oldest_queued_job_age_seconds`). Deliberately informational and non-blocking — it never flips overall system status on its own, exactly like the existing `graph`/`sms` components' "not configured is not a fault" pattern — but surfaces a human-readable `reason` past a conservative depth/failure threshold, so an operator watching this endpoint (or wiring an external monitor to it) sees the backlog building before it reaches Phase 28's scale.
4. **Built an explicit, operator-invoked maintenance tool** (`scripts/rq_queue_maintenance.py`) rather than any automatic cleanup — per the master prompt's explicit "do not blindly delete jobs as part of deployment; if cleanup is needed, make it explicit and safe" instruction. `--report` (default) is read-only. `--purge-failed`/`--purge-stale-queued` require `--confirm` (a bare invocation is a dry run that only prints what would be removed). Never run automatically by any deployment script, CI job, or startup path.
5. **7 new backend tests** (`tests/test_phase29_rq_hardening.py`) cover: enqueued jobs actually carry the configured TTLs, the health endpoint reports real counts, a deep-but-healthy backlog never flips overall status, and the maintenance script's dry-run/confirm/report behaviors are all correct — including a real bug the tests themselves caught in the script (`Queue.remove()` only pops the queue's Redis list entry, not the job's own hash — fixed to also call `job.delete()`).
6. **Cleared this session's own accumulated debris** (`redis-cli -n 0 flushdb` on the dev Redis instance) — the same class of action, and the same justification, Phase 28 used for its own 148,965-job cleanup: known load-test/manual-verification debris in a dev sandbox, not real application data, explicitly not a production remediation pattern.

**Operational runbook** (for `docs/LIFELINK_PHASE_29_CI_CD_STAGING_REPORT.md`'s own recommendations, and any future deployment): run `python -m scripts.rq_queue_maintenance --report` periodically (or wire `/admin/system/health`'s `rq` component into an external monitor) to watch queue depth; a sustained, growing backlog past a few thousand queued jobs is a signal that either the worker process isn't running/keeping up, or a burst of activity (a real incident, or another load test) needs investigating — not a reason to reflexively purge.

## 19. Branch protection

No `gh` CLI is installed in this sandbox, no GitHub remote exists for this project, and no GitHub API credentials are configured — so **no repository setting of any kind could be verified or changed this phase**, and none is claimed to have been. Documented recommendation only, for whoever connects a real remote (see §17's report equivalent for the full list): require `ci-summary` (the single required-status-check job) to pass before merge, require at least one review, disallow direct pushes to `main`, disallow force-push to `main`.

## 20. CI performance baseline

First obtained, not assumed, per the master prompt's explicit "do not optimize prematurely — first obtain a baseline" instruction:

| Stage | Measured locally (this sandbox) |
|---|---|
| `pip install -r requirements.txt -r requirements-dev.txt` | ~15s (warm PyPI cache) |
| Full backend pytest suite (808 tests) | 487-503s (~8-8.5 min), consistent across 3 separate full runs this phase |
| `npm ci` | ~1-2s (small dependency tree, 13 deps + 15 devDeps) |
| `npm run build` | 5-7.5s |
| `scripts/seed_demo_data.py` | ~110s (1000 synthetic donors + 85 transport routes + facilities) |
| Full Playwright suite (65 tests, single worker, `fullyParallel: false`) | ~4.2 min |

**Safe improvements identified from this baseline** (implemented in `ci.yml`): pip dependency caching (`actions/setup-python`'s `cache: pip`, keyed on `requirements*.txt`), npm dependency caching (`actions/setup-node`'s `cache: npm`, keyed on `package-lock.json`), Playwright browser install scoped to `chromium` only (not all 3 browsers) since that's the only project configured. **Not done**: parallelizing the Playwright suite itself (`fullyParallel: false`/`workers: 1` is the project's own existing, deliberate choice — several spec files share backend state via the same fixture accounts, e.g. rate-limit counters, and Phase 29 found real evidence of exactly this kind of cross-test state bleed during its own verification (§18's Redis-accumulation finding, and a registration-rate-limit 429 that only appeared after repeated manual runs) — parallelizing without first re-auditing every spec file's independence would trade reliability for speed with no new evidence backing that trade, which the master prompt explicitly forbids).

## 21. CI failure-safety verification

Each stage type's actual failure behavior was verified to genuinely fail the pipeline, not silently pass, by reasoning from the exact command each step runs (a broken migration → `alembic upgrade head` exits non-zero → step fails → job fails; a backend test failure → `pytest` exits non-zero without `-x`/`--continue-on-collection-errors` suppression → job fails; TypeScript/ESLint/build failures → each command's own non-zero exit is the step's exit code, no `|| true` anywhere in `ci.yml`; a production-config violation → the re-run `test_phase28_production_config.py` step fails independently of the full-suite step; a Playwright failure → `npx playwright test` exits non-zero on any failed test) and by directly grepping the finished `ci.yml` for `|| true`, `continue-on-error`, and `-f` (force flags) — **none present anywhere in the file.** `ci-summary`'s own logic (`contains(needs.*.result, 'failure')`) additionally guards against a single job's failure being masked if a future edit ever adds a job without wiring it into that check correctly.

## 22. No fake deployment — what is and isn't claimed

Following the master prompt's mandatory instruction precisely: this sandbox lacks GitHub/GitLab credentials, a real GitHub remote, cloud credentials, a staging server, DNS, external SMTP beyond a local dev catcher, Neo4j access beyond a real-but-unreachable Aura instance, and S3/MinIO access. **Nothing here claims those services were deployed or tested.** Every check performed either ran against a real local instance of this app (self-boot, or CI service containers) or is explicitly marked NOT-LIVE-VERIFIED with the exact reason and the exact configuration required to complete verification later (§16 for Docker registry access, §11-14 for a real staging target, §19 for GitHub API access).

## 23. Full regression

Run after every code change this phase, not just once at the end:

- **Backend**: 807 passed, 1 skipped (pre-existing, carried forward unchanged from Phase 28), 0 failures. 808 total tests (801 Phase 28 baseline + 7 new `test_phase29_rq_hardening.py` tests). Confirmed via 3 separate full runs across this phase (after the RQ hardening change, after the axe-core/CORS fixes, and as this section's own final check).
- **Frontend**: `tsc --noEmit` clean, `eslint .` clean, `npm run build` clean (same pre-existing >500kB main-chunk warning, unrelated to this phase).
- **Playwright**: 65/65 passed on the clean/final run (64/65 on an earlier run before a Redis flush between repeated manual verification passes removed accumulated rate-limit state — the single failure there was the same known transient full-suite-timing flake Phase 28 already disclosed for that exact test, confirmed passing cleanly in isolation both times).
- **Migrations**: fresh DB → head (19/19 revisions) → single-head check, verified locally exactly as `ci.yml`'s `migrations` job does it.
- **Production-config tests**: `tests/test_phase28_production_config.py` + `tests/test_phase28_seed_guards.py`, both included in and passing as part of the 807.
- **Docker**: build itself NOT-LIVE-VERIFIED here (registry blocked, §16); the exact `CMD`/`HEALTHCHECK` this phase's `docker` job would exercise were independently verified via this session's own self-boot smoke tests instead.
- **CI-equivalent local commands**: every command `ci.yml` runs was run locally at least once this phase in the same sequence (dependency install → syntax check → tests → migration checks → frontend checks → E2E) as the most direct available substitute for actually triggering the workflow on GitHub (which requires a real remote this sandbox cannot create).
- **ruff/mypy baseline**: 101 ruff findings / 5 mypy findings, both counts unchanged from Phase 28's own baseline, confirmed zero overlap with any file this phase touched (backend admin routes/schemas/config/services/workers, both new scripts, both new test files).

No regression caused by this phase's own changes was found uncorrected — the two script bugs found in `staging_smoke_test.py` and the missing-`axe-core`/CORS/demo-data/stale-storage gaps found while building the `e2e` CI job were all fixed within this same phase, not carried forward as known issues.
