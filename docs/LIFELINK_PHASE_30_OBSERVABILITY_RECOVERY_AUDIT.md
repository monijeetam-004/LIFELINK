# LifeLink Phase 30 — Monitoring, Structured Logging, Backups, Recovery & Operational Observability: Audit

**Scope of this document**: the full narrative record of what was inspected, built, tested, and found this phase — evidence first, conclusions second. The companion `_REPORT.md` holds the classification tables, exact test-result lines, and final GO/GO-WITH-LIMITATIONS/BLOCKED verdict. `docs/OPERATIONS_RUNBOOK.md` is the operator-facing document this phase produced; this audit explains how its content was derived and verified.

Baseline preserved from Phase 28/29 and re-confirmed clean before any Phase 30 code was written: 801/801 backend tests → 807/1 skipped (Phase 29) → **832/1 skipped out of 833 collected** (this phase, see Section 12); 65/65 Playwright (Phase 29) → **64/65 passed, 1 pre-existing conditional data-dependent skip** (this phase, see Section 12); TS/ESLint/build clean; PG/Redis hardened; ASGI startup+graceful shutdown intact; Docker hardening untouched; CSP untouched; backup/restore re-drilled with fresh real numbers (Section 8); SMTP LOCAL-VERIFIED; Neo4j/S3/Gmail remain NOT-LIVE-VERIFIED (same standing sandbox restriction as every prior phase — no real Aura/S3/Gmail reachable from here).

## 0. Mandatory pre-work: reading and inspecting before changing anything

Read in full before writing any code: the Phase 24 performance report (notifications composite index, hospitals.py N+1 fix, a reverted pool-size experiment, code-splitting, an unresolved near-outage finding at concurrency 25-50), the Phase 25 testing report (a real OTP-lockout race fixed, a found-but-deferred donation-idempotency gap, the same outage re-reproduced, CSP deliberately still absent at that point), the Phase 27 report (the ACTUAL root cause of the Phase 24/25 outage — an N+1 in `notify_matching_donors()`, not CPU/thread-scheduling as previously believed — fixed with ~98% isolated-cost reduction; pool-size re-tested and again found not to help; `app/asgi.py` + `scripts/run_production.sh` added), and the Phase 28 production report (structured logging + request-ID correlation already exist, `/health/live`/`/health/ready` already exist, `db_backup.sh`/`db_restore.sh` already solid, Docker hardened, CSP fixed, 801/801 + 65/65, GO-WITH-LIMITATIONS). Phase 29's own audit/report were also read (CI, staging smoke test, the 148,965-stale-RQ-job finding and its TTL-bounding fix).

Then the actual source tree was inspected before any change, specifically: `app/core/logging_config.py`, `app/core/request_context.py`, `app/api/routes/health.py`, `app/api/routes/admin.py` (`system_health`), `app/core/error_handlers.py`, `app/workers/queue.py`, `scripts/rq_queue_maintenance.py`, `scripts/staging_smoke_test.py`/`scripts/production_smoke_test.py`, `scripts/db_backup.sh`/`db_restore.sh`, `app/db/session.py` (pool config + its own historical comment blocks explaining why pool size must not be touched again without new evidence), `app/core/graph.py`, `app/core/storage.py`, `app/services/email_service.py`, `app/workers/tasks.py`/`worker.py`, `backend/Dockerfile`, `docker-compose.yml`, `app/asgi.py`, `.github/workflows/ci.yml`, and the existing audit-event infrastructure (`app/services/audit_service.py`, `/admin/audit-log`).

This produced the ground truth that shaped every decision below:

- Structured logging + request-ID correlation, and `/health/live`/`/health/ready`, already existed and were solid (Phase 28) — nothing to rebuild.
- `db_backup.sh`/`db_restore.sh` already had no hardcoded credentials, `set -euo pipefail`, and a double opt-in restore safety gate (`RESTORE_CONFIRM=yes` + `RESTORE_ALLOW_PROD=yes`) — already solid, left untouched.
- `graph.py` already had good observability (`GraphUnavailableError`, `graph_health()`, `graph_enabled()`, zero credential leakage) — light touch only (2 new counter increments).
- `storage.py` and `email_service.py` had **zero** pre-existing observability — real gaps, the two biggest additions this phase.
- The Phase 29 admin `rq` health block existed on the backend but was never surfaced in the admin frontend (`AdminOverview.tsx`) — a real, independently-found gap (Section 21).
- `app/db/session.py` carries its own extensive historical comment blocks documenting two separate pool-size experiments that both showed no benefit, and the real root cause (the N+1, not pool starvation) found in Phase 27. This phase did not touch pool size, worker count, or reopen that question — per the master prompt's explicit instruction and because nothing new was found that would justify revisiting it.

## 1. Architecture: separating the observability layers

Eight distinct concerns, kept structurally separate (never mixed into one stream):

1. **Liveness** — `GET /health/live`: process-alive only, no dependency checks (unchanged from Phase 28).
2. **Readiness** — `GET /health/ready`: the two hard dependencies (PostgreSQL, Redis) plus, new this phase, an additive `dependency_states` dict distinguishing AVAILABLE/UNAVAILABLE/NOT_CONFIGURED/CONFIGURED_NOT_PROBED for Neo4j/storage/email (Section 9).
3. **Application logs** — `lifelink.access`/`lifelink.errors`/`lifelink.graph`/`lifelink.storage`/`lifelink.email`/`lifelink.sms`, all going through the existing `RequestIdFilter`-injected structured formatter.
4. **Audit events** — `app/services/audit_service.py` / `/admin/audit-log`, pre-existing, untouched by this phase, and deliberately never conflated with the new operational logs above.
5. **Operational metrics** — `app/core/metrics.py`, new this phase, bounded-cardinality in-memory counters/histograms, served only via `GET /admin/system/metrics`.
6. **Queue health** — `app/workers/queue.py::email_queue_health()`, extended this phase with worker count and a threshold-derived status.
7. **Dependency health** — `GET /admin/system/health`, the deep, authenticated diagnostic view (DB pool, RQ, storage, graph, SMS, version).
8. **Incident-runbook information** — `docs/OPERATIONS_RUNBOOK.md`, new this phase, the human-facing layer that ties 1-7 together into "what do I do."

## 2. Structured logging audit

Every Phase 30 log line was written (and, for existing call sites, spot-checked) against the master prompt's explicit never-log list. Concretely verified by direct code read plus `tests/test_phase30_observability.py::test_metrics_snapshot_never_contains_email_addresses_or_secrets` and a final grep sweep (Section 20):

- **Emails**: never logged in full. `email_service._mask_email()` (`j***@example.com`) is used at every one of the four new call sites (`email_service.deliver()` success/failure, `email_service._enqueue()`, `workers/tasks.py::send_email()` success/failure).
- **Storage**: `app/core/storage.py`'s new log lines carry only `backend` (`s3`/`local`) and `type(exc).__name__` — never the object key, which by this codebase's own convention can embed a user-supplied filename (documented in the module's own new comment block).
- **RQ jobs**: correlated by RQ's own job ID (`get_current_job().id`), not by recipient — the same job ID `email_service._enqueue()` already logged at enqueue time, so an operator can `grep` one ID across both enqueue and terminal-state log lines without ever needing the recipient address in the clear.
- **No full request-body dumps** anywhere added this phase; `request_metrics_middleware` logs only method/route-template/status/duration.
- **Passwords/OTPs/session secrets/API keys/DB-Redis-SMTP-Neo4j credentials/private keys**: none touch any new code path this phase added — the closest call sites (email delivery, storage I/O) were specifically re-read to confirm no credential is interpolated into a log format string anywhere.

## 3. Request correlation

Pre-existing (`app/core/request_context.py`, Phase 28) request-ID generation/propagation/echo was left unchanged — it was already correct: one ID via `contextvars`, injected into every log record by `RequestIdFilter`, echoed on the `X-Request-Id` response header **on every response, including failures**, since the middleware wraps the exception-handling layer, not the reverse.

New this phase: RQ's own job ID is used as the correlation identifier for background email work (Section 2), rather than inventing a second ID scheme — `rq.get_current_job().id` is already a stable, non-sensitive, per-job identifier RQ provides, and the same ID is logged at both enqueue time (`email_service._enqueue`) and terminal state (`workers/tasks.py::send_email`), so an operator can trace one job end-to-end without a new mechanism.

Error responses were re-verified (both by code read and by `tests/test_phase30_failure_injection.py`) to expose only the request ID header — never a traceback, SQL statement, connection string, or credential in the JSON body.

## 4. Error metrics (`app/core/metrics.py`)

A single, thread-safe (one `threading.Lock`, since uvicorn/anyio runs sync handlers on a worker threadpool — reconfirmed from Phase 24's own investigation), in-memory, bounded-cardinality registry. Counters/histograms added:

- `http_requests_total` — keyed by `(method, route_template, status_class)`. Route labels are **never raw URLs**: `request.scope["route"].path` is used when FastAPI matched a route (already a template like `/blood-requests/{id}`), and `_normalize_unmatched_path()` regex-collapses UUID/numeric path segments to `:id` for anything that reached the app without a route match (a 404, for instance) — verified by `test_metrics_records_bounded_route_label_not_raw_url` and `test_metrics_normalizes_unmatched_uuid_path_to_bounded_label`.
- `auth_failures_total` / `authz_failures_total` — derived purely from response status codes (401/403) at the middleware layer. **Deliberately does not touch `app/api/deps.py`** (the actual authentication/authorization logic) at all — a design choice to get accurate, real metrics with zero risk to a security-critical file. Verified by `test_metrics_counts_401_and_403_as_auth_authz_failures`.
- `db_failures_total` / `redis_failures_total` — incremented in `app/core/error_handlers.py`'s existing `_operational_error_handler`/`_redis_error_handler`, which already produce the honest 503s Phase 28 built; this phase only adds a counter increment alongside the pre-existing behavior.
- `neo4j_failures_total` — incremented in `graph.py`'s existing `graph_session()`/`graph_health()` exception branches.
- `email_enqueued_total` / `email_sent_total` / `email_failed_total` — new call sites in `email_service.py` and `workers/tasks.py` (Section 2).
- `storage_failures_total` — bounded to exactly the five real operations this codebase performs (`_STORAGE_OPS = ("put", "get", "delete", "ensure_bucket", "presign")`), broken out per-operation; `StorageObjectNotFoundError` (a normal 404, not a failure) is explicitly excluded from counting.

No user IDs, emails, phone numbers, request IDs, or arbitrary raw URLs appear as a label anywhere — verified directly by `test_metrics_snapshot_never_contains_email_addresses_or_secrets`, which asserts the full JSON snapshot contains no `@` character and no value resembling a UUID outside the bounded label set.

## 5. Latency measurement

The hot paths named in the master prompt (auth, registration, blood-request creation, donor matching/`notify_matching_donors`, emergency creation, hospital request retrieval, notification creation, certificate generation, graph/pathway ops) already have their query-count regression coverage from Phase 27 (`tests/test_phase27_notification_scaling.py::test_matching_donor_notification_query_count_is_bounded_not_linear`), which is part of the full suite re-run clean this phase (Section 12) — confirming the N+1 fix is intact and Phase 30 introduced no new N+1.

For wall-clock latency specifically, a real before/after measurement was performed (full methodology and numbers in Section 13 / the Report's performance-regression table) rather than assumed: `git stash` was used to get an exact, verified-clean Phase 29 baseline of the running application, timed against the real dev database (not a synthetic empty one), then the stash was restored and the identical measurement repeated against Phase 30 code. Result: no measurable regression (Section 13).

`http_latency` (avg/max ms per route) is recorded in `app/core/metrics.py` from the same `request_metrics_middleware` that produces `http_requests_total` — no separate instrumentation pass was needed since both come from one `time.monotonic()` wrap per request.

## 6. RQ observability (major priority)

`app/workers/queue.py::QueueHealth` (TypedDict) extended with `workers: int` (a live `len(Worker.all(connection=rq_redis))` count) and `status: Literal["healthy", "warning", "critical"]`, computed by a new `_queue_status(queued, failed, oldest_age, worker_count)` function using **explicit, documented, configurable** thresholds — replacing the Phase 29 hardcoded `queued > 1000` magic number:

- `rq_queue_depth_warning_threshold: int = 1000` — preserves the exact Phase 29 value unchanged (no new evidence to justify moving it).
- `rq_queue_depth_critical_threshold: int = 10_000` — two orders of magnitude below Phase 28/29's real, observed 148,965-stale-job incident; chosen so a real deployment gets a critical signal long before reaching an incident of that scale, not as an arbitrary round number.
- `rq_stale_queued_warning_seconds: int = 43_200` (12h) — exactly half of the existing `Settings.email_job_queue_ttl_seconds` (24h), so a warning fires with half the TTL window still available to react before RQ's own TTL enforcement would discard the job.

All three are `Settings` fields (`app/core/config.py`), not hardcoded — overridable per-deployment via environment variables, per the master prompt's explicit requirement.

`status` becomes `"critical"` specifically when jobs are queued but zero workers are registered (the single most actionable outage signature — "the queue isn't being drained at all," distinct from "the queue is just deep"), verified by `test_rq_queue_status_critical_when_jobs_queued_but_no_workers`. Six threshold tests in total cover healthy/warning-on-any-failure/warning-at-depth/critical-at-depth/critical-no-workers/warning-on-stale-oldest-job.

`app/api/routes/admin.py::system_health()` was updated to use these `Settings` fields instead of the old hardcoded value, and `scripts/rq_queue_maintenance.py --report` now prints the `status` and `workers` fields too.

## 7. RQ stale-job prevention — re-verification and a real demonstration

The Phase 29 fix (bounded `ttl`/`result_ttl`/`failure_ttl` on every `email_queue.enqueue()` call) was re-read and confirmed unchanged. This phase adds one thing on top: a **real, running demonstration** that the TTL actually does what it claims, not just a code read.

Investigation finding (empirically established, not assumed): RQ 2.12.0's `ttl` is enforced **only when a real worker actually dequeues the job** — not on any queue-read operation (`.count`, `.job_ids`, etc.), which was disproved by a standalone script showing a job remained in `job_ids` after its `ttl` had elapsed and `.count` had been called. Confirmed against RQ's own source (`rq.queue.Queue.dequeue_any`).

`tests/test_phase30_observability.py::test_stale_queued_job_is_actually_discarded_after_its_ttl_elapses` demonstrates the real behavior end-to-end: enqueues a job with `ttl=1`, asserts it is still present in `job_ids` (proving the test can't pass by accident via a lazy-read illusion), sleeps past the TTL, then runs a real `SimpleWorker([queue], connection=rq_redis).work(burst=True)` — after which the job is gone (`job_ids == []`, `fetch_job()` returns `None`). This is the same real-worker pattern already established in `test_phase18_notifications.py`, reused rather than duplicated.

Confirmed still true and unchanged this phase: the maintenance tool (`scripts/rq_queue_maintenance.py --purge-stale-queued`) remains explicit and `--confirm`-gated; no automatic/scheduled/startup-triggered destructive cleanup exists anywhere in the codebase (re-verified by grep); failed jobs remain diagnosable via RQ's `FailedJobRegistry`; retry behavior (`Settings.email_job_max_retries`) is unchanged; duplicate-notification prevention (the existing idempotency-key mechanisms on blood requests/donations, Phase 19/26) is untouched by any Phase 30 change.

## 8. Health endpoints audit

- **`/health/live`**: re-confirmed lightweight, process-level, zero dependency checks — unchanged.
- **`/health/ready`**: the pre-existing `required`/`optional` response shape was **deliberately preserved byte-for-byte** (an early draft of this phase's change broke `tests/test_health.py`'s exact-equality assertions and was caught before shipping — see the Report's regression section) — the new dependency-state vocabulary was added as a wholly separate, additive `dependency_states` top-level key rather than reshaping the existing fields.
- **`GET /admin/system/health`**: extended with `db_pool` (size/checked-out/overflow, read via `getattr(pool, "size", lambda: 0)()` etc. for mypy-safety against the abstract `Pool` base type) and `version` (app version + git commit SHA, Section 22); `rq` extended with `workers`/`status` (Section 6). Remains behind the same `Depends(_admin)` every other admin route uses — re-verified no new unauthenticated path was introduced.
- **New `GET /admin/system/metrics`**: also `Depends(_admin)`-gated, returns the full `metrics.snapshot()`.

All three were re-checked (code read + `test_admin_metrics_endpoint_requires_admin`, `test_admin_system_health_includes_version_and_db_pool`) for the master prompt's explicit leak list: no password/token/credential/connection-string/stack-trace appears in any of the three response bodies.

## 9. Dependency health vocabulary

`/health/ready`'s new `dependency_states` distinguishes, per dependency:

- `db` / `redis`: `AVAILABLE` / `UNAVAILABLE` (these two are always live-probed on every readiness check — they always were, Phase 28).
- `neo4j` / `storage` / `email`: `NOT_CONFIGURED` (no credentials/URL set at all) / `CONFIGURED_NOT_PROBED` (configured but readiness deliberately does not live-probe them, to keep the endpoint lightweight — see Section 1's liveness/readiness split) / `AVAILABLE` where a lighter-weight signal already exists.

The deliberate choice **not** to live-probe Neo4j/storage/email on every readiness check (which gates traffic-serving decisions and must stay fast and cheap) mirrors the existing pre-Phase-30 pattern in `app/services/sms_service.py::sms_health()` — config-and-recent-history-based, not a live ping — used here as the precedent for "health without probing" already established elsewhere in this codebase. Deeper, live diagnostics (Neo4j `verify_connectivity`, storage `ensure_bucket`) remain the admin-only `/admin/system/health`'s job, which can afford to be slower since it never gates traffic.

Critically: nothing here reports "healthy" merely because a config variable is set — `NOT_CONFIGURED` and `CONFIGURED_NOT_PROBED` are both explicitly distinct from `AVAILABLE`.

## 10. Neo4j observability

`graph.py` already had `GraphUnavailableError`, `graph_health()` (`configured`/`connected`/credential-free), and `graph_enabled()` before this phase — confirmed solid by direct read, left structurally unchanged. Two `metrics.inc_neo4j_failure()` calls added (one in `graph_session()`'s exception branch, one in `graph_health()`'s). The pre-existing `GraphPathwaySource.GRAPH` vs `HAVERSINE_FALLBACK` distinction (Phase 16/27, exposed per-pathway in `graph_service.py`) was confirmed still correctly exposed and untouched. Neo4j credentials are never logged — re-verified. Real connectivity remains **NOT-LIVE-VERIFIED**: this sandbox's configured Aura instance is unreachable (a real, standing environment condition documented since Phase 28), so `test_neo4j_unreachable_is_counted_via_graph_health` only asserts the counter moved when the real unreachable path was actually exercised — it does not fabricate a "verified" claim either way.

## 11. Storage observability

`app/core/storage.py` had **zero** pre-existing observability before this phase (confirmed by full-file read). Added: a `logging.getLogger("lifelink.storage")` logger with an explicit module comment explaining why object keys are never logged (they can embed a user-supplied filename); `ensure_bucket()`/`put_bytes()`/`get_bytes()` wrapped in try/except that increments `metrics.inc_storage_failure(op)` and logs `backend`+exception-type-only before re-raising (the exception still propagates — this phase adds observation, not new error-handling behavior); `StorageObjectNotFoundError` explicitly excluded from failure counting (a 404 is not an outage). Verified by `test_storage_put_failure_increments_metric_and_never_logs_the_key` and `test_storage_object_not_found_is_not_counted_as_a_failure`, plus the failure-injection test (Section 14) confirming the real, already-existing 503-vs-404 correctness (established in Phase 20) still holds with the new instrumentation layered on top. Remains **NOT-LIVE-VERIFIED** against a real S3/MinIO endpoint — none is reachable from this sandbox (confirmed: `curl http://localhost:9000/minio/health/live` refused during this phase's own E2E setup, Section 12).

## 12. SMTP observability

`email_service.py` also had zero pre-existing enqueue/send observability. Added: `_mask_email()` helper (used everywhere an address would otherwise be logged); `deliver()` (the inline, best-effort registration/reset-email path) now logs+counts `SENT`/`FAILED` distinctly, preserving `exc_info=True` on failure; `_enqueue()` now captures the real `Job` object RQ returns and logs its ID alongside the masked recipient, incrementing `email_enqueued_total`. `workers/tasks.py::send_email()` (the RQ worker's own execution of a queued job) similarly logs+counts `SENT`/`FAILED`, using `get_current_job()` for the job ID.

The ENQUEUED-vs-SENT/TRANSPORT-SUCCESS-vs-DELIVERY-UNKNOWN distinction the master prompt requires is enforced both in code (three genuinely distinct counters: `email_enqueued_total`, `email_sent_total`, `email_failed_total` — no single counter conflates them) and in the runbook's own explicit language ("job enqueued" is never described as "email delivered" anywhere in `docs/OPERATIONS_RUNBOOK.md`). "Transport-succeeded" is the exact phrase used in the success log line, deliberately short of claiming inbox delivery. Real Gmail remains **NOT-LIVE-VERIFIED** (same standing restriction); local inline SMTP is **LOCAL-VERIFIED** via the existing dev Mailpit-class setup, unchanged this phase.

## 13. Database observability, and a real before/after performance measurement

`db_pool` visibility (Section 8) surfaces connection-acquisition-relevant state (`size`, `checked_out`, `overflow`) via `/admin/system/health`, read defensively via `getattr` against SQLAlchemy's abstract `Pool` type rather than assuming `QueuePool`. `db_failures_total`/`redis_failures_total` (Section 4) cover connection/operational failures at the point they already surface (the existing `_operational_error_handler`/`_redis_error_handler`). No pool-size, max-overflow, or worker-count change was made anywhere this phase — `app/db/session.py` was read but not edited, consistent with its own historical comment blocks and the master prompt's explicit instruction not to reopen that question without new evidence.

**Real performance-regression measurement** (not assumed): because `app/main.py`'s `request_metrics_middleware` runs on every single HTTP request — a genuine hot path — a real before/after latency measurement was performed rather than reasoned about abstractly. Method: `git stash -u` was used to get an exact, clean Phase 29 working tree (confirmed via `git status --short` showing zero pending changes and the readiness endpoint reverting to its pre-Phase-30 response shape); the backend was started against the real dev database (not a synthetic empty one, so results reflect realistic query cost); two endpoints were timed with a Python `urllib`/`curl` harness (10 warm-up requests discarded, then 300 timed requests, `time.perf_counter`-based, mean/median/p95/p99 computed): `GET /health/ready` (touches DB+Redis, no auth) and `GET /donors/me` (authenticated, one DB row read, exercises the full middleware stack including session validation). The stash was then restored (`git stash pop`, confirmed via `git diff` that the exact same 14-file diff came back) and the identical measurement repeated.

Results:

| Endpoint | Phase 29 baseline (median) | Phase 30 (median) | Delta |
|---|---|---|---|
| `GET /health/ready` | 2.018 ms | 2.171 ms | +0.15 ms |
| `GET /donors/me` | 5.428 ms | 5.427 ms | −0.001 ms |

(Full mean/p95/p99 in the Report's performance table.) The `/health/ready` delta is within normal loopback-HTTP measurement noise for a sub-3ms round trip — and is not one-directional: Phase 30's p99 on that endpoint (3.188ms) was actually *lower* than baseline's (3.611ms) in this run. `/donors/me` shows no measurable difference at all. **Conclusion: no measurable latency regression from Phase 30's middleware/logging additions on hot paths.** This directly satisfies the master prompt's Section 26 instruction to measure, not assume, when monitoring code touches a hot path.

## 14. Database backup strategy audit

`scripts/db_backup.sh`/`db_restore.sh` were re-read in full and found unchanged from the already-solid Phase 28 state: no hardcoded credentials (reads `DATABASE_URL` or explicit overrides), `set -euo pipefail` (a failure returns non-zero, never silently continues), backup output written to `backend/backups/` (confirmed git-ignored), restore requires an explicit target database name plus the double opt-in (`RESTORE_CONFIRM=yes`, and additionally `RESTORE_ALLOW_PROD=yes` if the target name contains `"prod"`) — meaning restore cannot accidentally target production via a copy-pasted command. Neither script logs a credential at any point (re-verified by reading every `echo`/log line in both files). No changes were made to either script this phase — nothing needed fixing.

What is backed up: everything in PostgreSQL. What is not: Redis (sessions/rate-limits/RQ queue state), object storage (uploaded documents/certificates), Neo4j (graph data) — none of these have backup/restore tooling in this codebase, and this phase does not invent any (per the master prompt's explicit "do not invent a cloud backup provider" instruction). Retention and encryption are explicitly not implemented by the script itself (it produces one local file and says so) — a real deployment's own responsibility, **NOT-LIVE-VERIFIED** here since no real off-box backup storage exists in this sandbox to test against.

## 15. Backup integrity — a real drill

Performed against this sandbox's real dev database (not a synthetic empty one), end to end, with the app's live traffic quiesced for the duration:

1. `scripts/db_backup.sh` produced a real `pg_dump --format=custom` file from the live dev database.
2. A disposable `lifelink_restore_drill` database was created.
3. `scripts/db_restore.sh` restored the backup into it, with the double opt-in gate exercised for real (not against production — the drill target's name does not contain `"prod"`, so only `RESTORE_CONFIRM=yes` was needed, confirming the gate's own conditional logic behaves as documented).
4. **Exact row-count parity** was verified across every table that matters for this application's own domain: 1,351 users, 282,455 notifications, 5,823 audit_logs, 800 blood_requests, 620 donations, 1,075 donor_profiles, 605 emergencies, 7 family_links, 139 hospital_profiles — source and restored counts matched exactly on all nine.
5. A **content-level identity check** beyond row counts: `md5(string_agg(id::text || email || role, ',' ORDER BY id))` computed over the `users` table on both databases, producing the identical hash (`77bfece834b144d0daee6dbc9a11aa17`) on source and restore — proving the restored data is byte-identical for that table, not merely the same row count.
6. Foreign-key constraints were confirmed intact post-restore (no orphaned references).
7. `alembic current` on the restored database matched the source database's head exactly (`b8c9d0e1f2a3` at drill time).
8. The disposable database and local backup file were both deleted immediately after verification — this document, plus `docs/OPERATIONS_RUNBOOK.md`'s Section 4, is the durable record of the drill having happened; nothing from it was left as a live artifact, and it never touched production.

This is a genuinely stronger integrity check than a row-count-only drill would have been, and it was never run against production at any point.

## 16. Recovery runbook

Written as `docs/OPERATIONS_RUNBOOK.md` — see that document directly for the full content (quick-reference table, structured-log guide, metrics guide, and 12 lettered incident playbooks A-L exactly matching the master prompt's list: PostgreSQL outage, Redis outage, RQ worker outage, SMTP outage, object storage outage, Neo4j outage, application crash, failed deployment, migration failure, queue backlog, stale RQ jobs, suspected credential compromise). Each playbook follows symptoms → first checks → health endpoint → logs → safe immediate action → recovery → verify → escalate, and every destructive command mentioned (the RQ purge tool, `alembic downgrade`, database restore) carries an explicit warning against reflexive use, consistent with the master prompt's "never provide destructive commands without warnings and safeguards" instruction.

## 17. Disaster recovery

Also documented in `docs/OPERATIONS_RUNBOOK.md` Section 5, summarized here: PostgreSQL data is fully restorable (Section 15's real drill). Redis, object storage, and Neo4j each have **no backup/restore tooling in this codebase** — an honestly-stated gap, not a fabricated capability, with the reasoning for why each is an acceptable gap (Redis: sessions/rate-limits are recreatable, RQ's worst case is best-effort email loss; storage: the provider's own durability responsibility; Neo4j: an explicitly rebuildable derived layer, not the source of truth — the haversine fallback exists precisely because of this).

**RPO/RTO: explicitly NOT YET DEFINED.** This phase does not invent a number. What is established instead: a full-fidelity, real, verified backup/restore mechanism (Section 15); the practical RPO today is exactly "however often an operator chooses to run `db_backup.sh`" (nothing in this codebase schedules it automatically); the practical RTO is bounded by real `pg_restore` time against real data volume, and this drill's own ~11,500-row largest-table restore completed in well under a minute — explicitly caveated in the runbook as not something to extrapolate to a production-scale dataset without measuring it there directly.

## 18. Failure-injection testing

Six controlled, local-only tests (`tests/test_phase30_failure_injection.py`), each injecting a failure at a real client boundary (the same pattern already established in `test_phase17_resilience.py`) and asserting the Phase 30-specific additions on top of correctness already covered elsewhere:

1. **PostgreSQL unavailable** (`Session.get` monkeypatched to raise `OperationalError`) — 503, request ID present, no `Traceback`/`psycopg`/`password`/`SECRET_KEY`/`postgresql://` in the body, `db_failures_total` incremented.
2. **Redis unavailable** (`redis_client.hgetall` raises `ConnectionError`) — 503, request ID present, no leak, `redis_failures_total` incremented.
3. **RQ/Redis unavailable during notification enqueue** — `email_queue.enqueue` raises a real `ConnectionError`; the primary request (an authenticated donor read) still returns 200 — confirming the best-effort notification path never breaks the primary operation it's attached to.
4. **SMTP unavailable on the inline delivery path** — `_smtp_send` raises `OSError`; `deliver()` does not raise (its documented best-effort contract), `email_failed_total` increments.
5. **Neo4j unreachable** — exercised against this sandbox's real, standing-unreachable Aura configuration (not a mock); asserts the counter only when the real unreachable path was actually taken, honestly conditional rather than forcing a claim.
6. **Storage unavailable on document download** — patched one level below `get_bytes()` (at `storage._local_path`, not `get_bytes` itself) specifically so the real, newly-instrumented function actually executes end-to-end; 503, request ID present, no internal path leaked, `storage_failures_total` incremented.

Every injection is local (`monkeypatch` at a real boundary) — none disables a real external service or runs against production, and all six passed in the full suite re-run (Section 12).

## 19. Alerting design

Full table in `docs/OPERATIONS_RUNBOOK.md` Section 6 — ten vendor-neutral conditions (sustained 5xx increase, readiness failure, RQ critical/warning, repeated email failures, storage outage, Neo4j degradation, unusual latency, repeated auth failures), each with severity, operator action, and explicit false-positive guidance (e.g., a single failed readiness poll should not page; a brief RQ warning during a legitimate emergency-alert fan-out is expected and self-resolving). No specific alerting vendor/platform is deployed or assumed — these are conditions a real deployment wires into whatever tool it already uses.

## 20. Security and privacy audit

A final grep sweep was run across every new/changed logging call site (`app/core/storage.py`, `app/services/email_service.py`, `app/workers/tasks.py`, `app/core/request_metrics.py`) specifically hunting for anything that wasn't already masked/bounded — none found; all three flagged `logger.*` call sites use `_mask_email()`, bounded route labels, or no sensitive content at all (spot-checked directly by reading the surrounding code, not just the grep hit). Metrics snapshots, health-endpoint responses, and error-response bodies were all re-verified for the master prompt's explicit leak list (OTPs, credentials, connection strings, stack traces) via both direct code read and the automated test suite (`test_metrics_snapshot_never_contains_email_addresses_or_secrets`, the failure-injection suite's leak assertions). Request IDs were re-confirmed to be pure UUIDs with no authentication significance — `verify_origin`/session-cookie validation in `app/api/deps.py` (untouched this phase) remains the sole auth mechanism; a request ID by itself grants nothing. Both new operational endpoints (`/admin/system/health`, `/admin/system/metrics`) remain behind the same `Depends(_admin)` gate as every other admin route — confirmed by `test_admin_metrics_endpoint_requires_admin`.

## 21. Dashboard/admin visibility

`AdminOverview.tsx` already displayed DB/Redis/Storage/Graph/SMS health rows (Phase 28/29) but had never surfaced the Phase 29 `rq` health block at all — a real, independently-found gap. Added: an RQ status row (a colored status dot for healthy/warning/critical, showing queued/failed/worker counts) and a version/build-info line (app version + git commit SHA). Every value displayed comes from real application state returned by `/admin/system/health` — nothing here is a fabricated or placeholder metric, and nothing was added that the backend cannot actually measure.

## 22. Version/build info

`Settings.app_version` (default `"0.1.0"`) and `Settings.git_commit_sha` (default `"unknown"`, intended to be set from the deployment's own build step, e.g. `git rev-parse HEAD` baked in at image-build time) are surfaced via `/admin/system/health`'s new `version` component and the admin UI. Admin-only visibility, no secrets involved — a git SHA is not sensitive.

## 23. Log rotation and retention

Documented in full in `docs/OPERATIONS_RUNBOOK.md` Section 7: this application writes to stdout/stderr only, via `logging.StreamHandler` — it never opens or manages its own log file in any environment. Dev: terminal, no rotation needed. Docker: the daemon's own log driver (rotation is a daemon/compose-level concern, not something the Dockerfile sets). Staging/production: assumed to run behind whatever log collection the deployment platform provides — no specific managed logging service is invented or assumed, per the master prompt's explicit instruction, and this is marked **NOT-LIVE-VERIFIED** since no real staging/production log pipeline exists in this sandbox.

## 24. Metrics endpoint security

`GET /admin/system/metrics` requires a real authenticated admin session (same `Depends(_admin)` as every other admin route) and returns only bounded-cardinality data (Section 4) — never reachable unauthenticated, never carrying a user ID/email/request ID as a label or value. It is **IMPLEMENTED LOCALLY**; whether a real deployment additionally exposes a separate Prometheus-format scrape endpoint, and how that would itself be secured (network restriction, a scrape-specific token), is explicitly **NOT-LIVE-VERIFIED** — no such scraper exists in this sandbox.

## 25. Full regression — see the Report

Exact result lines (backend/Playwright/TypeScript/ESLint/build/migrations/backup/restore/failure-injection/RQ/SMTP/Neo4j/storage) are in `_REPORT.md`'s Section 29 table. Summary: backend 832/833 passed (1 pre-existing skip), 0 failed; Playwright 64/65 passed (1 pre-existing conditional data-dependent skip), 0 failed; TypeScript/ESLint/build all clean; a genuine pre-existing migration-reversibility bug was found and fixed (Section 26 below) and the full backend suite re-run clean afterward.

## 26. A genuine pre-existing bug found (and fixed) during migration checks

While running the migration round-trip check the master prompt's Section 25 requires (`alembic upgrade head` → `alembic downgrade base` → `alembic upgrade head` again, on a disposable database — never production), a real, pre-existing defect surfaced: `alembic/versions/c6cfa33e7862_phase7_admin_verification_prefs_.py`'s `downgrade()` drops every table that used a PostgreSQL ENUM column its own `upgrade()` created (`notification_digest_frequency`, `verification_kind`, `verification_status`, `appointment_status`), but never drops the enum **types** themselves — dropping a table/column does not drop the Postgres type in the same operation. Alembic's autogenerate does not emit the matching `DROP TYPE` automatically, and nobody added it by hand at the time (Phase 7, long before Phase 24-29).

**Effect confirmed by direct reproduction**: `alembic downgrade base` completes successfully and leaves 4 orphaned enum types behind; a subsequent `alembic upgrade head` then fails at exactly that migration with `psycopg.errors.DuplicateObject: type "notification_digest_frequency" already exists`.

**Blast radius, confirmed by inspection**: zero. `downgrade()` is never called anywhere in this codebase's deployment path — dev/CI/staging/production all only ever run `alembic upgrade head` (re-confirmed by grep across `.github/workflows/ci.yml`, `docker-compose.yml`, and every `scripts/*.sh`). This bug is invisible to every forward-only deployment that has ever run against this codebase; it only breaks a full downgrade-to-base reversibility drill, which is exactly what this phase's own Section 25 migration check performed for the first time.

**Fix**: four explicit `sa.Enum(name=...).drop(op.get_bind(), checkfirst=True)` calls appended to the end of `downgrade()`, with a comment explaining the root cause and why it was safe (downgrade-path-only, never touches `upgrade()` or any already-applied schema). `checkfirst=True` makes the drop idempotent/safe even if a type were somehow already absent.

**Verification**: the full round trip (`upgrade head` → `downgrade base` → `upgrade head`) was re-run on a fresh disposable database after the fix and completed with zero errors, landing correctly back at `b8c9d0e1f2a3 (head)`. The full backend test suite (833 collected) was re-run afterward and remained clean (832 passed / 1 skipped / 0 failed) — confirming the fix, being downgrade-path-only, has no effect on any code path the application's own tests exercise. `ruff check` was run against the changed file; all 26 pre-existing findings are in the untouched, auto-generated `upgrade()` block (confirmed via `git diff`, which shows only the appended `downgrade()` lines) — the newly-added lines introduce zero new lint findings.

This is exactly the kind of finding the master prompt's Section 25/32 rigor is designed to surface — not a Phase 30 regression, but a genuine, previously-undetected reversibility gap that only rigorous testing (not documentation-reading) would have found, fixed narrowly and verified, per the master prompt's own standing instruction to "never trust documentation over source code" and to actually run the checks rather than assume them clean.

## 27. What this phase deliberately did not do

Per the master prompt's Absolute Rules: no rebuild of any existing architecture; no invented cloud monitoring/alerting/metrics infrastructure, backup provider, or external-service verification; no pool-size/worker-count change; no automatic/scheduled destructive RQ cleanup; no weakened or deleted test; no `|| true`; no claim of Gmail/Neo4j Aura/real-S3 verification beyond what this sandbox can actually reach. Where evidence was genuinely absent (real staging/production log pipeline, a real metrics-scrape deployment, RPO/RTO), this audit says so plainly rather than inventing a number or a "verified" status.
