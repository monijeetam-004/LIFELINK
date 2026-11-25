# LifeLink — Phase 21 Audit: Error, Loading, Empty & Offline UX Hardening

## 0. Mandatory baseline (actual, not assumed)

Per this phase's explicit instruction not to assume Phase 20's final counts, both were re-verified directly before any work began:

- Backend: `pytest --collect-only -q`, summed per-file → **689 tests** (the real Phase 20 final count, not the "655+" the phase brief anticipated as a floor).
- Frontend E2E: `npx playwright test --list` → **27 tests in 10 files**.

Both were re-confirmed again after this phase's work: **694 backend tests** (689 + 5 new), **30 Playwright tests in 11 files** (27 + 3 new). ruff (101 pre-existing findings, unchanged), mypy (3 pre-existing errors, unchanged), `tsc --noEmit` (clean), `eslint .` (clean), production `vite build` (clean).

## 1. Architecture already in place (inspected directly, not assumed)

Before auditing individual pages, the shared error/loading architecture was read in full to avoid duplicating what already exists (per this phase's explicit "reuse, don't rebuild" instruction):

- **`frontend/src/lib/api.ts`** — the single shared axios client. `toApiError(err)` normalizes every failure into `{detail, code?, status?, requestId?, retryAfterSeconds?}` with distinct messages for timeout, network-down (`ERR_NETWORK`), and each relevant HTTP status (400/401/403/404/409/422/429/500/502/503). A request interceptor attaches `X-Request-Id: crypto.randomUUID()` to every request — this already satisfies the phase's request-correlation goal. A response interceptor dispatches `lifelink:session-expired` for any 401 outside the four session-check paths.
- **`frontend/src/components/SessionExpiredListener.tsx`** — bridges that event to a real `navigate("/session-expired", ...)`, with a debounce against several near-simultaneous 401s.
- **`frontend/src/components/ErrorBoundary.tsx`** — a real class-based React error boundary wrapping the whole app (`main.tsx`: `ErrorBoundary > QueryClientProvider > BrowserRouter > AuthProvider > ToastProvider > App`), rendering `pages/errors/GenericError.tsx` with a working retry.
- **`frontend/src/App.tsx`** — dedicated, already-wired routes: `/403` (`AccessDenied`), `/session-expired` (`SessionExpired`), `/500` (`ServerError`), `/offline` (`OfflinePage`), and a catch-all `<Route path="*" element={<NotFound />} />`. All five `pages/errors/*.tsx` files were read in full: each is a real, distinct, non-generic page with a working action (retry/reload/sign-in-again/go-home), not a shared placeholder.
- **`frontend/src/components/OfflineBanner.tsx`** — a slim, `role="status"` banner driven by `navigator.onLine`/the `online`/`offline` browser events, correctly scoped as a connectivity *hint* rather than a claim about server health (matches the phase brief's explicit "no server-health, no offline-first sync" constraint).

**Conclusion:** the global architecture (error boundary, status pages, 404 route, session-expiry, offline hint, request-ID correlation) was already correct and complete. No changes were made to any of the files in this section — Phase 21's real work was auditing and fixing the ~58 individual page components against this already-solid foundation.

## 2. Audit method

A full page/component inventory (58 files under `frontend/src/pages/`) was split across four parallel, read-only research passes (each read every assigned file in full and cited file+line for every claim — no speculative gaps were accepted):

1. Patient/Donor/Family core flows (`PatientDashboard`, `DonorDashboard`, `FamilyDashboard`, `FamilyLinkedPatients`, `FamilyPatientDetail`, `EligibilityCenter`, `ReadyConsole`, `IdentityVerification`, `MilestonesHistory`, `EmergencySosTracking`, `NewRequestGuidedFlow`, `FindDonor`, `RequestHistory`, `DonorRecommendation`, plus modals).
2. Hospital core flows (`HospitalDashboard`, `InventoryConsole`, `RequestManagementDetail`, `DonorDirectoryAndLogistics`, `FacilityVerification`, `ResourcePathways`, `TransportRoutes`).
3. Admin flows (`AdminOverview`, `VerificationsQueue`, `UserManagement`, `AdminAnalytics`, `TransportRoutes`, `AdminFacilities`, `AdminDonors`, `AdminAuditFeed`).
4. Shared + decision-support + certificate pages (`Profile`, `Settings`, `NotificationsCenter`, `HelpAndLegal`, `StrategyLab`, `DonationCertificate`, `VerifyCertificate`, `DecisionTransparency`, `MathematicalModel`, `GraphDecisionEngine`, `SimulationMode`, `ResearchMode`, `Analytics`).

Each page was checked against: loading state present, empty state present and distinct from a fetch failure, error state present and distinct from empty, retry affordance, mutation pending-disable, mutation error handling (`onError` present, error rendered), double-submission protection, and `role="alert"`/`aria-live` usage.

## 3. Confirmed-correct patterns (not touched — cited so they aren't mistaken for gaps)

- `frontend/src/lib/api.ts`, `SessionExpiredListener.tsx`, `ErrorBoundary.tsx`, `App.tsx`'s routing, all five `pages/errors/*.tsx`, `OfflineBanner.tsx` (section 1).
- `NotificationsCenter.tsx`'s `list` query (loading/error/retry/empty, correctly distinguishing fetch failure from "no notifications").
- `VerifyCertificate.tsx` never rendered a false-positive "valid" state.
- `DonationCertificate.tsx` disabled dead action links via CSS instead of leaving broken `href="#"` links.
- `Settings.tsx`'s `PasswordSection` cleanly separates client-side validation error from server error and resets mutation state on resubmit.
- `RequestManagementDetail.tsx`, `StrategyLab.tsx`, `InventoryConsole.tsx` already used `toApiError` + `role="alert"` consistently in most places (this phase filled in the remaining gaps in each — section 5).
- `DecisionTransparency.tsx`, `MathematicalModel.tsx`, `GraphDecisionEngine.tsx`, `SimulationMode.tsx`, `ResearchMode.tsx` — all five already carry a fixed, `role="note"` "CONCEPTUAL ILLUSTRATION — NOT LIVE DATA" disclaimer (added in an earlier phase), correctly distinguishing intentionally-static demo content from a real bug. These were **not** treated as gaps, per this phase's explicit instruction not to flag intentionally-static illustrative pages.
- `blood_requests.py`'s idempotency-key mechanism (409 on same-key/different-payload, 200-replay on same-key/same-payload) was already correct and already covered by `tests/test_phase19_blood_request_idempotency.py` and `e2e/blood_request_idempotency.spec.ts` — Phase 21 did not change backend idempotency logic, only extended an existing frontend page to use the existing mechanism (section 4, bug 1).

## 4. Real bugs found and fixed

### Bug 1 — `NewRequestGuidedFlow.tsx`: missing idempotency key + re-entrancy guard → possible duplicate blood request

**Root cause:** `BloodRequestModal.tsx` (the modal form) and `NewRequestGuidedFlow.tsx` (the full-page guided flow) both call the same `POST /blood-requests`, but only the modal minted and sent an `idempotency_key` and guarded `submit()` against `create.isPending`. The guided flow's `submit()` had neither.

**User impact:** a patient whose first submit failed for a transient reason (network blip, timeout) and who retried by clicking "Submit request" again created a **second, duplicate blood request** — the exact "duplicate blood request from a retried failure" scenario this phase's brief calls out by name as not acceptable.

**Reproduction (pre-fix):** abort the first `POST /blood-requests`, let the UI show the network-failure message, click Submit again → two `BloodRequest` rows exist for the same intent.

**Fix:** `frontend/src/pages/patient/NewRequestGuidedFlow.tsx` — added a `newIdempotencyKey()`-derived key (same utility `BloodRequestModal` uses, `lib/idempotency.ts`), minted once per attempt and reused across retries of that attempt, rotated only on a genuine 409 (form changed since the key was minted); added `if (create.isPending) return;` at the top of `submit()`, mirroring the modal's existing comment and guard.

**Regression test:** `e2e/phase21_error_loading_empty_offline.spec.ts`, scenario (A) — aborts the first POST, retries, asserts via the real API that exactly one new request exists. `backend/tests/test_phase21_error_loading_empty_offline.py::test_blood_request_same_key_same_payload_replays_cleanly_not_a_duplicate` locks in the backend contract this fix depends on.

### Bug 2 — `RequestManagementDetail.tsx`: stale error persists after a subsequent successful action

**Root cause:** both the `allocate` and `fulfill` mutations write failures to the same `allocateError` state. `allocate`'s `onSuccess` cleared it; `fulfill`'s did not.

**User impact:** a hospital operator who saw an `allocate` failure, then successfully clicked "Mark fulfilled from inventory," would still see the old error message displayed underneath the now-successful, updated status — misleading about whether the fulfil actually worked.

**Fix:** `fulfill`'s `onSuccess` now also calls `setAllocateError("")`.

### Bug 3 (misleading-state) — `IdentityVerification.tsx` / `FacilityVerification.tsx`: a failed status fetch is shown as "Not submitted"

**Root cause:** the verification-status banner derives from `latest = [...verifications].sort(...)[0]`, itself derived from `verifications.data ?? []`. Neither file ever checked `verifications.isError`. On a fetch failure, `data` stays `undefined`, `latest` stays `undefined`, and the banner's `switch` falls through to its final default: **"Not submitted — Upload your documents below to start verification."**

**User impact:** a donor/hospital who had *already submitted* documents (or was even already verified, if `profile` loaded but `verifications` failed) would be told they hadn't submitted anything and prompted to re-upload — this is exactly the phase brief's named "network failure disguised as an empty/default state" anti-pattern.

**Fix:** both files now check `verifications.isError` (and `.isLoading`) before falling through to the "Not submitted" default, showing an honest "Couldn't load your verification status" message with the real `toApiError` detail and a Retry button.

### Bug 4 (misleading-state) — `VerifyCertificate.tsx`: a network/server failure looks identical to "invalid code"

**Root cause:** `q.isError` alone (with `retry: false`) triggered the single "Not verified / double-check the code" message for every failure kind — a genuine 404 and a network outage rendered the same UI.

**User impact:** during a transient outage, a visitor verifying a real, valid certificate would be told to double-check whether they copied the code correctly — actively misleading, and (with `retry: false` and no retry button) had no path to recover other than a manual page reload.

**Fix:** `toApiError(q.error).status === 404` now gates the "Not verified" copy; every other failure gets a distinct "Couldn't check this certificate" message with the real error detail and a working Retry button.

### Bug 5 (misleading-state) — `FamilyPatientDetail.tsx`: a failed `links` fetch leaves the page permanently blank

**Root cause:** the entire page body was gated on `link` (derived from `links.data`). Neither `links.isLoading` nor `links.isError` was ever rendered — only the header's tiny subtitle said "Loading…", and the "no accepted link" message required `links.data` to be truthy, so it never rendered on a fetch failure either.

**User impact:** on a fetch failure, the page showed nothing at all below the header — indistinguishable from a hang, with the phase brief's named "endless spinner"/blank-page anti-pattern realized almost literally (it's not even a spinner, just blank).

**Fix:** explicit `links.isLoading` and `links.isError` (with retry) states added; the "no accepted link" message now only renders once data has genuinely loaded with no match.

### Bug 6 — `Analytics.tsx`: fabricated data presented to real signed-in users with no disclaimer

**Root cause:** `DecisionTransparency.tsx`, `MathematicalModel.tsx`, `GraphDecisionEngine.tsx`, `SimulationMode.tsx`, and `ResearchMode.tsx` all received a fixed "CONCEPTUAL ILLUSTRATION — NOT LIVE DATA" banner in an earlier phase; `Analytics.tsx` — structurally identical (a 1:1 Stitch-port page with zero network calls and entirely fabricated numbers: lives-saved counters, a non-functional "Real-time" toggle, per-second scan timestamps, verified-looking transaction statuses) — never got one. Unlike its five siblings (all public, unauthenticated pages), `Analytics.tsx` is gated behind `RequireAuth roles={["ADMIN","HOSPITAL"]}` — reachable only by real, signed-in operational users, who could plausibly mistake its numbers for live data.

**Fix:** added the identical banner used by its five siblings.

## 5. Missing error/loading/retry coverage fixed (not logic bugs, but real, observed gaps)

All of the following were confirmed present (query/mutation exists, `isError`/`onError` genuinely absent) by direct file reads, then fixed with the established `toApiError` + `role="alert"` (+ Retry where the query supports `refetch()`) pattern already used elsewhere in the codebase — no new abstraction was introduced:

| File | Gap fixed |
|---|---|
| `pages/shared/Profile.tsx` | `FamilyFields`/`PatientFields`/`DonorFields`/`HospitalFields` queries never rendered `isError` (fetch failure looked like an empty/blank profile); `HospitalFields`' `save` mutation had no `onError` at all (silent save failure) |
| `pages/shared/Settings.tsx` | `donorProfile` query had no error handling; `setAvailabilityMut`/`locationMut`/`alertPrefsMut` all lacked `onError` (silent failure, control just reverts with no explanation); the geolocation-permission-denied callback was a no-op with no user-facing message |
| `pages/shared/NotificationsCenter.tsx` | `readOne`/`readAll` mutations had no `onError` (a failed "mark read" silently did nothing) |
| `pages/shared/StrategyLab.tsx` | `requests`/`emergencies` picker queries had no loading/error state (a fetch failure looked identical to "no items available"); `comparison` query's error had no Retry |
| `pages/DonationCertificate.tsx` | `q.isError` was fetched but never rendered — a load failure fell through to "No certificate yet" |
| `pages/family/FamilyDashboard.tsx` | `links.isError` had no detail/retry; `requests`/`emergencies`/`notifications` fetch failures were silently unreported (patient cards' stats/activity just looked emptier than reality) |
| `pages/hospital/RequestManagementDetail.tsx` | `inventory`/`hospitalList` queries had no error handling — a failure silently made "0 units available" look like real current data |
| `pages/hospital/InventoryConsole.tsx` | `log` query had no error state at all (neither loading, empty, nor error rendered on failure — a blank section); main inventory error had no Retry |
| `pages/patient/NewRequestGuidedFlow.tsx` | (see Bug 1) |
| `pages/PatientDashboard.tsx` | `acceptLink`/`revokeLink`/`rejectLink` family-link mutations had no `onError` (silent failure); `requests` error row had no detail/retry |

## 6. Scope note (honesty over "looks done")

The four-agent audit additionally flagged several files with the identical "query fetched but `isError` never rendered" or "mutation missing `onError`" pattern that were **not** modified in this pass, to keep this phase's diff focused on verified, reproducible bugs and the highest-traffic surfaces rather than a blanket sweep: `DonorDashboard.tsx` (5 mutations, 6 queries), `ReadyConsole.tsx` (5 mutations), `EligibilityCenter.tsx` (`cancelAppt`), `FamilyLinkedPatients.tsx` (`revoke`), `AdminOverview.tsx` (hardcoded notifications-error string, `readOne`/`readAll` mutations), `HospitalDashboard.tsx` (`emergencies`/`routes`/`activity`/`map` queries), `EmergencySosTracking.tsx` (per-card detail query), `DonorDirectoryAndLogistics.tsx` (`reqInfo` query), `TransportRoutes.tsx` (`health`/`directory` queries), `MilestonesHistory.tsx`. These follow the exact same fix pattern already applied in section 5 above and are the natural next targets for a follow-up pass; none of them were found to have the "false/misleading state" severity of the six bugs in section 4.

## 7. Backend error-contract spot-check

No backend behavior changed in this phase. A spot-check of the endpoints the fixed frontend code now depends on (`POST /blood-requests` 409-on-key-reuse, `GET /certificates/verify/{code}` 404-on-unknown-code, `GET /blood-requests/{id}` 404-on-unknown-id) confirmed clean, UI-safe `{"detail": "..."}` responses with no leaked internals (no stack traces, SQL, file paths, or credentials) — see `backend/tests/test_phase21_error_loading_empty_offline.py`, which locks these three contracts in as regression tests.

## 8. Security

No `MAIL_PASSWORD`/App Password/credential value was printed, logged, or altered in this phase. No new secrets were introduced. `.env` was not modified. No SMS-related code was touched (`tests/test_sms.py`'s 35 tests are unaffected and still pass).
