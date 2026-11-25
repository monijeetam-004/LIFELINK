import { expect, test } from "@playwright/test";
import { loginAsUi, logout } from "./helpers";
import { ensureDonorAndHospitalProfiles, USERS } from "./setup";

/**
 * Phase 19 -- offline / network-failure E2E coverage (section 14, listed as
 * a "MUST FIX" gap alongside the hospital workflow suite). Every failure
 * here is a real network abort at the browser level via `page.route()` +
 * `route.abort("failed")` against the real backend -- never a mocked
 * response body -- so what's actually being proven is that the existing
 * React Query `isError` branches (and, for the blood-request form, the
 * mutation's `onError`) show an honest error instead of a fake success, a
 * silently-empty list, or an infinite spinner, and that the app recovers
 * once the network genuinely comes back. Route filters below check the
 * HTTP method so aborting a GET (list/read) never also swallows an
 * unrelated POST/PUT to the same path.
 */
test.describe("Phase 19 E2E: offline / network-failure UX", () => {
  test.beforeAll(async () => {
    await ensureDonorAndHospitalProfiles();
  });

  test("the patient's request list shows an honest error when it fails to load, and recovers once the network returns", async ({
    page,
  }) => {
    await page.route("**/blood-requests", (route) => {
      if (route.request().method() === "GET") return route.abort("failed");
      return route.continue();
    });
    await loginAsUi(page, USERS.patient);
    await expect(page.getByText("Couldn't load requests.")).toBeVisible({ timeout: 10_000 });
    // No fake data and no infinite spinner alongside the error.
    await expect(page.getByText("Loading your requests…")).not.toBeVisible();

    // The network "returns" -- unroute and reload, same as a real page
    // refresh after connectivity is restored (this table has no dedicated
    // Retry button of its own, unlike the notification/inventory panels).
    await page.unroute("**/blood-requests");
    await page.reload();
    await expect(page.getByText("Couldn't load requests.")).not.toBeVisible({ timeout: 10_000 });
  });

  test("the notifications page shows an honest error when the API fails, and recovers via Retry", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.patient);
    // Matched by regex against the real backend's port (8000), not a
    // same-path glob -- a bare "**/notifications" glob also matches the
    // frontend's own SPA navigation request to
    // http://localhost:4173/notifications, which would abort the page
    // load itself instead of just the API call.
    const notificationsApiUrl = /:8000\/notifications$/;
    await page.route(notificationsApiUrl, (route) => {
      if (route.request().method() === "GET") return route.abort("failed");
      return route.continue();
    });
    await page.goto("/notifications");
    await expect(page.getByText("Couldn't load notifications.")).toBeVisible({ timeout: 10_000 });

    await page.unroute(notificationsApiUrl);
    await page.getByRole("button", { name: "Retry" }).click();
    await expect(page.getByText("Couldn't load notifications.")).not.toBeVisible({ timeout: 10_000 });
  });

  test("the hospital dashboard's inventory tile shows an honest error when inventory fails to load, and recovers via Retry", async ({
    page,
  }) => {
    await page.route("**/hospitals/me/inventory", (route) => {
      if (route.request().method() === "GET") return route.abort("failed");
      return route.continue();
    });
    await loginAsUi(page, USERS.hospital);
    await expect(page.getByText("Couldn't load inventory.")).toBeVisible({ timeout: 10_000 });

    await page.unroute("**/hospitals/me/inventory");
    await page.getByRole("button", { name: "Retry" }).first().click();
    await expect(page.getByText("Couldn't load inventory.")).not.toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("O+").first()).toBeVisible({ timeout: 10_000 });
  });

  test("a blood-request submission failure shows an honest error, keeps the form data, and a retry after recovery succeeds", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.patient);
    await page.getByRole("button", { name: "New Blood Request" }).first().click({ force: true });
    await page.getByLabel("Blood group").selectOption("O_NEG");
    const note = "phase19 offline-retry e2e note, kept across the failed attempt";
    await page.getByLabel("Note (optional)").fill(note);

    await page.route("**/blood-requests", (route) => {
      if (route.request().method() === "POST") return route.abort("failed");
      return route.continue();
    });
    await page.getByRole("button", { name: "Submit request" }).click();
    // Honest network-failure message from the real API client -- never a
    // fake "request submitted" success, and never a silent no-op.
    await expect(page.getByText("Can't reach the server. Check your connection and try again.")).toBeVisible({
      timeout: 10_000,
    });
    // The user's typed note wasn't thrown away by the failed attempt.
    await expect(page.getByLabel("Note (optional)")).toHaveValue(note);
    // The submit button is never disabled forever -- it's usable again.
    await expect(page.getByRole("button", { name: "Submit request" })).toBeEnabled();

    // Network "returns": the same click, with the same form state and the
    // same idempotency key (this was a genuine send failure, not a 409),
    // now reaches the real backend and actually creates the request.
    await page.unroute("**/blood-requests");
    await page.getByRole("button", { name: "Submit request" }).click();
    await expect(page.getByRole("button", { name: "Submit request" })).toHaveCount(0, {
      timeout: 10_000,
    });
    await expect(page.getByText("Couldn't load requests.")).not.toBeVisible();
  });

  test.afterEach(async ({ page }) => {
    await logout(page);
  });
});
