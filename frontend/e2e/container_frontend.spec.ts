import { expect, test } from "@playwright/test";

/**
 * Final external validation: the CONTAINERISED frontend (nginx serving the
 * production build from frontend/Dockerfile) against the containerised API.
 * Skipped unless CONTAINER_FRONTEND_URL is set.
 *
 *   CONTAINER_FRONTEND_URL=http://localhost:5173 CONTAINER_API_URL=http://localhost:8000 \
 *     npx playwright test e2e/container_frontend.spec.ts
 *
 * Checks: every static asset loads (no 4xx/5xx), no uncaught page errors or
 * console errors, SPA deep-link fallback, sign-in through the real form, and
 * a live workflow: the hospital runs the real pathway planner from the UI and
 * sees the engine's answer, then opens its blood-unit list.
 */
const FRONT = process.env.CONTAINER_FRONTEND_URL;
const API = process.env.CONTAINER_API_URL ?? "http://localhost:8000";
const PASSWORD = "LifeLink-Demo-2026!"; // documented, non-secret demo seed password

test.skip(!FRONT, "CONTAINER_FRONTEND_URL not set");

test("containerised SPA: assets, deep links, sign-in and the live planner", async ({ page }) => {
  const badAssets: string[] = [];
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("response", (r) => {
    if (r.url().startsWith(FRONT!) && r.status() >= 400) badAssets.push(`${r.status()} ${r.url()}`);
  });
  page.on("pageerror", (e) => pageErrors.push(e.message));
  page.on("console", (m) => {
    // map tiles are blocked by this sandbox's egress policy; the app shows its
    // own fallback banner for that, so tile fetch failures are not app errors
    if (m.type() === "error" && !/tile\.openstreetmap|ERR_TUNNEL|net::ERR/.test(m.text())) consoleErrors.push(m.text());
  });

  // deep link straight to a client-side route: nginx must fall back to index.html
  const deep = await page.goto(`${FRONT}/hospital/units`);
  expect(deep?.status()).toBe(200);

  await page.goto(`${FRONT}/login`);
  await page.getByPlaceholder("Email address").fill("demo.hospital@lifelink-demo.example.com");
  await page.getByPlaceholder("Password").fill(PASSWORD);
  const login = page.waitForResponse((r) => r.url() === `${API}/auth/login`);
  await page.locator("form").getByRole("button", { name: "Sign in" }).click();
  expect((await login).status()).toBe(200);
  await page.waitForURL(/\/hospital$/, { timeout: 30_000 });

  await page.goto(`${FRONT}/graph-engine`);
  await expect(page.getByRole("heading", { name: "Graph Decision Engine" })).toBeVisible({ timeout: 30_000 });
  const planned = page.waitForResponse((r) => r.url() === `${API}/pathways/simulate`, { timeout: 30_000 });
  await page.getByRole("button", { name: "Run planner" }).click();
  expect((await planned).status()).toBe(200);
  await expect(page.getByRole("heading", { name: "Primary plan" })).toBeVisible({ timeout: 30_000 });

  const units = page.waitForResponse((r) => r.url().startsWith(`${API}/hospitals/me/units`), { timeout: 30_000 });
  await page.goto(`${FRONT}/hospital/units`);
  expect((await units).status()).toBe(200);
  await expect(page.getByRole("table")).toBeVisible({ timeout: 30_000 });

  expect(badAssets, "frontend assets that failed to load").toEqual([]);
  expect(pageErrors, "uncaught page errors").toEqual([]);
  expect(consoleErrors, "console errors").toEqual([]);
  console.log(`CONTAINER_FRONTEND_OK assets_ok pageErrors=0 consoleErrors=0`);
});
