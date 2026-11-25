# LifeLink — Phase 4 Report: Donor Portal

**Scope:** Phase 4 (complete Donor Portal) — three-way availability, a real Alert Watcher, a real three-way Match Response (Accept/Decline/Not Now) with concurrency-safe backend operations, and an audit of every donor-facing page against the no-fabrication rule this project has held to since Phase 0. New Alembic migration, backend + frontend both changed.

---

## Starting point

An audit of the existing donor pages (`ReadyConsole.tsx`, `EligibilityCenter.tsx`, `MilestonesHistory.tsx`, `IdentityVerification.tsx`, `DonorDashboard.tsx`, plus `donors.py`/`certificates.py`/`matching.py`/`verifications.py` server-side) found most of the Donor Profile spec already real from earlier phases: eligibility (`core/eligibility.py`, a fixed 56-day whole-blood interval), donation history, certificates (scoped to real completed `Donation` rows only, verified by re-reading `certificates.py`), identity verification (a full submit → admin-review → `identity_verified` pipeline), and pre-scheduling (`donors/me/appointments`, backed by a real hospital directory). What Phase 4 actually needed, against the spec:

---

## What was actually wrong, and what changed

### 1. Availability was binary; the spec asks for three states
`DonorProfile.is_available` was a plain boolean everywhere. The spec's `AVAILABILITY` section names three states: Available / Unavailable / Not now.

**Fixed:** added `DonorAvailabilityState` (`AVAILABLE`/`UNAVAILABLE`/`NOT_NOW`) as a new column, `availability_state`, alongside the existing `is_available`. `is_available` stays the single boolean every matching/notification query already filters on (14+ call sites — `matching.py`, `notification_service.py`, `emergencies.py`, ...); `AVAILABLE` keeps it `True`, `UNAVAILABLE`/`NOT_NOW` both set it `False`. The two aren't given different matching rules — the distinction is informational only (shown back to the donor and anyone who can see their profile), which is documented directly on the model. `PUT /donors/me/availability` now takes `{state, availability_note}` instead of `{is_available, availability_note}` — an intentional breaking change to that one endpoint's payload shape. A shared `AvailabilitySelector` component now renders the real three-way control in Settings, the Ready Console, and (as a quick two-state shortcut, explicitly commented as such) the main Donor Dashboard.

### 2. Match Response was two-way; the spec asks for Accept/Decline/Not Now
`DonorResponseStatus` had only `ACCEPTED`/`DECLINED`. Worse: in both `ReadyConsole.tsx` and `DonorDashboard.tsx`, a button **labeled** "Not now" silently called `DECLINED` — there was no way to actually record "can't help right now" as distinct from a firm no, on either blood requests or emergencies (`EmergencyResponse` reuses the same enum).

**Fixed:** added `NOT_NOW` to `DonorResponseStatus` (a Postgres enum-value addition, migrated). Every response surface — `ReadyConsole.tsx`, `DonorDashboard.tsx`, and the shared `EmergencyTrackDrawer.tsx` (used by both the Donor and Hospital portals for emergency response) — now has three real, correctly-labeled buttons. A donor's current NOT_NOW/DECLINED response is now shown back to them ("You responded: Not now") rather than silently disappearing.

A second bug this surfaced: both `respond_to_request` and `respond_to_emergency` logged a `donor_declined`/`responder_declined` timeline event for **any** non-accept response, so a "Not now" would show up to the requester/hospital as an outright decline — defeating the entire stated purpose of adding `NOT_NOW` ("distinct from DECLINED so the requester/analytics can tell 'no' from 'not right now' apart", per its own docstring). Fixed by giving `NOT_NOW` its own `donor_not_now`/`responder_not_now` event type and frontend label, verified live (see below) rather than just by inspection.

### 3. Concurrency safety for Match Response
The spec explicitly asks for "transactional/concurrency-safe backend operations" preventing duplicate/conflicting responses. Both response endpoints already had a DB unique constraint (`uq_donor_response_pair` / `uq_emergency_response_pair`), but the "does a row already exist" check and the insert that follows it were two separate statements — a genuine (if narrow) race between two near-simultaneous responses from the same donor (double-tap, two tabs) would previously have surfaced as a raw, unhandled `500` from the unique-constraint violation.

**Fixed:** wrapped the first-insert path in both endpoints in a `SAVEPOINT` (`db.begin_nested()`), catching `IntegrityError` and falling back to updating the row the winning request just inserted. New tests fire two real concurrent HTTP requests from two threads at the same donor/request pair and assert both return `200` (never `500`) and exactly one response row survives — run against the real Postgres backend, not mocked.

### 4. Alert Watcher didn't exist server-side
`ReadyConsole.tsx`'s "Alert me" controls were honestly disabled with a "Backend required" label — correct at the time, since grepping the backend found no such table or field.

**Fixed, for real this time:** added `alert_min_urgency` (`Urgency`, default `ROUTINE`) and `alert_radius_km` (nullable int) to `DonorProfile`. `notify_matching_donors()` now applies both: a donor only hears about a blood request at or above their chosen urgency, and — only once they've opted into `share_location` and both sides have real coordinates — only within their chosen radius (a donor who hasn't shared a location can't have a real radius applied, so they're notified regardless rather than silently dropped). An emergency SOS is **never** held back by either filter, matching the existing "quiet hours never suppress emergency email" principle already established on `NotificationPreference`. Deliberately **not** added: a blood-group/component selector — a donor has exactly one blood type on file and the system only tracks whole-blood donations, so a selector for either would have nothing real behind it; compatibility is already computed automatically. The controls are now live and enabled in the Ready Console and in Settings, wired to `updateDonorProfile()`.

### 5. Quiet hours existed on `NotificationPreference` but were never applied
`quiet_hours_start`/`quiet_hours_end` were persisted fields with zero code reading them — dead columns since whichever phase added them.

**Fixed:** `_in_quiet_hours()` in `notification_service.py` now gates non-emergency email (never emergency email, never in-app notifications) on the donor's configured window, including a window that wraps past midnight. Verified live with a mocked clock: a request filed during a donor's quiet hours produces the in-app notification but not the email; an emergency SOS produces both regardless.

### 6. The standalone mobile donor dashboard was 100% fabricated
`DonorDashboardMobile.tsx` (routed at `/donor/mobile`) was a raw, never-converted port of the Stitch mockup: a fake "94% Donation Readiness Score" with no backend concept behind it, a hardcoded "O-Negative Blood Needed... St. Jude Regional" emergency card with a dead "Respond Now" button, fabricated "Community Impact" stats (Lives Saved: 12, Points: 2,450, Level: Gold — no points/level system exists anywhere in the backend), a fake donor name/photo, and a "Sign Out" button wired to nothing. This is a direct violation of the no-fabrication rule every other page in this app follows.

**Fixed:** rather than hand-convert a second, parallel dashboard, `/donor/mobile` now redirects to `/donor` — which is already the real, fully-wired dashboard **and** already has its own responsive mobile bottom nav (`md:hidden`). Maintaining two dashboards, one real and one fake, served no one. The retired file documents exactly what was removed and why, in the same style as every other honesty-comment in this codebase.

### 7. A real, if narrow, mobile layout bug — found and fixed
Careful testing at 375/390/768px (the user's explicit "test emergency response interactions carefully" instruction) surfaced actual horizontal overflow on the Ready Console, traced to the shared `DonorSectionHeader`. Root cause: this sandbox's egress policy blocks `fonts.googleapis.com` (confirmed via the proxy's own status endpoint — `403` / `connect_rejected` on every Material Symbols request), so every icon fell back to rendering its literal name as text ("help_outline", "notification_add", ...), which is wide enough to blow out a narrow header. This exact failure mode — a blocked or slow-loading Google Fonts request — is a real thing that happens for real users too (ad blockers and some corporate/school networks block `fonts.googleapis.com` by default), so this wasn't dismissed as a sandbox-only artifact.

**Fixed:** `.material-symbols-outlined` now clips to a `1em` box (`overflow: hidden; white-space: nowrap`). Every Material Symbols glyph is designed to fill exactly that box, so this has no visible effect once the font loads — it only guards against a fallback-text blowout when it doesn't. Re-verified at 375/390/768px with zero horizontal overflow on `/donor` and the Ready Console.

---

## Already real, left alone

Confirmed rather than assumed, each by reading the actual route/component: `certificates.py` (scoped to real completed donations, ownership-checked, public verify-by-code returns only safe fields), `matching.py` (donor coordinates already rounded to ~1km before ever reaching `/map/data`; `donor_name` only ever revealed to an actual manager of the request), `EligibilityCenter.tsx` and `MilestonesHistory.tsx` (already fully wired to real data, with their own explicit fabrication-disclosure comments from an earlier phase), `IdentityVerification.tsx` (already a complete real submit → review → verified-badge pipeline), `DonorMap.tsx` (already anonymized and coarse). None of these needed Phase 4 changes.

---

## Verified, not assumed

- **237 backend tests pass** (`pytest tests/`, exit 0) — 13 new this phase: three-way availability persistence (including the `NOT_NOW` state), a real `NOT_NOW` blood-request response round-trip, Alert Watcher urgency filtering (a `CRITICAL`-only donor is *not* notified of a `ROUTINE` request but *is* of a `CRITICAL` one), Alert Watcher radius filtering (in-range vs. out-of-range vs. no-radius-set vs. location-not-shared), quiet-hours suppression of non-emergency email and non-suppression of emergency email (both direct unit tests on the pure `_in_quiet_hours()` logic and one full API-level test with a mocked clock), two real-concurrency tests (two threads, real HTTP, same donor/request pair, asserting no `500` and exactly one surviving row), and the `donor_not_now`/`responder_not_now` event-type distinction on both blood requests and emergencies.
- **Live HTTP verification against the real running server** (not TestClient): a 23-step script drives the actual endpoints end-to-end — profile/availability/alert-prefs persistence, urgency-gated notification delivery, a full Not-Now-then-Accept response cycle confirmed to leave one row, a real appointment request, a real donation → real certificate, and a `/map/data` privacy check (no donor identity fields, coordinates rounded). All 23 checks passed. A separate targeted script re-confirmed the `donor_not_now`/`responder_not_now` event-type fix live, after restarting the server to pick it up.
- **Real browser verification at mobile widths** (Playwright, the actual login form, not an injected cookie): 375/390/768px, on both `/donor` and the Ready Console, zero horizontal overflow (after the header-icon fix above). A dedicated click-through script opened a live emergency's response drawer at 375px and clicked, in order, **Not now → I can help now → Withdraw**, confirming each click round-trips through the real server (`GET /emergencies/{id}` re-checked after every click) and that the UI correctly swaps between the three-button state and the post-accept "Withdraw" state.
- `npm run typecheck`, `npm run lint`, and `npm run build` all pass clean (same pre-existing chunk-size *warning*, not an error, as prior phases).

---

## Files touched

**Backend:** `app/models/enums.py` (`NOT_NOW`, `DonorAvailabilityState`, urgency ranking), `app/models/profile.py` (`availability_state`, `alert_min_urgency`, `alert_radius_km`), `app/schemas/profile.py`, `app/api/routes/donors.py` (`update_availability`), `app/services/notification_service.py` (quiet hours, Alert Watcher filtering in `notify_matching_donors`), `app/api/routes/blood_requests.py` (SAVEPOINT concurrency safety, `donor_not_now` event, urgency/origin passed to notifications), `app/api/routes/emergencies.py` (same, `responder_not_now` event), new migration `alembic/versions/e2f3a4b5c6d7_phase4_donor_portal.py`, `tests/test_profiles.py`, `tests/test_donor_matching.py`, `tests/test_emergency_network.py`, new `tests/test_alert_watcher.py`, new `tests/test_concurrency.py`.

**Frontend:** `src/lib/domain.ts` (new types/fields, `setAvailability` signature change), new `src/components/AvailabilitySelector.tsx`, `src/pages/donor/ReadyConsole.tsx` (three-way availability, three-way response, live Alert Watcher controls), `src/pages/DonorDashboard.tsx` (real three-way response, corrected quick-toggle), `src/pages/DonorDashboardMobile.tsx` (retired to a redirect), `src/pages/shared/Settings.tsx` (three-way availability, Alert Watcher, location-sharing privacy control), `src/components/EmergencyTrackDrawer.tsx` (real three-way response, `responder_not_now` label), `src/components/RequestTrackDrawer.tsx` (`donor_not_now` label, corrected status display), `src/index.css` (icon-font overflow guard).

---

## What's next

Not part of this phase's spec, but worth naming honestly: `NotificationPreference.sms_enabled` is still a UI toggle with no backing SMS provider anywhere in the codebase (no Twilio/equivalent config) — pre-existing from an earlier phase, unchanged here, and still overstated by that one toggle in Settings. A future phase should either wire a real provider or remove the toggle rather than leave it silently doing nothing.
