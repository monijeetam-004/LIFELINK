from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import ORMExecuteState, Session, sessionmaker

from app.core.config import settings

# Phase 24: pool_size/max_overflow were investigated as a possible fix for a
# real, reproduced degradation -- see LIFELINK_PHASE_24_PERFORMANCE_REPORT.md's
# Connection Pool Findings section for the full before/after evidence. A
# concurrency-50 burst across several read/write endpoints on this box's
# real dev Postgres drove pooled connections into `idle in transaction` /
# `ClientRead`, and a handful of endpoints stayed unresponsive at even
# concurrency=1 well after the burst ended. The hypothesis was that the
# library-default 15-connection ceiling (pool_size=5, max_overflow=10) was
# the bottleneck; raising it to a 50-connection ceiling (pool_size=20,
# max_overflow=30) was tried and measured with the IDENTICAL load-test
# methodology, same fresh-restart procedure, same scenarios. Result: NO
# meaningful improvement -- the same endpoints degraded and stalled the
# same way at the same concurrency levels with either setting (in some
# cases marginally worse with the larger pool). That rules out pool size as
# the bottleneck: with only 2 CPUs backing a single uvicorn worker process
# serving 110 of this app's 111 routes as synchronous `def` handlers
# (Starlette/anyio's default threadpool), the real constraint is CPU/thread
# scheduling capacity, not how many Postgres connections are allowed to be
# open at once -- more open connections just means more of them sit idle
# waiting for a CPU-bound thread slot, not more real parallelism.
#
# Per this phase's own rule ("if an optimization shows no meaningful
# improvement, REVERT IT"), the pool-size change was reverted to the
# library defaults it started at, and the bottleneck was documented as a
# CPU/thread-scheduling limitation.
#
# Phase 27 re-investigation: that CPU/thread-scheduling conclusion does not
# hold up under direct measurement in this environment. A live `top -bn1`
# during a reproduced outage showed 0.0% system CPU usage, and a `py-spy
# dump` of the running process showed all busy AnyIO worker threads blocked
# identically at this exact pool checkout call (sqlalchemy/pool/impl.py
# `_do_get`) -- not executing any CPU-bound Python code -- while roughly a
# third of AnyIO's own thread pool sat genuinely idle in its work queue.
# Neither half of "CPU/thread-scheduling capacity" held up: the CPU was not
# busy, and thread-pool capacity was not exhausted either. The actual
# bottleneck was a real, fixable application bug: `notification_service.
# notify_matching_donors()` ran an unbatched per-donor loop (~3 DB round
# trips + 1 Redis round trip per matching donor, ~129 real donors for
# O_NEG) inside the SAME open transaction/connection for the entire loop --
# see that function's own docstring for the batching fix. That bug, not
# pool size, is why Phase 24's pool-size experiment showed no improvement:
# a bigger pool just gave more of the same multi-second stalled
# transactions somewhere to sit, without shortening any of them.
#
# With that fix in place, per-request hold time for the write endpoints
# that trigger this path dropped from several seconds to ~250ms in
# isolation (measured directly: scripts/phase27_loadtest.py single-
# scenario runs at c=25/c=50 -- see the Phase 27 report's before/after
# table). Given that, this exact pool-size experiment was REPEATED here
# (library default 5/10 -> 15/15, a modest 2x ceiling) to check whether,
# with transaction lifetime no longer the dominant cost, more headroom
# would help the harshest synthetic reproduction (`scripts/
# phase27_loadtest.py mixed 25 10` -- all 6 scenarios' concurrency-25
# bursts fired simultaneously). Measured result: NO improvement --
# pg_stat_activity showed all 30 connections pinned `idle in transaction`
# just as the 15-connection ceiling had been, and every one of the 150
# concurrent requests still timed out at 10s, 15s, and even 30s/60s
# client timeouts (5xx errors appeared at 60s from the pool's own
# checkout-timeout firing). Per this phase's own rule ("if an
# optimization shows no meaningful improvement, REVERT IT"), the
# pool-size change is reverted here, a second time, for a different
# reason than Phase 24's: the remaining bottleneck under this specific
# all-scenarios-at-once synthetic burst is not the Postgren connection
# ceiling but the per-donor notification fan-out loop's Redis round
# trips (still sequential, still executed while holding a DB connection)
# saturating under ~50 concurrent 129-donor loops at once. This is
# documented as a NOT-FIXED, narrowed-further finding in the Phase 27
# report rather than papered over with a config value that, again,
# measurably didn't fix it.
#
# Phase 28: connect_timeout / statement_timeout / pool_timeout / pool_recycle
# added below. These are NOT a pool-size change (pool_size/max_overflow are
# deliberately left at library defaults, per the history above) -- they
# bound connection/query *lifetime and hang time*, a property no prior
# phase measured or touched. See app/core/config.py's own comment on each
# setting for the exact rationale and default.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
    pool_timeout=settings.db_pool_timeout_seconds,
    pool_recycle=settings.db_pool_recycle_seconds,
    connect_args={
        "connect_timeout": settings.db_connect_timeout_seconds,
        "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
    },
)
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True
)


@event.listens_for(Session, "do_orm_execute")
def _locking_reads_refresh_loaded_objects(state: ORMExecuteState) -> None:
    """Phase J: a `SELECT ... FOR UPDATE` must return what it locked.

    Sessions here use `expire_on_commit=False`, and SQLAlchemy does not
    overwrite the attributes of an object that is already in the session's
    identity map when it loads that row again -- not even for a locking
    read (`select(...).with_for_update()` or `Session.get(...,
    with_for_update=True)`). So a lock taken on a row the session had
    already loaded (or re-taken after a mid-transaction commit, as the
    lazy expiry sweep in `inventory_service._get_or_create_row` does)
    returned the OLD values while holding the NEW lock: the subsequent
    read-check-write then overwrote another transaction's committed
    change. Reproduced by the Phase J race R4 (concurrent stock additions
    while sweeping reads ran): the aggregate lost a committed +2.

    Every locking ORM read now refreshes the loaded objects from the row it
    locked (`populate_existing`). Non-locking reads are unchanged."""
    if (
        state.is_select
        and getattr(state.statement, "_for_update_arg", None) is not None
        and not state.execution_options.get("populate_existing")
    ):
        state.update_execution_options(populate_existing=True)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
