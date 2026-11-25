import { expect, test } from "@playwright/test";
import { loginAsUi, logout } from "./helpers";
import { USERS } from "./setup";

/**
 * Phase 25 QA pass: two real gaps found while auditing the existing E2E
 * suite (see LIFELINK_PHASE_25_TESTING_AUDIT.md).
 *
 * 1. `src/pages/NotFound.tsx` (the client-side catch-all "*" route) and
 *    `src/pages/errors/{AccessDenied,ServerError}.tsx` all exist and are
 *    wired into App.tsx's router, but no existing spec ever actually
 *    navigates to an unknown URL or to /500 directly to prove the catch-all
 *    renders instead of a blank page or a crash.
 * 2. Every existing "network failure" spec (offline_network_failure.spec.ts)
 *    uses `route.abort("failed")`, which is a browser-level connection
 *    failure -- never a real HTTP 500 response body. That leaves the
 *    genuinely-different "the server answered, but with an error" code path
 *    unverified. This file closes that gap with `route.fulfill({status:500})`.
 */
test.describe("Phase 25 E2E: catch-all 404 and real-500 error UX", () => {
  test("navigating to an unknown URL renders the real 404 page, not a blank screen or crash", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.patient);
    await page.goto("/this-route-does-not-exist-phase25");
    await expect(page.getByText("404")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("Page not found")).toBeVisible();
    await expect(page.getByRole("link", { name: "Go home" })).toBeVisible();

    await page.getByRole("link", { name: "Go home" }).click();
    await expect(page).toHaveURL(/\/patient$/, { timeout: 10_000 });
    await logout(page);
  });

  test("an unauthenticated visit to an unknown URL also renders 404, not a login loop or crash", async ({
    page,
  }) => {
    await page.goto("/this-route-also-does-not-exist");
    await expect(page.getByText("404")).toBeVisible({ timeout: 10_000 });
  });

  test("a genuine HTTP 500 response body (not a network abort) shows an honest error, distinct from the offline case", async ({
    page,
  }) => {
    await page.route("**/blood-requests", (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Internal Server Error" }),
        });
      }
      return route.continue();
    });
    await loginAsUi(page, USERS.patient);
    // Same honest-error branch as the network-abort case proves the
    // component's isError handling doesn't distinguish (or need to) between
    // a dropped connection and a real 5xx -- either way, the user sees a
    // real error, never fake/stale data and never an infinite spinner.
    await expect(page.getByText("Couldn't load requests.")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("Loading your requests…")).not.toBeVisible();

    await page.unroute("**/blood-requests");
    await page.reload();
    await expect(page.getByText("Couldn't load requests.")).not.toBeVisible({ timeout: 10_000 });
    await logout(page);
  });
});
