# LifeLink — Phase J: Testing

**Date:** 2026-10-05 · **Scope (Phase A plan):** "Full concurrency race suite for new workflows; required E2E journeys; axe + manual keyboard checks; responsive viewport checks including donor mobile." Scope source as in the H/I reports (original master plan text not retained; Phase A plan + matrix are the surviving record).

## 1. Concurrency (real PostgreSQL, real threads)

**Existing coverage re-verified, not rewritten.** The workflows added in B–G already had real-thread races: double reserve/allocate, release vs allocate, reservation vs lazy expiry (Phase 34); concurrent donation-justified adds (35); shipment double-dispatch, double-receive, dispatch vs cancel, double-create, direct allocation vs shipment (D); request races A–D and many-partials (E); concurrent verify; concurrent document resubmission; reconciliation restock-once; audit-chain fork and deadlock (E); gap-free ordered event publication under 8 writers (F). All pass in the final run (§5).

**Added — `tests/test_phase_j_cross_workflow_races.py`** (each 4 rounds, barrier-released threads, each thread its own client/session, ending with the full domain invariant checker returning no violations):

| Race | Result |
|---|---|
| R1 receive vs fail on an ARRIVED shipment (the one fork in the shipment state machine with two outgoing edges that had no race test) | exactly one 200 and one 409 every round; RECEIVED ⇒ 2 units at destination, no reconciliation; FAILED ⇒ nothing transferred, reconciliation REQUIRED; origin units ALLOCATED either way |
| R2 verify vs reject on the same donation | both succeed **in sequence** (row lock; VERIFIED↔REJECTED is a legal audited correction); final state = the decision last in the audit chain; certificate gate follows it |
| R3 admin facility detail (which writes, via the Phase G sweep) vs the facility's own reserve and allocate | no 500, no deadlock; expired units never handed out; admin numbers = unit rows |
| R4 3 adds + 3 reserves + 3 sweeping component reads on one facility at once | **failed on its first run — a real lost update** (aggregate 6 vs 8 units on hand); passes after the fix below |

**Bug found by R4 — stale row under a fresh lock (invariant 46).** Sessions use `expire_on_commit=False`; SQLAlchemy does not overwrite an object already in the session's identity map when it re-reads it — not even for `SELECT … FOR UPDATE` or `Session.get(…, with_for_update=True)`. `inventory_service._get_or_create_row` commits the lazy expiry sweep mid-transaction and then re-locks the row: the lock was current, the values were not, and the following `row.units += n` overwrote another transaction's committed addition. **Fix:** `app/db/session.py` makes every locking ORM read refresh what it locked (`populate_existing`), for every model and call site, not only inventory. `tests/test_phase_j_locking_reads.py` pins it deterministically (3 tests; the two locking tests fail 3/3 with the fix disabled, the plain-read test documents that ordinary reads are unchanged). R4 itself is timing-dependent (it reproduced the loss once; with the fix disabled it passed three further runs), which is why the deterministic tests exist.

## 2. E2E journeys (real backend, fresh database)

New `frontend/e2e/phase_j_journeys.spec.ts` (9 tests), filling gaps in the existing 79:

1. **Partial → full fulfilment, live for the patient**: hospital allocates 1 of 2 → "1/2 units fulfilled" appears without reload; last unit → moves to the Fulfilled tab ("2/2"); a further allocation is refused by the backend (409).
2. **Shipment happy path through the UI, two hospitals in two browser sessions**: origin dispatches and marks in transit, destination marks arrived and receives; the origin's open page shows RECEIVED live; destination platelet stock grew by exactly 2.
3. **Donor on a phone (375×812)**: `/donor/mobile` lands on the real `/donor`; bottom section navigation works; no horizontal overflow; **zero** WCAG 2.1 A/AA axe violations.
4. **Keyboard only**: sign in with Tab/typing/Enter; open notifications with Enter; Escape closes; focus returns to the bell.
5. **Each of the 5 roles' dashboards**: no application console errors, no page errors, no 5xx, and no 4xx other than the anonymous `/auth/me` probe on the login page.

Journey coverage across the whole suite: donor (log donation → verification → certificate; respond to requests; mobile), patient (request → allocation live → fulfilled; SOS; history), family (link request/accept; dashboard), hospital (inventory console + components; requests; shipment create → dispatch → … → receive; failure → reconciliation), admin (verification review, donation review, audit-chain verify, certificate revocation, facilities), plus security (session, CSRF, IDOR, rate limit), error/offline UX and real-time.

## 3. Accessibility and responsive

Bugs found and fixed by the J checks:

| Finding | Fix | Evidence |
|---|---|---|
| Notifications bell panel could only be closed with a mouse; toggle had no expanded state; unread count was a visual badge only | Escape closes and returns focus; `aria-expanded`/`aria-controls`; count exposed as the button's description ("2 unread" / "Unread count unavailable"); panel is a labelled region | 2 new component tests; E2E keyboard journey |
| Account menu: same (no Escape, no expanded state) | same pattern | `AccountMenu.test.tsx` |
| Donor dashboard's decorative 160 px heart was icon-font **text**, the one color-contrast exception Phase 27 had to disclose | rendered as an SVG (no text to contrast-check, same look) | Phase 27's spec now asserts **zero** contrast violations (stricter); donor-mobile axe run clean |
| CSP blocked Leaflet's built-in 1×1 data-URI image on every map page (console violation each pan/zoom) | `img-src` allows `data:` (an image data URI cannot run script) | hospital dashboard console check clean |
| Donor bottom navigation had no accessible name; icon ligature text leaked into link names | `aria-label="Donor sections"`, icons `aria-hidden` | donor-mobile journey uses the names |

Existing checks re-run in the final suite: axe on 20+ routes (Phases 26, 27, 31.1, G, J), modal focus trap / Escape (22, G), mobile hamburger navigation for admin/patient/family (22), no page overflow at 320–1440 px (26, 31.1, G), inventory console at 375/768/1280 (G).

## 4. Test-harness bugs fixed (assertions unchanged)

- `blood_request_idempotency.spec.ts` "resubmit after failure": `before` could be read before the list query had started (waiting only for the loading text to be absent passes instantly). Reproduced (before 0 → after 2 on an account that already had a request). Now waits for a rendered row and no loading row.
- Same spec, "rapid double-click": `Promise.all([click(), click()])` could hang in Playwright's own actionability wait once the first click disabled the button. Now a real `dblclick()` (two back-to-back click events; still creates two submissions if the guard regressed).
- My own first versions of the J spec used `networkidle`, which never occurs with the live event stream open; replaced by explicit element waits.
- One full Playwright run had failures caused by my test environment, not the app: an SMTP sink left running from the Phase I clean-room check made inline (`RQ_SYNC=true`) email delivery real, so a 12-request rate-limit test exceeded its timeout (`POST /emergencies` 11.7 s with the sink, 2.05 s without). Stopped; not counted as an app result.

## 5. Final results (after every H–L change)

| Suite | Result |
|---|---|
| Backend `pytest tests --ignore=tests/e2e` | **1,179 passed, 0 skipped, 0 failed** (20 min 26 s). The one skip carried since Phase 23 (`importorskip("moto")`) now runs: moto is a declared dev dependency (Phase I). |
| New J backend tests (races 4, locking reads 3) | 7/7 |
| Vitest | **155/155** in 18 files |
| `tsc --noEmit`, ESLint (`src` + `e2e`, max-warnings 0) | clean |
| Playwright, fresh `lifelink_e2e` DB each time | **88 passed, 0 skipped, 0 failed** — two consecutive full runs (6.1 min each). Phase 21 test C's conditional skip did not trigger (the demo activity seed provides an unread notification to probe). |
| Invariant checker on the E2E DB after the run | 12/12 OK, audit chain 242 entries, 0 unsealed; real-time outbox 0 unsequenced / 0 unpublished |

Intermediate runs disclosed: Playwright full run 1 (pre-fix) 80 passed / 8 failed; run 2 interrupted by a container restart at 25/88; run 3 86 passed / 2 failed (double-click harness hang; donor-dashboard contrast) — both fixed (§3, §4), then the two clean runs above.

## 6. Remaining limitations

- Concurrency tests run on one machine against one Postgres; they prove serialization and invariants, not throughput under production load.
- Accessibility evidence is automated (axe) plus scripted keyboard paths; no screen-reader user testing was performed.
- Several conceptual-illustration pages (labelled "CONCEPTUAL ILLUSTRATION — NOT LIVE DATA") still contain inert demo controls by design.
