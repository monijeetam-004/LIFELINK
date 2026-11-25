# LifeLink — Phase F: Real-Time Event Layer

**Date:** 2026-10-05 · **Scope:** the Phase A plan's Phase F — "real-time event layer (backend + frontend)": the matrix row *Real-time architecture* (11+ polling sites → a push mechanism for emergency / request / inventory / notification events, SSE with Redis pub/sub fan-out, cookie-session auth, multi-process safe) and the two-browser-session E2E scenarios. Design reference: `LIFELINK_REALTIME_EVENTS.md`.

**Scope note, stated plainly:** the original master plan document is not retained in this workspace. Phase F's scope was taken from the authoritative surviving record of it — the Phase A matrix (`LIFELINK_PHASE_35_RECON_MATRIX.md`) and the phase plan recorded at Phase A. Phase E's handoff list was evaluated against that mapping: the real-time layer is Phase F; external audit anchoring/HMAC, unsealed-entry alerting, per-unit shipment reconciliation, plasma post-thaw tracking, and CI additions (invariant checks, Vitest) are not part of F or G and were **not** implemented.

## 1. What was built

- **Outbox** (`realtime_events`, migration `e4f5a6b7c8d9`): written in the same transaction as the change by a `before_flush` listener over the history rows every mutation already writes, plus donation/verification status changes and notification read state; deduplicated per transaction.
- **Publisher**: after the root transaction ends, on its own connection, under a session advisory lock held through the Redis publish — assigns gap-free `seq`, computes and stores the audience, commits, publishes in order, marks `published_at`; republishes backlog after a Redis outage; prunes after 72 h. Batched (one audience query per notification batch, one executemany UPDATE) so its cost does not grow with fan-out.
- **Audience** (`realtime_audience.py`): mirrors each resource's REST read rule; ADMIN where the admin API can read; notifications owner-only.
- **SSE endpoint** `GET /events/stream`: 401 without a session; subscribe-then-snapshot; `Last-Event-ID`/`?since=` replay with audience filter in SQL; `resync` for unknown/pruned/too-many-missed; `ready`; `ping` with resume id; session re-validation that does not slide the idle timeout; no DB connection held while streaming.
- **Channel namespacing** by Postgres database name.
- **Frontend**: `RealtimeProvider` (EventSource, invalidation map, batching, dedupe, reconnect/backoff, resync, session end → existing session-expired flow), mounted once per signed-in user; all 12 polling sites (10 files) now poll **only while the stream is down** (`useLiveFallbackInterval`); "Live / Reconnecting…" indicator in the notifications panel.
- `_load_user_from_request(touch=False)` option in `app/api/deps.py` (default behaviour unchanged).

## 2. Tests

| Suite | Count | Result |
|---|---|---|
| `tests/test_phase_f_realtime.py` (creation, dedupe, rollback, audience isolation, audience ⊆ REST `_can_view`, concurrency ordering, Redis outage, replay, channel namespace, fan-out cost) | 16 | pass |
| `tests/test_phase_f_realtime_stream.py` (real uvicorn + real Redis: 401, owner/hospital/admin receive, other patient doesn't, donor isolation, Last-Event-ID replay, resync ×2, logout ends stream) | 7 | pass, 3 consecutive runs |
| `frontend/src/lib/realtime.test.tsx` | 8 | pass |
| `frontend/e2e/phase_f_realtime.spec.ts` (two browser sessions: patient sees a hospital allocation live on a page that does not poll; donor's bell updates live) | 2 | pass |

Regression: see §6.

## 3. Bugs found and fixed

1. **Cross-environment seq contamination** (found while running the suite next to a live e2e server): Redis pub/sub ignores database numbers, so two environments sharing a Redis would see each other's sequence numbers; a foreign larger seq would make a stream discard its own events. Fixed by namespacing the channel per Postgres database (+ regression test).
2. **Publisher cost scaled with notification fan-out** (caught by the existing Phase 27 bounded-query-count test failing in the full run: 53 statements vs ≤ 40): per-event audience lookups and UPDATEs. Batched; the Phase 27 test passes again and a new test asserts 3-donor and 15-donor requests cost the same number of statements.

## 4. Security verification

Audience never exceeds REST visibility (property test against the real `_can_view` functions over a mixed population including linked/unlinked family, responding/non-responding donors, in/out-of-city hospitals); live-stream isolation over real HTTP; unauthenticated 401; logout/expiry ends open streams; idle timeout not extended by an open stream; payloads contain no PII; replay filtered with the same stored audience. CORS/credentials unchanged (same origin list as the REST API); CSP `connect-src` already covers the API origin.

## 5. Performance (dev database, measured after the final regression)

| Measurement | Result |
|---|---|
| Commit of a business change, publication suppressed | median ≈ 5.2–5.4 ms, p95 6.3–8.4 ms |
| Same commit + publication (sequence, audience, Redis) | median ≈ 14.7–16.4 ms, p95 23.6–29.2 ms |
| Commit start → message on the Redis channel | median ≈ 14.8–16.6 ms |
| Publication statements vs notification fan-out | identical for 3 and 15 notified donors (test-enforced); Phase 27's ≤ 40-statement bound holds again |
| Replay query | filtered by GIN-indexed `audience_user_ids` + unique `seq` range |

So live updates reach a subscribed browser in tens of milliseconds after commit (vs 15–60 s polling before); the cost is ≈ +10 ms on each committing write, paid after the business transaction has released its locks and connection. A restructuring of the publisher from five commits per run to two measured ≈ 2 ms better at the median and within noise at p95 (16.7–18.7 → 14.7–16.4 ms); it was kept because it is also simpler, and the gain is reported as small, not claimed as significant. Front-end main chunk: 789.6 kB / 229.0 kB gzip (Phase E: 785.5 / 227.6).

## 6. Regression results

Combined F + G regression is recorded in the Phase G report §6 (one final run of each suite after all F and G changes).

## 7. Known limitations

- One Redis connection per open stream (adequate now; a per-process broker is the scaling step).
- One extra short transaction per committing write for publication (plus the Phase E audit sealer's).
- Not evented: family-link changes, certificate issuance/revocation (no history row).
- Donors who haven't responded to an open request/emergency are reached via their notification event, not per-resource events.
