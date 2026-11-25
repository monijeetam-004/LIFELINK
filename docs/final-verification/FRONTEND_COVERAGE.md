# Frontend coverage

## Static checks (final code)

| Check | Result |
|---|---|
| `npx tsc --noEmit` | 0 errors |
| `npx eslint .` | 0 problems |
| `npm run build` | succeeds (also from a fresh `npm ci` in the clean start) |
| `npm audit --omit=dev` | 0 vulnerabilities |

## Unit and component tests (Vitest + Testing Library, jsdom)

* **170 / 170 passed** in 22 files (`npx vitest run --coverage`).
* The only boundaries mocked are the network-calling functions (`lib/api`, `lib/gapApi`) and the signed-in user (`lib/auth`). Rendering, role gating and wording run for real.
* v8 coverage covers the 117 source files that tests load, out of 119 non-test source files:

  | Measure | Coverage |
  |---|---|
  | Statements | 59.08% (1054/1784) |
  | Branches | 56.52% (966/1709) |
  | Functions | 34.68% (283/816) |
  | Lines | 64.18% (1000/1558) |

* Coverage of the files added or rewritten in this round:

  | File | Lines | Branches | Covered by |
  |---|---|---|---|
  | components/live/PlanView.tsx | 92.9% | 73.3% | `Live.test.tsx` (engine label, estimate label, rejected facilities with reasons, feasibility text) |
  | components/live/LivePageShell.tsx | 100% | 100% | `Live.test.tsx` (sign-in gate, role gate, synthetic-data note) |
  | components/live/ScenarioForm.tsx, EmergencyPicker.tsx | 100% | 100% | rendered through the live pages |
  | pages/Analytics.tsx | 81.8% | 62.9% | `Live.test.tsx` (says when the model does NOT beat the baseline; synthetic label) |
  | pages/hospital/UnitLifecycle.tsx | 53.6% | 60.0% | `Live.test.tsx` (only the actions each state allows); the action handlers are exercised by Playwright |
  | pages/hospital/Shipments.tsx | 19.4% | 7.5% | mostly Playwright (`gap-shipments-*`) and the backend API tests; **low unit coverage**: cold-chain and receipt panels have no Vitest test |
  | pages/admin/AdminFacilities.tsx | 63.3% | 72.2% | `PhaseG.test.tsx`; revoke control in Playwright |
  | lib/gapApi.ts | 51.2% | 0% | thin request wrappers; exercised for real by Playwright against the API |

## Browser end-to-end (Playwright, Chromium, real backend and real PostgreSQL)

* Full suite: **106 passed** of 107. The 1 failure was a counting race in a test; it was fixed and the spec re-run, passing 3 / 3. The 1 remaining test runs only in the clean start, where it passed. Details are in FINAL_TEST_REPORT.md §4.
* `e2e/gap_live_pages.spec.ts` (9 tests) covers this round's screens:
  * the live planner;
  * before/after route-failure simulation;
  * strategy comparison;
  * model parameters;
  * decision transparency with a real emergency;
  * distance-limited donor recommendation;
  * labelled forecast;
  * unit lifecycle, resource pathways and shipments;
  * admin revoke control;
  * the map tile fallback.

  Each screen is checked at 375 / 768 / 1280 px for axe violations and horizontal overflow (ACCESSIBILITY_AUDIT.md).
* `e2e/clean_start.spec.ts` covers the freshly built SPA against the freshly initialised API (CLEAN_START_VERIFICATION.md).

## Known gaps

* `Shipments.tsx` (cold-chain and receipt panels) and `gapApi.ts` have low *unit* coverage. They are covered by browser and API tests instead.
* Visual regression (pixel diffs) is not automated. Screenshots are kept as evidence only.
