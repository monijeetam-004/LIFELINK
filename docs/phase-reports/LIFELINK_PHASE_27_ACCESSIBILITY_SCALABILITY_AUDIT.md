# LIFELINK — PHASE 27 ACCESSIBILITY REMEDIATION + SCALABILITY ARCHITECTURE & LOAD HARDENING

## Audit

This document records what was actually found before any Phase 27 code change was made, per the phase's own Part A mandate: "read all prior phase docs and inspect real code before touching anything — do not trust previous reports without checking the actual code." Every claim below traces to a command that was actually run against this environment's real Postgres, real Redis, and a real running backend/frontend — never assumed from a prior phase's prose.

## 1. Methodology

Before any fix: read `LIFELINK_PHASE_24_PERFORMANCE_AUDIT.md`/`_REPORT.md`, `LIFELINK_PHASE_25_TESTING_AUDIT.md`/`_REPORT.md`, and `LIFELINK_PHASE_26_DONATION_FRONTEND_AUDIT.md`/`_REPORT.md` in full, then independently verified the claims that mattered to this phase's two mandates (accessibility, scalability) against the real backend (`app/`) and frontend (`frontend/src/`) source rather than trusting the prose. Concretely: read `app/main.py`, `app/db/session.py`, `app/core/config.py`, `backend/.env`, and `frontend/src/App.tsx` directly; ran the full backend pytest suite as a pre-change baseline; ran a fresh axe-core scan across every route rather than reusing Phase 26's route count or violation list.

## 2. Pre-change backend baseline

`pytest -q` (full suite, real Postgres `lifelink_test`, real Redis db 15): **780 passed, 1 skipped, 1 warning in 426.08s** — exactly matches Phase 26's own final numbers, confirming no drift had occurred before this phase touched anything.

## 3. Stack reconciliation

The Phase 27 brief's Part E language ("Flask application factory", "WSGI") does not match this codebase, which is FastAPI on uvicorn (ASGI) — confirmed via direct AST-based route inspection: 111 routes across 13 routers, 110 synchronous `def` handlers and 1 `async def` (`verifications.py::submit_verification`). This is treated as the brief describing *intent* (a distinct production entrypoint, safe secrets/config handling, graceful shutdown) rather than a literal Flask requirement — see this report's Part E section for how that intent was honored using the real stack.

## 4. Accessibility: real pre-fix axe-core scan

A fresh Playwright + axe-core (`axe-core/axe.min.js`, WCAG 2.1 A/AA rule set) scan was run across the full, freshly-reconstructed 53-route list (read directly from `frontend/src/App.tsx`'s route table, not assumed from Phase 26's "52 routes" figure) under every relevant authenticated role.

**Result: 28 of 53 routes had at least one violation, totalling roughly 1,168 violation nodes**, overwhelmingly dominated by a single issue:

- **aria-command-name, ~1,133 nodes**, concentrated on `/patient/find-donor` and `/hospital` (the Leaflet donor-map routes). Phase 26 had disclosed this as unfixable third-party DOM ("Leaflet renders its own marker `<img>`/`<div>` elements outside React's control"). Direct inspection of `node_modules/leaflet/dist/leaflet-src.js` (`Marker.prototype._initIcon`) showed this claim does not hold: Leaflet sets `icon.title = options.title` on the marker's real DOM node — including for a custom `L.divIcon`, which is what `DonorMap.tsx` uses — and also sets `icon.tabIndex = '0'` / `role="button"` when `keyboard: true` (Leaflet's own default). A `title` attribute is a valid WCAG accessible-name source for a `role="button"` element, and `react-leaflet`'s `Marker` component (`createLayerComponent`) spreads every extra prop, including `title`, straight into `new LeafletMarker(position, options)`. `DonorMap.tsx` was passing no `title` at all.
- **link-in-text-block, on Leaflet's own attribution link** (`.leaflet-control-attribution a`) — a real, if minor, contrast/underline issue on the library's own rendered output.
- **color-contrast, 317+ nodes across 22 routes** — Phase 26 disclosed this as "systemic design-system debt". Re-investigated token-by-token (real WCAG relative-luminance arithmetic, computed per instance, never assumed) rather than accepted as an unfixable redesign-scale problem: the `warning`/`success` Tailwind tokens themselves failed AA contrast in every context they were used, and the remaining ~90% of nodes were one repeated pattern — an `opacity-50/60/70` wrapper applied to a card/row/icon that was *already* visually distinguished by background color, badge color, and/or icon, where the opacity was incidental and simply dropped that element's own text below 4.5:1 (or 3:1 for large text/icons).
- Two `animate-pulse` icons (Tailwind's default 1↔0.5 opacity keyframe) spent part of every animation cycle below the 3:1 UI-component contrast floor.

## 5. Accessibility: filter/keyboard/semantics spot-checks

Forms, keyboard operability, and screen-reader semantics were checked as part of the same axe pass (its label/select-name/button-name rule set covers form semantics directly) plus a targeted `grep` confirming every `outline-none` usage in `frontend/src/` is paired with a `focus:ring`/`focus-visible:ring` replacement (i.e., no invisible-focus regressions anywhere in the codebase, not just the routes this phase touched).

## 6. Scalability: real, reproduced baseline

Phase 24/25's own single-scenario load-test methodology (real httpx+asyncio against the real running dev backend, pre-provisioned sessions for the seeded `loadtest.*@example.com` accounts, no mocking) was re-run first, extended with the concurrency=2 point this phase's brief requires. It reproduced Phase 24/25's qualitative shape — write endpoints (`POST /blood-requests`, `POST /emergencies`) degrading badly under load, up to roughly 17.9s max latency at concurrency 50 — but did **not** reproduce a full outage at any concurrency up to 50 in single-scenario mode: no timeouts, no 5xx, and instant recovery after each burst.

This directly motivated building a **new "mixed" mode** (`scripts/phase27_loadtest.py mixed`) that fires all six scenarios' concurrency bursts *simultaneously* rather than one scenario fully drained before the next starts — the "Scenario H" (mixed workflow) that both Phase 24 and Phase 25 explicitly left unmeasured in their own reports. At `mixed 25 10` (150 total concurrent requests across all 6 scenarios, 10s client timeout), **the full outage described by Phase 24/25 WAS reproduced**: 149/150 requests timed out, `/health` returned connection-refused (`000`), and `pg_stat_activity` showed exactly 15 connections stuck `idle in transaction` — matching the pool's ceiling exactly.

## 7. Scalability: root-cause investigation, live evidence

Phase 24/25 concluded (`app/db/session.py`'s own prior comment) that the bottleneck was "CPU/thread-scheduling capacity" on this sandbox's 2 CPUs running a single uvicorn worker, and that a pool-size increase (5/10 → 20/30) had already been tried and measurably failed to help — used as evidence for the CPU theory.

That conclusion was tested directly, live, during a fresh reproduction of the outage:

- `top -bn1` during the reproduced outage: **0.0% user CPU, 95%+ idle** — directly contradicting a CPU-bound theory.
- `py-spy dump --pid <uvicorn-pid>`: every one of the 40 "busy" AnyIO worker threads was blocked identically at `sqlalchemy/pool/impl.py:157 _do_get` — waiting on the connection-pool's internal queue — not executing any CPU-bound Python code. A further ~23 of AnyIO's own thread-pool slots were sitting genuinely idle in their work queue, proving thread-pool *capacity* was not exhausted either (the second half of the "CPU/thread-scheduling" theory).
- `pg_stat_activity`'s `xact_age` on the 15 stuck connections, cross-referenced against the application source, pointed at exactly one code path: `app/services/notification_service.py::notify_matching_donors()`, called from exactly two places (`POST /blood-requests` and `POST /emergencies`, confirmed via `grep` across the whole `app/api/routes/` tree).

## 8. The root cause

`notify_matching_donors()` ran an **unbatched Python `for` loop** over every compatible, available donor (129 real O_NEG donors in this environment's seeded dev database) and called the single-recipient `notify()` helper once per donor. Each call performed 3 sequential DB round trips (`Notification` INSERT+flush, `NotificationPreference` SELECT, `User` SELECT) plus one Redis round trip (the RQ email enqueue) — **all held open on the SAME database connection/transaction for the entire loop's duration**. For 129 donors that is 500+ sequential round trips inside one open transaction, taking several seconds to tens of seconds depending on load, during which that one request pins one of the pool's 15 slots. Under the "mixed" concurrent-burst pattern, enough simultaneous writers do this at once to exhaust the pool entirely, starving every other endpoint on the same connection pool — including trivial reads and even `/health` (which also calls `engine.connect()`).

This is a different, more precise, and directly fixable root cause than Phase 24/25's "CPU/thread-scheduling capacity" conclusion, which this phase's own live measurement overturns on both halves (CPU was idle; thread-pool capacity was not exhausted).

## 9. Neo4j / MinIO status (carried forward, re-verified)

- **Neo4j (Aura)**: DNS-unreachable from this sandbox, exactly as every prior phase found — re-confirmed this phase, not re-litigated. Graceful-degradation paths (`app/core/graph.py`, `graph_service.py`) still log-and-continue rather than raising, confirmed via the warning lines seen in this phase's own test runs (`Neo4j operation failed: ServiceUnavailable`). **NOT LIVE-VERIFIED** (unchanged classification).
- **MinIO/S3**: confirmed not running in this sandbox (`curl http://localhost:9000/minio/health/live` → connection refused). `backend/.env` ships a real-looking `S3_ENDPOINT_URL`/`S3_ACCESS_KEY`/`S3_SECRET_KEY`, which makes `storage_backend()` return `"s3"` unless explicitly overridden — this phase's own Playwright run initially hit real document-upload failures for exactly this reason before the dev server was restarted with `S3_ENDPOINT_URL=""`, matching Phase 24/26's own documented workaround. **NOT LIVE-VERIFIED** (unchanged classification).

## 10. Environment-quirk notes for future phases

- The Bash tool's default 2-minute timeout silently truncates the full backend pytest run (~7-8 minutes); pass an explicit `timeout` of 480000ms+ for any full-suite invocation, and prefer `--junit-xml` for a trustworthy pass/fail/skip count — this environment's `pytest -q` run was observed, twice, to complete successfully (confirmed via `--junit-xml` and a non-error exit code) without ever printing its own final "N passed" summary line to captured stdout, for reasons not fully root-caused; treat the junit XML, not the absence/presence of that line, as authoritative.
- Repeated Playwright/manual test invocations accumulate `rl:*` Redis keys past their real rate-limit thresholds; clear with `redis-cli KEYS "rl:*" | xargs -r redis-cli DEL` before an authoritative run — this is clearing self-inflicted test-tooling state, never a change to limiter logic or thresholds.
