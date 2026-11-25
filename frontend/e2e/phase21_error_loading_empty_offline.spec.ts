import { expect, test } from "@playwright/test";
import { loginAsUi, logout } from "./helpers";
import { USERS } from "./setup";

// Same reasoning as document_storage_security.spec.ts's identical constant:
// page.context().request shares the real browser session's cookie jar, but
// a relative URL there resolves against the frontend origin (baseURL:
// http://localhost:4173), not the backend -- so API calls made through it
// need the backend's absolute origin.
const BACKEND_ORIGIN = "http://localhost:8000";

/**
 * Phase 21 -- Comprehensive Error, Loading, Empty & Offline UX Hardening.
 *
 * These are CONTROLLED-NETWORK-FAILURE E2E scenarios (page.route(...).abort()
 * against the real backend/UI, the same technique offline_network_failure.spec.ts
 * and document_storage_security.spec.ts already established) plus one REAL-
 * BACKEND E2E scenario that needs no network interception at all. Each one
 * exercises a concrete bug this phase found and fixed (see
 * LIFELINK_PHASE_21_AUDIT.md / LIFELINK_PHASE_21_REPORT.md), not a generic
 * "does a spinner show" check:
 *
 *  A. NewRequestGuidedFlow.tsx (the full-page guided blood-request flow) had
 *     no idempotency_key and no re-entrancy guard, unlike the structurally
 *     identical BloodRequestModal -- a failed submit retried by the user
 *     could create a genuine duplicate blood request. Mirrors
 *     blood_request_idempotency.spec.ts's proven "abort the first POST,
 *     retry, expect exactly one new row" pattern, but against the guided
 *     flow instead of the modal.
 *  B. VerifyCertificate.tsx collapsed every failure (a genuine 404 and a
 *     network/server error) into the identical "Not verified / double-check
 *     the code" message. Confirms the real, distinct behavior now: a
 *     genuinely-unknown code says "Not verified" (real backend, no
 *     interception -- REAL-BACKEND E2E), while a network failure says
 *     "Couldn't check this certificate" with a working Retry.
 *  C. NotificationsCenter.tsx's readOne/readAll mutations had no onError --
 *     a failed "mark read" silently did nothing. Confirms a failed mark-read
 *     now surfaces a real, visible error message instead of silently no-op'ing.
 */
test.describe("Phase 21 E2E: error / loading / empty / offline UX", () => {
  test("(A) resubmitting the guided New Blood Request flow after a lost first attempt does not duplicate the request", async ({
    page,
  }) => {
    // Counted via the real API through the browser's own authenticated
    // session (page.context().request shares its cookie jar -- the same
    // technique document_storage_security.spec.ts uses for its own
    // API-level assertions) rather than scraping UI table rows: this
    // patient's request history is shared, ever-growing state across every
    // spec file run in this persistent dev database, so a UI row count is
    // sensitive to pagination/render timing in a way a direct count of
    // "how many rows does /blood-requests return right now" is not.
    async function countMyRequests() {
      const resp = await page.context().request.get(`${BACKEND_ORIGIN}/blood-requests`);
      expect(resp.ok()).toBeTruthy();
      return ((await resp.json()) as unknown[]).length;
    }

    await loginAsUi(page, USERS.patient);
    const before = await countMyRequests();

    await page.goto("/patient/new-request");
    await page.getByRole("button", { name: "O+", exact: true }).click();
    await page.getByRole("button", { name: "Continue" }).click();
    await page.getByRole("button", { name: "Continue" }).click();

    // The first POST is lost in transit (never reaches the server) --
    // exactly the scenario NewRequestGuidedFlow's Phase 21 fix (a stable
    // idempotency_key reused across retries of the same attempt) protects
    // against. Every subsequent POST goes through normally.
    let seen = 0;
    await page.route("**/blood-requests", (route) => {
      if (route.request().method() === "POST" && seen === 0) {
        seen += 1;
        return route.abort("failed");
      }
      return route.continue();
    });

    const submit = page.getByRole("button", { name: "Submit request" });
    await submit.click();
    await expect(page.getByText("Can't reach the server. Check your connection and try again.")).toBeVisible({
      timeout: 10_000,
    });

    // Same form, same idempotency key, retried -- must be treated as the
    // first successful creation of that key, not a second request.
    await submit.click();
    await expect(page.getByText("Request submitted")).toBeVisible({ timeout: 10_000 });
    await page.unroute("**/blood-requests");

    const after = await countMyRequests();
    expect(after).toBe(before + 1);
  });

  test("(B) certificate verification distinguishes a genuinely-unknown code from a network failure, and Retry recovers", async ({
    page,
  }) => {
    const bogusCode = "phase21-e2e-nonexistent-code-does-not-exist";

    // Real backend, no interception: a genuinely-unknown code is an honest
    // "Not verified", never a false positive and never confused with a
    // network problem.
    await page.goto(`/verify/${bogusCode}`);
    await expect(page.getByText("Not verified")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("No certificate matches this code.")).toBeVisible();

    // Now the exact same lookup fails at the network level instead -- before
    // the Phase 21 fix this rendered the identical "Not verified" message,
    // telling the user their code was bad when the truth is the check never
    // completed.
    await page.route("**/certificates/verify/**", (route) => route.abort("failed"));
    await page.goto(`/verify/${bogusCode}`);
    await expect(page.getByText("Couldn't check this certificate")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("Not verified")).not.toBeVisible();

    // Retry, now unrouted, reaches the real backend again and falls back to
    // the honest (still-correct, since the code really doesn't exist) 404
    // message -- proving Retry actually re-fetches rather than being inert.
    await page.unroute("**/certificates/verify/**");
    await page.getByRole("button", { name: "Retry" }).click();
    await expect(page.getByText("Not verified")).toBeVisible({ timeout: 10_000 });
  });

  test("(C) a failed 'mark notification read' shows a real error instead of silently doing nothing", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.donor);
    await page.goto("/notifications");
    await expect(page.getByText("Loading…")).not.toBeVisible({ timeout: 10_000 }).catch(() => {});

    const markReadButtons = page.getByRole("button", { name: "Mark read" });
    const unreadBefore = await markReadButtons.count();
    test.skip(unreadBefore === 0, "no unread notification currently exists to probe");

    await page.route("**/notifications/*/read", (route) => route.abort("failed"));
    await markReadButtons.first().click();
    await expect(
      page.getByText("Can't reach the server. Check your connection and try again."),
    ).toBeVisible({ timeout: 10_000 });
    // The item is still shown unread -- the failed action didn't silently
    // pretend to succeed.
    await expect(markReadButtons).toHaveCount(unreadBefore);

    await page.unroute("**/notifications/*/read");
    await markReadButtons.first().click();
    await expect(markReadButtons).toHaveCount(unreadBefore - 1, { timeout: 10_000 });
  });

  test.afterEach(async ({ page }) => {
    await logout(page);
  });
});
