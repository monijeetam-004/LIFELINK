# LifeLink — Phase K: Performance

**Date:** 2026-10-05 · **Scope (Phase A plan):** "Run real EXPLAIN ANALYZE against matching / reservation / allocation / emergency / notification / admin / map / audit queries; document plans and justify any new indexes." Phase 24 already did this for the pre-Phase-35 code at 47k notifications (two notification indexes, one N+1). This phase measures the paths added or changed since (components, shipments, verification, audit chain, real-time outbox) and re-measures the rest at higher volume.

## 1. Method

- **Volume database** (`lifelink_perf`): fresh migrations → `seed_demo_data` → new `scripts/perf_seed.py` (dev-only, refuses production). Contents: 126,795 blood units (8% given a past expiry, deliberately unswept), 976 inventory rows across 61 facilities, 10,001 blood requests across every status, 9,064 donor responses, 2,001 emergencies, 250,062 notifications, 30,022 sealed audit entries, 251,161 real-time events, 1,565 users. Units were created through `inventory_service` (aggregates and unit rows consistent); `VACUUM ANALYZE` before measuring.
- **Real SQL, not hand-written queries**: new `scripts/perf_explain.py` calls 27 real endpoints in-process as real seeded accounts, records every statement with its bound parameters and time (SQLAlchemy cursor events), and re-runs the slow and hot-path statements under `EXPLAIN (ANALYZE, BUFFERS)` inside a rolled-back transaction. Each endpoint ran 5 times; medians reported.
- Machine: the same 2-CPU sandbox as every earlier phase. Some "before" measurements overlapped the full backend test run, so only medians and statement counts are compared, never single samples.

## 2. Findings and fixes

| # | Finding (evidence) | Fix | After |
|---|---|---|---|
| 1 | **`GET /donors/me/requests` (donor match feed) — N+1.** 3 queries per open compatible request: **2,043 statements, 1,423 ms median** for 681 open requests. | Patient names, accepted counts and the donor's own responses fetched once each for the whole list (`donors.py`); same rows/order/fields. Same batching applied to `GET /donors/me/responses`. | **6 statements, 72 ms** |
| 2 | **`GET /emergencies/active` — N+1.** 2 queries per open emergency: **405 statements, 278 ms**. | Own responses + accepted counts in one grouped query each. | **5 statements, 33 ms** |
| 3 | **Real-time publisher read of "sequenced but unpublished" events walked every published row.** `seq IS NOT NULL AND published_at IS NULL ORDER BY seq LIMIT 2000` used the unique `seq` index and filtered all already-published rows: **68.6 ms per committing write** at 179k retained events (8.7–10.0 ms at 31k), growing with retention. | Partial index `ix_realtime_events_pending_publish (seq) WHERE seq IS NOT NULL AND published_at IS NULL` (migration `f5a6b7c8d9e0`). | **0.010 ms** (index on the in-flight rows only) |
| 4 | **Real-time publisher drained only one batch per commit — a correctness bug found by measurement.** Each run sequences ≤ 500 events; any transaction creating more (an emergency notifying every compatible donor; the volume load) left the rest unsequenced until some later, unrelated commit ran the publisher. The volume DB ended with **172,106 events never sequenced or published** — live updates for those users would have lagged indefinitely. | `publish_pending_now` now repeats runs while a run was full (each still its own short, lock-ordered transaction; bounded at 200 runs). Drain throughput measured ≈ 3,300 events/s. | Backlog 0 after every commit; `test_publisher_drains_a_burst_larger_than_one_batch` (1,037 events in one commit → all sequenced 1…1,037 and published) |
| 5 | **Real-time retention never ran in ordinary traffic — a correctness bug found by measurement.** The prune trigger was `events sequenced IN THIS RUN % 97 == 0`; an ordinary write sequences 1–3 events, so the 72-hour retention promised in the Phase F report effectively never executed. When it did, the delete was a **sequential scan: 47.1 ms** at 251k rows. | Prune once per `PRUNE_EVERY` (500) events, when the sequence crosses a multiple; index `ix_realtime_events_created_at` (same migration). Never deletes unpublished events. | **0.013 ms** plan; `test_retention_runs_in_ordinary_single_event_traffic`, `test_retention_never_deletes_unpublished_events` |
| 6 | Reserve / allocate request cost | Combined effect of #3/#4 on the write path. | reserve **169 → 56 ms**, DB time 90.7 → 18.1 ms; allocate **150 → 49 ms**, 71.5 → 18.2 ms. Part of the "before" DB time was the stuck backlog of #4 being drained 500 at a time, so the gain is not attributed to the index alone. |

Migration `f5a6b7c8d9e0` verified: applied to the volume DB (upgrade), and in the Phase J/L migration checks (fresh → head, up/down round trip). The SQLAlchemy model declares the same two indexes so `create_all` (test schema) and Alembic agree.

## 3. Measured and left alone (with the evidence)

| Path | Measurement (volume DB, after) | Why no change |
|---|---|---|
| Notifications list / unread-only / unread count (250k rows) | 13.2 / 13.3 / 12.6 ms; plans use Phase 24's `ix_notifications_user_*` (0.16 ms execution) | Already optimal. |
| Real-time replay (`Last-Event-ID`, 400 events, admin) | 2.6 ms; index range scan on `seq` with audience filter, 0.18 ms execution | Already optimal. |
| Request / emergency match candidates | 42 / 28 ms, 4–5 statements; seq scans only on `users` (1.5k) and `donor_profiles` (≈500) — cheaper than index access at that size | Correct planner choice. |
| Hospital requests, hospital emergencies, inventory log, patient requests | 9.5–26.5 ms, 3–6 statements | — |
| Admin facilities list (with the Phase G expired-unswept subtraction) | 27 ms, 3 statements | — |
| Admin facility detail / hospital component inventory (both run the lazy expiry sweep) | 68 / 79 ms, 52–53 statements | Statement count is bounded by the facility's (blood type × component) buckets (≤ 32), not by unit count; each bucket's sweep also performs the self-healing reconciliation, so skipping buckets would change behaviour. Documented. |
| Admin blood-request list / analytics overview | 16.7 / 24.7 ms; sequential scan + top-N sort over 10,001 requests (6.5 ms execution) | Fine at this volume. **Scaling ceiling**: grows linearly with total requests; a `created_at` index for the list is the next step at ~10× volume. Not added without a measured problem. |
| Map data | 27.7 ms, 3 statements | — |
| Audit chain verification (`POST /admin/audit-log/verify`) | 1.75 s for 30,022 entries (≈17k entries/s), 28 statements | Linear by design (re-hashes the whole chain); explicit admin action, not on any user path. |
| Audit log page / chain head | 17.6 / 13.1 ms | — |
| Emergency fan-out on the write path | `POST /emergencies` notifying ≈1,000 donors: 2.05 s end to end on the E2E stack (SMTP unreachable, inline mode) | Dominated by per-donor notification work established in Phase 27; publishing the ≈1,000 resulting events is ≈0.3 s of it (≈0.3 ms/event) and now completes in-request (#4). Moving publication off the request thread is the next step if this matters. |

## 4. Frontend

Production build after the Phase H router upgrade and Phase G–J changes: main chunk 810.2 kB / 235.3 kB gzip (Phase G: 789.6 / 229.0); most of the +20.6 kB raw is React Router 7. Route-level code splitting from Phase 24 is unchanged. During the J journeys the live event stream replaced the 12 polling loops (Phase F), so idle dashboards make no periodic API requests while connected.

## 5. Tests

`tests/test_phase_k_performance.py` — 5 tests, all pass: burst drain (#4), retention in ordinary traffic and never deleting unpublished events (#5), and "statement count does not grow with rows" for the donor feed (2 vs 9 open requests) and active emergencies (2 vs 7) (#1, #2). The existing Phase 27 bounded-fan-out test still passes (publication cost independent of 3 vs 15 notified donors).

## 6. Remaining limitations

- The publisher runs in the request thread after commit; very large fan-outs pay ≈0.3 ms per event in that request.
- Admin request list and analytics scan the whole requests table (6.5 ms at 10k rows; linear).
- Audit verification is O(chain length).
- Measured on a 2-CPU sandbox with no network latency to Postgres/Redis; absolute numbers will differ in deployment, statement counts and plan shapes will not.
