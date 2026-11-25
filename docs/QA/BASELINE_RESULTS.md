# Baseline results

**Code under test.**

* Recorded **before any change in this round**, on tag `qa-baseline-2026-10-20` = commit `0422954`.
* The working tree was clean (`git status` empty) when the tag was created.
* The backend run and the static checks used this code directly. The vitest baseline ran in a `git worktree` of the tag.

**Environment.**

* Sandbox VM: 2 vCPUs, 8 GB RAM, Linux.
* Python 3.11.17 (backend venv from `requirements-dev.lock`).
* Node 22.22.0 / npm 10.9.4. CI uses Node 20.
* PostgreSQL 16, Redis 7.0.15, clamd (ClamAV 1.5.4, project signatures).
* MinIO RELEASE.2025-04-22T22-12-26Z and Mailpit v1.21.8 (official binaries).
* Playwright's pinned Chromium.

## 1. Commands and results

| Check | Exact command | Start → end (UTC) | Result | Evidence |
|---|---|---|---|---|
| Backend suite | `CLAMD_TEST_REQUIRED=1 S3_TEST_*=<real MinIO> SMTP_TEST_*=<real Mailpit> QA_ROUTE_LOG=… .venv/bin/python -m pytest -p no:cacheprovider -o addopts= -q -rs --junitxml=…` | 12:09:16 → 12:42:24 (33 min 02 s) | **1,396 passed, 1 failed, 1 skipped** (1,398 collected) | `evidence/baseline/backend_baseline.log`, `backend_junit_baseline.xml`, `routes_baseline.log` |
| Frontend unit tests | `npx vitest run` (worktree of the tag) | 14:21 | **170 / 170 passed** (22 files) | `evidence/baseline/vitest_baseline.log` |
| Browser E2E | `npx playwright test` with the CI-identical harness | Not re-run at the tag (see the note below) | **106 passed, 2 skipped, 0 failed** on the same application code (2026-10-13) | `docs/final-verification/evidence/external/regression/playwright_ci_harness_run.log`, `playwright_junit.xml` |
| Lint (backend) | `.venv/bin/ruff check .` | 12:11 | All checks passed | `evidence/baseline/static_backend.txt` |
| Formatting (backend, informational) | `.venv/bin/ruff format --check .` | 12:11 | 179 files would be reformatted (pre-existing; not a CI gate) | same |
| Migrations | `alembic heads`; `alembic branches`; `alembic check` on a freshly migrated database | 12:11 | One head `e1f2a3b4c5d6`; no branches; "No new upgrade operations detected" | same |
| Type check (frontend) | `npx tsc --noEmit` | 12:11 | exit 0 | `evidence/baseline/static_frontend.txt` |
| Lint (frontend) | `npx eslint .` | 12:11 | exit 0 | same |
| Build (frontend) | `npm run build` | 12:11 | built in 10.9 s; one Vite chunk-size warning | same |
| Python dependency audit | `pip-audit -r requirements.lock --no-deps --disable-pip` | 12:12 | No known vulnerabilities | `evidence/security/pip_audit_requirements_lock.txt` |
| npm audit (shipped bundle) | `npm audit --omit=dev` | 12:12 | 0 vulnerabilities | `evidence/security/npm_audit_prod.txt` |
| npm audit (full tree) | `npm audit`; `npm run audit:gate` | 12:13 | 7 advisories (5 high, 2 moderate), all dev tooling; the gate passes (1 high allowlisted, dev-only) | `evidence/security/npm_audit_all.txt`, `npm_audit_gate.txt` |
| Static security analysis | `bandit -r app` | 12:14 | 0 high, 1 medium, 4 low | `evidence/security/bandit_app.txt` |

**Why the Playwright baseline was not re-run at the tag.**

* Between the 2026-10-13 run and the tag, the only changes were to documentation, the paper, `scripts/export_dataset.py` (not imported by the app) and the README.
* The application code under test was therefore identical.
* Re-running the 10-minute suite would have shown nothing new, so the recorded run is used as the baseline.
* The final run of this round (FINAL_AUDIT_REPORT.md §4) is a fresh execution.

## 2. The one baseline failure (pre-existing)

* **Test.** `tests/test_phase25_qa_hardening.py::test_alembic_head_schema_matches_orm_metadata`.
* **Error.** `/usr/bin/python3: No module named alembic.__main__`.
* **Cause.** The test started its subprocess with the bare name `python3`. When pytest runs as `.venv/bin/python -m pytest` without an activated venv, that name resolves to the system interpreter.
* **Classification.** A **pre-existing** test-portability defect, also seen in the gap-elimination baseline (FINAL_GAP_MATRIX G0, recorded there as an environment problem). It is not a migration defect: `alembic check` passed in the same environment.
* **Fixed in this round** as defect D6 (DEFECTS_FIXED.md).

## 3. Skipped

`tests/test_gap_neo4j.py::test_real_neo4j_paths_equal_relational_paths` needs a live Neo4j server (`NEO4J_TEST_URI`). None is obtainable here; see INTEGRATION_STATUS.md.

## 4. Separating old failures from new ones

| Failure | Present at baseline? | Introduced by this round? | Final state |
|---|---|---|---|
| D6 interpreter portability | Yes | No | Fixed |
| D1 donor double-booking race | Yes (latent; no test covered it) | No | Found by a new test; fixed |
| D2–D5 | Yes (latent) | No | Found by new checks; fixed |
| Anything else | — | — | No other failure in the final runs |
