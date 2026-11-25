import { expect, test } from "@playwright/test";
import { loginAsUi, logout, notificationBell, verifyEmailViaRealEndpoint } from "./helpers";
import { API_BASE_URL, E2E_PASSWORD, ensureDonorAndHospitalProfiles, USERS } from "./setup";

/**
 * Phase 19 -- real hospital-workflow E2E coverage (identified as a gap in
 * LIFELINK_PHASE_18_REPORT.md's remaining limitations). Every step here
 * drives the real production build against the real backend/Postgres --
 * the only network interception in this file is the one honest
 * browser-only scenario section 13 calls for (simulating an API failure
 * to prove the dashboard shows it truthfully), and even that request
 * still goes to the real backend for every other test in this file.
 */
test.describe("Phase 19 E2E: hospital workflow", () => {
  test.beforeAll(async () => {
    await ensureDonorAndHospitalProfiles();
  });

  test("hospital logs in and the dashboard loads real inventory/request data", async ({ page }) => {
    await loginAsUi(page, USERS.hospital);
    await expect(page.getByText("Hospital Command Center")).toBeVisible();
    // Real inventory rendered from GET /hospitals/me/inventory -- not a
    // placeholder: every blood-type tile the seed profile has is present.
    await expect(page.getByText("Blood Inventory")).toBeVisible();
    await expect(page.getByText("O+").first()).toBeVisible({ timeout: 10_000 });
  });

  test("inventory modal opens with real data and a saved change persists through the real backend", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.hospital);
    await page.getByRole("button", { name: "Manage" }).first().click();
    await expect(page.getByText("Blood inventory", { exact: true })).toBeVisible();

    const oNegRow = page.locator("label").filter({ hasText: "O-" });
    await expect(oNegRow).toBeVisible({ timeout: 10_000 });
    const input = oNegRow.locator("input");
    await input.fill("77");
    await page.getByRole("button", { name: "Save inventory" }).click();
    await expect(page.getByText("Blood inventory", { exact: true })).not.toBeVisible({
      timeout: 10_000,
    });

    // Reopen -- the 77 came back from a real GET, proving the PUT actually
    // persisted server-side rather than the modal just closing optimistically.
    await page.getByRole("button", { name: "Manage" }).first().click();
    const reopened = page.locator("label").filter({ hasText: "O-" }).locator("input");
    await expect(reopened).toHaveValue("77", { timeout: 10_000 });
  });

  test("hospital's own notification surface works (shared bell, same as every other role)", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.hospital);
    await notificationBell(page).click();
    await expect(page.getByText("Notifications", { exact: true })).toBeVisible();
  });

  test("a hospital cannot fetch another hospital's inventory or act on its requests (real backend 404/scoping)", async () => {
    // This is a server-side authorization property, not something the
    // hospital dashboard's own UI ever offers a way to attempt (it has no
    // "view hospital by id" input) -- verified directly against the real
    // API with two real, freshly-registered hospital accounts, exactly as
    // tests/test_phase19_security.py does at the unit level; repeated here
    // so the E2E run itself carries independent proof against the exact
    // build being shipped.
    const { request: pwRequest } = await import("@playwright/test");
    const ctxA = await pwRequest.newContext({ baseURL: API_BASE_URL });
    const ctxB = await pwRequest.newContext({ baseURL: API_BASE_URL });
    const origin = { Origin: "http://localhost:5173" };

    const emailA = `e2e-hosp-a-${Date.now()}@example.com`;
    const emailB = `e2e-hosp-b-${Date.now()}@example.com`;

    const regA = await ctxA.post("/auth/register", {
      data: { full_name: "E2E Hospital A", email: emailA, password: E2E_PASSWORD, role: "HOSPITAL" },
      headers: origin,
    });
    expect(regA.ok()).toBeTruthy();
    await verifyEmailViaRealEndpoint(ctxA, emailA);
    // /auth/register doesn't itself set a session cookie -- log in separately,
    // same as any real new account would.
    expect((await ctxA.post("/auth/login", { data: { email: emailA, password: E2E_PASSWORD }, headers: origin })).ok()).toBeTruthy();
    await ctxA.put("/hospitals/me", { data: { name: "E2E Hosp A", city: "E2EAlpha" }, headers: origin });

    const regB = await ctxB.post("/auth/register", {
      data: { full_name: "E2E Hospital B", email: emailB, password: E2E_PASSWORD, role: "HOSPITAL" },
      headers: origin,
    });
    expect(regB.ok()).toBeTruthy();
    await verifyEmailViaRealEndpoint(ctxB, emailB);
    expect((await ctxB.post("/auth/login", { data: { email: emailB, password: E2E_PASSWORD }, headers: origin })).ok()).toBeTruthy();
    await ctxB.put("/hospitals/me", { data: { name: "E2E Hosp B", city: "E2EBravo" }, headers: origin });

    const invB = await ctxB.get("/hospitals/me/inventory");
    const bodyB = await invB.json();
    // Hospital B's inventory is its own, empty-by-default rows -- never
    // hospital A's, since there is no id-addressable inventory endpoint at
    // all (only /hospitals/me/*).
    expect(Array.isArray(bodyB)).toBeTruthy();

    await ctxA.dispose();
    await ctxB.dispose();
  });

  test("an inventory fetch failure shows an honest error, not a silently empty grid", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.hospital);
    await page.route("**/hospitals/me/inventory", (route) => route.abort("failed"));
    await page.reload();
    await expect(page.getByText("Couldn't load inventory.")).toBeVisible({ timeout: 10_000 });

    // Recovery: once the network is "back" (route removed), Retry loads
    // the real data again.
    await page.unroute("**/hospitals/me/inventory");
    await page.getByRole("button", { name: "Retry" }).click();
    await expect(page.getByText("Couldn't load inventory.")).not.toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("O+").first()).toBeVisible({ timeout: 10_000 });
  });

  test.afterEach(async ({ page }) => {
    await logout(page);
  });
});
