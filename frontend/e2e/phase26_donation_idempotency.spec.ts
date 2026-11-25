import { expect, test } from "@playwright/test";
import { loginAsUi, logout } from "./helpers";
import { USERS } from "./setup";

/**
 * Phase 26 -- donation-logging idempotency and UI completeness, exercised
 * through the real UI/backend. `POST /donors/me/donations` existed on the
 * backend since Phase 5/6 but had no frontend entry point at all until this
 * phase (LogDonationModal.tsx, wired into MilestonesHistory.tsx) -- see
 * LIFELINK_PHASE_26_DONATION_FRONTEND_AUDIT.md. This drives the real modal
 * a donor uses, with a real double-click and a real same-key
 * resubmit-after-failure, and checks the *observable* consequence -- the
 * donor's own donation history -- rather than reaching into the database
 * (that proof already lives in
 * tests/test_phase26_donation_idempotency.py).
 */
test.describe("Phase 26 E2E: donation-logging idempotency through the real UI", () => {
  async function donationRowCount(page: import("@playwright/test").Page): Promise<number> {
    const empty = page.getByText("No donations on record yet.");
    if (await empty.isVisible().catch(() => false)) return 0;
    return page.locator("table tbody tr").count();
  }

  /** Open the milestones page and wait until the donor's donation list has
   * really been fetched AND rendered, so "before" counts are never taken
   * from a page that is still loading (final verification: the retry test
   * read before=0 while 3 rows were still arriving, then saw 4). */
  async function openMilestonesSettled(page: import("@playwright/test").Page): Promise<void> {
    const listed = page.waitForResponse(
      (r) => r.request().method() === "GET" && /\/donors\/me\/donations(\?|$)/.test(r.url()),
      { timeout: 15_000 },
    );
    await page.goto("/donor/milestones");
    const res = await listed;
    const n = ((await res.json()) as unknown[]).length;
    if (n === 0) await expect(page.getByText("No donations on record yet.")).toBeVisible({ timeout: 10_000 });
    else await expect(page.locator("table tbody tr")).toHaveCount(n, { timeout: 10_000 });
  }

  test("rapid double-click on Log donation creates exactly one donation, not two", async ({ page }) => {
    await loginAsUi(page, USERS.donor);
    await openMilestonesSettled(page);
    await expect(page.getByRole("button", { name: "Log a donation" })).toBeVisible({ timeout: 10_000 });
    const before = await donationRowCount(page);

    // The modal's own "accepted requests" dropdown loads asynchronously and
    // can shift the form's layout (a select appearing above the date/units
    // fields) shortly after the modal opens -- wait for that specific
    // response before the double-click race below, so the race is testing
    // the submit guard, not accidentally racing an unrelated layout shift.
    // (Deliberately not `waitForLoadState("networkidle")`: this sandbox's
    // egress proxy rejects Chromium's own background requests to Google
    // domains rather than resolving them, which can leave the page never
    // reporting "idle".)
    const responsesLoaded = page.waitForResponse(
      (r) => r.url().includes("/donors/me/responses"),
      { timeout: 10_000 },
    );
    await page.getByRole("button", { name: "Log a donation" }).click();
    await expect(page.getByRole("heading", { name: "Log a donation" })).toBeVisible();
    await responsesLoaded.catch(() => {});
    const submit = page.getByRole("button", { name: "Log donation" });
    await expect(submit).toBeEnabled();
    // Two real, back-to-back clicks on the same button -- dispatched
    // synchronously in one page.evaluate() (rather than Playwright's
    // `Promise.all([locator.click(), locator.click()])`, which gives each
    // click its own actionability-polling loop and is flaky here: donation
    // creation has fewer side effects than a blood request and can resolve
    // fast enough against this sandbox's local backend that the modal
    // closes mid-poll, detaching the button between the two clicks). The
    // disabled-while-pending state plus the `create.isPending` guard in
    // LogDonationModal.submit() are what's supposed to collapse two real
    // click events into a single mutation; if either one regressed, this
    // would create two rows.
    await submit.evaluate((btn: HTMLButtonElement) => {
      btn.click();
      btn.click();
    });
    await expect(submit).toHaveCount(0, { timeout: 10_000 });

    await page.waitForFunction(
      (n) => {
        const empty = document.body.textContent?.includes("No donations on record yet.");
        const rows = document.querySelectorAll("table tbody tr").length;
        return empty ? false : rows > n;
      },
      before,
      { timeout: 10_000 },
    );
    const after = await donationRowCount(page);
    expect(after).toBe(before + 1);
    await logout(page);
  });

  test("resubmitting after a failed attempt with the same form (same idempotency key) does not duplicate the donation", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.donor);
    await openMilestonesSettled(page);
    await expect(page.getByRole("button", { name: "Log a donation" })).toBeVisible({ timeout: 10_000 });
    const before = await donationRowCount(page);

    await page.getByRole("button", { name: "Log a donation" }).click();
    await page.getByLabel("Hospital (optional)").fill("Phase 26 Retry Hospital");

    // Simulate the response never reaching the client on the first attempt
    // (real request lost in transit) by aborting only the first POST, then
    // letting every subsequent one through -- the "client retries after not
    // seeing a response" scenario, driven through the real form rather than
    // a raw API replay.
    let seen = 0;
    await page.route("**/donors/me/donations", (route) => {
      if (route.request().method() === "POST" && seen === 0) {
        seen += 1;
        return route.abort("failed");
      }
      return route.continue();
    });

    const submit = page.getByRole("button", { name: "Log donation" });
    await submit.click();
    await expect(
      page.getByText("Can't reach the server. Check your connection and try again."),
    ).toBeVisible({ timeout: 10_000 });

    // Same form, same idempotency key, sent again -- this time it reaches
    // the real backend, which must treat it as the *first* successful
    // creation of this key (the aborted attempt never reached the server at
    // all), not a duplicate.
    await submit.click();
    await expect(submit).toHaveCount(0, { timeout: 10_000 });

    await page.unroute("**/donors/me/donations");
    await page.waitForFunction(
      (n) => {
        const empty = document.body.textContent?.includes("No donations on record yet.");
        const rows = document.querySelectorAll("table tbody tr").length;
        return empty ? false : rows > n;
      },
      before,
      { timeout: 10_000 },
    );
    const after = await donationRowCount(page);
    expect(after).toBe(before + 1);
    await logout(page);
  });

  test("a genuine server error while logging a donation shows an honest error, not a false success", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.donor);
    await page.goto("/donor/milestones");
    await expect(page.getByRole("button", { name: "Log a donation" })).toBeVisible({ timeout: 10_000 });

    await page.route("**/donors/me/donations", (route) => {
      if (route.request().method() === "POST") {
        return route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Internal Server Error" }),
        });
      }
      return route.continue();
    });

    await page.getByRole("button", { name: "Log a donation" }).click();
    await page.getByRole("button", { name: "Log donation" }).click();
    await expect(page.getByRole("alert")).toBeVisible({ timeout: 10_000 });
    // The modal stays open on a genuine failure -- no false "success" close.
    await expect(page.getByRole("heading", { name: "Log a donation" })).toBeVisible();

    await page.unroute("**/donors/me/donations");
    await logout(page);
  });
});
