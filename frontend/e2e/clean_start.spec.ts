import { expect, test } from "@playwright/test";

/**
 * Clean-start verification (docs/final-verification/CLEAN_START_VERIFICATION.md):
 * the freshly built SPA, served from a fresh checkout, talks to the freshly
 * initialised API in a real browser. Skipped unless CLEAN_FRONTEND_URL is set.
 *
 *   CLEAN_FRONTEND_URL=http://127.0.0.1:4300 CLEAN_API_URL=http://127.0.0.1:8200 \
 *     npx playwright test e2e/clean_start.spec.ts
 */
const FRONT = process.env.CLEAN_FRONTEND_URL;
const API = process.env.CLEAN_API_URL ?? "";
const PASSWORD = "LifeLink-Demo-2026!"; // documented, non-secret demo seed password

test.skip(!FRONT, "CLEAN_FRONTEND_URL not set");

test("fresh SPA signs in against the fresh API and loads live hospital data", async ({ page }) => {
  const apiCalls: { url: string; status: number }[] = [];
  page.on("response", (r) => {
    if (API && r.url().startsWith(API)) apiCalls.push({ url: r.url().slice(API.length), status: r.status() });
  });
  await page.goto(`${FRONT}/login`);
  await page.getByPlaceholder("Email address").fill("demo.hospital@lifelink-demo.example.com");
  await page.getByPlaceholder("Password").fill(PASSWORD);
  await page.locator("form").getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/hospital$/, { timeout: 30_000 });
  const unitsResponse = page.waitForResponse((r) => r.url().startsWith(`${API}/hospitals/me/units`), {
    timeout: 30_000,
  });
  await page.goto(`${FRONT}/hospital/units`);
  const units = await unitsResponse;
  expect(units.status()).toBe(200);
  const rows = (await units.json()) as { unit_code: string }[];
  expect(rows.length).toBeGreaterThan(0);
  // the first unit the API returned is rendered in the table
  await expect(page.getByRole("cell", { name: rows[0].unit_code, exact: true })).toBeVisible({ timeout: 30_000 });
  const login = apiCalls.find((c) => c.url.startsWith("/auth/login"));
  expect(login?.status).toBe(200);
  console.log(`CLEAN_START_BROWSER units_returned=${rows.length} api_calls=${JSON.stringify(apiCalls.slice(0, 12))}`);
});
