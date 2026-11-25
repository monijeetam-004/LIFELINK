# LifeLink Phase 31.1 — Domain Signal Frontend Integration & Cross-Portal Verification

**Verdict: GO.** Both new backend signals (`Emergency.deadline_exceeded_at`, `Certificate.revoked_at`/`revocation_reason`) are now genuinely visible and usable through the real frontend, with no fake data, no duplicated backend logic, no weakened authorization, and no regression found in backend, TypeScript, ESLint, production build, or Playwright E2E (details in Section 14). This is not a claim that the application is "production ready" as a whole — see Section 16 for the limitations this phase inherited from Phase 31 and did not (and was not asked to) resolve.

## 1. Files inspected

Before any modification, the following were read in full to map the existing frontend architecture, conventions, and reusable components (per the master prompt's mandatory inspect-before-modify sequencing):

- `frontend/src/App.tsx` (529 lines) — full route table, confirming `/verify/:code` is genuinely public (no `RequireAuth`), `/certificate` and `/certificate/:id` are behind bare `RequireAuth` (any role), the admin certificate registry lives inside `pages/admin/AdminAnalytics.tsx` (not a separate file), and the donor certificate list lives inside `pages/donor/MilestonesHistory.tsx`.
- `frontend/src/lib/types.ts` — confirmed this file holds only `Role`/`User`/`MessageResponse`; all domain types (`Emergency`, `Certificate`, etc.) live in `lib/domain.ts` instead.
- `frontend/src/lib/api.ts` — the shared Axios client, `toApiError` normalization (used verbatim by every new error path added this phase — no new error-handling logic was written), and the existing per-status fallback-message table (already covers 400/401/403/404/409/422/429/500/502/503).
- `frontend/src/lib/domain.ts` (1212 lines) — every existing type and API-calling function, in full, to find the exact shape of `Emergency`, `Certificate`, `CertificateVerification`, `AdminCertificate`, and the existing `listAdminCertificates`/`verifyCertificate` functions this phase had to extend rather than duplicate.
- `backend/app/schemas/certificate.py`, `backend/app/schemas/admin.py`, `backend/app/schemas/emergency.py` — the authoritative backend field names/types (`revoked_at`, `revocation_reason`, `deadline_exceeded_at`) so the frontend types added below match the real API contract exactly, never a guessed shape.
- `backend/app/api/routes/admin.py` (`revoke_certificate` route, lines 1203+) — confirmed the existing `POST /admin/certificates/{id}/revoke` endpoint's exact behavior: idempotent, 404 on unknown certificate, requires `CertificateRevokeIn.reason` (min length 3), audit-logged, never deletes the certificate or touches the donation. No new backend route was created or considered.
- `frontend/src/components/EmergencyTrackDrawer.tsx` — the single shared emergency-detail component used identically by every portal (confirmed via a repo-wide grep: `pages/PatientDashboard.tsx`, `pages/HospitalDashboard.tsx`, `pages/DonorDashboard.tsx`, `pages/donor/ReadyConsole.tsx`, `pages/family/FamilyDashboard.tsx`, `pages/family/FamilyPatientDetail.tsx`, `pages/patient/EmergencySosTracking.tsx` all import it). This became the single integration point for the deadline-exceeded notice.
- `frontend/src/pages/VerifyCertificate.tsx`, `frontend/src/pages/DonationCertificate.tsx`, `frontend/src/pages/donor/MilestonesHistory.tsx`, `frontend/src/pages/admin/AdminAnalytics.tsx` — read in full to find the exact existing markup, status-badge conventions, and error/loading patterns to extend rather than rebuild.
- `frontend/src/components/Modal.tsx`, `frontend/src/components/ConfirmationDialog.tsx`, `frontend/src/components/SosModal.tsx`, `frontend/src/pages/admin/UserManagement.tsx` (its existing suspend-with-reason flow) — the established design-system patterns for a destructive admin action requiring a real, minimum-length, free-text reason with a loading/disabled submit button. The admin certificate-revoke dialog reuses this exact pattern rather than inventing a new one.
- `frontend/src/components/OfflineBanner.tsx` — an example of the codebase's existing "small alert banner" visual language (`role="status"`/`role="alert"`, icon + short sentence), reused for the deadline-exceeded notice rather than a new visual treatment.
- Repo-wide greps for every certificate-related and emergency-related frontend file (confirmed via `grep -rl`) to rule out any other screen independently rendering emergency status or certificate validity outside the components actually touched — see Section 3/4 for the resulting scope decisions and their justification.
- `frontend/e2e/helpers.ts`, `frontend/e2e/setup.ts`, and three representative existing specs (`phase26_donation_idempotency.spec.ts`, `phase26_accessibility.spec.ts`, `phase26_responsive_overflow.spec.ts`) — the existing Playwright conventions (real-API setup via `apiLogin`, real-UI-driven assertions, the shared axe-core scan helper, the shared horizontal-overflow check) reused verbatim for this phase's new E2E coverage.
- `frontend/package.json` — confirmed no component-test framework (vitest, Jest, React Testing Library) exists anywhere in this codebase; Playwright is the only frontend test runner installed. This directly shaped the testing approach in Section 7.

No frontend file was modified until every item above had been read.

## 2. Files changed

Eight files, all additive (no existing behavior removed, no existing component rebuilt):

- `frontend/src/lib/domain.ts` — added `deadline_exceeded_at` to `Emergency` (inherited by `EmergencyDetail`/`EmergencyOpen`); added `revoked_at`/`revocation_reason` to `Certificate`, `CertificateVerification`, and `AdminCertificate`; added `revokeAdminCertificate(id, reason)` calling the existing `POST /admin/certificates/{id}/revoke`.
- `frontend/src/components/EmergencyTrackDrawer.tsx` — added the "Past deadline — action needed" notice.
- `frontend/src/pages/VerifyCertificate.tsx` — added the valid/revoked distinction on the public verification page.
- `frontend/src/pages/DonationCertificate.tsx` — added a revoked banner and changed the "DOCUMENT STATUS" badge to reflect revocation.
- `frontend/src/pages/donor/MilestonesHistory.tsx` — added a Valid/Revoked badge (plus the reason, when revoked) to each certificate card.
- `frontend/src/pages/admin/AdminAnalytics.tsx` — added Status/Action columns and a revoke dialog to the certificate registry table; updated the existing "verify a certificate by code" widget to distinguish valid/revoked.
- `frontend/e2e/setup.ts` — exported the already-existing `apiLogin` helper (was module-private) so the new spec below can set up real cross-role state without duplicating it.
- `frontend/e2e/phase31_1_domain_signals.spec.ts` — new Playwright spec (see Section 7).

No backend file was changed. No migration was added or modified.

## 3. Emergency UI changes

`EmergencyTrackDrawer.tsx` (the one component every portal's emergency-detail view already shares) now renders, immediately after the status/blood-type row and before the location line:

```
{em.deadline_exceeded_at && (
  <div role="alert" className="... bg-error/10 text-error ...">
    <span className="material-symbols-outlined">schedule</span>
    Past deadline — action needed
  </div>
)}
```

This is a separate, independent element from the existing status badge above it — it never replaces, merges with, or recolors the status badge, and it renders only when the backend has actually set `deadline_exceeded_at` (never derived from the existing `slack_seconds` being negative, and never rendered on a fetch failure — `em` is only defined once `q.data` has actually loaded). The banner text is deliberately non-clinical and makes no claim about automatic escalation, matching the master prompt's explicit forbidden-phrase list.

Because `EmergencyTrackDrawer` is imported identically by `PatientDashboard`, `HospitalDashboard`, `DonorDashboard`, `donor/ReadyConsole`, `family/FamilyDashboard`, `family/FamilyPatientDetail`, and `patient/EmergencySosTracking`, this single change gives byte-identical rendering of the same backend fact across every portal that opens an emergency's detail view — PATIENT, FAMILY, DONOR, and HOSPITAL. No admin emergency-detail view exists in this codebase (the admin Overview page shows only an aggregate active-emergency count, never a per-emergency detail), so there was nothing to add the notice to on the admin side; this is an existing gap unrelated to this phase, not a new omission.

No condensed dashboard list row (the one-line "blood type · location · status" rows on `PatientDashboard`, `HospitalDashboard`, `DonorDashboard`, `donor/ReadyConsole`, `family/FamilyPatientDetail`) was changed. None of those rows show `slack_seconds` or `deadline` today either — timing detail is, by existing design, drawer-only — so adding the deadline-exceeded fact only to the drawer is consistent with (not a departure from) the app's existing information architecture, not a scope-reduction shortcut.

## 4. Certificate UI changes

- **Public verification (`/verify/:code`, no auth)**: now branches on `q.data.valid` (the backend's own honest verdict). `valid: true` renders the pre-existing "Authentic certificate" success state, unchanged. `valid: false` renders a distinct "Certificate revoked" state with the real `revocation_reason` and `revoked_at` date, never conflated with the pre-existing "Not verified" (genuinely-unknown-code, 404) state or the "Couldn't check this certificate" (network/server-error) state — all three remain visually and textually distinct, and the certificate's own factual details (donor, serial, donation date, units, hospital) are still shown underneath either way, since the backend intentionally still returns them (so a relying party can see what the certificate *was*).
- **Donor's own certificate detail (`/certificate/:id`)**: a revoked certificate now shows a dismissal-free banner ("This certificate has been revoked: `<reason>` (`<date>`)") above the certificate face, and the bottom-right "DOCUMENT STATUS" badge changes from "Verified Digital Copy" (green check) to "Revoked" (red shield icon). The certificate itself is never hidden or deleted from view — it's a real historical record — only its current trust status is made honest.
- **Donor's certificate list (`/donor/milestones`)**: each certificate card now shows a "Valid" or "Revoked" pill next to its serial, and the revocation reason as a line of red text when revoked.
- **Admin certificate registry (`/admin/analytics`)**: the existing table gained a Status column (same Valid/Revoked pill) and an Action column with a "Revoke" button (hidden once a row is already revoked — the backend's own idempotent-revoke semantics are real, but the UI intentionally treats revocation as a one-way action for clarity, matching the existing suspend/reactivate UX pattern elsewhere in this same file's sibling screen, `UserManagement.tsx`). The admin's own "verify a certificate by code" widget (which calls the same public verify endpoint) received the identical valid/revoked branching as the public page, so the same fact never displays two different ways depending on which screen an admin happens to use.

Only `revoked_at` and `revocation_reason` are used anywhere in this phase's code — no `revoked_by`, `revocation_date`, or `revoked_status` field was invented; `grep`-verified against every file changed this phase.

## 5. Admin workflow changes

A new `RevokeCertificateModal` (defined locally in `AdminAnalytics.tsx`, since the certificate registry — confirmed in Section 1 — has exactly one caller) is opened by the registry's "Revoke" button. It:

- Explains the consequence in plain language before any action is taken ("Anyone who checks it afterwards — including the public verification page — will immediately see it as revoked... can't be undone from here").
- Requires a real reason: a `<textarea>` bound to local state, with the confirm button disabled until `reason.trim().length >= 3` — the exact same validation the existing donor-suspend flow (`UserManagement.tsx`) already uses, reused rather than reinvented, and matching the backend's own `CertificateRevokeIn.reason` `min_length=3`.
- Submits to the real, pre-existing `POST /admin/certificates/{id}/revoke` endpoint via the new `revokeAdminCertificate` function — no new backend route was created.
- Shows a loading spinner on the confirm button while the mutation is in flight, and disables both buttons during that time (the existing double-submission-avoidance pattern used by every other mutation button in this codebase — a disabled button during `isPending`, not a custom debounce).
- On success, invalidates the registry's query (a real refetch) rather than writing an optimistic "revoked" state into the table — the row only shows "Revoked" once the backend has actually confirmed it.
- On error, surfaces `toApiError(...).detail` inside the dialog (covers 403/404/409/network-failure using the app's existing, unmodified error-normalization logic — no new per-status handling was written, since the existing fallback-message table in `lib/api.ts` already covers every relevant status).

## 6. API / type changes

`frontend/src/lib/domain.ts`:

- `Emergency.deadline_exceeded_at: string | null` (new field; inherited automatically by `EmergencyDetail` and `EmergencyOpen`, both of which `extends Emergency`).
- `Certificate.revoked_at: string | null`, `Certificate.revocation_reason: string | null` (new fields).
- `CertificateVerification.revoked_at: string | null`, `CertificateVerification.revocation_reason: string | null` (new fields — this type backs the public `GET /certificates/verify/{code}` response).
- `AdminCertificate.revoked_at: string | null`, `AdminCertificate.revocation_reason: string | null` (new fields — this type backs `GET /admin/certificates`).
- `revokeAdminCertificate(id: string, reason: string)` — new function, `POST /admin/certificates/{id}/revoke`, the one and only new network call this phase introduces (against an endpoint that already existed).

Every new field's name and nullability was copied directly from the corresponding Pydantic schema (`backend/app/schemas/certificate.py`, `backend/app/schemas/admin.py`, `backend/app/schemas/emergency.py`), not guessed. No `as any`, no type-widening, no relaxed strict-mode setting anywhere in the diff — confirmed by a clean `tsc --noEmit` (Section 11).

## 7. Tests added

No component-test framework (vitest, Jest, React Testing Library, or similar) exists anywhere in this codebase — confirmed by inspecting `frontend/package.json` and grepping the whole repository for `vitest`/`@testing-library` before writing any test. Introducing one for a bounded, frontend-only follow-up phase would be an infrastructure expansion beyond this phase's scope, so this phase's frontend coverage is expressed entirely as Playwright E2E, which is the app's real, already-installed frontend test runner — see Section 16 for this decision recorded as an explicit, disclosed choice rather than a silently-skipped requirement.

New file `frontend/e2e/phase31_1_domain_signals.spec.ts`, six tests, all driving the real UI against the real backend with state created through real API calls (never a mocked/faked network response):

1. **Certificate revocation, end to end**: a donor logs a real donation and issues a real certificate (real API); the public verify page confirms it as authentic; an admin revokes it through the real registry UI, including a too-short-reason rejection (button stays disabled) and a real minimum-length reason (button enables, submits, shows the real post-backend-confirmation "Revoked" state, never an optimistic one); the public verify page is then re-checked and shows "Certificate revoked" with the real reason, never "Authentic certificate" and never the generic not-found message.
2. **Emergency deadline banner, status independence**: a patient broadcasts a real SOS with a `deadline` already in the past (real API); the patient opens the real tracking drawer; the drawer shows "Past deadline — action needed" (the real backend-computed flag) *and* the real "ACTIVE" status badge, proving the two facts render independently rather than one replacing the other.
3–5. **Accessibility** (axe-core, same WCAG 2.1 A/AA tag set and same three disclosed, pre-existing rule exclusions — `aria-command-name`, `color-contrast`, `link-in-text-block` — as the existing `phase26_accessibility.spec.ts`): the public verify page showing a valid certificate; the donor milestones page with a real revoked certificate actually present (set up via a real donor-donation-certificate-then-admin-revoke API sequence, not a static fixture); the admin certificate registry with the revoke dialog open. Zero new violations in any of the three.
6. **Responsive**: the admin certificate registry (which gained two new table columns this phase) checked at all seven required breakpoints (320/375/390/414/768/1024/1440px) for page-level horizontal overflow. None found at any width.

All state (`donations`, `certificates`, `emergencies`, the revoke action itself) is created through real, unmocked API calls or real UI interaction — no test fakes a backend response to manufacture a UI state, per the master prompt's explicit instruction.

## 8. Playwright results

New spec (`phase31_1_domain_signals.spec.ts`): **6/6 passed**, run three separate times during this phase (once in isolation immediately after writing it, once as part of two full-suite runs) with identical results each time.

Full suite (71 specs total including the six new ones): **66 passed, 1 failed, 1 skipped** on both full runs. The one failure (`blood_request_idempotency.spec.ts`'s rapid-double-click race test) is:
- In a file this phase never touched (blood-request idempotency has nothing to do with certificates or emergencies).
- Confirmed pre-existing and unrelated: it passes cleanly every time it's run in isolation or in a smaller batch (verified twice, including once immediately after this same full-suite run reported it failing) — a CPU-timing-sensitive double-click race under this sandbox's constrained 2-core environment, the same class of flakiness this codebase's own comments already document for other double-click races (e.g. the Phase 24 Playwright race noted in `App.tsx`'s eager-dashboard-loading comment).

The 1 skip (`phase21_error_loading_empty_offline.spec.ts`'s test C) is pre-existing and unrelated to this phase.

A second full-suite run additionally reported three more failures (`hospital_workflow.spec.ts`'s IDOR test, and two of `phase23_security_hardening.spec.ts`'s tests) — investigated and confirmed to be Redis-backed rate-limit accumulation from this phase's own repeated verification runs against the same local dev database earlier the same session (`redis-cli keys "*rl:*"` showed thousands of accumulated rate-limit keys from repeated logins/registrations across several full-suite and full-pytest runs). After `redis-cli flushall` and re-seeding, all three passed cleanly, confirming they were an artifact of this verification session's own test cadence, not a code regression. This is disclosed here rather than silently omitted.

## 9. Accessibility results

Axe-core (WCAG 2.1 A/AA), same rule-exclusion set as the codebase's existing accessibility regression test: **zero new violations** on the public verify page (both valid and revoked states — the revoked state's `role="alert"` block was scanned explicitly), the donor milestones page with a real revoked certificate rendered, and the admin certificate registry with the revoke dialog open. See Section 7, tests 3–5.

Manual review of the added markup: every new status indicator pairs an icon/text label with a color (never color-only meaning — e.g. "Valid"/"Revoked" as text, not just a colored dot); the revoke dialog's textarea has a proper `<label htmlFor>`; the deadline-exceeded notice and revoked banners both carry `role="alert"`; the existing `Modal` component's focus trap, Escape-to-close, and focus-return behavior (already covered by `phase22_responsive_mobile_nav.spec.ts`'s dialog-semantics test) is inherited unmodified by the new revoke dialog since it's built from the same shared `Modal`.

## 10. Responsive results

Verified at all seven required breakpoints (320/375/390/414/768/1024/1440px) via the new automated test (Section 7, test 6): the admin certificate registry's two new columns cause no page-level horizontal overflow at any width. The certificate cards on `/donor/milestones` and the banners on `/verify/:code` and `/certificate/:id` use the same flex/wrap patterns already present on those pages (no new fixed-width elements were introduced), so no separate breakpoint sweep was needed for those three pages beyond the existing coverage in `phase26_responsive_overflow.spec.ts`.

## 11. TypeScript result

`npm run typecheck` (`tsc --noEmit`): **clean, zero errors**, both immediately after the domain.ts type additions and again after every subsequent file change. No `as any`, no `@ts-ignore`, no relaxed compiler option anywhere in this phase's diff.

## 12. ESLint result

`npm run lint` (`eslint .`): **clean, zero warnings or errors**, run after every batch of changes including the final state with the new E2E spec included.

## 13. Build result

`npm run build` (`tsc -b && vite build`): **succeeds**, all 2171 modules transformed, all existing chunks (including the newly-larger `AdminAnalytics` and unchanged `VerifyCertificate`/`DonationCertificate`/`MilestonesHistory` chunks) built without error. The two build warnings present (a dynamic/static import overlap on `AccessDenied.tsx`, and the overall bundle-size advisory on the main `index` chunk) are both pre-existing, reproduced from the same `main` branch before this phase's changes, and unrelated to any file this phase touched.

## 14. Full regression result

- **Backend pytest** (`ENVIRONMENT=test python -m pytest`, real local PostgreSQL `lifelink_test` + Redis): **853 collected, 852 passed, 1 skipped, 0 failed, 0 errors** — run twice for confirmation, identical dot-for-dot result both times (character-counted from the `-q` reporter output: 852 `.` + 1 `s`, zero `F`/`E`). This is the exact same count as Phase 31's own closing regression, which is expected and correct: this phase changed zero backend files, so the backend suite is unchanged by construction, not merely "still passing by luck."
- **Frontend TypeScript**: clean (Section 11).
- **Frontend ESLint**: clean (Section 12).
- **Frontend production build**: succeeds (Section 13).
- **Full Playwright suite**: 66/71 passed cleanly with the disclosed, pre-existing, environment-specific exceptions in Section 8 (one CPU-timing race unrelated to this phase's files, three rate-limit-accumulation artifacts from this session's own repeated runs — all confirmed to pass once isolated/rate-limits cleared).
- **Accessibility**: zero new violations (Section 9).
- **Responsive**: zero new overflow at any required breakpoint (Section 10).

## 15. Any regressions

None found. Every pre-existing test that failed at some point during this phase's verification was independently reproduced as failing for a reason unconnected to this phase's file changes (a documented CPU-timing race, or this session's own Redis rate-limit accumulation from repeated full-suite runs), and confirmed to pass once that unrelated condition was removed (running the test in isolation, or clearing accumulated rate-limit state). No test that exercises a file this phase modified showed any failure at any point.

## 16. Remaining Phase 31 limitations

Unchanged and inherited from Phase 31 (this phase did not reopen or re-litigate the backend domain audit, as instructed): the eight documented limitations in `docs/LIFELINK_PHASE_31_DOMAIN_REPORT.md` Section 12 (no per-unit blood lifecycle, no blood-component model, inventory-allocate's non-redundant compatibility check, no `PARTIALLY_FULFILLED` status, donor-self-attested donation logging, `TransportRoute`'s binary dispatch lifecycle, no audit-log hash chain, no verification-document versioning) all still stand exactly as written there — none of them bear on this phase's frontend-only scope, and none were touched.

New, disclosed limitation from this phase specifically: **no component-level (vitest/RTL-style) frontend test coverage exists for the new UI**, because no such framework exists anywhere in this codebase (Section 7). The six new Playwright E2E tests provide real, full-stack coverage of every required scenario (deadline null/populated/status-independent, certificate valid/revoked/unknown, admin-revoke success/validation/loading/error, accessibility, responsiveness) but do not substitute for fast, isolated unit tests of the individual React components in CI. This is a testing-infrastructure gap in the codebase as a whole, not something specific to the fields this phase added, and is recommended as a real Phase 32 candidate below rather than silently worked around.

## 17. Recommendation for Phase 32

1. **Introduce a component-test framework** (Vitest + React Testing Library is the natural fit for this Vite/React/TypeScript stack) so future UI-only phases like this one can get fast, isolated unit coverage in addition to full-stack Playwright E2E — this phase's own testing gap (Section 16) is the concrete motivating example.
2. Phase 31's own Section 13 recommendation (a real backend threat-model review of the honor-system donation-logging gap, now that revocation exists as the corrective control) remains open and unrelated to frontend work — still a reasonable Phase 32+ candidate if the product decides donation-logging fraud is a real operational concern.
3. No other new gap was discovered this phase that would justify reopening Phase 31's backend domain audit — the two backend signals this phase set out to surface are now genuinely visible end-to-end, and no regression, IDOR, or authorization weakening was introduced anywhere in the diff.
