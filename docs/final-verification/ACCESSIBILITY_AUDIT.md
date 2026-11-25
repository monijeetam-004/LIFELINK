# Accessibility audit — gap-elimination round

## Method

* **Automated:** axe-core (the copy bundled in `frontend/node_modules/axe-core`) was injected into the real built SPA and run against the real backend with the synthetic seed. Tag set: `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`.
  * Every new or rewritten screen was scanned at **375, 768 and 1280 px** wide.
  * At each width the page's horizontal overflow was also measured (`scrollWidth − innerWidth ≤ 1 px`).
* **Spec:** `frontend/e2e/gap_live_pages.spec.ts`. The test fails on any violation; it does not merely log it. Raw per-page results: `screenshots/axe-gap-pages.json`. Screenshots: `screenshots/gap-*-{375,768,1280}.png`.
* **Regression:** the earlier accessibility suites, `e2e/phase26_accessibility.spec.ts` and `e2e/phase27_accessibility.spec.ts`, cover the pre-existing routes. They ran in the full Playwright regression (FINAL_TEST_REPORT.md).

## Screens scanned

| Route | Role | Widths | axe violations | Horizontal overflow |
|---|---|---|---|---|
| `/graph-engine` (live planner) | hospital | 375 / 768 / 1280 | 0 | none |
| `/simulation` (before/after route failure) | hospital | 375 / 768 / 1280 | 0 | none |
| `/research-mode` (strategy comparison) | hospital | 375 / 768 / 1280 | 0 | none |
| `/mathematical-model` (live parameters) | hospital | 375 / 768 / 1280 | 0 | none |
| `/decision-transparency` (plan + backups) | hospital | 375 / 768 / 1280 | 0 | none |
| `/recommendation` (distance-limited donors) | hospital | 375 / 768 / 1280 | 0 | none |
| `/analytics` (labelled forecast) | hospital | 375 / 768 / 1280 | 0 | none |
| `/hospital/units` (unit lifecycle) | hospital | 375 / 768 / 1280 | 0 | none |
| `/hospital/resource-pathways` | hospital | 375 / 768 / 1280 | 0 | none |
| `/hospital/shipments` (cold chain, receipt) | hospital | 375 / 768 / 1280 | 0 | none |
| `/admin/facilities` (revoke verification) | admin | 375 / 768 / 1280 | 0 | none |

That makes 33 page-and-width scans with 0 violations. The final numbers come from the last run of the spec, recorded in `axe-gap-pages.json`.

## Defects found and fixed in this round

| Finding | Fix |
|---|---|
| `scrollable-region-focusable` on the new wide tables (plan options, units, shipments). At 375 px they scroll horizontally, but a keyboard user could not reach the scroll region. | The scroll containers get `tabIndex=0`, `role="region"` and an `aria-label`, for example "Blood units (scrollable)". |
| Decorative Material Symbols icons were announced as words, such as "arrow_back" or "analytics". | `aria-hidden="true"` on every icon, applied by a codemod across all components. |
| The map gave no feedback when tiles failed to load (blocked or offline): it showed only a grey box. | An explicit text banner ("Map background tiles could not be loaded …"), with markers still rendered. Tested with tile requests aborted (`gap-map-tile-fallback-1280.png`). |

## Status values not conveyed by colour alone

Feasibility (`VIABLE`, `DEADLINE RISK`, `PARTIAL`, `IMPOSSIBLE`), route basis (recorded routes, or "Straight-line ESTIMATE") and cold-chain status are always shown as text. Colour only reinforces them. `Live.test.tsx` asserts the text.

## Limits of this audit

* axe finds roughly a third to a half of WCAG issues. No manual screen-reader pass (NVDA, VoiceOver) and no keyboard-only walkthrough of every new flow was carried out in this environment.
* Colour contrast of the charts on the analytics page was checked by axe only for text elements, not for the chart marks themselves.
* Result: **PASS (automated)**. This is not a WCAG conformance claim.
