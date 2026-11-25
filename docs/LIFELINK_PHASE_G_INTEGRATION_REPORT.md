# LifeLink — Phase G: Frontend Integration Closures

**Date:** 2026-10-05 · **Scope:** the Phase A plan's Phase G — "frontend integration closures (DonorDashboardMobile, NotificationsBell fix, admin inventory honesty, stale-state audit)" — plus the item Phase B/C explicitly deferred to G "only if the frontend work actually calls for it" (component-level inventory visibility). Scope source as in the Phase F report (original master plan text not retained; Phase A matrix is the surviving record).

## 1. Requirements and outcome

| Item | Finding (recon) | Outcome |
|---|---|---|
| DonorDashboardMobile | Already a `<Navigate to="/donor">` stub replacing a fabricated mockup (closed before Phase A, re-verified) | No change needed; `/donor` is the real, responsive dashboard. |
| NotificationsBell per-item Read | No pending guard → double-click sent two requests (documented as a known gap by Phase 33). Same bug in the donor Ready Console and admin overview notification lists. | Shared `useInFlightIds` per-row lock in all three; "Reading…" state; regression test (one request for a double-click + click while pending). |
| Admin inventory honesty | Facility detail read aggregates raw (expired units could count as available); facility list likewise; the detail's `component` field (Phase 35) was never shown — O- whole blood and O- plasma rendered as two identical "O-" rows with a duplicate React key. | Detail runs the real lazy sweep; list subtracts expired-unswept units read-only (invariant 43); component column shown. |
| Component-level inventory visibility | Shipments (Phase E) can carry plasma/platelets/red cells, but the console only ever showed/managed whole blood — component stock could not be added or seen through the UI. | `GET /hospitals/me/inventory/components`; "Stock by component" section; component selector in every inventory action. |
| Stale-state audit | (a) 12 polling sites; (b) hospital sub-nav "Analytics" linked to the conceptual-illustration page (fixed demo numbers) from operational screens; (c) inventory action prompt was a hand-rolled overlay with no dialog semantics, focus trap or Escape; (d) static demo pages re-checked — all carry the "CONCEPTUAL ILLUSTRATION — NOT LIVE DATA" banner; (e) every list mutation button checked for a pending guard. | (a) real-time invalidation (Phase F), polling only as fallback; (b) link → real `/hospital/analytics-mobile`; (c) shared accessible `Modal`; (d) honest, unchanged; (e) only the three Read buttons lacked one — fixed. |

## 2. Workflow verification (real backend)

Covered end-to-end by Playwright against a fresh migrated database: donor (log → verification → certificate, Phase E spec), patient (request → hospital allocation visible live, Phase F spec), hospital (component stock via console → admin sees component; shipment create → dispatch → fail → reconcile, Phase E spec), admin (donation review, audit-chain verify, facility detail). Full-suite results in §6.

## 3. Authorization

New endpoint `GET /hospitals/me/inventory/components` is role-gated (HOSPITAL only; DONOR/PATIENT/FAMILY/ADMIN → 403) and own-facility only (no id accepted) — tested. Admin facility routes unchanged (`require_roles(ADMIN)`). No authorization moved to the frontend.

## 4. Responsive / accessibility

`phase_g_integration.spec.ts`: inventory console has no horizontal overflow at 375 / 768 / 1280 px; console with the action dialog open has **zero** WCAG 2.1 A/AA axe violations; dialog takes focus, Tab stays inside across 8 presses, Escape closes it. Component test also checks Escape and the dialog role.

## 5. Tests

| Suite | Count | Result |
|---|---|---|
| `backend/tests/test_phase_g_integration.py` | 5 | pass |
| `frontend/src/pages/hospital/PhaseG.test.tsx` | 5 | pass |
| `NotificationsBell.test.tsx` (+1 per-item guard test) | 12 | pass |
| `frontend/e2e/phase_g_integration.spec.ts` | 3 | pass |

## 6. Combined F + G regression (final, after every F and G change)

| Suite | Result |
|---|---|
| Backend full suite (`pytest tests --ignore=tests/e2e`) | **1,137 passed, 1 skipped, 0 failed** (Phase E baseline 1,109 + 16 + 7 + 5 new). The skip is the pre-existing one. |
| Frontend Vitest | **152/152** across 17 files (Phase E 138 + 8 realtime + 1 bell + 5 Phase G) |
| `tsc --noEmit`, ESLint (`src` + `e2e`, max-warnings 0) | clean |
| `npm run build` | OK; main chunk 789.6 kB / 229.0 kB gzip |
| Playwright, fresh `lifelink_e2e` DB, CI-equivalent env | **78 passed, 1 skipped, 0 failed** of 79 — two consecutive clean full runs (the second after the final publisher change). The skip is Phase 21 test C's own conditional skip ("no unread notification currently exists to probe"); it ran and passed in an earlier run where one existed. |
| Migrations | fresh empty DB → head (`e4f5a6b7c8d9`); dev DB up → down → up; ORM-vs-migrated-schema drift test passes in the suite |
| `scripts/check_invariants.py` | dev DB: all checks + audit chain OK; e2e DB after the full Playwright run: 12/12 OK |

Intermediate runs are disclosed, not discarded: full backend run 1 had one failure (Phase 27 query-count bound — the real-time publisher's fan-out cost, fixed, see the Phase F report); Playwright run 1 had two failures — the bell header text change broke `hospital_workflow.spec.ts`'s exact "Notifications" match (my change; fixed by keeping the label in its own element), and `blood_request_idempotency.spec.ts` (below).

## 7. Bugs found and fixed

1. Per-item "Read" double-submit (3 screens).
2. Admin facility detail/list could count expired units as on hand.
3. Admin facility detail hid the component (duplicate "O-" rows, duplicate React key).
4. Component stock invisible/unmanageable in the hospital UI.
5. Operational "Analytics" link pointed at fixed demo data.
6. Inventory action prompt lacked dialog semantics, focus trap and Escape.
7. **`blood_request_idempotency.spec.ts` "rapid double-click" re-diagnosed.** Phase 31.1 and Phase E recorded it as a CPU-timing flake that passes in isolation. It is deterministic: on an account with no requests the table body holds one "No requests yet" placeholder row, so creating the first request leaves the row count at 1 and the wait for "more rows than before" can never succeed — it failed on every fresh database and passed on any reused one. The spec now counts real request rows only (`tbody tr:not(:has(td[colspan]))`); the assertion "exactly one new request" is unchanged. Verified on a fresh database, then in both final full runs.

## 8. Known limitations

- The legacy whole-blood grid (bulk PUT contract) still edits whole blood only; other components are managed via actions, not the in-place count editor.
- Conceptual-illustration pages remain (honestly labelled) demo content.
