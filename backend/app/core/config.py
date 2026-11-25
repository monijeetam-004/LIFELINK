from __future__ import annotations

import re
from functools import lru_cache
from typing import ClassVar

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False,
        # Hardening (found by tests/test_qa_email.py): a refused configuration
        # used to echo EVERY input value -- including MAIL_PASSWORD and
        # SECRET_KEY -- into the ValidationError printed at start-up.
        hide_input_in_errors=True
    )

    environment: str = "development"
    secret_key: str = "dev-insecure-secret-change-me"
    # Phase 28: env-driven log verbosity for app/core/logging_config.py's
    # configure_logging(). Validated against the real logging module below
    # rather than left to fail obscurely inside dictConfig.
    log_level: str = "INFO"

    # Database
    database_url: str = "postgresql+psycopg://lifelink:lifelink@localhost:5432/lifelink"
    test_database_url: str | None = None
    # Phase 28: connection-lifecycle/safety settings -- deliberately NOT
    # pool_size/max_overflow, which Phase 24 and Phase 27 both measured
    # (twice, for two different underlying reasons) to have no effect on
    # this app's real bottlenecks and explicitly reverted to library
    # defaults each time; see app/db/session.py's own comment before ever
    # touching those two again. These four are orthogonal to pool
    # *capacity* -- they bound how long a single connection attempt/query
    # is allowed to hang and how long a stale pooled connection is kept
    # around, which is a pure reliability property no prior phase measured
    # or ruled out.
    #
    # Time to establish a brand-new TCP+auth connection to Postgres before
    # giving up -- without this, an unreachable/firewalled DB host hangs a
    # request indefinitely instead of failing fast into the existing
    # OperationalError -> 503 path (app/core/error_handlers.py).
    db_connect_timeout_seconds: int = 10
    # PostgreSQL-side statement_timeout (ms): aborts any single SQL
    # statement that runs longer than this, protecting against a genuinely
    # runaway query (a missing index, an unexpected lock wait) holding a
    # pool connection forever. 30s is generous relative to every real
    # measurement in this codebase (Phase 27's own worst-case single-call
    # timing was ~6.3s at concurrency 50 for the heaviest write endpoint;
    # see LIFELINK_PHASE_27_ACCESSIBILITY_SCALABILITY_REPORT.md) -- this is
    # a safety backstop, not a tuning knob expected to fire in normal
    # operation.
    db_statement_timeout_ms: int = 30_000
    # How long a request waits for a connection to free up from the pool
    # before raising (SQLAlchemy QueuePool's own default is already 30s;
    # made an explicit, documented, env-overridable setting here rather
    # than left as an undiscoverable library default).
    db_pool_timeout_seconds: int = 30
    # Recycle (transparently close and reopen) a pooled connection after
    # this many seconds, so a connection that has sat idle long enough for
    # a middlebox/proxy/managed-Postgres idle-kill to have silently dropped
    # it is never handed back out stale. pool_pre_ping (already set below)
    # catches a dead connection reactively per-checkout; this bounds how
    # old a connection is allowed to get in the first place, which matters
    # more for managed/proxied Postgres (e.g. behind PgBouncer or a cloud
    # provider's connection proxy) than for this sandbox's direct local
    # Postgres.
    db_pool_recycle_seconds: int = 1800

    # Redis / queue
    redis_url: str = "redis://localhost:6379/0"
    # Phase F -- real-time event layer (app.services.realtime_service,
    # app.api.routes.events). Heartbeat keeps proxies from closing an idle
    # SSE stream; the session is re-validated on that cadence so a logged-
    # out/suspended user's open stream ends instead of living forever.
    realtime_heartbeat_seconds: float = 15.0
    realtime_session_recheck_seconds: float = 30.0
    # Max events replayed on reconnect before telling the client to resync
    # (re-fetch everything) instead.
    realtime_replay_limit: int = 500
    # Hardening: "background" = a per-process publisher thread drains the
    # outbox after each commit (off the request path); "inline" = the
    # committing request publishes before returning (the Phase F behaviour).
    realtime_publish_mode: str = "background"
    # Background mode also drains on this interval (backlog left by a crash
    # or a Redis outage is published without waiting for the next write).
    realtime_publisher_interval_seconds: float = 5.0
    # Published events older than this are pruned; a client whose
    # Last-Event-ID predates the oldest retained event gets a resync.
    realtime_retention_hours: int = 72
    # Phase 28: without an explicit timeout, redis-py blocks indefinitely on
    # a hung/unreachable connection -- the same class of gap as the DB
    # connect_timeout added above, and for the same reason: session
    # validation and rate limiting (this app's two hard, no-fallback Redis
    # dependencies -- see app/core/error_handlers.py's own docstring) must
    # fail fast into the existing RedisError -> 503 path, not hang a
    # request forever. socket_connect_timeout bounds establishing the TCP
    # connection; socket_timeout bounds each individual command.
    redis_connect_timeout_seconds: float = 5.0
    redis_socket_timeout_seconds: float = 5.0
    rq_sync: bool = False
    rq_queue_name: str = "emails"
    # Phase 18: the email RQ job (app.workers.tasks.send_email) previously had
    # NO retry policy at all -- a transient SMTP hiccup (connection reset, a
    # brief mail-server restart) went straight to FailedJobRegistry on the
    # first attempt with no second try. `Retry(max=N)` with no interval asks
    # RQ to re-enqueue immediately (no scheduler/delay needed) up to N times
    # before giving up for good, which is enough to ride out a genuinely
    # transient failure without adding exponential-backoff machinery this
    # deployment has no evidence it needs. See test_phase18_notifications.py
    # for the real (non-mocked) worker tests this makes possible.
    email_job_max_retries: int = 3

    # Phase 29: bounds on how long an email job can live in Redis, added in
    # direct response to a real finding -- this session's Redis instance was
    # found holding 8,893+ queued email jobs (and Phase 28 found 148,965) with
    # `ttl == -1` (never expires) on every job hash, accumulated from
    # repeated load-test runs with no worker draining them. RQ's own
    # defaults (`ttl` unset = job waits in the queue forever;
    # `failure_ttl`/`result_ttl` unset = RQ's built-in default of ~1 year /
    # 500s respectively) do not actually bound the failure mode observed
    # here (an indefinitely-growing QUEUED backlog, not a finished/failed
    # job leak). These three settings are passed to `Queue.enqueue()`:
    #   - queue_ttl:   max time a job may sit UNSTARTED in the queue before
    #                  RQ discards it as stale, rather than accumulating
    #                  forever. 24h matches this app's own verification-link
    #                  TTL (EMAIL_VERIFICATION_TTL_SECONDS) -- an email this
    #                  stale is chasing a token that has likely already
    #                  expired, so dropping it is consistent with existing
    #                  app semantics, not an arbitrary new cutoff.
    #   - result_ttl:  how long a *successful* job's result is kept for
    #                  inspection afterward.
    #   - failure_ttl: how long a *failed* job stays in FailedJobRegistry
    #                  for an operator to see and investigate, before RQ
    #                  reclaims it.
    email_job_queue_ttl_seconds: int = 86400
    email_job_result_ttl_seconds: int = 3600
    email_job_failure_ttl_seconds: int = 604800

    # Session cookie
    session_cookie_name: str = "lifelink_session"
    session_cookie_secure: bool = False
    session_cookie_samesite: str = "lax"
    session_cookie_domain: str | None = None
    # Idle/sliding timeout: the Redis TTL is refreshed on every authenticated
    # request (see app/api/deps.py:_load_user_from_request), so an actively
    # used session never expires mid-session -- only inactivity for this long
    # logs the user out. Bounded below by session_absolute_ttl_seconds so a
    # session that is *always* active still can't live forever.
    session_ttl_seconds: int = 60 * 60 * 24 * 14
    # Absolute cap on a session's lifetime, measured from the login that
    # created it (its `issued_at`), regardless of activity. This is what
    # actually bounds exposure if a session id were ever stolen and kept
    # continuously "warm" by an attacker -- the sliding idle timeout alone
    # cannot do that. Deliberately longer than session_ttl_seconds so a
    # genuinely active user is never forced to re-authenticate by this cap
    # in practice; it exists as a backstop, not a everyday UX limit.
    session_absolute_ttl_seconds: int = 60 * 60 * 24 * 90

    # CORS / frontend. Comma-separated list of allowed browser origins.
    cors_origins: str = "http://localhost:5173"
    frontend_base_url: str = "http://localhost:5173"

    # Mail. Two supported modes (docs/LIFELINK_EMAIL.md):
    #   Mailpit (dev catcher): MAIL_HOST=mailpit|localhost, MAIL_PORT=1025,
    #     MAIL_STARTTLS=false, no credentials.
    #   Gmail SMTP: MAIL_HOST=smtp.gmail.com, MAIL_PORT=587, MAIL_STARTTLS=true
    #     (or 465 + MAIL_USE_SSL=true), MAIL_USERNAME=<gmail>,
    #     MAIL_PASSWORD=<Google App Password>, MAIL_FROM=<same gmail>.
    # MAIL_PASSWORD is a SecretStr: it never appears in repr()/logs/errors.
    mail_host: str = "localhost"
    mail_port: int = 1025
    mail_from: str = "no-reply@lifelink.local"
    mail_username: str | None = None
    mail_password: SecretStr | None = None
    mail_starttls: bool = False
    mail_use_ssl: bool = False
    mail_timeout_seconds: float = 10.0

    # Auth tuning
    email_verification_ttl_seconds: int = 60 * 60 * 24
    password_reset_otp_ttl_seconds: int = 60 * 10
    password_reset_max_attempts: int = 5

    # Phase 11: independent per-role self-registration cap. Each of the four
    # self-registerable roles (PATIENT/FAMILY/DONOR/HOSPITAL) may have up to
    # this many accounts created through POST /auth/register -- these are
    # four SEPARATE limits, not one shared pool (see
    # app.models.registration_counter.RegistrationCounter). ADMIN never
    # consumes this at all, since it can't self-register in the first place.
    max_accounts: int = 100

    @model_validator(mode="after")
    def _validate_max_accounts(self) -> Settings:
        if self.max_accounts <= 0:
            raise ValueError(
                f"MAX_ACCOUNTS must be a positive integer, got {self.max_accounts!r}"
            )
        return self

    # Rate limits: (limit, window_seconds)
    rl_login_ip: tuple[int, int] = (10, 300)
    rl_login_account: tuple[int, int] = (5, 300)
    rl_register_ip: tuple[int, int] = (5, 3600)
    rl_verify_resend_account: tuple[int, int] = (3, 3600)
    rl_forgot_ip: tuple[int, int] = (10, 3600)
    rl_forgot_account: tuple[int, int] = (3, 3600)
    rl_reset_account: tuple[int, int] = (10, 3600)
    rl_change_password_account: tuple[int, int] = (5, 3600)
    rl_verification_submit_account: tuple[int, int] = (5, 3600)
    # Phase 13: caps repeated family-link requests -- also the mitigation for
    # a FAMILY account trying to enumerate registered patient emails via the
    # request-link endpoint's exists/doesn't-exist response difference.
    rl_family_link_request_account: tuple[int, int] = (10, 3600)
    # Phase 23: POST /emergencies and POST /blood-requests had NO rate limit
    # at all -- unlike every other state-changing endpoint in the app, each
    # of these triggers a real donor/hospital notification fan-out (push +
    # email + SMS where configured), so an unbounded account could be used
    # to flood every compatible donor's phone with "EMERGENCY" pushes with
    # no cost to the caller. Deliberately generous, per the explicit
    # instruction that emergency workflows need special care: a real crisis
    # can reasonably involve several distinct broadcasts (a patient plus more
    # than one family member acting on their behalf, a location change, a
    # hospital raising it for several patients at once) well within these
    # limits, while a scripted spam loop is not. The existing idempotency-key
    # replay behavior already prevents the same *exact* submission from
    # counting twice, so this only bounds genuinely distinct creations.
    rl_emergency_create_account: tuple[int, int] = (10, 600)
    rl_blood_request_create_account: tuple[int, int] = (20, 3600)

    # Object storage
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "lifelink-dev"
    s3_presign_expiry_seconds: int = 900
    # Local fallback used only when S3 is not configured (dev / tests).
    storage_local_dir: str | None = None

    # Certificates
    certificate_download_ttl_seconds: int = 600

    # Neo4j graph engine (resource/pathway discovery). All optional: the
    # graph engine must degrade gracefully -- never fake success -- when
    # unset or unreachable. See app/core/graph.py.
    neo4j_uri: str | None = None
    neo4j_username: str | None = None
    neo4j_password: str | None = None
    neo4j_database: str | None = None
    # Max time to wait for a Neo4j connection/query before treating it as
    # unavailable, so one slow/unreachable host can't hang a request.
    neo4j_connection_timeout_seconds: float = 5.0

    @property
    def neo4j_configured(self) -> bool:
        return bool(self.neo4j_uri and self.neo4j_username and self.neo4j_password)

    # ---- Pathway planning (app/services/pathway_planner.py) ----
    # Planning ASSUMPTIONS, not measured values. Every response that uses
    # them reports them, and docs/final-verification/RESEARCH_LIMITATIONS.md
    # lists them. Change them per deployment.
    #
    # Minutes added to every leg's route transit time for issuing, packing,
    # loading and hand-over at the destination.
    pathway_handling_minutes: int = Field(default=30, ge=0, le=720)
    # A plan whose arrival leaves less than this much slack before the
    # deadline is classified DEADLINE_RISK instead of VIABLE.
    pathway_deadline_risk_minutes: int = Field(default=15, ge=0, le=720)
    # Longest path (in transport routes) the search will consider.
    pathway_max_hops: int = Field(default=6, ge=1, le=12)
    # How many backup pathways are stored alongside the primary.
    pathway_max_backups: int = Field(default=3, ge=0, le=10)

    # ---- Donor distance policy ----
    # Donors whose known distance from the request/emergency exceeds this are
    # excluded from ranking (a donor exactly at the limit is included). An
    # emergency can lift the limit only through an audited ADMIN override.
    max_donor_distance_km: float = Field(default=50.0, gt=0, le=20000)
    # Audit D4 (2026-10-31): the shared policy in app/core/distance_policy.py.
    # A request with neither coordinates nor a city alerts no donor unless
    # this is deliberately enabled (off by default: no silent nationwide
    # broadcast).
    donor_alert_allow_unscoped: bool = False
    # A shared donor location older than this is reported as stale (still
    # used for the distance check, but labelled and scored lower).
    donor_location_stale_days: int = Field(default=90, ge=1, le=3650)

    # ---- Donor eligibility policy (app/core/eligibility.py) ----
    # IN_DCR_2020: India, Drugs and Cosmetics (Second Amendment) Rules 2020
    #   (G.S.R. 166(E), 11 Mar 2020), Schedule F Part XII-B, H(4): whole blood
    #   once in 90 days (males) / 120 days (females). LifeLink does not record
    #   donor sex, so the stricter 120-day interval is applied.
    # US_21CFR630: United States, 21 CFR 630.15(a)(1)(i): once in 8 weeks
    #   (56 days) -- the interval LifeLink used before 2026-10-31.
    # Either way the software check is a pre-screen; clinical review pending.
    donor_eligibility_policy: str = "IN_DCR_2020"

    # ---- Unified Donor Priority Score (app/core/unified_priority.py) ----
    # Provisional engineering weights (NOT validated probabilities, NOT a
    # donation probability). Validated at start-up (finite, >= 0, positive
    # sum) and normalised to sum to 1. Changing any of them changes the
    # model fingerprint that is stored with every contact decision.
    # (Replaces SHORTLIST_WEIGHT_* of the 2026-10-31 CONFIDENCE_SHORTLIST
    # score, which this model supersedes; see docs/UNIFIED_DONOR_PRIORITY.md.)
    priority_weight_availability: float = Field(default=0.25, ge=0, le=1)
    priority_weight_response: float = Field(default=0.15, ge=0, le=1)
    priority_weight_acceptance: float = Field(default=0.10, ge=0, le=1)
    priority_weight_distance: float = Field(default=0.20, ge=0, le=1)
    priority_weight_activity: float = Field(default=0.05, ge=0, le=1)
    priority_weight_freshness: float = Field(default=0.10, ge=0, le=1)
    priority_weight_verification: float = Field(default=0.15, ge=0, le=1)
    # Below this share of (weighted) observed evidence the score is reported
    # as unavailable and the candidate is ordered by the TIME_RESILIENCE
    # fallback after all scored candidates.
    priority_min_coverage: float = Field(default=0.30, ge=0, le=1)
    # Responses needed before a donor's own response rate is trusted fully;
    # with fewer, the rate is shrunk towards a neutral prior of 0.5.
    shortlist_min_response_samples: int = Field(default=3, ge=1, le=100)
    # How many candidates to contact per requested unit/donor, per batch.
    shortlist_contact_multiplier: int = Field(default=2, ge=1, le=10)
    shortlist_max_batch: int = Field(default=10, ge=1, le=100)
    # A contacted donor who has not answered after this long is TIMED_OUT.
    # CRITICAL requests use the shorter value.
    shortlist_response_timeout_minutes: int = Field(default=30, ge=1, le=1440)
    shortlist_response_timeout_minutes_critical: int = Field(default=10, ge=1, le=1440)
    # Meaningful activity within this window counts as "recent".
    shortlist_activity_window_days: int = Field(default=30, ge=1, le=365)
    # After a decline or a timeout, contact the next ranked donors automatically
    # (only for a request whose shortlist has been started).
    shortlist_auto_escalate: bool = True
    # How donors first hear about a new request/emergency:
    #   broadcast  -- every eligible donor inside the distance policy gets the
    #                 open alert (the behaviour before 2026-10-31); the
    #                 shortlist then tracks and personally invites the top few.
    #   shortlist  -- no open alert; only the first ranked batch is contacted,
    #                 and further batches follow declines/timeouts.
    donor_alert_mode: str = "broadcast"

    # ---- Demonstration mode (synthetic donors only) ----
    # Enables the admin-only response simulator for SYNTHETIC donors
    # (e-mail domain synthetic.lifelink.example). Refused in production.
    demo_mode: bool = False

    # ---- Hospital emergency alerts ----
    # Verified facilities within this radius of an emergency's coordinates
    # that hold compatible usable stock are alerted.
    hospital_alert_radius_km: float = Field(default=50.0, gt=0, le=20000)
    # At most this many emergency alerts per donor per rolling hour.
    donor_emergency_alerts_per_hour: int = Field(default=6, ge=1, le=1000)

    # ---- Deadline monitor (app/workers/deadline_monitor.py) ----
    deadline_warning_minutes: int = Field(default=30, ge=1, le=1440)
    deadline_monitor_interval_seconds: int = Field(default=60, ge=5, le=3600)

    # ---- Cold-chain telemetry ----
    # A gap longer than this between consecutive readings is reported as
    # DATA_GAP (missing data), never silently treated as in-range.
    telemetry_max_gap_minutes: int = Field(default=30, ge=1, le=1440)

    # ---- Geocoding (app/services/geocoding.py) ----
    # Optional external geocoder (a Nominatim-compatible /search endpoint).
    # OFF by default: an emergency's free-text address is then resolved only
    # against the local facility gazetteer, so no patient location text is
    # sent to a third party unless a deployment opts in.
    geocoder_url: str | None = None
    geocoder_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    geocoder_user_agent: str = "LifeLink/1.0 (emergency geocoding)"

    # ---- Synthetic-data disclosure ----
    # True for every deployment whose data comes from the seed/simulation
    # scripts. Shown on dashboards, analytics, forecasts and exports.
    data_is_synthetic: bool = True

    # SMS (MSG91) -- see app/services/sms/ and LIFELINK_SMS_PROVIDER.md.
    # All optional, same pattern as the Neo4j graph engine: unset is a normal
    # "feature off" state, never faked as success (app/services/sms_service.py).
    # Which SMS provider to use: "msg91" (real; needs MSG91_* below),
    # "console" (development only: the message is written to the log and
    # recorded as LOGGED_ONLY -- never as delivered; refused in production),
    # or "none".
    sms_provider: str = "msg91"
    msg91_enabled: bool = False
    msg91_auth_key: str | None = None
    msg91_flow_id: str | None = None
    msg91_sender_id: str | None = None
    msg91_dlt_template_id: str | None = None
    msg91_timeout_seconds: float = 10.0
    # Optional per-template Flow ID overrides, as JSON: {"EMERGENCY_CREATED": "<flow_id>", ...}.
    # A real MSG91/DLT setup registers one approved message template (and
    # therefore one Flow) per distinct wording -- MSG91_FLOW_ID alone can't
    # serve six differently-worded, individually DLT-approved templates.
    # Falls back to msg91_flow_id for any template key not present here.
    msg91_template_flow_map: str | None = None
    # Region used to parse a phone number typed without a country code
    # (app/core/phone.py). LifeLink targets India by default.
    sms_default_region: str = "IN"
    # RQ queue SMS jobs are enqueued on (kept separate from the email queue
    # so an SMS backlog/outage can never delay email delivery, and so the
    # worker's per-queue metrics stay meaningful). The same worker process
    # listens to both (app/workers/worker.py).
    rq_sms_queue_name: str = "sms"

    # Phase 30: queue-depth health thresholds for GET /admin/system/health's
    # `rq` component (app/workers/queue.py::email_queue_health). Config
    # values, not hardcoded, per the master prompt's own instruction ("make
    # thresholds configuration values rather than hardcoded assumptions").
    # Origins, stated rather than left to look arbitrary:
    #   - warning: replaces the ">1000" magic number Phase 29 hardcoded
    #     directly in admin.py::system_health -- moved here unchanged in
    #     value, now overridable per deployment.
    #   - critical: two orders of magnitude below the real 148,965-job
    #     backlog Phase 28 found accumulated with no bound -- deep enough
    #     that it is unambiguously a real backlog needing attention, not a
    #     normal traffic bump, while still catching a growing problem long
    #     before it reaches Phase 28's scale.
    #   - stale-job warning age: half of email_job_queue_ttl_seconds (the
    #     TTL a job is discarded at if never started) -- a job that has
    #     been waiting half its own lifetime is a legitimate "worker may
    #     not be keeping up" signal before it's actually dropped.
    rq_queue_depth_warning_threshold: int = 1000
    rq_queue_depth_critical_threshold: int = 10_000
    rq_stale_queued_warning_seconds: int = 43_200

    # Phase 30: safe, non-secret build/version identification for the
    # admin-only system-health endpoint (Section 22 of the master prompt).
    # Both default to a plain "unknown" -- this app never invents a commit
    # SHA or version number; a real deployment sets these from its own CI
    # (e.g. `GIT_COMMIT_SHA=$(git rev-parse HEAD)` at image build time).
    app_version: str = "0.1.0"
    git_commit_sha: str = "unknown"

    # Phase 34: the shelf-life window (days) a new, real `BloodUnit` row
    # gets at creation -- `expires_at = received_at + this`. This is an
    # OPERATIONAL PARAMETER, not a cited clinical/regulatory standard: the
    # Phase E: the former `blood_unit_shelf_life_days` (35) setting was
    # removed. Since Phase 35 nothing read it -- every shelf-life figure
    # comes from the cited `app.core.clinical_sources.COMPONENT_SHELF_LIFE`
    # -- so keeping it only advertised a knob that silently did nothing.
    # (extra="ignore" above means an old env file that still sets
    # BLOOD_UNIT_SHELF_LIFE_DAYS keeps loading; it just has no effect, as
    # it already had none before this removal.)

    sms_max_retries: int = 3
    # Base delay before the first retry of a retryable failure; doubles each
    # attempt (1x, 2x, 4x, ...). Not used when RQ_SYNC=true (see sms_service).
    sms_retry_base_seconds: int = 30

    @property
    def sms_provider_configured(self) -> bool:
        """Whether MSG91 has the minimum configuration to attempt a send.

        Mirrors ``neo4j_configured``/``storage_backend()``: this is a plain
        "is it set up" check, not a reachability probe. It intentionally
        does NOT require msg91_flow_id or msg91_dlt_template_id -- a
        deployment can be legitimately configured (real auth key + sender
        id) with only per-template Flow IDs set via
        msg91_template_flow_map, no single global default. Whether a
        *specific* template is actually sendable is resolved per-attempt by
        flow_id_for()/send_sms, which honestly reports INVALID_TEMPLATE
        rather than guessing when neither a global nor a per-template Flow
        ID exists for it.
        """
        provider = self.sms_provider.strip().lower()
        if provider == "console":
            return not self.is_production
        if provider != "msg91":
            return False
        return bool(self.msg91_enabled and self.msg91_auth_key and self.msg91_sender_id)

    # Verification document uploads
    verification_max_file_bytes: int = 10 * 1024 * 1024
    verification_max_files: int = 5
    verification_allowed_content_types: str = "application/pdf,image/jpeg,image/png"

    # Malware scanning of uploaded documents (app/services/document_scanner.py).
    # "clamd" = ClamAV daemon over TCP (required in production); "none" =
    # explicitly disabled (dev/test only; recorded as "not scanned").
    document_scanner: str = "none"
    clamd_host: str = "localhost"
    clamd_port: int = 3310
    clamd_timeout_seconds: float = 15.0
    # Delay before the worker retries a document whose scan failed.
    document_rescan_delay_seconds: int = 300
    # Escape hatch for a production-labelled environment that genuinely has
    # no scanner (documents then stay quarantined only if a scan errors --
    # "none" records them as unscanned). Must be set deliberately.
    document_scanner_allow_none_in_production: bool = False

    # Hardening -- HMAC-signed audit-chain checkpoints, stored OUTSIDE the
    # database (app.services.audit_checkpoint_service). Disabled unless both
    # the key and the path are set. The key must not be SECRET_KEY and should
    # live in a secret store the database credentials cannot read; the path
    # should be on storage the database credentials cannot write (separate
    # volume / host, ideally append-only or object-locked). Retired keys
    # (after rotation) stay verifiable: "id1=key1;id2=key2".
    audit_checkpoint_key: str = ""
    audit_checkpoint_key_id: str = "k1"
    audit_checkpoint_retired_keys: str = ""
    audit_checkpoint_path: str = ""
    # Audit E4 (2026-10-31): when true, the API refuses to start unless
    # checkpoints are configured (fail closed). The container stack sets it.
    audit_checkpoint_required: bool = False
    # How often the deadline monitor writes a checkpoint when enabled (0 = never;
    # scripts/audit_checkpoint.py can still be scheduled externally).
    audit_checkpoint_interval_minutes: int = Field(default=15, ge=0, le=1440)

    @property
    def verification_allowed_content_type_list(self) -> list[str]:
        return [t.strip() for t in self.verification_allowed_content_types.split(",") if t.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def is_test(self) -> bool:
        return self.environment.lower() in {"test", "testing"}

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    # The literal default below is intentionally recognizable (never a random
    # string) so that the check in _validate_secret_key can tell "operator
    # never set SECRET_KEY" apart from "operator picked something weak" --
    # this codebase has no way to judge the latter, and doesn't try to.
    DEFAULT_SECRET_KEY: ClassVar[str] = "dev-insecure-secret-change-me"
    # Phase 28: minimum SECRET_KEY length enforced in production -- see
    # _validate_secret_key below.
    MIN_PRODUCTION_SECRET_KEY_LENGTH: ClassVar[int] = 32

    @model_validator(mode="after")
    def _validate_secret_key(self) -> Settings:
        """Fail closed on booting in production with the shipped-in-source
        default secret key.

        ``secret_key`` is the sole key behind ``app.core.security.sign()``
        (used to store password-reset OTPs and email-verification tokens at
        rest as HMACs) and behind certificate download tokens
        (``certificate_service.make_download_token``/``check_download_token``).
        Anyone who can read this source -- which is everyone, since the
        default is a plaintext literal in a public-shaped settings class --
        can forge or verify those HMACs for a deployment that never
        overrode it. This was flagged in LIFELINK_PHASE_0_AUDIT.md section
        12 and never fixed; mirrors the existing _validate_cookie_security
        fail-closed pattern below rather than only warning.
        """
        if self.is_production and self.secret_key == self.DEFAULT_SECRET_KEY:
            raise ValueError(
                "SECRET_KEY must be set to a unique, non-default value when "
                "ENVIRONMENT=production -- refusing to start with the "
                "publicly-known default, which would let anyone forge "
                "password-reset/verification tokens and certificate "
                "download links."
            )
        # Phase 28: "not the shipped default" alone doesn't rule out an
        # operator setting SECRET_KEY=x -- technically unique, still trivial
        # to guess/brute-force as HMAC-SHA256 key material. 32 bytes (256
        # bits) matches the hash's own output size, the standard minimum
        # recommended for an HMAC key (RFC 2104: a key shorter than the
        # hash output is weaker than one at least that long). This is a
        # length floor, not a strength/entropy judgement this codebase has
        # no way to make -- a 32-char low-entropy string still passes, same
        # as the existing default-value check doesn't judge quality either.
        if self.is_production and len(self.secret_key) < self.MIN_PRODUCTION_SECRET_KEY_LENGTH:
            raise ValueError(
                "SECRET_KEY must be at least "
                f"{self.MIN_PRODUCTION_SECRET_KEY_LENGTH} characters when "
                "ENVIRONMENT=production -- refusing to start with a key too "
                "short to be a safe HMAC-SHA256 secret. Generate one with, "
                "e.g., `python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"`."
            )
        return self

    @model_validator(mode="after")
    def _validate_log_level(self) -> Settings:
        import logging

        level_name = self.log_level.strip().upper()
        if not isinstance(logging.getLevelName(level_name), int):
            raise ValueError(
                f"LOG_LEVEL must be a valid logging level name (DEBUG/INFO/WARNING/"
                f"ERROR/CRITICAL), got {self.log_level!r}"
            )
        self.log_level = level_name
        return self

    @model_validator(mode="after")
    def _validate_cors_origins(self) -> Settings:
        """Phase H: fail closed on a CORS origin list that would expose
        authenticated responses to other sites.

        The API runs CORSMiddleware with ``allow_credentials=True`` (the
        session is a cookie). With a ``*`` entry, Starlette answers a
        credentialed request by echoing back WHATEVER Origin sent it plus
        ``Access-Control-Allow-Credentials: true`` -- i.e. any website a
        signed-in user visits could read their notifications, requests and
        profile via fetch(..., {credentials: "include"}). verify_origin only
        protects state changes, not reads. Previously this was only a
        sentence in the deployment checklist; now the process refuses to
        start, in every environment.

        Also refused: an entry that is not a bare ``scheme://host[:port]``
        origin (a trailing slash or a path never equals a browser's Origin
        header, so the frontend would silently fail every request).
        """
        for origin in self.cors_origin_list:
            if "*" in origin:
                raise ValueError(
                    "CORS_ORIGINS must list exact origins; a wildcard with "
                    f"credentialed cookies exposes every user's data ({origin!r})"
                )
            if not re.fullmatch(r"https?://[A-Za-z0-9.\-]+(:\d{1,5})?", origin):
                raise ValueError(
                    "CORS_ORIGINS entries must be bare origins like "
                    f"https://app.example.org (no path or trailing slash), got {origin!r}"
                )
        return self

    @model_validator(mode="after")
    def _validate_document_scanner(self) -> Settings:
        """Hardening: production must not silently accept unscanned uploads."""
        backend = self.document_scanner.strip().lower()
        if backend not in {"none", "clamd"}:
            raise ValueError(
                f"DOCUMENT_SCANNER must be 'clamd' or 'none', got {self.document_scanner!r}"
            )
        if (
            self.is_production
            and backend == "none"
            and not self.document_scanner_allow_none_in_production
        ):
            raise ValueError(
                "DOCUMENT_SCANNER=none is refused when ENVIRONMENT=production: uploaded "
                "documents would never be malware-scanned. Run clamd and set "
                "DOCUMENT_SCANNER=clamd (or set DOCUMENT_SCANNER_ALLOW_NONE_IN_PRODUCTION=true "
                "deliberately)."
            )
        return self

    @model_validator(mode="after")
    def _validate_mail(self) -> Settings:
        """Hardening: refuse mail settings that cannot work, instead of
        failing silently at the first send. Messages never include the
        password."""
        has_user = bool(self.mail_username)
        has_pw = bool(self.mail_password and self.mail_password.get_secret_value())
        if has_user != has_pw:
            raise ValueError("MAIL_USERNAME and MAIL_PASSWORD must be set together")
        if self.mail_starttls and self.mail_use_ssl:
            raise ValueError("MAIL_STARTTLS and MAIL_USE_SSL are mutually exclusive")
        if self.mail_host.strip().lower() == "smtp.gmail.com":
            if not has_user:
                raise ValueError(
                    "Gmail SMTP needs MAIL_USERNAME (the Gmail address) and MAIL_PASSWORD "
                    "(a Google App Password)"
                )
            if not ((self.mail_port == 587 and self.mail_starttls)
                    or (self.mail_port == 465 and self.mail_use_ssl)):
                raise ValueError(
                    "Gmail SMTP requires MAIL_PORT=587 with MAIL_STARTTLS=true "
                    "(or MAIL_PORT=465 with MAIL_USE_SSL=true)"
                )
        if has_user and not (self.mail_starttls or self.mail_use_ssl) \
                and self.mail_host.strip().lower() not in {"localhost", "127.0.0.1", "mailpit"}:
            raise ValueError(
                "Refusing to send SMTP credentials over an unencrypted connection: "
                "set MAIL_STARTTLS=true or MAIL_USE_SSL=true"
            )
        return self

    @property
    def mail_provider(self) -> str:
        """`gmail`, `mailpit` (local dev catcher) or `smtp` (any other)."""
        host = self.mail_host.strip().lower()
        if host == "smtp.gmail.com":
            return "gmail"
        if host in {"mailpit", "localhost", "127.0.0.1"} and not self.mail_username:
            return "mailpit"
        return "smtp"

    def mail_summary(self) -> str:
        """Loggable description of the mail transport -- never the password."""
        tls = "ssl" if self.mail_use_ssl else "starttls" if self.mail_starttls else "plain"
        auth = "auth" if self.mail_username else "no-auth"
        return f"{self.mail_provider} {self.mail_host}:{self.mail_port} {tls} {auth}"

    @model_validator(mode="after")
    def _validate_audit_checkpoint(self) -> Settings:
        if bool(self.audit_checkpoint_key) != bool(self.audit_checkpoint_path):
            raise ValueError(
                "AUDIT_CHECKPOINT_KEY and AUDIT_CHECKPOINT_PATH must be set together "
                "(signed audit checkpoints need both a key and somewhere to write them)"
            )
        if self.audit_checkpoint_key:
            if len(self.audit_checkpoint_key) < 32:
                raise ValueError("AUDIT_CHECKPOINT_KEY must be at least 32 characters")
            if self.audit_checkpoint_key == self.secret_key:
                raise ValueError("AUDIT_CHECKPOINT_KEY must differ from SECRET_KEY")
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", self.audit_checkpoint_key_id):
                raise ValueError("AUDIT_CHECKPOINT_KEY_ID must be 1-32 of [A-Za-z0-9_.-]")
        if self.audit_checkpoint_required and not self.audit_checkpoints_enabled:
            raise ValueError(
                "AUDIT_CHECKPOINT_REQUIRED=true but AUDIT_CHECKPOINT_KEY / AUDIT_CHECKPOINT_PATH "
                "are not set -- refusing to start without the tamper-evidence the deployment "
                "requires (see docs/AUDIT_CHECKPOINTS.md)."
            )
        return self

    def priority_weights_raw(self) -> dict[str, float]:
        return {
            "availability": self.priority_weight_availability,
            "response": self.priority_weight_response,
            "acceptance": self.priority_weight_acceptance,
            "distance": self.priority_weight_distance,
            "activity": self.priority_weight_activity,
            "freshness": self.priority_weight_freshness,
            "verification": self.priority_weight_verification,
        }

    @model_validator(mode="after")
    def _validate_policies(self) -> Settings:
        if self.donor_alert_mode not in {"broadcast", "shortlist"}:
            raise ValueError("DONOR_ALERT_MODE must be broadcast or shortlist")
        if self.donor_eligibility_policy not in {"IN_DCR_2020", "US_21CFR630"}:
            raise ValueError("DONOR_ELIGIBILITY_POLICY must be IN_DCR_2020 or US_21CFR630")
        if self.demo_mode and self.is_production:
            raise ValueError("DEMO_MODE must not be enabled when ENVIRONMENT=production")
        # Unified priority weights: finite, non-negative, positive sum
        # (app.core.unified_priority.validate_weights is the single check).
        from app.core.unified_priority import validate_weights

        validate_weights(self.priority_weights_raw())
        return self

    @property
    def audit_checkpoints_enabled(self) -> bool:
        return bool(self.audit_checkpoint_key and self.audit_checkpoint_path)

    @model_validator(mode="after")
    def _validate_cookie_security(self) -> Settings:
        """Fail closed on the two cookie misconfigurations that would
        otherwise ship an insecure session cookie: running in production
        without Secure, and SameSite=None without Secure (browsers drop such
        a cookie outright, so this indicates a broken config, not just a
        theoretical risk). This is intentionally NOT "always require
        Secure=true" or "always require SameSite=lax" -- which of Lax/
        Strict/None is correct depends on whether the frontend and backend
        share a registrable domain in this deployment (see
        LIFELINK_PHASE_7_REPORT.md), so the choice itself is left to
        deployment configuration; only the unsafe combinations are refused.
        """
        samesite = self.session_cookie_samesite.strip().lower()
        if self.is_production and not self.session_cookie_secure:
            raise ValueError(
                "SESSION_COOKIE_SECURE must be true when ENVIRONMENT=production "
                "-- refusing to start with a session cookie that would be sent "
                "over plain HTTP."
            )
        if samesite == "none" and not self.session_cookie_secure:
            raise ValueError(
                "SESSION_COOKIE_SAMESITE=none requires SESSION_COOKIE_SECURE=true "
                "-- browsers reject a SameSite=None cookie that isn't also Secure."
            )
        if samesite not in {"lax", "strict", "none"}:
            raise ValueError(
                f"SESSION_COOKIE_SAMESITE must be one of lax/strict/none, got {samesite!r}"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
