# LifeLink — Real-Time Event Layer

Introduced in Phase F. Code: `backend/app/models/realtime_event.py`, `backend/app/services/realtime_service.py` (creation + publication), `backend/app/services/realtime_audience.py` (authorization), `backend/app/api/routes/events.py` (`GET /events/stream`), `frontend/src/lib/realtime.tsx` (client). Migration `e4f5a6b7c8d9`. Tests: `backend/tests/test_phase_f_realtime.py`, `backend/tests/test_phase_f_realtime_stream.py`, `frontend/src/lib/realtime.test.tsx`, `frontend/e2e/phase_f_realtime.spec.ts`.

## Pipeline

```
business transaction ──(same tx)──► realtime_events row (outbox)
        │ COMMIT; transaction ends; connection returned to the pool
        ▼
publisher (own connection, pg_advisory_lock held until the publish is done)
   1. assign seq = head+1… to committed, unsequenced rows (created_at, id order)
   2. compute + store the audience from the REST visibility rules
   3. COMMIT, then PUBLISH each row to Redis in seq order, mark published_at
        ▼
GET /events/stream (SSE) — ONE Redis subscription per process, shared via _Broker (hardening)
   subscribe → snapshot head → replay (Last-Event-ID, cursor..head) → `ready`
   → live: drop seq ≤ last, deliver if the user is in the audience
   → `ping` (with id = last seq) every heartbeat; session re-checked (no slide)
        ▼
browser EventSource → invalidate React Query keys → normal authorized refetch
```

### Why each piece exists

| Concern | Mechanism |
|---|---|
| Never announce a rolled-back change; never lose a committed one | Outbox row written by a `before_flush` listener in the **same transaction**. Rolled-back work leaves no row (`test_rolled_back_change_creates_no_event`). |
| Coverage without editing every route | The listener maps the history rows every mutation in this codebase already writes — `BloodRequestEvent`, `EmergencyEvent`, `ShipmentEvent`, `HospitalInventoryLog`, `Notification` (new + `read_at` change) — plus `Donation` (new / `verification_status`) and `VerificationRequest` (new / `status`). |
| One change, one event | Deduplicated per transaction on `(type, resource)`. |
| No pool deadlock, no business lock contention | Publication runs after the root transaction ends, after the connection is back in the pool (same lesson as the Phase E audit sealer). **Hardening:** by default (`REALTIME_PUBLISH_MODE=background`) the request thread only wakes a per-process background thread (`lifelink-realtime-publisher`), which publishes on its own pooled connection; the request no longer pays for sequencing/publication. The thread also runs every `REALTIME_PUBLISHER_INTERVAL_SECONDS` (5 s), so a backlog left by a crash or Redis outage is drained even with no further writes. `inline` mode keeps the old behaviour (used by a few tests). An `atexit` hook flushes pending work on clean shutdown; anything left is published by the next process. |
| Ordering / exact replay | `seq` is assigned only to committed rows, under a session advisory lock held **until the Redis publish completes**, so publication is globally in seq order and "everything after N" is exact. |
| Redis outage | Rows keep their seq with `published_at` NULL; the next run republishes them first, in order (`test_redis_outage_keeps_events_and_republishes_in_order`). Reconnecting clients replay from Postgres regardless. |
| Two environments on one Redis | Pub/sub ignores Redis database numbers, so the channel is `lifelink:realtime:<postgres database name>`. Found during this phase: the backend suite and a live e2e server shared Redis, and a foreign larger seq would have made a stream discard its own events as "already seen". |
| Duplicates / stale events | Server drops `seq ≤ last sent`; client drops `seq ≤ last seen` and ignores anything ≤ the `ready` head. |
| Missed events | `Last-Event-ID` replay from Postgres (audience-filtered in SQL with the stored audience, GIN index). More than `realtime_replay_limit` (500) missed, an unknown/unparseable cursor, or a cursor older than retained rows → `resync`: the client re-fetches everything it shows. |
| Long-lived stream after logout/suspension | Session re-validated every `realtime_session_recheck_seconds` **without** sliding the idle timeout (`_load_user_from_request(touch=False)`); invalid → `session_ended`, stream closes, client routes to the session-expired page. |
| Proxies closing idle streams | `ping` every `realtime_heartbeat_seconds`; `X-Accel-Buffering: no`, `Cache-Control: no-store`. |
| An open stream pinning a DB connection | Auth and every replay/re-check use a short-lived session closed immediately; the stream itself holds no DB connection. |
| Redis connections per viewer | **Hardening:** one Redis pub/sub reader per process/event loop (`_Broker`) fans out to every open stream through bounded in-memory queues (1,000). A stream that falls behind gets `resync` (reason `stream_lagged`) instead of blocking others; a lost Redis connection sends `resync` to every stream and the reader reconnects with capped backoff. Tests: `tests/test_hardening_realtime.py` (4 streams → 1 reader; lagging stream; Redis unreachable). |
| Retention | Published rows older than `realtime_retention_hours` (72) are deleted by the publisher once per `PRUNE_EVERY` (500) sequenced events, via `ix_realtime_events_created_at`. Unpublished rows are never deleted. (Phase K: the original trigger never fired in ordinary traffic -- see the Phase K report.) |
| Backlog | After each commit the publisher repeats runs (each ≤ 500 sequenced / ≤ 2,000 published, each its own short lock-ordered transaction) until a run is not full, so a single transaction that creates thousands of events is fully published before the request returns (Phase K; previously one batch per commit). The "pending publish" read uses the partial index `ix_realtime_events_pending_publish`. |

## Authorization

The audience is computed from the **same rules the REST API enforces** for reading the resource (`realtime_audience.py` documents the mapping), and never exceeds them — `test_audience_never_exceeds_rest_visibility_for_requests_and_emergencies` checks every audience member against the real `_can_view` functions over a mixed population. Notifications go only to their owner (never ADMIN). The payload is only `{seq, type, resource_type, resource_id}`; even an audience member learns only "re-fetch this", and the re-fetch is authorized normally.

Deliberate narrowing (smaller than API visibility, never larger): donors who have not responded to an open request/emergency are not individually enumerated; they receive a `notification.created` event for the matching-donor notification itself.

Live-stream isolation is tested over a real HTTP server (uvicorn in a thread, real Redis): owner + city hospital + admin receive a request event; another patient does not; a donor who did not respond does not; unauthenticated → 401; logout ends the stream.

## Client

`RealtimeProvider` (mounted once, keyed by user id so a different account never inherits a stream) opens `EventSource(…/events/stream, {withCredentials: true})`, maps `resource_type` → query-key prefixes (`INVALIDATION_MAP`), batches invalidations (150 ms), and:

- `ready` after a gap → re-fetches everything on screen (cheap insurance against stale UI);
- `resync` → re-fetches everything;
- browser gives up (401/5xx on connect) → its own capped exponential backoff;
- `session_ended` → closes and raises the existing `lifelink:session-expired` flow.

The 12 former polling sites now call `useLiveFallbackInterval(ms)`: `false` while live, the old interval only while the stream is down. Status is shown as "Live" / "Reconnecting…" in the notifications panel (`role="status"`, `aria-live="polite"`).

## Known limitations

- (Resolved in hardening) one Redis connection per open stream → shared per-process broker.
- (Resolved in hardening) publication in the request thread → background publisher; latency from commit to publish is now "next wake of the thread" (measured in `docs/LIFELINK_HARDENING_REPORT.md`).
- (Resolved in hardening) family links and certificates are now evented: `family_link.updated` (audience: the two linked parties only, no roles) and `certificate.updated` (issue/revoke; audience: the donor + ADMIN), mirroring the REST read rules. Client invalidates `["family"]`, `["certificates"]`, `["admin","certificates"]`.
- The broker is per process: N API worker processes hold N Redis readers. Each queue is in memory; a process crash drops its streams, which reconnect and replay from Postgres via Last-Event-ID.
- A process that is killed (SIGKILL) between commit and the background publish leaves rows unpublished until another process's publisher runs (at most one interval later); nothing is lost because the rows are committed.
