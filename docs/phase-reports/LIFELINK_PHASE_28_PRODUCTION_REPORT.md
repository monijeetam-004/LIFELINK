# LIFELINK — Phase 28: Production Environment, Database & Deployment Hardening — Report

Companion to `LIFELINK_PHASE_28_PRODUCTION_AUDIT.md`, which has the full narrative and evidence per area. This document is the classification and final gate.

## Classification key

- **FIXED** — a real gap existed and was closed this phase, with evidence.
- **PARTIALLY FIXED** — improved, but a known limitation remains (stated, not hidden).
- **NOT FIXED** — identified, not addressed this phase (with a reason).
- **LIVE-VERIFIED** — tested against the real external system in this environment.
- **LOCAL-VERIFIED** — tested against a real local stand-in (e.g. `aiosmtpd` for Mailpit) or a real local boot of the app itself.
- **CODE-VERIFIED** — confirmed correct by direct source-code inspection; the live system was not reachable to test end-to-end.
- **NOT-LIVE-VERIFIED** — the real external system could not be reached from this sandbox; the code/config path was independently confirmed correct where possible.
- **BLOCKED BY ENVIRONMENT** — the specific reason a check could not be LIVE-VERIFIED here.

## Quality gate

| Area | Status | Evidence | Remaining limitation |
|---|---|---|---|
| Backend | FIXED | 801 pytest tests, 0 failures, exit code 0 | — |
| Frontend | FIXED | `tsc -b && vite build` clean; `eslint .` clean | Large main JS chunk (>500kB) — pre-existing, not a Phase 28 regression |
| Authentication | FIXED (pre-existing, re-verified) | Fail-closed SECRET_KEY validators (§2 audit); real login/logout/password-change tested via `production_smoke_test.py` and Playwright | — |
| Authorization | FIXED (pre-existing, re-verified) | `protected_routes_reject_unauthenticated` check passes against a real prod-config boot; Phase 23 IDOR/RBAC Playwright suite still passes | — |
| PostgreSQL | FIXED | connect/statement/pool timeouts added and live-verified (`SHOW statement_timeout` = 30s); real migration smoke test (fresh DB + downgrade/upgrade round-trip) | `pool_size`/`max_overflow` deliberately unchanged — see Scalability row |
| Redis | FIXED | Explicit connect/socket timeouts added to both clients; verified safe for RQ's blocking dequeue by reading RQ's own source | Email queue has no depth bound — see Phase 29 recommendation |
| RQ | PARTIALLY FIXED | Worker construction against real Redis verified (`rq_worker_can_start` check); retry policy pre-existing and unchanged | 148,965 stale jobs found accumulated with no automatic bound (new Phase 28 finding, not fixed this phase) |
| SMTP | LOCAL-VERIFIED (design), NOT-LIVE-VERIFIED (real Gmail) | Real message sent through `_smtp_send` to a local `aiosmtpd` server; Gmail sender-vs-user-password architecture confirmed correct by code review | BLOCKED BY ENVIRONMENT — no outbound network to `smtp.gmail.com` in this sandbox |
| File storage | CODE-VERIFIED, NOT-LIVE-VERIFIED (S3/MinIO) | Path-traversal guard, presigned URLs, credential handling all reviewed and unchanged (already correct) | BLOCKED BY ENVIRONMENT — no MinIO/S3 reachable in this sandbox |
| Neo4j | CODE-VERIFIED, NOT-LIVE-VERIFIED | Driver lifecycle, credential handling, timeout, fallback all reviewed and unchanged (already correct) | BLOCKED BY ENVIRONMENT — confirmed as a general sandbox DNS/egress restriction, not Neo4j-specific |
| CORS | FIXED (pre-existing, re-verified) | Real preflight from configured origin allowed; real preflight from arbitrary origin rejected (`production_smoke_test.py`) | — |
| Sessions | FIXED (pre-existing, re-verified) | Fail-closed cookie-security validators; Phase 23 Playwright suite (HttpOnly, logout invalidation, password-change invalidation) still passes | — |
| HTTPS | NOT FIXED (documented, not this app's layer) | — | This backend does not terminate TLS; now explicitly documented in the deployment checklist rather than left implicit |
| Security headers | FIXED | Pre-existing headers re-verified live on a real response; CSP gap (Phase 23) closed this phase with real browser evidence (0 violations after fix) | `frame-ancestors` cannot work via `<meta>` (CSP spec limitation) — documented as a static-host-layer responsibility |
| Logging | FIXED | New request-ID-correlated structured logging, wired and live-tested end-to-end; secret-leak grep re-run clean | — |
| Health checks | FIXED | New `/health/live` and `/health/ready`, 5 new tests, optional-dependency isolation verified via monkeypatch | — |
| Migrations | FIXED | Real fresh-DB upgrade + downgrade/upgrade round-trip performed, zero-diff schema comparison | — |
| Backups | FIXED | `scripts/db_backup.sh` implemented, no hardcoded credentials, gitignored output | — |
| Restore | FIXED | `scripts/db_restore.sh` implemented with fail-closed prod guard; a REAL restore drill performed against real dev data with row-level verification | — |
| ASGI deployment | FIXED | `app.asgi:application` boot + graceful SIGTERM re-verified live; Dockerfile hardened (non-root, healthcheck, correct entrypoint) | Full `docker build` NOT-LIVE-VERIFIED — see below |
| Production frontend build | FIXED | `npm run build` succeeds repeatedly, including with the new CSP plugin; verified as part of `production_smoke_test.py --self-boot` | — |
| Scalability | PARTIALLY FIXED (carried forward, not re-litigated) | N+1 fix's bounded-query-count regression test re-confirmed passing; light concurrency sanity checks clean | Residual Redis-fan-out-under-extreme-contention limitation from Phase 27 stands; no new pool/worker topology evidence gathered this phase (by design) |
| Accessibility | FIXED (pre-existing, re-verified) | Phase 27's 65/65 Playwright suite re-run this phase, including all accessibility specs | — |
| Error handling | FIXED (pre-existing, re-verified) | `errors_do_not_leak_internals` and `malformed_request_rejected_cleanly` checks pass against a real prod-config boot | — |
| Tests | FIXED | 801 backend + 65 Playwright (see below) | — |

## Test / check results

| Check | Result | Verification level |
|---|---|---|
| Backend tests | 801/801 passed, 1 skipped (pre-existing), exit 0 | LIVE-VERIFIED (real Postgres/Redis) |
| Playwright | 65/65 passed (1 showed a transient timing flake in the full-suite run, confirmed to pass cleanly on isolated re-run — a rapid-double-click UI timing test unrelated to any Phase 28 change) | LIVE-VERIFIED (real backend + real built frontend) |
| TypeScript | Clean (`tsc -b`, part of `npm run build`) | LIVE-VERIFIED |
| ESLint | Clean (`eslint .`) | LIVE-VERIFIED |
| Frontend build | Succeeds, including the new CSP plugin | LIVE-VERIFIED |
| Migration smoke test | Fresh DB → head (19/19 revisions, 0 errors); downgrade(-3) → upgrade(head) round-trip clean; schema diff = 0 | LIVE-VERIFIED |
| Production startup | `app.asgi:application` boots with real `ENVIRONMENT=production` config, becomes healthy, self-boot smoke test 18/18 | LIVE-VERIFIED |
| Health | `/health` re-verified unchanged | LIVE-VERIFIED |
| Readiness | `/health/ready` new, 5/5 dedicated tests + live self-boot check | LIVE-VERIFIED |
| Backup/restore | Real backup of real dev data (1347 users, etc.), real restore into a disposable DB, row-level diff verified | LIVE-VERIFIED |
| Neo4j | Code path correct; live connectivity not reachable | NOT-LIVE-VERIFIED (BLOCKED BY ENVIRONMENT) |
| Storage (S3/MinIO) | Code path correct; live connectivity not reachable | NOT-LIVE-VERIFIED (BLOCKED BY ENVIRONMENT) |
| SMTP | Design correct; real transport tested against a local stand-in | LOCAL-VERIFIED; Gmail itself NOT-LIVE-VERIFIED (BLOCKED BY ENVIRONMENT) |
| Docker build | Dockerfile reviewed correct; exact `CMD` command independently boot-tested outside Docker; `docker build` itself blocked | NOT-LIVE-VERIFIED (BLOCKED BY ENVIRONMENT — registry egress) |

## Ruff / mypy baseline (carried forward honestly)

- `ruff check .`: 101 findings, all in files untouched by Phase 28 (confirmed by cross-referencing every finding's file path against every file Phase 28 touched — zero overlap). All files Phase 28 created or modified are individually ruff-clean.
- `mypy app/ scripts/`: 5 findings — 3 pre-existing (`app/api/routes/verifications.py` ×2, `app/api/routes/donors.py` ×1, carried forward from prior phases) plus 2 in `scripts/phase27_loadtest.py` (pre-existing, Phase 27, untouched this phase). All files Phase 28 created or modified are individually mypy-clean.

## Final status: **GO-WITH-LIMITATIONS**

Every critical production-safety mechanism this phase was asked to verify is either FIXED with live evidence, or was already correct and is now confirmed and documented rather than assumed. The limitations that keep this from an unqualified GO are exactly the ones this sandbox's network restrictions make impossible to close here (real Neo4j, real Gmail SMTP, real S3/MinIO, a full `docker build`) plus the two carried-forward, honestly-stated operational gaps (RQ email-queue growth has no bound; the Phase 27 scalability limitation under extreme synthetic contention stands, not re-litigated without new evidence). None of these is a defect discovered in this phase's own new code — each is either an explicit environment restriction (independently confirmed via three unrelated probes, §17 of the audit) or an intentional, disclosed scope boundary. Per the master prompt's own guidance, this is the honest classification, not a downgrade to be avoided: a **GO-WITH-LIMITATIONS** backed by real evidence is preferable to an unverifiable **PRODUCTION READY** claim.
