import { expect, test, type Page } from "@playwright/test";
import { loginAsUi, logout } from "./helpers";
import { USERS } from "./setup";

// Real request rows only. On an account with no requests yet the table body
// holds a single "No requests yet" placeholder row (a <td colSpan>); counting
// that as a request made the first test fail deterministically on a fresh
// database (it was mis-diagnosed as a CPU-timing flake in earlier reports --
// re-diagnosed in Phase G: 0 requests -> 1 placeholder row, 1 request -> 1 row).
const REQUEST_ROWS = "tbody tr:not(:has(td[colspan]))";

/** Wait until the request list has really settled. Waiting only for the
 * "Loading your requests…" text to be absent is satisfied instantly if the
 * query has not STARTED yet, so `before` could be read from an empty table
 * (Phase J: reproduced -- before=0, after=2 on an account that already had a
 * request). Require a rendered row (real or the empty-state placeholder)
 * and no loading row. */
async function listSettled(page: Page) {
  await expect(page.locator("tbody tr").first()).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("Loading your requests…")).toHaveCount(0, { timeout: 10_000 });
}

/**
 * Phase 19 -- blood-request creation idempotency, exercised through the
 * real UI/backend (section 19 explicitly lists "Playwright: request
 * creation retry" alongside the unit-level proof in
 * tests/test_phase19_blood_request_idempotency.py). This drives the same
 * BloodRequestModal a real patient uses, with a real double-click and a
 * real same-key resubmit-after-failure, and checks the *observable*
 * consequence -- exactly one new row appears in the requester's own
 * request list -- rather than reaching into the database.
 */
test.describe("Phase 19 E2E: blood-request idempotency through the real UI", () => {
  test("rapid double-click on Submit creates exactly one request, not two", async ({ page }) => {
    await loginAsUi(page, USERS.patient);
    // Let the list's own query settle before taking the "before" count.
    await listSettled(page);
    const before = await page.locator(REQUEST_ROWS).count();

    await page.getByRole("button", { name: "New Blood Request" }).first().click({ force: true });
    await page.getByLabel("Blood group").selectOption("AB_POS");
    const submit = page.getByRole("button", { name: "Submit request" });
    // Two real, near-simultaneous clicks on the same still-mounted button --
    // the disabled-while-pending state plus the `create.isPending` guard in
    // BloodRequestModal.submit() are what's supposed to collapse this into
    // a single mutation; if either one regressed, this would create two
    // rows below.
    // Phase J: a real double-click (two click events back to back on the
    // same element). The previous `Promise.all([click(), click()])` raced
    // Playwright's own actionability wait: when the first click disabled
    // the button first, the second click() waited for it to become enabled
    // again, then for a detached element, and timed out -- a test-harness
    // hang, not an application result.
    await submit.dblclick();
    await expect(submit).toHaveCount(0, { timeout: 10_000 });

    await page.waitForFunction(
      ([n, sel]) => document.querySelectorAll(sel).length > n,
      [before, REQUEST_ROWS] as const,
      { timeout: 10_000 },
    );
    const after = await page.locator(REQUEST_ROWS).count();
    expect(after).toBe(before + 1);
  });

  test("resubmitting after a failed attempt with the same form (same idempotency key) does not duplicate the request", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.patient);
    await listSettled(page);
    const before = await page.locator(REQUEST_ROWS).count();

    await page.getByRole("button", { name: "New Blood Request" }).first().click({ force: true });
    await page.getByLabel("Blood group").selectOption("B_NEG");

    // Simulate the response never reaching the client on the first attempt
    // (real request lost in transit) by aborting only the first POST, then
    // letting every subsequent one through -- this is the "client retries
    // after not seeing a response" scenario section 4's idempotency
    // contract requires, driven through the real form rather than a raw
    // API replay.
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

    // Same form, same idempotency key, sent again -- this time it reaches
    // the real backend, which must treat it as the *first* successful
    // creation of this key (the aborted attempt never reached the server
    // at all), not a duplicate.
    await submit.click();
    await expect(submit).toHaveCount(0, { timeout: 10_000 });

    await page.waitForFunction(
      ([n, sel]) => document.querySelectorAll(sel).length > n,
      [before, REQUEST_ROWS] as const,
      { timeout: 10_000 },
    );
    const after = await page.locator(REQUEST_ROWS).count();
    expect(after).toBe(before + 1);
  });

  test.afterEach(async ({ page }) => {
    await logout(page);
  });
});
