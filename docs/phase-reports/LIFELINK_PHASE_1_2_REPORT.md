# LifeLink — Phase 1 & Phase 2 Report

**Scope:** Phase 1 (shared frontend foundation) and Phase 2 (complete patient portal), built on the ground truth established in `LIFELINK_PHASE_0_AUDIT.md`. Backend and database were not modified — everything below is frontend-only, plus verification.

---

## Starting point

Phase 0 found the frontend already substantially real: patient pages (`FindDonor`, `NewRequestGuidedFlow`, `EmergencySosTracking`, `RequestHistory`), `Settings`, `HelpAndLegal`, `NotificationsBell`/`NotificationsCenter`, `RequireAuth`, and the full Tailwind design-token set already existed, wired to real backend endpoints, with careful in-code documentation of every place a Stitch mock had fabricated data that was deliberately *not* carried into the real build. Phase 1/2 work below is an audit against the two phase specs plus targeted fixes — not a rebuild.

---

## Phase 1 — Shared frontend foundation

### New shared infrastructure (built this pass)
- **Toast system** — `lib/toast.tsx` (`ToastProvider`/`useToast`), mounted in `main.tsx`.
- **ConfirmationDialog**, generic **Drawer**, **Breadcrumbs**, **PageHeader** — `components/`.
- **AppShell / Sidebar / Topbar / MobileNav** — `components/shell/` — an opt-in, role-driven nav shell for *new* pages, so future work doesn't hand-roll navigation again.
- **Global state pages**: `/403` (AccessDenied), `/session-expired` (401), `/500`, `/offline`, plus `ErrorBoundary` → `GenericError` for uncaught render errors, and `OfflineBanner` for live connectivity loss.
- **Shared form library** — `components/forms/`: Text/Email/Phone/Password/Number/Search/Textarea/Select/BloodGroup/Date/Time/Checkbox/RadioGroup/Toggle/FileUpload, each with validation, error, disabled, loading and a11y support out of the box.

### API client hardening (`lib/api.ts`)
Added a 20s timeout, a `X-Request-Id` header per request, status-aware error normalization (400/401/403/404/409/422/429/500/502/503, including FastAPI's array-shaped 422 validation errors and a 429 `Retry-After`), and a `lifelink:session-expired` event: a 401 on any authenticated call (not the initial `/auth/me` probe or login/register themselves) now clears the session and routes to an explanatory page instead of failing silently.

### Routing fixes
- `RequireAuth` now renders `AccessDenied` inline on a role mismatch instead of silently bouncing to the user's own dashboard.
- `AuthPage` now redirects an already-authenticated, verified user straight to their dashboard instead of re-showing the login form.
- Confirmed Family has its own dashboard/pages (`pages/family/*`) — it does not reuse `PatientDashboard`.

### Confirmed already correct (no change needed)
Design tokens (all required hex values and both fonts were already centralized in `tailwind.config.ts`), notification infrastructure (already real, no fabricated data), Settings (already role-aware, real endpoints), Help/Legal (expanded with explicit Consent/Safety/About sections, previously folded into the legal text).

### Explicitly deferred
Retrofitting `AppShell`/the form library onto the ~20 already-shipped dashboard pages was **not** done — each already has a working, tested, bespoke layout, and Phase 1 says not to redesign existing pages unnecessarily. The new shell/form primitives are available for new work; migrating existing pages onto them is a distinct, separately-reviewable follow-up.

### Acceptance
`npm run typecheck`, `npm run lint`, and `npm run build` all pass clean (one pre-existing, unrelated bundle-size *warning*, not an error).

---

## Phase 2 — Patient portal

Audited all 18 listed capabilities against `PatientDashboard` + `pages/patient/*`. Found real, honestly-built coverage for all of them (dashboard, profile via `/profile`, find donor, blood requests, emergency/SOS with the full `ACTIVE → ACKNOWLEDGED → MATCHING → ASSISTANCE_FOUND → RESOLVED`/`CANCELLED` lifecycle, decision transparency, tracking, history, notifications, settings, help/legal). Fixed four real defects found during the audit:

1. **Broken navigation link** — the sidebar on all 4 `pages/patient/*` screens linked to `/patient/emergency-sos`, a route that doesn't exist (the real route is `/patient/sos-tracking`); every click 404'd. Fixed in all four files.
2. **Dead settings button** — the gear icon in `PatientDashboard`'s header had no `onClick`. Wired to `/settings`; the header avatar (previously a stock photo passed off as the user's picture) now links to `/profile` and shows the user's real initials instead.
3. **Fabricated claims in the dashboard's "Decision Transparency Panel"** — "within a 5km radius" and "Euclidean distance calculation within D < 5km" (no such radius exists anywhere in the backend's matching logic) and "Algorithm validated by 14 Clinical Hematologists" with two fake stock-photo headshots. Replaced with an honest description of the real ranking logic (graph/haversine distance, 56-day donation interval, availability) and a link to the real `/decision-transparency` page.
4. **No pagination in Request History** — added real client-side pagination (10/page) over the already-fetched data; the backend's `GET /blood-requests` has no server-side paging, so this is genuine paging of real rows, not a fake page count.

### Authorization — verified, not assumed
Traced all six required guarantees directly in backend source and confirmed each is covered by a passing test in the 209-test suite (all still pass after these changes, re-run for the 8 most relevant files): cross-patient request access (`test_unrelated_user_cannot_view_request`), donor PII exclusion from map data (`test_map_data_privacy`), hospital-inventory writes gated to the hospital role (`_hospital` dependency), verification approval gated to admin-only (`_admin = require_roles(ADMIN)`), and emergency acknowledgement excluding PATIENT by role while emergency resolution is scoped to the emergency's own owner.

### Mobile — measured, not assumed
Registered a throwaway QA patient account, logged in for a real session cookie, and used Playwright to load all 5 patient routes at all 5 required widths (320/375/390/430/768) and measure `scrollWidth` vs `clientWidth`. **Zero horizontal overflow at any width on any page.**

---

## What's next
Phase 0's recommended sequence remains the reference for Phase 3 onward. The AppShell/form-library adoption into existing non-patient dashboards, and the same dead-button/fabricated-claim sweep applied to Hospital/Donor/Admin dashboards (which share the same Stitch-derived header template as `PatientDashboard` and may carry the same settings-button pattern), are natural next candidates.

---

## Addendum — re-verification pass against the Phase 2 checklist

The Phase 2 instruction was re-sent verbatim after the work above shipped. Rather than assume nothing changed, each of the 18 patient capabilities plus every named sub-requirement (Find Donor filters, blood-request validation, emergency lifecycle, decision transparency, tracking, history, mobile, testing) was checked again individually against the current code and, where relevant, the live backend. One genuine gap surfaced and was fixed; everything else already checked out:

- **Capability #11, "view blood compatibility information," was missing entirely** — no compatibility chart or reference existed anywhere in the patient-facing frontend. Added `components/BloodCompatibilityChart.tsx`, with the donor→recipient table transcribed exactly from the backend's own matching logic (`app/models/enums.py::_CAN_DONATE_TO`) — not a generic textbook chart from elsewhere, so it can't drift from what the matching engine actually does. Wired into `FindDonor.tsx` (a collapsible full grid, defaulting to the selected blood type's compatible donors when one is chosen) and into `NewRequestGuidedFlow.tsx`'s blood-group step (an inline summary once a group is picked).
- Confirmed already correct on closer inspection: `RequestTrackDrawer`/`EmergencyTrackDrawer` already expose real match candidates (capability #9), a real event timeline covering both progress and "reopened for matching" recovery events, and working cancel/resolve actions (capability #16) — nothing more was needed there.
- Confirmed `Profile.tsx` already has full real read/write forms per role (capability #2), backed by `GET/PUT` profile endpoints.
- Emergency "outcomes/timestamps/status" history (part of the HISTORY requirement) already exists as its own All/Active/Closed view inside `EmergencySosTracking.tsx`, reachable from the same sidebar as Request History — a second, redundant emergency table was deliberately not added to `RequestHistory.tsx` to avoid duplicating that already-complete view.
- Re-ran `typecheck`/`lint`/`build` (all clean) and, with a live logged-in Playwright session, re-measured horizontal overflow at all five required widths with the new compatibility panels expanded — still zero overflow everywhere.
