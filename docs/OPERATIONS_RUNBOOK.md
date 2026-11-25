# LifeLink Operations Runbook

Companion to `docs/LIFELINK_PHASE_30_OBSERVABILITY_RECOVERY_AUDIT.md` and `_REPORT.md`. This is the document an on-call operator opens during an incident — it assumes no prior context beyond "something is wrong with LifeLink" and points at the exact endpoint, log line, or command that answers each question in the Phase 30 master prompt's own list (is it running, is it healthy, what's failing, what do I do about it).

Every command below is real and was run against this project's real dev environment while writing this document, unless explicitly marked otherwise. Nothing here is a hypothetical "you would run something like...".

## 0. Where to look first, always

| Question | Where |
|---|---|
| Is the process alive at all? | `GET /health/live` — always 200 if the process can answer HTTP at all, checks nothing else |
| Can it safely take traffic? | `GET /health/ready` — 503 if PostgreSQL or Redis (the two hard, no-fallback dependencies) are down; also reports `dependency_states` for Neo4j/storage/email |
| What's actually broken, in detail? | `GET /admin/system/health` (admin-authenticated) — per-component `db`/`redis`/`storage`/`graph`/`sms`/`rq`/`db_pool`/`version` |
| What's the request-level error/latency picture? | `GET /admin/system/metrics` (admin-authenticated) — counts by route/status-class, latency, auth/authz/db/redis/neo4j/email/storage failure counters |
| What happened around a specific failed request? | Grep the application log for the `X-Request-Id` the client received (every response carries one, failures included) — every log line from that request carries the same ID |
| Is the RQ email queue backed up? | `python -m scripts.rq_queue_maintenance --report` (read-only) or the `rq` block of `/admin/system/health` |

## 1. Structured logs — what's in them and how to search

Every log line (`app/core/logging_config.py`) has the shape:

```
<timestamp> <LEVEL>    [<request-id>] <logger-name>: <message>
```

Loggers worth knowing: `lifelink.access` (one line per HTTP request: method, route template, status, duration — `app/core/request_metrics.py`), `lifelink.errors` (DB/Redis infrastructure failures), `lifelink.graph` (Neo4j), `lifelink.storage` (object storage), `lifelink.email` (SMTP/RQ email jobs), `lifelink.sms`.

To find everything that happened for one failed request: take the `X-Request-Id` response header the client saw (or that the frontend's own error UI surfaces) and `grep '\[<that-id>\]'` the application log. Outside a request (startup) the request-id field is `-`. Since Phase I, an email job carries the id of the request that enqueued it, so the worker's `lifelink.email` "sent"/"failed" lines share that id (jobs enqueued before Phase I log `-`).

**What is never in a log line, by design** (grepped as part of Phase 30's own security/privacy audit, and enforced by this phase's masking helpers): passwords, OTP codes, session cookies, API keys, SMTP/Redis/Neo4j/database credentials, full email addresses (masked to `j***@example.com`), request bodies, storage object keys (which can embed a user-supplied filename), signed URLs, document contents.

## 2. Metrics — what's tracked and how to read it

`GET /admin/system/metrics` (admin-only) returns a JSON snapshot from `app/core/metrics.py`: `http_requests` (count by method/route-template/status-class), `http_latency` (avg/max ms by route), and `counters` (`auth_failures_total`, `authz_failures_total`, `db_failures_total`, `redis_failures_total`, `neo4j_failures_total`, `email_enqueued_total`/`email_sent_total`/`email_failed_total`, `storage_failures_total` + a per-operation breakdown).

This is **process-local and in-memory** — it resets on restart and reflects only this one running instance, not a fleet. It is IMPLEMENTED LOCALLY and NOT-LIVE-VERIFIED against any real metrics backend (Prometheus, CloudWatch, ...) — see the Phase 30 report's classification table. A real deployment wanting historical/cross-instance metrics would scrape this endpoint into one.

## 3. Incident playbooks

Each entry: symptoms → first checks → health endpoint → logs → safe immediate action → recovery → verify → escalate.

### A. PostgreSQL outage

- **Symptoms**: most/all authenticated and data-reading endpoints return `503 {"code": "DATABASE_UNAVAILABLE"}`.
- **First checks**: `GET /health/ready` → `required.db: false`. `GET /admin/system/health` → `db.ok: false`, `db.detail` has the exception type (never a connection string or password).
- **Logs**: `lifelink.errors: database operational error on <method> <path>: <ExceptionClassName>`.
- **Safe immediate action**: none destructive — this is a read-only diagnosis phase. Confirm the database process/host is actually reachable (`pg_isready`, or the managed database's own status page). Do not restart the application process — a DB outage is not fixed by restarting the app, and it would drop in-flight requests for no benefit (this is exactly why `/health/live` never checks the DB — see its own docstring).
- **Recovery**: restore PostgreSQL availability (restart the service, fix network/firewall, fail over to a replica per your infrastructure's own DR plan — LifeLink itself has no built-in failover, see Section 5 below). Once reachable again, `pool_pre_ping=True` (already configured, `app/db/session.py`) means the app's connection pool self-heals — no app restart needed.
- **Verify**: `GET /health/ready` returns `200`/`required.db: true`; `db_failures_total` in `/admin/system/metrics` stops incrementing.
- **Escalate when**: the database itself won't come back with routine recovery (disk full, corruption, a managed-service-provider outage) — this is now a database-infrastructure incident, not an application one.

### B. Redis outage

- **Symptoms**: same shape as A but `{"code": "DEPENDENCY_UNAVAILABLE"}` — session validation AND rate limiting both depend on Redis with **no in-memory fallback, by deliberate design** (`app/core/error_handlers.py`'s own docstring: a silent fallback would be an insecure rate-limit/session bypass). This means a Redis outage fails EVERY authenticated/rate-limited request closed (503), never open.
- **First checks**: `GET /health/ready` → `required.redis: false`. `GET /admin/system/health` → `redis.ok: false`.
- **Logs**: `lifelink.errors: redis error on <method> <path>: <ExceptionClassName>`.
- **Safe immediate action**: none destructive. Confirm Redis reachability (`redis-cli -u $REDIS_URL ping`).
- **Recovery**: restore Redis. No app restart needed once reachable (redis-py reconnects; `redis_connect_timeout_seconds`/`redis_socket_timeout_seconds`, `app/core/config.py`, bound how long a hung attempt blocks a request rather than requiring a restart).
- **Verify**: `/health/ready` 200; `redis_failures_total` stops climbing; a real login succeeds.
- **Escalate when**: Redis data itself was lost (not just unreachable) — see Section 5, "Redis recovery expectations" (session/rate-limit state, not business data, is what lives there).

### C. RQ worker outage (no worker process running/keeping up)

- **Symptoms**: emails stop actually sending (recipients report never receiving verification/reset/notification emails) while the app itself behaves normally (registration, login, notifications all still return 200 — the in-app half of a notification is independent of email, and `_enqueue`'s Redis write succeeding is independent of whether anything drains the queue).
- **First checks**: `GET /admin/system/health` → `rq.status`: `"critical"` specifically means jobs are queued but zero workers are registered (`app/workers/queue.py::_queue_status`) — this is the most direct outage signature. `rq.workers` — the live count of registered RQ worker processes.
- **Logs**: absence of `lifelink.email: email job <id> sent for ...` / `... failed for ...` lines despite `... enqueued for ...` lines accumulating is itself the signature (nothing is draining the queue). Note: before Phase I the worker process never installed the app's logging config, so these worker lines were missing even when mail WAS being delivered — only trust this signal on a Phase I or later worker.
- **Safe immediate action**: `python -m scripts.rq_queue_maintenance --report` (read-only) to see the real backlog depth before doing anything.
- **Recovery**: restart/redeploy the worker process (`python -m app.workers.worker`). Because every job already carries a bounded TTL (`Settings.email_job_queue_ttl_seconds` = 24h by default — Phase 29/30), a worker outage shorter than that window loses nothing: once the worker comes back, it drains the backlog from where it left off.
- **Verify**: `rq.status` returns to `"healthy"`; `rq.workers >= 1`; `email_sent_total` (in `/admin/system/metrics`) starts climbing again.
- **Escalate when**: the worker outage exceeded `email_job_queue_ttl_seconds` — some queued jobs (stale verification/reset links, in particular) were silently discarded by RQ's own TTL enforcement rather than delivered late. This is the deliberate, documented trade-off Phase 29 made (a 24h-stale email is chasing an already-expired token anyway) — but it means affected users need to re-request verification/reset, not that data was lost.

### D. SMTP outage (mail host unreachable, auth failing, etc.)

- **Symptoms**: `email_failed_total` climbing in `/admin/system/metrics`; `lifelink.email: email job <id> failed for j***@...` lines (worker path) or `lifelink.email: inline email delivery failed for j***@...` (registration/reset's own inline-send path, see `email_service.deliver`'s docstring for why that path is separate and best-effort).
- **First checks**: `GET /admin/system/health` → `email` isn't a dedicated component today (email has no live-probe health check by design — see Section 12 of the audit for why: there's no safe way to "ping" SMTP without sending a real message). Use the metrics counters and RQ's own `failed` count/FailedJobRegistry instead.
- **Logs**: as above; RQ's own retry policy (`Settings.email_job_max_retries = 3`) already retries a transient failure automatically before it lands in `FailedJobRegistry`.
- **Safe immediate action**: none destructive. This never rolls back the primary operation it's attached to (registration, blood-request notification, etc.) — email is architected as best-effort everywhere in this codebase (confirmed by direct code read, Phase 17/30).
- **Recovery**: fix SMTP credentials/connectivity/host. Failed jobs already in `FailedJobRegistry` are NOT automatically retried once fixed — that's RQ's own design (max retries already exhausted); `python -m scripts.rq_queue_maintenance --report` shows the failed count, and an operator decides whether to re-enqueue manually or accept the loss (typically acceptable: an expired verification link, a stale password-reset code).
- **Verify**: `email_sent_total` climbing again; `rq.failed` in `/admin/system/health` not growing further.
- **Escalate when**: a Gmail-specific auth failure (app password revoked/expired) — this needs the mailbox owner, not a code fix.

### E. Object storage outage (S3/MinIO unreachable, or local-disk full)

- **Symptoms**: `503` on document upload/download (verification documents, certificates) — never a `500` or a leaked internal path (`test_phase20_secure_file_storage.py`'s own coverage; Phase 30 added the request-ID/metric on top, `tests/test_phase30_failure_injection.py`).
- **First checks**: `GET /admin/system/health` → `storage.ok: false`, `storage.detail` starts with the backend name (`"s3: ..."` or `"local: ..."`) and an exception type only.
- **Logs**: `lifelink.storage: storage <operation> failed (backend=<s3|local>): <ExceptionClassName>` — never the object key/filename (Phase 30's own explicit design, see `app/core/storage.py`'s module docstring).
- **Safe immediate action**: none destructive. For local-disk storage, check free disk space (`df -h` on the storage volume) — a full disk is the most common local-backend cause.
- **Recovery**: restore S3/MinIO reachability, or free disk space for the local backend. No app restart needed — every storage call re-resolves the backend per-call.
- **Verify**: `storage.ok: true`; `storage_failures_total` stops climbing; a real upload succeeds.
- **Escalate when**: data loss is suspected (objects missing that should exist) — this needs the storage provider's own durability guarantees investigated, outside this application's scope.

### F. Neo4j outage / degradation

- **Symptoms**: none visible to end users under normal operation — every graph-dependent pathway/matching call already has a haversine-distance fallback (`GraphPathwaySource.HAVERSINE_FALLBACK`, `app/services/graph_service.py`), which is the expected, correct behavior of an "optional dependency" per this app's own architecture, not an incident by itself.
- **First checks**: `GET /admin/system/health` → `graph.configured`/`graph.connected`/`graph.status`. `configured: false` is a normal deployment choice (Neo4j never provisioned). `configured: true, connected: false` means Neo4j was set up but is currently unreachable — this DOES warrant investigation (degraded pathway quality, not correctness).
- **Logs**: `lifelink.graph: Neo4j operation failed: <ExceptionClassName>` / `Neo4j verify_connectivity failed: ...`.
- **Safe immediate action**: none — the fallback is already active and safe.
- **Recovery**: restore Neo4j reachability (credentials, network/firewall, Aura instance status). No app restart needed — `graph_session()` re-attempts per call.
- **Verify**: `graph.connected: true`; `neo4j_failures_total` stops climbing; a pathway response's `source` field returns to `GRAPH` instead of `HAVERSINE_FALLBACK`.
- **Escalate when**: never urgent by itself (the app degrades gracefully) — treat as a scheduled fix, not a page.

### G. Application crash / process not running

- **Symptoms**: `/health/live` itself doesn't respond (connection refused), not just a 503.
- **First checks**: is the process running at all (`ps`/your process manager/orchestrator's own view)? Check the process's own stdout/stderr for a crash traceback — this is the one case where `lifelink.access`'s last-logged line (the last request before the crash) is genuinely useful context, alongside whatever the process manager captured on exit.
- **Safe immediate action**: restart the process via your deployment's own mechanism (`scripts/run_production.sh` / your orchestrator). `app.asgi:application`'s lifespan handler (`app/main.py`) closes the Neo4j driver cleanly on a graceful `SIGTERM`, so a normal restart is safe; a hard crash (OOM-kill, segfault-class) is not something this application can protect against at the process level — that's the orchestrator's job (restart policy).
- **Recovery/verify**: `/health/live` returns 200 again; `/health/ready` reflects real dependency state.
- **Escalate when**: it crashes repeatedly right after restart — this is a real bug or a resource limit (memory, file descriptors), not a transient blip.

### H. Failed deployment

- **Symptoms**: the new version won't start, or starts but immediately fails readiness.
- **First checks**: application startup logs (fail-closed config validators — `Settings._validate_secret_key`, `_validate_cookie_security`, `_validate_log_level` — raise and refuse to start on a real misconfiguration, by design, per Phase 28; this is a feature, not a bug, if a real deployment's `SECRET_KEY`/cookie config is wrong). `GET /health/ready` on the new instance before routing real traffic to it.
- **Safe immediate action**: do not force traffic onto an instance failing readiness. Roll back to the previous known-good deployment artifact.
- **Recovery**: fix the startup-blocking config/code issue in a new deployment; never patch a running failed instance in place.
- **Verify**: full smoke test (`scripts/production_smoke_test.py` / `scripts/staging_smoke_test.py`) against the new deployment before it takes real traffic.
- **Escalate when**: the rollback target is itself unhealthy — this needs a real incident response beyond this runbook's scope.

### I. Migration failure

- **Symptoms**: `alembic upgrade head` exits non-zero during deployment.
- **First checks**: the migration's own error output (a real DDL conflict, a missing dependency revision, a lock timeout against a live table).
- **Safe immediate action**: **do not** run `alembic downgrade` reflexively against a partially-applied migration without understanding exactly what state the schema is in — Alembic migrations in this codebase are not all guaranteed trivially reversible in a partial-failure state. Do not start the application against a database mid-migration.
- **Recovery**: fix the migration (or the blocking condition — e.g. a long-running query holding a lock the migration needs) and re-run `alembic upgrade head`; it is idempotent from a clean failed state for the overwhelming majority of this codebase's migrations (additive column/index changes). For anything destructive, restore from the most recent backup (Section 4) rather than guessing.
- **Verify**: `alembic current` matches the expected head; `alembic heads` reports exactly one head (see `.github/workflows/ci.yml`'s own `migrations` job for the same check run in CI).
- **Escalate when**: the migration partially applied against production data in a way that isn't cleanly re-runnable — treat as a data-integrity incident, verify against a backup before making further schema changes.

### J. Queue backlog (RQ queue depth growing)

- **Symptoms**: `GET /admin/system/health` → `rq.status: "warning"` or `"critical"`; `rq.queued` climbing over successive checks.
- **First checks**: `rq.workers` — zero means Section C (worker outage) is the real root cause, not the backlog itself. `rq.oldest_queued_job_age_seconds` — how long the oldest waiting job has been stuck.
- **Safe immediate action**: `python -m scripts.rq_queue_maintenance --report` for the full picture. **Never** run `--purge-stale-queued --confirm` reflexively — that permanently discards real user-facing emails (verification links, reset codes) before deciding whether the backlog is actually unrecoverable.
- **Recovery**: usually Section C's recovery (get a worker running/keeping up) resolves this on its own, since new jobs' bounded TTL (Phase 29/30) means the backlog is self-limiting even while a worker is down.
- **Verify**: `rq.queued` trending back down; `rq.status` back to `"healthy"`.
- **Escalate when**: `rq.queued` is climbing even WITH a healthy worker running — this means enqueue rate genuinely exceeds SMTP throughput (a real capacity problem, not an outage), worth investigating the mail provider's own rate limits.

### K. Stale RQ jobs (a real backlog predates the Phase 29/30 TTL fix, or accumulated during an extended worker outage)

- **Symptoms**: `rq.oldest_queued_job_age_seconds` is very large (multiple days); `rq.queued` is large and not shrinking even with a worker running (every job in it is already past the point of being useful, per the app's own 24h verification-link semantics, but RQ hasn't reaped it yet because nothing has attempted to dequeue it — see the Phase 30 stale-job demonstration test, `tests/test_phase30_observability.py::test_stale_queued_job_is_actually_discarded_after_its_ttl_elapses`, for exactly how/when RQ's own `ttl` enforcement fires).
- **Safe immediate action**: `python -m scripts.rq_queue_maintenance --report` first, always.
- **Recovery**: `python -m scripts.rq_queue_maintenance --purge-stale-queued --older-than-days N --confirm` — explicit, operator-invoked, requires `--confirm`, reviews the count via a dry run first (the same invocation without `--confirm`). **This never runs automatically** — no deployment step, CI job, or startup path calls it (verified by grep, Phase 29/30).
- **Verify**: `rq.queued` and `rq.oldest_queued_job_age_seconds` drop to reflect only genuinely-recent jobs.
- **Escalate when**: never strictly required — this is routine, safe maintenance once reviewed.

### L. Suspected credential compromise

- **Symptoms**: unexpected admin actions in the audit log (`GET /admin/audit-log`), unrecognized sessions, anomalous login patterns.
- **First checks**: `GET /admin/audit-log` filtered by the suspected actor/time range — every role-check denial (`authz.role_denied`) and every sensitive admin action is already durably audited (`app/services/audit_service.py`), independent of this phase's own logging/metrics work.
- **Safe immediate action**: do **not** attempt to fix this by editing users/sessions directly in the database — use the application's own admin actions (`POST /admin/users/{id}/suspend`) so the action itself is audited.
- **Recovery**: suspend the affected account(s) via the admin API; rotate `SECRET_KEY` (invalidates every session cookie's signature, per Phase 28's fail-closed validators) if session-forgery is suspected — this is a disruptive, all-users-logged-out action, so treat it as a last resort, not a first response; rotate any external credential (SMTP, S3, Neo4j, MSG91) believed compromised, following that provider's own credential-rotation process (never something this application does for you).
- **Verify**: the audit log shows the suspension action; the rotated credential is confirmed working (a real login, a real send) before considering the incident closed.
- **Escalate when**: always — a suspected compromise is a security incident, not a routine operational one; this runbook entry is about safe immediate containment, not full incident response.

## 4. Backup and restore

`scripts/db_backup.sh` — takes a full `pg_dump --format=custom` backup of whatever `DATABASE_URL` points at (or an explicit override), written to `backend/backups/` (git-ignored). No credential is ever hardcoded or logged.

`scripts/db_restore.sh` — restores a backup into an explicitly-named target database. Requires `RESTORE_CONFIRM=yes`; additionally requires `RESTORE_ALLOW_PROD=yes` if the target database name contains `"prod"` — a double opt-in specifically so a copy-pasted restore command can never silently land on production.

**Real drill performed this phase** (Phase 30, evidence in `docs/LIFELINK_PHASE_30_OBSERVABILITY_RECOVERY_AUDIT.md`): backed up this sandbox's real dev database (1,351 users, 282,455 notifications, 5,823 audit log rows, and more — full table list in the audit), restored into a disposable `lifelink_restore_drill` database, and verified **exact row-count parity across every table** plus a content-identity check (an md5 hash of every user's id+email+role, computed identically on both databases) plus Alembic migration-head parity. The drill database was dropped immediately after verification — this runbook entry is the durable record of that drill having happened, not a live artifact.

**What backup covers**: everything in PostgreSQL (all application data, audit logs, sessions are NOT in Postgres — see below). **What it does not cover**: Redis (sessions, rate-limit counters, the RQ queue itself — see Section 5), object storage (uploaded documents/certificates — a separate backup concern for whatever S3/MinIO bucket is configured, outside this script's scope), Neo4j (the graph engine's own data, if used — also outside this script's scope).

**Retention/encryption**: not implemented by this script — it produces one file and says so explicitly ("copy it somewhere durable... this script does not do that for you"). A real deployment needs its own retention policy (how many backups, how long) and encryption-at-rest for wherever backups are stored, neither of which this sandbox can verify without real off-box storage. **NOT-LIVE-VERIFIED.**

## 5. Disaster recovery model

**What can be restored**: PostgreSQL data, in full, from a `db_backup.sh` snapshot — demonstrated above with real row-level and content-level verification.

**What cannot currently be restored from this application's own tooling**:
- **Redis** (sessions, rate-limit counters, RQ queue state) has no backup/restore tooling in this codebase. This is an intentional, low-risk gap: sessions are re-creatable by the user logging in again, rate-limit counters are safe to lose (they just reset), and the RQ queue's own worst case on total Redis data loss is "some in-flight email jobs are lost" — not a correctness or data-integrity problem, since email is already architected as best-effort everywhere. A real production deployment should still configure Redis's own persistence (RDB/AOF) or accept this loss profile explicitly — this is a deployment-configuration decision outside this application's code, **NOT YET DEFINED** by this project.
- **Object storage** (S3/MinIO — uploaded verification documents, certificates) has no backup/restore tooling here; this is the storage provider's own durability/backup responsibility (e.g. S3 versioning/cross-region replication), not something `db_backup.sh` touches.
- **Neo4j** (graph engine data, if provisioned) similarly has no backup/restore tooling in this codebase — and is explicitly a rebuildable cache/derived-data layer in this architecture (the haversine fallback exists precisely because the graph is not the source of truth for whether a match is valid, only for path-quality) — so its loss is a degradation, not a data-loss incident, as long as PostgreSQL (the actual source of truth for hospitals/routes) is intact.

**RPO/RTO**: **NOT YET DEFINED.** This project has no stated Recovery Point Objective or Recovery Time Objective, and this phase does not invent one — an RPO/RTO decision needs a real deployment's actual backup cadence (how often `db_backup.sh` is scheduled to run, which is an infrastructure decision, not a code one) and a business decision about acceptable downtime, neither of which exists yet for this project. What this phase DOES establish: a full-fidelity backup/restore mechanism exists and was proven to work end-to-end; the RPO in practice is exactly "how often you choose to run it" (not automated/scheduled by anything in this codebase today), and the RTO is bounded by how long `pg_restore` takes against your data volume (this drill's ~11,500 real-shaped rows across the largest table restored in well under a minute — not a number to extrapolate to a production data volume without measuring it there).

## 6. Alerting design (conditions, not a specific vendor)

No alerting *system* is deployed in this sandbox (no PagerDuty/Opsgenie/vendor-specific config exists or is invented here) — these are the CONDITIONS a real deployment should wire into whatever alerting tool it uses, evaluated against the endpoints/metrics this phase added.

| Condition | Source | Severity | Operator action | False-positive notes |
|---|---|---|---|---|
| `/health/ready` returns 503 for > 1 minute | readiness probe | Critical | Follow playbook A or B above depending on which required dependency is down | A single failed poll can be transient network noise — alert on sustained failure, not one sample |
| Sustained 5xx rate increase (e.g. 5xx count / total count over a rolling window exceeds a few percent) | `/admin/system/metrics` `http_requests` by status_class | Critical | Check `lifelink.access` WARNING-level lines (5xx logged at WARNING) for the affected route(s); correlate with a recent deploy | A brief spike during a deploy's own rolling restart is expected — alert on a sustained window, not an instant |
| `rq.status == "critical"` | `/admin/system/health` | High | Playbook C (worker outage) or J (backlog) | None — this status is already threshold-gated (Section 6/7 of the audit), so it should not fire on routine traffic bumps |
| `rq.status == "warning"` for > 15 minutes | `/admin/system/health` | Medium | Playbook J | A brief warning during a legitimate traffic burst (e.g. a mass-notification emergency alert fan-out) is expected and self-resolving; alert on sustained state |
| Repeated email failures (`email_failed_total` climbing steadily) | `/admin/system/metrics` | Medium | Playbook D | A handful of failures to genuinely-invalid addresses is normal background noise; alert on a rate increase, not any failure at all |
| Storage failures (`storage_failures_total` climbing) | `/admin/system/metrics` | High | Playbook E | None significant — a real storage outage should alert promptly since it blocks verification/certificate workflows |
| Neo4j degraded (`graph.configured=true, graph.connected=false`) | `/admin/system/health` | Low | Playbook F | Not urgent by design (graceful fallback) — a daily/hourly check is enough, not a page |
| Unusually high p95/max latency on a specific route | `/admin/system/metrics` `http_latency` | Medium | Investigate that route; cross-reference Phase 24/27's own documented concurrency-limitation findings before assuming a new regression | This app has a KNOWN, documented capacity limitation under extreme synthetic concurrency (Phase 24/25/27 reports) — a latency alert during a genuine traffic burst may be that known, disclosed limitation, not a new bug |
| Repeated authentication failures from one source (`auth_failures_total` spiking) | `/admin/system/metrics` | Medium | Possible credential-stuffing attempt — the existing login rate limiter (`Settings.rl_login_ip`/`rl_login_account`, unchanged this phase) already blocks it at the application layer; this alert is for operator awareness, not a missing control | A single user repeatedly mistyping a password looks identical at low volume — alert on a rate/volume threshold, not any occurrence |

## 7. Log rotation and retention

This application writes structured logs to **stdout/stderr only** (`logging.StreamHandler`, `app/core/logging_config.py`) — it never opens or manages its own log file, in any environment (dev, Docker, staging, production). This is deliberate: a process should not be responsible for its own log rotation/retention, since that is exactly what the surrounding platform already does well (Docker's own log driver, `journald`, a cloud platform's log collection agent, `kubectl logs`, etc.).

- **Development**: logs go to the terminal running `uvicorn app.main:app`. No rotation needed or attempted.
- **Docker**: the container's stdout/stderr is captured by the Docker daemon's configured log driver (`json-file` by default, which itself supports `max-size`/`max-file` rotation options at the daemon/compose level — a deployment concern, not something this application's own Dockerfile sets, since the right rotation policy depends on the host's own disk budget).
- **Staging/production**: assumed to run behind whatever log collection the deployment platform provides (a cloud provider's log group, a self-hosted ELK/Loki stack, etc.) — this project does not invent or assume a specific managed logging service, per the Phase 30 master prompt's own instruction. **NOT-LIVE-VERIFIED**: no real staging/production log pipeline exists in this sandbox to confirm against.

## 8. Metrics endpoint security

`GET /admin/system/metrics` requires a real authenticated admin session (`Depends(_admin)`, the same dependency every other `/admin/*` route uses) — it is never reachable unauthenticated, and its label set is bounded (route templates, status classes, fixed operation names) with no user IDs, emails, or request IDs ever appearing as a label or value (verified directly, `tests/test_phase30_observability.py::test_metrics_snapshot_never_contains_email_addresses_or_secrets`). It is **IMPLEMENTED LOCALLY**; whether a real deployment additionally exposes a Prometheus-format scrape endpoint (and how that would itself be secured — network-level restriction, a separate token, etc.) is **NOT-LIVE-VERIFIED**, since no such scraper is deployed in this sandbox.

## 9. Phase H–K operational notes

- **CORS**: the API refuses to start if `CORS_ORIGINS` contains a wildcard or a non-origin entry (path, trailing slash). List exact frontend origins.
- **Dependency audit**: CI's `npm run audit:gate` fails on ANY advisory in shipped (runtime) frontend dependencies; dev-only exceptions live in `frontend/audit-allowlist.json` with a `review_by` date that fails CI once passed. Python: `pip-audit -r backend/requirements.txt`; `urllib3>=2.8.0` is floor-pinned.
- **Startup order (compose)**: `api` waits for db, redis, mailpit, minio (healthy) and `createbuckets` (completed); `worker` waits for `api` healthy. A container stuck in "waiting" names the dependency that is not healthy.
- **Real-time outbox**: after every commit the publisher drains the whole backlog; `realtime_events` keeps published rows for `REALTIME_RETENTION_HOURS` (72) and prunes once per 500 events. A growing count of rows with `seq IS NULL` or `published_at IS NULL` means publication is failing (usually Redis) — the API logs `realtime publish to Redis failed; will retry on next run`; the next commit after Redis returns publishes the backlog in order.
- **Performance investigation**: `scripts/perf_seed.py` (volume data, dev only) and `scripts/perf_explain.py` (captures the real SQL of 27 endpoints and EXPLAIN ANALYZEs it) reproduce the Phase K measurements against any non-production database.
