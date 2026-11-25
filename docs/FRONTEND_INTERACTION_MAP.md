# LifeLink — Frontend Interaction Map

_Audit date: 2026-09-06_

> **Phase 1 update (2026-09-06):** the auth surface is now wired to the backend.
> `AuthPage` submit calls real `login`/`register`; "Forgot password?" links to
> `/forgot-password`; new pages `VerifyEmail` / `ForgotPassword` / `ResetPassword`
> exist; `RequireAuth` guards the role dashboards; `AccountMenu` (bottom-left)
> provides a real sign-out on every authenticated page.
>
> **Phase 2 update (2026-09-06):** `PatientDashboard` and `DonorDashboard`
> (desktop) are wired to the real backend — design/markup unchanged:
> - **Patient:** notifications bell (`NotificationsBell`, live), "New Request"
>   (sidebar + hero) → `BloodRequestModal`, "Emergency SOS" (header + hero) →
>   `SosModal`, "Recent Requests Timeline" table → real `/blood-requests`,
>   "Track" → `RequestTrackDrawer` (status + timeline + donor responses +
>   cancel/fulfill). Left two stat cards now show the patient's real open-request
>   / accepted-donor counts.
> - **Donor:** real name, **availability toggle** (persisted), blood-group
>   setter, "Emergency Requests" list → real compatible open requests with
>   Respond / Not-now, "Next Eligibility" computed from `last_donation_date`,
>   "Your Impact" → real donation totals, Milestones panel → real **Donation
>   History**, notifications bell.
>
> **Phase 3 update (2026-09-06):** `HospitalDashboard` is wired — design/markup
> unchanged:
> - KPIs → real (active emergencies, total blood stock, unhandled requests,
>   donors on map); **Blood Inventory cards → real** + "View Full Inventory" /
>   "Manage" → `HospitalInventoryModal`; **Request Management board → real
>   incoming requests** (city-scoped) grouped by handling status, card click →
>   `RequestTrackDrawer` in hospital mode (ACKNOWLEDGE / PREPARING / DISPATCHED /
>   DECLINED + factual "Compatible donors" list); "Active Donors (Live)" → real
>   `DonorMap` (Leaflet + OpenStreetMap, anonymised opt-in donor pins);
>   Verification-queue panel → real "Incoming Emergencies"; EMERGENCY SOS →
>   `SosModal`; New Request → `BloodRequestModal`; bell → `NotificationsBell`.
> - `DonorDashboard`: + "Share approx. location" opt-in (browser geolocation,
>   coarsened) so a donor can appear on the hospital map.
> - `RequestTrackDrawer` (patient view) now also shows the factual match-candidate
>   list from `/blood-requests/{id}/match-candidates`.
>
> **Phase 4 update (2026-09-06):** `DonorDashboard` matching workflow completed —
> design/markup unchanged:
> - The fabricated "Readiness Score 94 / Optimal" ring is now **"Donation
>   Readiness"** — real recovery progress toward the 56-day interval
>   (`eligible` / `eligible_on` from `/donors/me`), with the ring arc computed
>   from that fraction.
> - "Respond Now" is **disabled with a reason** when the donor is unavailable or
>   still inside the donation interval — mirrors the server rule.
> - An accepted request now shows **"Accepted ✓" + "Withdraw"**; Withdraw calls
>   `respond` with `DECLINED`, notifies the requester, and (if it was the last
>   acceptance) reopens the request.
> - **Donation History** panel also lists closed commitments (fulfilled /
>   cancelled / withdrawn) from the new `GET /donors/me/responses`.
> - `RequestTrackDrawer` timeline learned `donor_withdrew` / `reopened` labels.
>
> Still static (Phase 4 / out of scope): `DonorDashboardMobile`, the admin
> dashboard, decision-support/research pages, hospital "Avg Match Time" +
> Analytics/Simulation nav, Patient "Donors Nearby / Avg Response Time", donor
> "Readiness Score" and scheduling buttons.
>
> **Phase 5 update (2026-09-06):** the emergency / SOS network is wired end to end
> — design/markup unchanged:
> - **new `EmergencyTrackDrawer`** — status badge, responder list, timeline,
>   compatible-responder list (creator only), and role-appropriate actions
>   (creator: assistance-found / resolve / cancel · donor & hospital:
>   acknowledge / "I can help now" / withdraw).
> - **Patient/Family** (`PatientDashboard`): an "Active Emergency SOS" panel
>   appears whenever the user has an open emergency; rows open the drawer in
>   creator mode. `SosModal` unchanged.
> - **Donor** (`DonorDashboard`): an "Active Emergencies Near You" panel lists
>   `GET /emergencies/active`; rows open the drawer in responder mode and show
>   "You're responding ✓" once accepted.
> - **Hospital** (`HospitalDashboard`): the existing "Incoming Emergencies" rows
>   are now clickable → drawer in responder mode.
> - `src/lib/domain.ts`: emergency responder / detail / open / match types;
>   `EmergencyStatus` gains `MATCHING` / `ASSISTANCE_FOUND`; calls for `active`,
>   `getEmergency`, `respond`, `acknowledge`, `assistance-found`,
>   `match-candidates`.
>
> **Phase 6 update (2026-09-06):** donation certificates are real —
> design/markup preserved:
> - `DonationCertificate` (`/certificate`, `/certificate/:id`) is bound to real
>   `GET /certificates` data: donor name, hospital, serial ("Document ID"), issue
>   date and verification URL are live. **`window.print()` is gone** — "Download
>   PDF" is an `<a>` to the signed `/certificates/{id}/download?token=…` URL;
>   "Verify Document" links to the public `/verify/:code`. A serial switcher shows
>   when the donor has more than one certificate; empty / loading states added.
> - **new `VerifyCertificate`** page — public `/verify/:code` (no auth): valid →
>   "Authentic certificate" + safe fields; unknown → "Not verified".
> - `DonorDashboard` Donation-History rows gained **Get certificate** (issues via
>   `POST /certificates`, then opens `/certificate/:id`) / **Certificate** (opens
>   an already-issued one).
> - `src/lib/domain.ts`: `Certificate` / `CertificateVerification` types and
>   `listCertificates` / `issueCertificate` / `getCertificate` /
>   `verifyCertificate` / `certificateDownloadUrl`.

Inventory of every interactive element in `frontend/src` and whether it does
anything real. **Summary: the only functioning interactivity in the whole app is
`AuthPage`'s local state (mode/role toggles, show-password), the fake submit, and
the `ShaderBackground` mouse listener. Everything else is inert markup.**

## Routes (`src/App.tsx`)

All routes render **unconditionally — no auth guard, no role check**.

| Path | Component | Guard? |
|---|---|---|
| `/`, `/login`, `/register` | `AuthPage` | none |
| `/dev-index` | `DevIndex` (inline) | none |
| `/patient` | `PatientDashboard` | none |
| `/hospital` | `HospitalDashboard` | none |
| `/donor`, `/donor/mobile` | `DonorDashboard`, `DonorDashboardMobile` | none |
| `/admin/verification` | `AdminVerificationPortal` | none |
| `/decision-transparency` | `DecisionTransparency` | none |
| `/mathematical-model` | `MathematicalModel` | none |
| `/graph-engine` | `GraphDecisionEngine` | none |
| `/simulation` | `SimulationMode` | none |
| `/analytics` | `Analytics` | none |
| `/research-mode` | `ResearchMode` | none |
| `/certificate` | `DonationCertificate` | none |
| `/recommendation` | `DonorRecommendation` | none |
| `*` | `<Navigate to="/">` | — |

## `AuthPage.tsx` (`/`, `/login`, `/register`)

| Element | Handler | Real? | Notes |
|---|---|---|---|
| "Sign in" / "Create account" tabs | `setMode` | ✅ local | animates toggle |
| Role grid (Patient/Family, Donor, Hospital, Admin) | `setRole` | ✅ local | **4 options; combines PATIENT+FAMILY into one; brief needs 5 distinct roles** |
| Full name / Email / Password inputs | `useState` controlled | ✅ local | `minLength={6}` on password |
| Show/hide password | `setShowPassword` | ✅ local | |
| "Forgot password?" | — | ❌ | `<a href="#">`, no route |
| Form submit | `handleSubmit` | ❌ FAKE | `localStorage.setItem("lifelink_user", …)` then `setTimeout(650)` → `navigate(rolePath)`. No API, no validation, no auth. |
| "Explore an emergency search first" | `navigate("/recommendation")` | ✅ nav | |
| Rotating impact stats (`QUOTES`) | `setInterval` | ✅ local | hardcoded marketing numbers ("12 min", "50k+", "98.4%", "24/7") |
| Bottom "Create an account / Sign in" links | `setMode` | ✅ local | |

**Needed:** replace `handleSubmit` with real register/login mutations; add
`FAMILY` role; wire "Forgot password?" to an OTP flow; add loading/error states;
drop the `localStorage` write.

## `PatientDashboard.tsx` (`/patient`)

| Element | Real? | Notes |
|---|---|---|
| "Request Blood Now" / hero CTAs (l.40, 93, 96) | ❌ | no `onClick` |
| Header "New Request" button (l.69) | ❌ | no `onClick` |
| Bell / settings icon buttons (l.74, 77) | ❌ | decorative |
| `#donor-count` = `128`, `#response-time` = `4.2` (l.146, 159) | ❌ | hardcoded; ids are dead Stitch counter hooks |
| Active-request cards + "Track" buttons (l.240, 260) | ❌ | no handler |
| "Archive" button (l.280) | ❌ | `opacity-50 cursor-not-allowed`, intentionally dead |
| All nav `<a href="#">` | ❌ | |

## `DonorDashboard.tsx` / `DonorDashboardMobile.tsx` (`/donor`, `/donor/mobile`)

| Element | Real? | Notes |
|---|---|---|
| "Pre-Schedule Now" / "Review Criteria" (l.72–73) | ❌ | no handler |
| "Respond Now" (l.130) | ❌ | match-offer accept — inert |
| "Alert Me" (l.146) | ❌ | inert |
| "View All" (l.154) | ❌ | inert |
| Floating action button (l.213) | ❌ | inert |
| Bell/settings icons (l.19, 22) | ❌ | decorative |
| Eligibility %, next-eligible date, donation history | ❌ | hardcoded |
| Bottom nav / sidebar `href="#"` (mobile l.144–184) | ❌ | |

## `HospitalDashboard.tsx` (`/hospital`)

| Element | Real? | Notes |
|---|---|---|
| Sidebar nav links (l.20–32) | ❌ | `href="#"` |
| Search input (l.58) | ❌ | uncontrolled, no submit |
| "New Request" button (l.64) | ❌ | inert |
| Notification bell (l.69) | ❌ | decorative badge |
| Inventory bars / counts (l.99–143) | ❌ | hardcoded |
| Request-queue "PENDING (2)" etc. (l.260) | ❌ | hardcoded |
| Sparkline placeholders (l.172–234) | ❌ | empty `div`s |
| **Map (l.354–356)** | ❌ | `<div style={{backgroundImage:url('https://lh3.googleusercontent.com/aida-public/...')}}>` — a **static PNG**, `data-location="New York Medical District"`. **Not Leaflet.** No markers, no coordinates. |
| Map zoom buttons (l.247, 250) | ❌ | inert |
| Donor-row action buttons (l.384, 396, 401) | ❌ | inert |

## `AdminVerificationPortal.tsx` (`/admin/verification`)

| Element | Real? | Notes |
|---|---|---|
| Top tabs Impact/Eligibility/Ready/Account (l.14–17) | ❌ | `href="#"` |
| Sidebar nav (l.45–71) | ❌ | `href="#"` |
| Search input (l.134) | ❌ | uncontrolled |
| "Export" / secondary header buttons (l.88, 92) | ❌ | inert |
| **Approve / Reject** buttons (l.177–178, 210–211, 234–235) | ❌ | no handler — core admin action is fake |
| Pagination buttons (l.243–249) | ❌ | inert; note `disabled=""` string attr (invalid in React, should be `disabled` / `disabled={true}`) |
| Bottom mobile nav (l.258–270) | ❌ | `href="#"` |
| Verification queue rows | ❌ | hardcoded |

## `DonationCertificate.tsx` (`/certificate`)

| Element | Real? | Notes |
|---|---|---|
| "Download" button (l.36) | 🟡 | `onClick={() => window.print()}` — opens browser print dialog; **no server-generated PDF, no stored file, no verify** |
| Nav tabs Certificates/Verification/History (l.15–17) | ❌ | `href="#"` |
| Certificate serial / donor name / date | ❌ | hardcoded |

## `DonorRecommendation.tsx` (`/recommendation`)

| Element | Real? | Notes |
|---|---|---|
| `#counter1..4` (l.70–82) render `0s / 0% / 0k+ / 0ms` | ❌ | Stitch animated-counter `<script>` was stripped; permanently shows zero |
| `<canvas id="shader-canvas-ANIMATION_2">` (l.36) | ❌ | orphan canvas, no JS attached |
| CTA buttons (l.25, 56, 59, 62, 237, 240) | ❌ | no handlers |
| Nav links Guidelines/Protocols/Analytics + footer (l.16–18, 260–263) | ❌ | `href="#"` |
| Donor result cards | ❌ | hardcoded, incl. any match figures |

## `SimulationMode.tsx` (`/simulation`)

| Element | Real? | Notes |
|---|---|---|
| "EMERGENCY SOS" button (l.20) | ❌ | inert |
| **"Run" button `#runSim`** (l.68) | ❌ | Stitch driver script stripped — clicking does nothing |
| `#step-0..7` pipeline nodes, `#donor-nodes`, `#connections` svg, `#log-container`, `#sim-progress`, `#success-card` (l.79–198) | ❌ | DOM hooks for the missing animation script; render static |
| "Execute Dispatch" button (l.176) | ❌ | inert |

## `Analytics.tsx` (`/analytics`)

| Element | Real? | Notes |
|---|---|---|
| Sidebar nav (l.16–38) | ❌ | `href="#"` |
| Tabs Certificates/Verification/History (l.59–61) | ❌ | `href="#"` |
| "Search Hospital UID" input (l.66) | ❌ | uncontrolled |
| All KPI numbers & charts | ❌ | hardcoded; `recharts` not used |

## `DecisionTransparency.tsx`, `MathematicalModel.tsx`, `GraphDecisionEngine.tsx`, `ResearchMode.tsx`

Static explanatory / research pages. All nav + footer links are `href="#"`.
`MathematicalModel` has static `#counter-*` spans. `GraphDecisionEngine` uses
hand-drawn SVG (no `reactflow`). No forms, no real controls.

## `components/ShaderBackground.tsx`

✅ **Fully functional.** WebGL shader canvas; `useRef` + `useEffect`, compiles
GLSL, `requestAnimationFrame` loop, `window.addEventListener("mousemove", …)`
updating a uniform, cleaned up on unmount. Keep unchanged.

## Dead-control tally (to fix in the post-implementation interaction audit)

- `href="#"` links: **~70** across all pages (nav bars, footers, tab strips).
- Buttons with no `onClick`: **~40** (Approve/Reject, Respond Now, Track, New
  Request, Run simulation, map zoom, pagination, hero CTAs, …).
- Fake/stripped script hooks: `#counter1..4`, `#counter-u`, `#runSim` +
  `#step-*` + sim log/progress, orphan `#shader-canvas-ANIMATION_2`.
- Fake auth: `AuthPage.handleSubmit` localStorage + setTimeout.
- Uncontrolled search inputs with no submit: `HospitalDashboard`,
  `AdminVerificationPortal`, `Analytics`.
- `window.print()` standing in for certificate download.
- Invalid React attr: `disabled=""` in `AdminVerificationPortal.tsx:243`.
- Hardcoded data in **every** page.

Target after implementation: **zero known dead controls** on in-scope pages
(auth + the connected role dashboards); decision-support/research pages may keep
static illustrative content but their nav must resolve.
