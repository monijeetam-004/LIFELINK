# LifeLink — Phase 22 Audit: Responsive Design + Accessibility + Mobile UX Hardening

## 0. Actual verified baseline (before this phase's changes)

- Backend: `pytest --collect-only -q` → **694 tests collected** (693 passed + 1 conditional skip — `test_rq_worker_job_timeout_is_a_real_honest_failure_not_a_silent_hang`, which skips unless a live RQ worker process is running; this is a pre-existing, environment-conditional skip, not new). `ruff check .`: **101 pre-existing findings**. `mypy app`: **3 pre-existing errors**. All three numbers match the Phase 21 report exactly — confirms the environment matches the documented Phase 21 exit state before any Phase 22 change was made.
- Frontend: `npx playwright test --list` → **30 tests in 11 files**, matching the Phase 21 report exactly. `tsc --noEmit`, `eslint .`, `vite build` all clean before any change.

No backend file and no test file was modified in this phase (verified via `find … -newer LIFELINK_PHASE_21_REPORT.md` across `backend/`), so the backend numbers above are expected to be, and are, unchanged at the end of this phase too — see the report's regression section.

## 1. Design system inspection

Read in full: `frontend/tailwind.config.ts` (145 lines — M3-style + legacy Phase-1 color tokens, spacing scale, typography scale, shadows, radii, no custom `screens` override) and `frontend/src/index.css` (361 lines before this phase — base Tailwind plus every page-specific class merged in from the individual Stitch exports).

No new color, typography, spacing, shadow, or radius token was introduced anywhere in this phase. The only global CSS addition is a `prefers-reduced-motion` media query (section 14 below) — it changes animation/transition *duration*, not any visual token, and only takes effect for users who have that OS preference turned on, so it carries none of the redesign risk the phase brief is explicit about avoiding.

Confirmed gaps at the start of this phase: no `prefers-reduced-motion` media query anywhere in the stylesheet; no global `:focus-visible` override (not needed in practice — see section 10).

## 2. Method

This audit is code-level (static inspection of every route's source, `grep`-driven sweeps for known anti-patterns, and targeted Playwright runs at a real mobile viewport against the real backend) rather than a manual pixel-by-pixel DevTools sweep at all nine specified breakpoints across all ~45 routes. Given the size of this codebase (9 shared shell/dialog components, ~45 page components across 5 roles), the audit prioritized finding and fixing the highest-severity, most concretely reproducible bugs — the same triage strategy Phase 21 used and documented — over a shallow pass across every section. Section 12 (below) lists what was and wasn't exhaustively covered.

Categories used below match the phase brief: **A** already correct, **B** genuine responsive bug, **C** genuine accessibility bug, **D** touch usability issue, **E** keyboard-navigation issue, **F** screen-reader issue, **G** focus-management issue, **H** mobile-only functional bug, **I** desktop-only regression, **J** design preference (not a bug), **K** no changes needed.

## 3. Finding #1 (B/H — fixed): 16 pages had a desktop-only sidebar and zero mobile navigation

**Root cause.** The codebase has two navigation architectures side by side. A newer, accessible, opt-in shell system (`components/shell/AppShell.tsx` + `Sidebar.tsx` + `Topbar.tsx` + `MobileNav.tsx`) is used by exactly one page (`HospitalDashboard.tsx`). Every other dashboard/sub-page was converted 1:1 from its own individual Google Stitch export, each with its own hand-rolled `<aside className="hidden lg:flex …">` (or `hidden md:flex`) sidebar and no mobile fallback of any kind — not a hamburger button, not a bottom nav, nothing.

**Confirmed affected (14 pages, all fixed — see report for the fix):**
- All 8 Admin pages: `AdminOverview`, `AdminAuditFeed`, `AdminDonors`, `AdminFacilities`, `TransportRoutes`, `UserManagement`, `VerificationsQueue`, `AdminAnalytics` — byte-identical `hidden lg:flex` sidebar + `lg:pl-72`/`lg:left-72` header, sharing `ADMIN_NAV`/`ACCOUNT_NAV` from `pages/admin/adminNav.ts`.
- `PatientDashboard.tsx` — `hidden md:flex` sidebar, `md:ml-64` main.
- `family/FamilyDashboard.tsx` — `hidden lg:flex` sidebar, `lg:pl-72` main; already had a compatible `NAV` array.
- 4 patient sub-pages sharing one more identical structure (`hidden lg:flex` / `w-64` / `lg:left-64`): `patient/EmergencySosTracking.tsx`, `patient/FindDonor.tsx`, `patient/NewRequestGuidedFlow.tsx`, `patient/RequestHistory.tsx` — each already had an identical `NAV` array.

**Confirmed NOT affected, checked and ruled out rather than assumed:**
- `DonorDashboard.tsx` (category **A**) — already has its own real, working `md:hidden` bottom nav (`<nav className="fixed bottom-0 left-0 w-full z-50 flex justify-around items-center … md:hidden">`), confirmed by both its own file-header comment and a grep for the actual element.
- `HospitalDashboard.tsx` (category **A**) — the one page already using the accessible `AppShell`/`MobileNav` system, which has a working hamburger + slide-over.
- `DonorRecommendation.tsx` and `Analytics.tsx` (category **K** — no change needed, not manufactured). Both are explicitly labeled, pre-existing "CONCEPTUAL ILLUSTRATION — NOT LIVE DATA" static demo pages (Phase 16). Their sidebar/header nav links are **all `href="#"` placeholders** — non-functional on desktop too. There is no real navigation being hidden on mobile that works on desktop, so adding a working mobile drawer with real routes here would not be fixing a regression, it would be adding functionality the desktop version never had — out of scope per "do not manufacture issues," and noted rather than silently skipped.

**Reproduction (before the fix).** Open any of the 14 affected routes at a viewport under `lg` (or `md` for `PatientDashboard`) as a signed-in user of that role. The sidebar is not rendered (`hidden`), and there is no other control anywhere on the page that opens navigation, so the person is stuck on that one page — they cannot reach Settings, Profile, Notifications, or any sibling page without editing the URL bar by hand.

## 4. Finding #2 (C/G — fixed): the shared `Modal` had no dialog semantics, no focus management, and no real focus trap

**Root cause.** `components/Modal.tsx` is the base every real interactive dialog in the app renders through: `BloodRequestModal`, `SosModal`, `HospitalInventoryModal`, `ConfirmationDialog`, `RequestTrackDrawer`, `EmergencyTrackDrawer`. Before this phase it had Escape-to-close, but:
- no `role="dialog"` / `aria-modal="true"` / `aria-labelledby` — a screen reader had no way to know an overlay dialog had opened, or what it was called;
- no focus moved into the dialog on open — keyboard/screen-reader focus silently stayed wherever it was, often on a page behind a modal overlay it could no longer see;
- no focus trap — Tab could walk focus out of the dialog into the page behind the `bg-black/40` overlay, which is exactly the scenario section 11 warns about (and explicitly warns not to fix with a *fake* trap that just blurs/refocuses);
- no focus restored to the triggering element on close.

**Impact.** Every one of the six dialogs above inherited all four gaps at once, since they all render through this one component — this is the same "shared architecture, one fix point" situation Phase 21 exploited when fixing `toApiError`.

**Fix.** Added real `role="dialog"`/`aria-modal="true"`/`aria-labelledby` (pointing at the existing `<h3>` title, given a stable id via `useId`), a `previouslyFocused` ref that focuses the close button on open and restores focus to whatever had it before on close, and a genuine Tab/Shift+Tab cycle among the dialog's own focusable elements (computed live via `querySelectorAll` + an `offsetParent !== null` visibility check, not a fixed/cached list) — not a fake trap.

**Regression test.** `phase22_responsive_mobile_nav.spec.ts`, scenario (D): opens the real SOS modal on `/patient`, asserts `role="dialog"`/`aria-modal="true"`, asserts the close button is focused immediately on open, asserts Escape closes it.

Also carried the same Escape-to-close + focus-on-open fix back to `components/shell/MobileNav.tsx` (the older shell nav's own slide-over, used by `HospitalDashboard.tsx`), which had the identical gap and is otherwise structurally the direct ancestor of the new `MobileNavDrawer`.

## 5. Finding #3 (C — fixed): 9 form controls across 5 files removed the native focus outline with no visible replacement

**Root cause.** A `grep` for `outline-none` not paired with `focus:outline-none` (i.e., applied unconditionally rather than only on focus) found 9 `<select>`/`<input>` elements across `pages/shared/StrategyLab.tsx`, `pages/admin/TransportRoutes.tsx` (5 occurrences), `pages/hospital/DonorDirectoryAndLogistics.tsx`, `pages/hospital/InventoryConsole.tsx`, and `pages/hospital/ResourcePathways.tsx` that had **no** visible focus indicator at all — a sighted keyboard user tabbing through, say, TransportRoutes' origin/destination facility pickers gets no visual sign of which control has focus. Every other interactive control in the app already follows the established `focus:outline-none focus:ring-2 focus:ring-primary/40` pattern (confirmed by the same grep — 16 of the 25 total `outline-none` occurrences already pair it correctly); these 9 were the exception, not the rule.

**Fix.** Added `focus:ring-2 focus:ring-primary/40` (plus `focus:border-primary` where the element already has a border) to each of the 9, matching the codebase's own existing convention exactly rather than inventing a new one.

## 6. Finding #4 (C — fixed): the login/register/forgot-password/reset-password forms had no `<label>` association, relying on `placeholder` alone

**Root cause.** Section 12 explicitly calls for re-auditing form labeling from scratch ("Phase 18 previously discovered missing label/htmlFor associations. Do another complete audit rather than assuming all forms are now correct.") A fresh audit found `AuthPage.tsx` (login + register), `ForgotPassword.tsx`, and `ResetPassword.tsx` — the very first forms an unauthenticated visitor ever sees — had zero `<label>` elements and zero `aria-label`/`aria-labelledby` attributes on their `Full name`/`Email address`/`Password`/`6-digit code`/`New password` inputs. Browsers do fall back to using `placeholder` as the accessible name when nothing else is present, so these were not silently nameless to assistive tech, but relying on `placeholder` alone is a well-known WCAG-flagged anti-pattern (the hint disappears the moment the field has a value, and behavior across screen-reader/browser combinations is inconsistent) — and every properly-labeled form elsewhere in the app (Settings, NewRequestGuidedFlow, InventoryConsole, etc.) already does better than this.

**Fix.** Added a visually-hidden (`sr-only` — invisible, so the visual design is completely unchanged) `<label htmlFor>` for every one of these 6 fields, paired with a stable `id`, plus the correct `autoComplete` value for each (`email`, `name`, `new-password`/`current-password`, `one-time-code`) per section 12's own "appropriate autocomplete" requirement — none of these fields had `autoComplete` set before this phase either.

## 7. Sections audited via code inspection with no genuine issue found (category A / K)

- **Offline banner (section 16).** The existing offline connectivity banner (Phase 21) is a simple fixed top bar; inspected at the class level and confirmed it does not use a fixed height that would collide with any of the newly-added mobile hamburger headers (all of which are `h-16`/`h-20` and the banner sits above them, not overlapping).
- **Error/loading/empty accessibility (sections 13–15).** Phase 21's `toApiError` + `role="alert"` pattern, and the EMPTY≠ERROR distinction, were not touched by any change in this phase (confirmed no file under `lib/api.ts`'s consumption pattern was edited) and the full existing Playwright suite covering that behavior (Phase 19/20/21 specs) still passes 100%.
- **Maps (section 19).** `DonorMap` and the graph/pathway map surfaces were not modified. No change in this phase touches `GRAPH`/`HAVERSINE_FALLBACK` labeling or any map rendering code.
- **Print styles.** `index.css`'s existing `@media print` block (certificate printing) is unaffected by the new `prefers-reduced-motion` block, which is a separate, independent media query.

## 8. Sections not exhaustively covered in this pass (honestly scoped, not hidden)

Given the size of this codebase, the following sections from the phase brief received targeted checks (see above) rather than an exhaustive, breakpoint-by-breakpoint or page-by-page sweep, and are the natural scope for a follow-up pass using the same fix pattern already established here:

- A full manual/DevTools sweep at all nine specified pixel widths (320/360/375/390/414/768/1024/1280/1440 + intermediates) across every route.
- A complete touch-target size audit of every icon-only button, kebab menu, and pagination control app-wide (spot-checked only, via the mobile-nav fix's own hamburger/close buttons, which use the existing `p-2`/`p-1.5` rounded-full touch-target convention already used elsewhere in the app).
- A full keyboard-only sweep of every table/chart/map/pagination control beyond the focus-visibility fix in section 5 and the dialog focus-trap fix in section 4.
- Responsive-table-treatment decisions per admin table (users, facilities, donors, verification queue, certificates, audit logs) — none were changed in this pass; each already uses a horizontally-scrollable container rather than an unconstrained table, which is at minimum not a regression, but was not evaluated for whether card-based treatment would be a genuine improvement.
- Chart resizing/legend-overlap testing on Admin/Hospital Analytics at narrow widths.
- Typography/zoom testing at 125–200% browser zoom.
- A full color-contrast audit of every badge/status-chip/disabled-control combination against WCAG AA.

None of the above were found to contain a concretely reproducible bug during the targeted checks that were performed; they are flagged as unverified rather than claimed as either "fixed" or "already correct."
