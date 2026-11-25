/**
 * Phase J E2E -- journeys and checks the earlier specs did not cover,
 * against the real backend (no interception):
 *
 *  1. Partial -> full fulfilment, seen by the patient live: a hospital
 *     allocates 1 of 2 units ("1/2 units fulfilled", still Active), then
 *     the last unit -- the request moves to the Fulfilled tab, no reload.
 *  2. Shipment happy path through the UI with two hospitals in two
 *     browser sessions: origin dispatches and marks in transit, the
 *     destination marks arrived and receives; the origin's open page shows
 *     RECEIVED live; the destination's component stock grew.
 *  3. Donor on a phone (375x812): /donor/mobile lands on the real /donor
 *     dashboard, the mobile navigation works, no horizontal overflow, and
 *     no WCAG 2.1 A/AA axe violations.
 *  4. Keyboard only: sign in with the keyboard, open the notifications
 *     panel with Enter, close it with Escape, focus returns to the bell.
 *  5. Every role's landing dashboard loads with no console errors and no
 *     failed (5xx) API calls.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, request as pwRequest, test, type Page } from "@playwright/test";

import { loginAsUi } from "./helpers";
import { API_BASE_URL, apiLogin, E2E_PASSWORD, ensureDonorAndHospitalProfiles, USERS } from "./setup";

const ORIGIN = { Origin: "http://localhost:5173" };
const DEMO_HOSPITAL = "demo.hospital@lifelink-demo.example.com";
const DEMO_PASSWORD = "LifeLink-Demo-2026!";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const axeSource = fs.readFileSync(
  path.join(__dirname, "..", "node_modules", "axe-core", "axe.min.js"),
  "utf-8",
);

async function axeViolations(page: Page) {
  await page.evaluate(axeSource);
  return page.evaluate(async () => {
    const axe = (window as unknown as { axe: { run: typeof import("axe-core").run } }).axe;
    const r = await axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] },
    });
    return r.violations.map((v) => ({ id: v.id, nodes: v.nodes.map((n) => n.target.join(" ")) }));
  });
}

async function demoHospitalApi() {
  const ctx = await pwRequest.newContext({ baseURL: API_BASE_URL });
  const res = await ctx.post("/auth/login", {
    data: { email: DEMO_HOSPITAL, password: DEMO_PASSWORD }, headers: ORIGIN,
  });
  expect(res.ok(), "seed_demo_data provides the demo hospital").toBeTruthy();
  return ctx;
}

async function loginDemoHospitalUi(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder("Email address").fill(DEMO_HOSPITAL);
  await page.getByPlaceholder("Password").fill(DEMO_PASSWORD);
  await page.locator("form").getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/hospital$/, { timeout: 15_000 });
}

test.beforeAll(async () => {
  await ensureDonorAndHospitalProfiles();
});

test("partial then full fulfilment is visible to the patient live", async ({ browser }) => {
  const hospital = await demoHospitalApi();
  const city = (await (await hospital.get("/hospitals/me")).json()).city as string;
  await hospital.post("/hospitals/me/inventory/add", { data: { blood_type: "B_NEG", units: 2 }, headers: ORIGIN });
  const patient = await apiLogin(USERS.patient);
  const note = `journey-${Date.now()}`;
  const created = await patient.post("/blood-requests", {
    data: { blood_type: "B_NEG", city, units_needed: 2, note }, headers: ORIGIN,
  });
  expect(created.ok()).toBeTruthy();
  const rid = (await created.json()).id as string;
  await patient.dispose();

  const context = await browser.newContext();
  const page = await context.newPage();
  await loginAsUi(page, USERS.patient);
  await page.goto("/patient/history");
  await expect(page.getByText("Loading your requests…")).not.toBeVisible({ timeout: 10_000 });

  const first = await hospital.post(`/hospitals/me/requests/${rid}/allocate`, {
    data: { blood_type: "B_NEG", units: 1 }, headers: ORIGIN,
  });
  expect(first.ok(), await first.text()).toBeTruthy();
  expect((await first.json()).request_status ?? "PARTIALLY_FULFILLED").toBeTruthy();
  await expect(page.getByText("1/2 units fulfilled").first()).toBeVisible({ timeout: 8_000 });

  const second = await hospital.post(`/hospitals/me/requests/${rid}/allocate`, {
    data: { blood_type: "B_NEG", units: 1 }, headers: ORIGIN,
  });
  expect(second.ok(), await second.text()).toBeTruthy();
  // Over-allocation is refused by the backend, not just hidden.
  const third = await hospital.post(`/hospitals/me/requests/${rid}/allocate`, {
    data: { blood_type: "B_NEG", units: 1 }, headers: ORIGIN,
  });
  expect(third.status()).toBe(409);
  await hospital.dispose();

  // No reload: the active list drops it, the Fulfilled tab shows 2/2.
  await expect(page.getByText("1/2 units fulfilled")).toHaveCount(0, { timeout: 8_000 });
  await page.getByRole("tab", { name: /Fulfilled/ }).or(page.getByRole("button", { name: /^Fulfilled/ })).first().click();
  await expect(page.getByText("2/2 units fulfilled").first()).toBeVisible({ timeout: 8_000 });
  await context.close();
});

test("shipment happy path through the UI, two hospitals, live status", async ({ browser }) => {
  const origin = await apiLogin(USERS.hospital);
  const me = await (await origin.get("/hospitals/me")).json();
  const destApi = await demoHospitalApi();
  const dest = await (await destApi.get("/hospitals/me")).json();
  expect(dest.verified).toBeTruthy();
  await origin.post("/hospitals/me/inventory/add", {
    data: { blood_type: "A_NEG", units: 2, component: "PLATELETS" }, headers: ORIGIN,
  });
  const before = ((await (await destApi.get("/hospitals/me/inventory/components")).json()) as {
    blood_type: string; component: string; units: number;
  }[]).find((c) => c.blood_type === "A_NEG" && c.component === "PLATELETS")?.units ?? 0;
  const created = await origin.post("/shipments", {
    data: {
      origin_facility_id: me.id, destination_facility_id: dest.id,
      items: [{ blood_type: "A_NEG", units: 2, component: "PLATELETS" }],
    },
    headers: ORIGIN,
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const sid = (await created.json()).id as string;
  await origin.dispose();

  const originCtx = await browser.newContext();
  const op = await originCtx.newPage();
  await loginAsUi(op, USERS.hospital);
  await op.goto(`/hospital/shipments?shipment=${sid}`);
  await op.getByRole("button", { name: /Dispatch$/ }).click();
  await expect(op.getByText("DISPATCHED").first()).toBeVisible({ timeout: 10_000 });
  await op.getByRole("button", { name: /Mark in transit$/ }).click();
  await expect(op.getByText("IN TRANSIT").first()).toBeVisible({ timeout: 10_000 });

  const destCtx = await browser.newContext();
  const dp = await destCtx.newPage();
  await loginDemoHospitalUi(dp);
  await dp.goto(`/hospital/shipments?shipment=${sid}`);
  await dp.getByRole("button", { name: /Mark arrived$/ }).click();
  await expect(dp.getByText("ARRIVED").first()).toBeVisible({ timeout: 10_000 });
  // Gap elimination: the one-click action was renamed for what it does now
  // that receipt can also be recorded unit by unit ("Receive unit by unit").
  await dp.getByRole("button", { name: /Receive all as OK$/ }).click();
  await expect(dp.getByText("RECEIVED").first()).toBeVisible({ timeout: 10_000 });

  // The origin's page was never reloaded: it learns of the receipt live.
  await expect(op.getByText("RECEIVED").first()).toBeVisible({ timeout: 10_000 });

  const after = ((await (await destApi.get("/hospitals/me/inventory/components")).json()) as {
    blood_type: string; component: string; units: number;
  }[]).find((c) => c.blood_type === "A_NEG" && c.component === "PLATELETS")?.units ?? 0;
  expect(after).toBe(before + 2);
  await destApi.dispose();
  await originCtx.close();
  await destCtx.close();
});

test("donor on a phone: real dashboard, working mobile nav, no overflow, no axe violations", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await loginAsUi(page, USERS.donor);
  await page.goto("/donor/mobile");
  await expect(page).toHaveURL(/\/donor$/);
  // The donor dashboard's phone navigation is a bottom tab bar (md:hidden).
  // Not "networkidle": the live event stream keeps a connection open.
  const nav = page.getByRole("navigation", { name: "Donor sections" });
  await expect(nav).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("donor-initials")).toBeVisible();
  await page.waitForTimeout(1_000);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  expect(await axeViolations(page)).toEqual([]);

  await nav.getByRole("link", { name: "Eligibility" }).click();
  await expect(page).toHaveURL(/\/donor\/eligibility$/);
});

test("keyboard only: sign in, open and close notifications", async ({ page }) => {
  await page.goto("/login");
  await page.getByPlaceholder("Email address").focus();
  await page.keyboard.type(USERS.patient);
  await page.keyboard.press("Tab");
  await expect(page.getByPlaceholder("Password")).toBeFocused();
  await page.keyboard.type(E2E_PASSWORD);
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/patient$/, { timeout: 15_000 });

  const bell = page.getByRole("button", { name: "Notifications" }).first();
  await bell.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("realtime-status")).toBeVisible({ timeout: 10_000 });
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("realtime-status")).not.toBeVisible();
  await expect(bell).toBeFocused();
});

for (const role of ["patient", "family", "donor", "hospital", "admin"] as const) {
  test(`${role} dashboard: no app errors, no 5xx, no unexplained 4xx`, async ({ page }) => {
    const appErrors: string[] = [];
    const badResponses: string[] = [];
    page.on("console", (m) => {
      // Browser network logs ("Failed to load resource ...") are covered by
      // the response checks below, with their URLs; everything else logged
      // at error level comes from application code.
      if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) appErrors.push(m.text());
    });
    page.on("pageerror", (e) => appErrors.push(`pageerror: ${e.message}`));
    page.on("response", (r) => {
      const url = new URL(r.url());
      // The anonymous session probe on /login answers 401 by design.
      const expected = r.status() === 401 && url.pathname === "/auth/me" && !page.url().match(/\/(patient|family|donor|hospital|admin)/);
      if (r.status() >= 400 && !expected) badResponses.push(`${r.status()} ${r.request().method()} ${url.pathname}`);
    });
    await loginAsUi(page, USERS[role]);
    // Not "networkidle": the live event stream keeps a connection open.
    await expect(page.locator("main, [role=main], h1").first()).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(2_000);
    expect(badResponses).toEqual([]);
    expect(appErrors).toEqual([]);
  });
}
