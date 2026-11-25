# LIFELINK — PHASE 32: FRONTEND COMPONENT TEST INFRASTRUCTURE + UI CONTRACT HARDENING

**Verdict: GO**

This phase added an isolated component/unit-test layer (Vitest + React Testing Library) to the LifeLink frontend, alongside — never in place of — the existing real-browser/real-API Playwright suite. Zero backend files, zero application source files, and zero existing Playwright specs were changed. The only changes are: two new devDependency-only config/setup files, seven new test files, three `package.json` scripts, and one `.gitignore` line.

---

## 1. Objective

Give this frontend a fast, isolated component-test layer that can run without Postgres, Redis, Neo4j, SMTP, or a running backend at all — something this codebase has never had — while keeping the existing Playwright E2E suite exactly as authoritative as it already was for real-browser, real-API, real-database behavior. The two layers are permanent and complementary, not a migration from one to the other; see section 11.

## 2. Initial testing-state audit

Before writing anything, the full existing testing architecture was inspected:

- `frontend/package.json` — scripts (`dev`, `build`, `preview`, `lint`, `typecheck`), dependencies and devDependencies. No test runner of any kind was present.
- `frontend/package-lock.json` — `lockfileVersion: 3`, clean, no partial/broken state.
- `frontend/vite.config.ts` — a function-form `defineConfig(({mode}) => ...)` that calls `loadEnv` and feeds the real `VITE_API_URL` into a custom `cspPlugin`; `@` alias to `./src`; dev server on port 5173.
- `frontend/tsconfig.json` — single config (no `tsconfig.app.json`/project references), ES2022, full strict flags (`strict`, `noUnusedLocals`, `noUnusedParameters`, `noFallthroughCasesInSwitch`), `@/*` → `src/*`, `"include": ["src"]` only, `"noEmit": true` (the production `build` script's `tsc -b` step is a type-check gate; Vite does the actual bundling).
- `frontend/eslint.config.js` — ESLint 9 flat config with an existing override block for `*.config.{js,ts}` files (Node globals, `no-require-imports` off) that a new `vitest.config.ts` automatically inherits.
- `frontend/.gitignore` — had no `coverage/` entry.
- `App.tsx`, `lib/api.ts`, `lib/domain.ts`, `lib/types.ts`, `lib/auth.tsx` — read in full (several already read during Phase 31.1 and reconfirmed unchanged).
- `EmergencyTrackDrawer.tsx`, `VerifyCertificate.tsx`, `DonationCertificate.tsx`, `MilestonesHistory.tsx`, `AdminAnalytics.tsx`, `Modal.tsx`, `ConfirmationDialog.tsx`, `OfflineBanner.tsx` — read in full, current versions (post-Phase-31.1).
- `NotificationsBell.tsx`, `MobileNavDrawer.tsx`, `DonorSectionHeader.tsx`, `LogDonationModal.tsx` — read because they are rendered, unconditionally or on-mount, by one or more of the five mandatory components and therefore affect what a test of those components must account for.
- `playwright.config.ts`, `e2e/helpers.ts`, `e2e/setup.ts`, `e2e/phase26_accessibility.spec.ts`, `e2e/phase26_responsive_overflow.spec.ts`, `e2e/phase31_1_domain_signals.spec.ts` — read in full.
- `docs/LIFELINK_PHASE_31_DOMAIN_REPORT.md` and `docs/LIFELINK_PHASE_31_1_REPORT.md` — read (authored earlier this engagement).
- Repo-wide greps for `vitest`, `jest`, `@testing-library`, `describe(`, `test(`, `expect(`, `vi.mock`, `jest.mock` inside `src/` and config files: **zero real hits** (the only match was a descriptive comment in this session's own Phase 31.1 e2e spec, confirmed by inspection to be prose, not code).
- `find docs -iname "*test*"`: no existing frontend-testing documentation.

**Conclusion of the audit: a genuinely clean slate.** No component-test infrastructure, convention, or partial attempt existed anywhere in this codebase.

## 3. Test framework decision

**Vitest 5 + React Testing Library 16 + `@testing-library/jest-dom` 7 + jsdom 30**, explicitly not Jest, Cypress, or Enzyme, per the master prompt.

Package versions installed (exact, from `npm view`/`package.json` after install):

| Package | Version |
|---|---|
| `vitest` | 5.0.1 |
| `@testing-library/react` | 16.3.3 |
| `@testing-library/jest-dom` | 7.0.1 |
| `jsdom` | 30.1.0 |
| `@vitest/coverage-v8` | 5.0.1 |
| `@types/node` | 22.20.4 |

`@types/node` was added as a new devDependency: Vitest 5's own peer-dependency range (`^22.0.0 || >=24.0.0`) requires it, and this repo previously had none (Node types weren't needed anywhere else in this Vite/React app). Pinned to `^22.20.4` to match the sandbox's installed Node (`v22.22.2`).

No `@testing-library/user-event` was added. Every interaction in the new tests (`fireEvent.click`, `fireEvent.change`, `fireEvent.keyDown`) is expressible with React Testing Library's built-in `fireEvent`, which is already a transitive dependency of `@testing-library/react`; adding a second interaction-simulation library would have gone beyond "the minimum … required."

An **out-of-scope, pre-existing** finding surfaced by `npm install`: `npm audit` reports 2 moderate `react-router`/`react-router-dom` advisories (open redirect, arbitrary constructor injection in SSR hydration) fixed only by a major-version bump of the production `react-router-dom` dependency. That is a production-dependency change, not a test-infrastructure one, and is out of this bounded phase's scope — flagged here rather than silently left out of the report.

## 4. Configuration added

**`frontend/vitest.config.ts`** — a **separate** file from `vite.config.ts`, not merged into it. Rationale (documented in the file itself): `vite.config.ts` calls `loadEnv` and feeds `VITE_API_URL` into a real CSP plugin — logic that matters for the served/built app and has no meaning for a jsdom component test — and keeping it in its own file means `npm run build`/`npm run dev` literally never read this file, so neither can regress because of anything added here. It shares only the `"@"` alias and the React plugin with `vite.config.ts`, deliberately duplicated rather than imported, to avoid any coupling to the CSP/env logic.

Config: `environment: "jsdom"`, `setupFiles: ["./src/test/setup.ts"]`, `css: false`, `include: ["src/**/*.test.{ts,tsx}"]`, `exclude: ["node_modules", "dist", "e2e"]` (so Vitest can never accidentally pick up Playwright's own `e2e/*.spec.ts` files), and a `coverage` block (`provider: "v8"`, diagnostic only — see section 12).

**`frontend/src/test/setup.ts`** — imports `@testing-library/jest-dom/vitest` (registers jest-dom matchers against Vitest's `expect`, including the TypeScript module augmentation — no separate type-shim package needed) and calls `cleanup()` in an explicit `afterEach`.

**`frontend/src/test/test-utils.tsx`** — `renderWithProviders()`, a thin wrapper that mounts a component inside the same `QueryClientProvider` + `MemoryRouter` every real page runs inside, with `retry: false` / `gcTime: 0` on the test `QueryClient` so a mocked rejection surfaces immediately instead of after react-query's real retry/backoff delay. Re-exports everything from `@testing-library/react`.

**`frontend/src/test/apiErrors.ts`** — `makeAxiosError(status, detail, opts)` and four named builders (`notFoundError`, `serverError`, `networkError`, `forbiddenError`) that produce Axios-error-shaped rejections. See section 8 for why this exists and how it's used.

**`frontend/src/test/fixtures.ts`** — factory functions (`makeEmergencyDetail`, `makeCertificate`, `makeCertificateVerification`, `makeAdminCertificate`) that return a **complete** object of the real `lib/domain.ts` type (every field, not a `Partial<T>`), so a test can never accidentally compile by omitting a field a component actually reads.

**`frontend/package.json`** — added `"test": "vitest run"`, `"test:watch": "vitest"`, `"test:coverage": "vitest run --coverage"`. All three run from the `frontend/` directory exactly like `lint`/`typecheck`/`build` already do, and none require the backend, Postgres, Redis, Neo4j, or SMTP.

**`frontend/.gitignore`** — added `coverage/`.

`npm run build` was re-verified to produce the exact same `dist/` output shape after every one of these additions (see section 14) — the only pre-existing build warning (a large `index-*.js` chunk) is unrelated and unchanged.

## 5. Test architecture

**Convention chosen: co-located `Component.test.tsx` files**, sitting directly beside the component/page they test (e.g. `src/components/Modal.tsx` + `src/components/Modal.test.tsx`; `src/pages/admin/AdminAnalytics.tsx` + `src/pages/admin/AdminAnalytics.test.tsx`). This was applied consistently to all seven new test files — no `__tests__/` directories were introduced anywhere. Co-location was chosen over `__tests__/` because it keeps a test next to the exact file it exercises with no import-path indirection, matching how this codebase already keeps a page and its immediate concerns (e.g. `RevokeCertificateModal`) in one file rather than splitting by kind.

**Mocking boundary, applied uniformly across every test file:**

- The only things ever mocked are (a) the network-calling functions exported from `lib/domain.ts` (thin wrappers that do nothing but call the shared `api` axios instance and unwrap `.data`), and, in the two tests that render a component using `useAuth()`, (b) `lib/auth.tsx`'s `useAuth` hook, whose only real logic is calling `/auth/me`, `/auth/login`, etc. Both are transport-boundary concerns, never the component's own rendering/derivation logic.
- Every `vi.mock("../lib/domain", ...)` (or the appropriate relative path) uses `importOriginal()` and spreads the real module first, overriding only the specific functions that test needs — so `BLOOD_LABEL`, type exports, and every function not exercised by a given test stay completely real.
- `toApiError` (`lib/api.ts`) is **never** mocked anywhere. It is imported directly, real, in every test that needs to assert an error message — see section 8.
- `Modal`, `ConfirmationDialog`, `NotificationsBell`, `MobileNavDrawer` are never mocked as components. Where a mandatory component renders one of these (e.g. `AdminAnalytics` renders `NotificationsBell`, which calls `unreadCount()`/`listNotifications()`), the *transport calls that component itself makes* are covered by the same domain-module mock — never a mock of the component.

## 6. Components covered

| Component | File | Tests |
|---|---|---|
| EmergencyTrackDrawer (mandatory A) | `src/components/EmergencyTrackDrawer.test.tsx` | 10 |
| VerifyCertificate (mandatory B) | `src/pages/VerifyCertificate.test.tsx` | 8 |
| DonationCertificate (mandatory C) | `src/pages/DonationCertificate.test.tsx` | 6 |
| MilestonesHistory (mandatory D) | `src/pages/donor/MilestonesHistory.test.tsx` | 7 |
| AdminAnalytics certificate registry (mandatory E) | `src/pages/admin/AdminAnalytics.test.tsx` | 12 |
| Modal (shared primitive) | `src/components/Modal.test.tsx` | 5 |
| ConfirmationDialog (shared primitive) | `src/components/ConfirmationDialog.test.tsx` | 4 |
| **Total** | **7 files** | **52 tests** |

`AdminAnalytics.tsx` is the file that contains `RevokeCertificateModal` — this codebase has no separate `AdminCertificates.tsx`, so `AdminAnalytics.test.tsx` necessarily exercises the whole page (sidebar, header, `NotificationsBell`, the analytics breakdown section, the registry table, and the revoke dialog) rather than an isolated sub-component; nothing was extracted or exported solely to make it easier to test.

## 7. Domain contracts covered

**A. EmergencyTrackDrawer** — all 10 checklist items: loading state; error state (via real `toApiError`); status badge stays visible; `deadline_exceeded_at = null` → no alert; populated → "Past deadline — action needed" shown; alert never replaces status (both visible together); never derived from `slack_seconds` (tested with `slack_seconds: -600` and `deadline_exceeded_at: null` together — no alert, but the existing "10m overdue" slack text still renders, proving these are two independent facts); never invented when data is missing (`slack_seconds: null`); `role="alert"` semantics; existing Cancel-button mutation still fires with the real emergency id.

**B. VerifyCertificate** — all 10: valid → authentic; revoked → revoked; reason shown when present; `revoked_at` date shown via the component's own `toLocaleDateString()` call (test computes the same string from the same `Date`, never a hardcoded format string); 404 → genuine not-found copy; server error → honest "couldn't check" copy via real `toApiError`; revoked never renders as authentic; revoked never renders as the generic not-found copy; network failure renders as neither not-found nor revoked nor authentic; donor/serial factual details remain visible in the revoked state.

**C. DonationCertificate** — all 6: valid → existing "Verified Digital Copy" state; revoked → `role="alert"` banner + "Revoked" status badge; reason shown when present; certificate remains viewable (donor name, serial, hospital name all still rendered) when revoked; the Revoked/Valid distinction is asserted via real accessible text (`getByText("Revoked")`), not merely a CSS class; existing certificate body content survives the trust-status change.

**D. MilestonesHistory** — all 7: "Valid" shown; "Revoked" shown; reason shown when available; a valid certificate never shows a revocation-reason line; empty-state copy intact; loading state intact (a never-resolving mock, checked before data arrives); fetch-error state intact via real `toApiError`.

**E. Admin certificate registry** — all 13: Valid row renders Valid; Revoked row renders Revoked; Revoke action present for a non-revoked cert; Revoke action absent once revoked; the revoke dialog opens; a `<3`-char reason keeps Confirm disabled; a valid reason enables it; the confirm button disables itself while the mutation is pending (tested with a never-resolving mock — no way to double-submit); a real 403 from the mutation surfaces through the dialog's existing error paragraph (via real `toApiError`) rather than a fabricated success, and the dialog stays open with the row still showing "Valid" (no optimistic flip); the row only flips to "Revoked" after the mutation resolves **and** the certificate list has genuinely re-fetched (`listAdminCertificates` mocked with two different sequential resolutions, asserted called exactly twice); the reason `<textarea>` has a real `<label htmlFor>` association (`getByLabelText`); the dialog shares `Modal`'s real `role="dialog"`/`aria-modal`/close-button semantics.

The literal required sentence, stated once here as its own line for clarity and reused verbatim in this report's other relevant sections: **UI tests verify presentation of API outcomes; backend tests verify authorization.**

## 8. API mocking boundary

Every mocked rejection is built with `src/test/apiErrors.ts`'s `makeAxiosError()`, which produces an object shaped exactly the way a real Axios error is shaped (`isAxiosError: true`, `response.{status,data,headers}`, `config.headers.get(...)`, `code` for network/timeout cases) — never a bare `new Error("...")` or a hand-shaped `{status, message}` object. Every test that asserts an error message imports the real, un-mocked `toApiError` from `lib/api.ts` and calls it with the *same* error object handed to the mock, then asserts the component rendered `toApiError(error).detail`. This means:

- The test and the component are both exercising the one real `STATUS_FALLBACK_MESSAGE` mapping table in `lib/api.ts` — there is no second, hand-written copy of "what a 500 should say" anywhere in the test suite.
- A change to that mapping table's wording is automatically reflected in what the tests expect, with zero test edits — proof that nothing was duplicated.

`apiErrors.ts`'s `makeAxiosError` return value is built with `as unknown as AxiosError` (not `as any`, not `@ts-ignore`, not a non-null assertion) — a deliberate, documented cast of a test double that intentionally implements only the subset of `AxiosError`'s shape `toApiError` actually reads, not the full real `AxiosError`/`InternalAxiosRequestConfig` interface. This is scoped to one third-party library type in test-only code; it does not touch, weaken, or cast around any of the actual domain nullability the master prompt calls out (see section 13).

## 9. Accessibility testing boundary

Component tests assert role, accessible name, label association, alert semantics, and disabled state exactly where the master prompt scopes them to: `getByRole("alert")` for the deadline/revocation notices, `getByRole("dialog", {name: ...})` + `aria-modal` for `Modal`/`RevokeCertificateModal`, `getByLabelText(/Reason for revocation/)` for the textarea, `toBeDisabled()`/`toBeEnabled()` for the confirm button's real-time state.

This is explicitly **not** a replacement for, or duplicate of, the existing Phase 26/27/31.1 axe-core Playwright coverage (`phase26_accessibility.spec.ts`, `phase27_accessibility.spec.ts`, and the three accessibility tests inside `phase31_1_domain_signals.spec.ts`). Those remain the authoritative full-WCAG-ruleset scans against real rendered pages in a real browser; nothing here runs axe-core, and no axe rule exclusion was touched.

## 10. Responsive testing boundary

Not a jsdom/component-test concern at all — no new test in this phase sets a viewport size, measures layout, or asserts anything about breakpoints. That coverage lives exclusively in `phase26_responsive_overflow.spec.ts` and the responsive `test.describe` block inside `phase31_1_domain_signals.spec.ts`, both re-verified passing in section 14. The separation is intentional: component tests own behavior, state, and accessibility *semantics*; Playwright owns viewport/layout/real-browser rendering, real API round-trips, and cross-role workflows.

## 11. Playwright coexistence

Zero Playwright files were changed, renamed, deleted, or weakened. `playwright.config.ts` is untouched. Every existing `e2e/*.spec.ts` file, including `phase31_1_domain_signals.spec.ts`, was re-run in full (section 14) after all component-test infrastructure was added, against a real backend/Postgres/Redis, with identical results to what Phase 31.1 already documented. `vitest.config.ts`'s `exclude: ["e2e"]` makes it structurally impossible for `npm run test` to ever pick up a Playwright spec, and Playwright's own `testDir: "./e2e"` makes the reverse equally impossible.

## 12. Test counts

**52 tests across 7 files, 0 failed, 0 skipped.** Breakdown in section 6's table. This count is not a target that was optimized for — no snapshot tests exist anywhere in this suite, and several components (`Modal`, `ConfirmationDialog`) have fewer tests than the mandatory five specifically because they have a much smaller real behavioral surface, not because coverage was padded.

Coverage (`npm run test:coverage`, `@vitest/coverage-v8`, diagnostic only — **no arbitrary global threshold was set or enforced**, per the master prompt):

| File | Statements |
|---|---|
| `EmergencyTrackDrawer.tsx` | 64.81% |
| `VerifyCertificate.tsx` | 85.71% |
| `DonationCertificate.tsx` | 84.61% |
| `MilestonesHistory.tsx` | 48.64% (the milestone-badge/donation-history/log-donation-modal parts of this page are untested here — out of this phase's mandatory scope, which was the certificates section) |
| `AdminAnalytics.tsx` | 64.28% (the platform-analytics breakdown section and sidebar nav are largely untested here — out of this phase's mandatory scope, which was the certificate registry + revoke flow) |
| `Modal.tsx` | 57.14% |
| `ConfirmationDialog.tsx` | 100% |
| **Whole `src/` tree** | **8.94% statements** |

The whole-tree figure is expected and unremarkable: this phase covered exactly the five mandatory components plus two shared primitives, out of dozens of pages in this app — see section 16 for what remains untested and section 20 for what should be covered next, prioritized by risk.

## 13. Runtime

`npx vitest run`: **7 test files, 52 tests, ~10.4s wall-clock**, of which ~48% is jsdom environment creation (one jsdom instance per test file, by design, for isolation) and ~30% is actual test execution — the tests themselves run in a few seconds. Vitest's own performance hint suggests `pool: 'vmThreads'` or `isolate: false` to share the jsdom environment across files; this was deliberately **not** applied, because `isolate: false` would share the module registry across test files in the same worker, and several test files declare conflicting `vi.mock()` overrides of the same modules (`lib/domain.ts`, `lib/auth.tsx`) — sharing module state between them risks one file's mock leaking into another's assertions. At 7 files and ~10s total, this suite is already comfortably CI-suitable; the isolation trade-off is not worth taking at this size, and is noted here as a real, evaluated option rather than left unconsidered.

No test makes a real network call, touches a timer beyond what react-query's own default scheduling needs (mitigated with `retry: false`/`gcTime: 0` in the test `QueryClient`), or depends on module-level global setup beyond the one `setup.ts` file.

## 14. Full regression results

All six items run after the component-test layer was added, against this session's actual dev environment (Postgres 16, Redis, and a real `uvicorn` backend — `S3_ENDPOINT_URL`/`S3_ACCESS_KEY`/`S3_SECRET_KEY` overridden empty at process start so certificate storage falls back to local disk, exactly as Phase 31.1 documented and re-used unchanged here; `vite preview` serving a real production build on :4173):

1. **Component tests** — `npm run test`: **52 passed, 0 failed, 0 skipped.**
2. **TypeScript** — `npx tsc -b --force` (the exact command `npm run build` runs, so this also proves the new `*.test.tsx` files under `src/` — which `tsconfig.json`'s `"include": ["src"]` does pick up — type-check cleanly under this repo's full strict-mode flag set, including `noUnusedLocals`/`noUnusedParameters`): **clean, 0 errors.**
3. **ESLint** — `npx eslint .`: **clean, 0 errors, 0 warnings.**
4. **Production build** — `npm run build`: **succeeds**, identical `dist/` output shape to before this phase (same pre-existing "large chunk" advisory on `index-*.js`, unrelated to this phase and unchanged by it). Test files are not present anywhere in `dist/` — Vite only bundles what's reachable from real app entry points.
5. **Existing Playwright suite** — `npx playwright test` (all specs, 71 tests): **70 passed, 1 failed** on the first full run (`blood_request_idempotency.spec.ts`'s double-click race — the exact same test, exact same failure mode, already disclosed as pre-existing/timing-flaky in `docs/LIFELINK_PHASE_31_1_REPORT.md`). Re-run in isolation (`npx playwright test e2e/blood_request_idempotency.spec.ts`): **2/2 passed**, in 4.6s versus the 18.7s-then-10000ms-timeout pattern seen inside the full serial run. See section 15 for the classification.
6. **Phase 31.1 spec specifically** — all 6 tests inside `phase31_1_domain_signals.spec.ts` (certificate revocation E2E, emergency deadline-banner E2E, 3 accessibility scans, 1 responsive check): **6/6 passed**, unchanged from Phase 31.1's own report.
7. **Backend pytest** — `python -m pytest -q` (full suite): **853 passed, 0 failed, 0 errors** (dot-stream counted programmatically as a cross-check, the same technique used in Phase 31.1, since this sandbox's pytest run again did not print its final one-line summary to the captured log despite completing cleanly — the process exited, `ps aux` shows nothing running, and the dot count matches the collected-test count with no `F`/`E` characters anywhere in the stream).

No backend file was touched by this phase, so pytest passing in full was expected, not a coincidence — it's included per the master prompt's explicit "full regression after adding Vitest" requirement, not because backend risk was introduced.

## 15. Failures and how classified

**One failure observed, in the full Playwright run only:** `blood_request_idempotency.spec.ts:16` ("rapid double-click on Submit creates exactly one request, not two"), failing with `page.waitForFunction: Timeout 10000ms exceeded` waiting for a new table row to appear.

Classification, following the master prompt's A/B/C/D/E protocol:

- **(A) Caused by this phase?** No. This phase changed zero backend files, zero e2e spec files, and zero application source files (`git status` after this phase shows only new test files, new test-only config, and `package.json`/`.gitignore`/`package-lock.json` — nothing under `src/` outside the new `*.test.tsx` files, `vitest.config.ts`, and `src/test/`). The failing test exercises a blood-request double-click UI flow that has no relationship to certificates or emergencies.
- **(B) Pre-existing?** Yes — `docs/LIFELINK_PHASE_31_1_REPORT.md` documents this exact test, this exact failure signature, and this exact non-reproducing-in-isolation behavior from that phase's own verification run.
- **(C) Environmental / (D) timing contamination?** Yes. Reproduction method: re-ran the single spec file in isolation immediately after the full-suite failure — both tests in that file passed in 9.1s total (4.6s + 3.2s), versus the 18.7s the first test took inside the 71-test serial run before timing out. This points to CPU contention from the full serial run (single Playwright worker, but sharing the sandbox's CPU with the concurrently-running 853-test pytest suite and the Vitest run that had just finished) slowing the double-click race's real network round-trip past the test's fixed 10-second `waitForFunction` timeout — not a functional break in the double-click idempotency guard itself (which the *second* test in the same file, exercising the same idempotency-key code path via a different trigger, passed in both runs).
- **(E) Unrelated infrastructure failure?** Not applicable — this is (C)/(D), not an unrelated infra outage.

No Redis rate-limit contamination was observed or needed clearing this run (`redis-cli keys "*rl:*"` was not required — the failure signature and isolated-rerun evidence pointed cleanly at CPU-timing contention from concurrent suites, not accumulated rate-limit state; the flushall+reseed procedure documented in Phase 31.1 remains the correct remedy if that specific signature — 401/429-adjacent auth-setup failures across multiple unrelated specs — recurs, but that was not what was observed here).

No test was skipped, deleted, weakened, or reclassified as "flaky" without this reproduction step.

## 16. Remaining frontend testing gaps

Explicitly untested by this phase (component-test layer): every page/component not listed in section 6 — the milestone-badge and donation-logging portions of `MilestonesHistory.tsx`, the platform-analytics breakdown and sidebar-nav portions of `AdminAnalytics.tsx`, `BloodRequestModal.tsx`, `SosModal.tsx`, `HospitalInventoryModal.tsx`, `LogDonationModal.tsx`, `NotificationsBell.tsx`'s own read/mark-read interactions beyond what `AdminAnalytics.test.tsx` exercises incidentally, every dashboard (`PatientDashboard`, `DonorDashboard`, `HospitalDashboard`, `FamilyDashboard`), the matching/graph-engine pages (`ResourcePathways`, `GraphDecisionEngine`, `MathematicalModel`, `DecisionTransparency`, `StrategyLab`), and all admin sub-pages other than the certificate registry (`UserManagement`, `AdminFacilities`, `AdminDonors`, `TransportRoutes`, `AdminAuditFeed`, `VerificationsQueue`). These remain covered only by the existing Playwright suite (real browser, real API) where such coverage exists, and by nothing at the component-test level yet.

Responsive/viewport behavior, real cross-role workflows, and full-WCAG-ruleset accessibility scanning remain exclusively Playwright's domain by design (sections 9–10) — this is a permanent boundary, not a gap to close later.

This frontend strengthening does **not** make any claim about the application as a whole being "production-ready" — that determination, if ever made, would need to weigh the full body of prior phase reports (security, performance, accessibility, observability), not this phase's test-infrastructure addition alone.

## 17. Files changed

```
 frontend/.gitignore                                    |  1 +
 frontend/package-lock.json                             | (npm-managed lockfile update)
 frontend/package.json                                  |  9 ++-
 frontend/vitest.config.ts                               | new file
 frontend/src/test/setup.ts                              | new file
 frontend/src/test/test-utils.tsx                        | new file
 frontend/src/test/apiErrors.ts                          | new file
 frontend/src/test/fixtures.ts                           | new file
 frontend/src/components/EmergencyTrackDrawer.test.tsx   | new file
 frontend/src/components/Modal.test.tsx                  | new file
 frontend/src/components/ConfirmationDialog.test.tsx     | new file
 frontend/src/pages/VerifyCertificate.test.tsx           | new file
 frontend/src/pages/DonationCertificate.test.tsx         | new file
 frontend/src/pages/donor/MilestonesHistory.test.tsx     | new file
 frontend/src/pages/admin/AdminAnalytics.test.tsx        | new file
```

**14 changed/new paths, 0 of them backend, 0 of them existing application `src/` files, 0 of them existing Playwright specs.** `package-lock.json` changed only because `npm install` recorded the five new devDependencies (`vitest`, `@testing-library/react`, `@testing-library/jest-dom`, `jsdom`, `@vitest/coverage-v8`) and the newly-added `@types/node`.

## 18. Final quality gate

Every item genuinely verified, not assumed:

| # | Item | Verified |
|---|---|---|
| 1 | Vitest installed/configured correctly | ✅ `vitest.config.ts`, ran successfully (section 14.1) |
| 2 | React Testing Library configured correctly | ✅ used in all 7 test files, `render`/`screen`/`fireEvent`/`waitFor`/`within` all working |
| 3 | jest-dom configured | ✅ `setup.ts` imports `@testing-library/jest-dom/vitest`; matchers (`toBeInTheDocument`, `toBeDisabled`, `toHaveAttribute`, `toHaveTextContent`, `toHaveFocus`, `toBeEmptyDOMElement`, `toBeInstanceOf`) used and passing throughout |
| 4 | Component tests pass | ✅ 52/52 (section 14.1) |
| 5 | Test command works cleanly | ✅ `npm run test` → `vitest run`, verified directly |
| 6 | No fake production functionality added | ✅ zero application `src/` files outside `src/test/` and `*.test.tsx` changed |
| 7 | No backend files changed unless explicitly justified | ✅ zero backend files changed; none were needed |
| 8 | Phase 31.1 Playwright tests still pass | ✅ 6/6 (section 14.6) |
| 9 | Existing Playwright suite does not regress | ✅ 70/71 first run, 71/71 confirmed via isolated re-run of the one pre-existing flaky test (section 14.5, 15) |
| 10 | TypeScript clean | ✅ `tsc -b --force`, 0 errors (section 14.2) |
| 11 | ESLint clean | ✅ 0 errors/warnings (section 14.3) |
| 12 | Production build succeeds | ✅ (section 14.4) |
| 13 | Backend pytest passes | ✅ 853/853 (section 14.7) |
| 14 | No test relies on real external services unnecessarily | ✅ every component test mocks the transport boundary; `npm run test` was also confirmed to need no backend/DB/Redis running |
| 15 | No secrets introduced | ✅ no credentials, tokens, or `.env` values in any new file |
| 16 | No authorization weakened | ✅ nothing in `app/`, route guards, or `RequireAuth.tsx` touched; the AdminAnalytics 403 test asserts the UI *honestly surfaces* a denial, never that it bypasses one |
| 17 | Domain state distinctions remain honest | ✅ `Emergency.deadline_exceeded_at`/`Certificate.revoked_at`/`revocation_reason` stay `string \| null` everywhere; no `as any`, `@ts-ignore`, or non-null assertion touches any domain field (the one `as unknown as AxiosError` cast in test-only code is scoped to a third-party HTTP-library type, not a domain field — see section 8) |

## 19. GO / GO-WITH-LIMITATIONS / NO-GO

**GO.**

All 17 quality-gate items are genuinely satisfied. The component-test layer is real, isolated, fast, and covers every mandatory domain contract (A–E) plus two shared primitives, without touching, weakening, or duplicating the existing Playwright/backend coverage. The one observed Playwright failure was investigated, reproduced in isolation as pre-existing timing flakiness (not a regression), and documented transparently rather than hidden or silently reclassified. No backend change was needed or made. This strengthens the frontend's testing posture; it does not by itself change this application's overall production-readiness determination (section 16).

---

### Section 20 — Next candidates (not implemented this phase)

Prioritized by real risk (mutation-bearing / security-sensitive / shared-everywhere / error-heavy), for a future phase to pick up:

1. **`LogDonationModal.tsx` / `BloodRequestModal.tsx` / `SosModal.tsx`** — the three remaining idempotency-key-per-attempt mutation forms in this app (the same double-click-safety contract Playwright already regression-tests end-to-end at `blood_request_idempotency.spec.ts` and `phase26_donation_idempotency.spec.ts`); a component-level test would isolate the 409-retry/re-mint-key branch far faster than a real double-click-timing E2E test can, and is the most direct analogue to the revoke-flow tests just added.
2. **`NotificationsBell.tsx`** — shared across nearly every authenticated page (already indirectly exercised, but not directly tested); its own honest-failure contract (Phase 18's "a failed unread-count fetch must never render as a confident 0" — the `!` fallback badge) is a real domain-honesty rule with no direct component test yet.
3. **`UserManagement.tsx`'s suspend/reactivate flow** — the original source of the `reason.trim().length < 3` validation pattern this phase's admin-revoke tests reused; testing it directly would confirm the pattern stays correct at its origin, not just at its most recent reuse.
4. **`RequireAuth.tsx`** — a small, security-adjacent component (redirect-on-unauthenticated / Access-Denied-on-role-mismatch); low line count but high consequence if its logic silently regressed.
5. **`HospitalInventoryModal.tsx`** — the remaining real-mutation modal with numeric-input validation (units) not yet covered by any component test.

No implementation of this backlog was started — flagged here only, per the master prompt's explicit "identify, don't build" instruction for this section.
