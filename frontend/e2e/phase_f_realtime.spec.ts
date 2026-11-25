/**
 * Phase F E2E -- live updates through the real SSE stream, two real
 * browser sessions, real backend (no mocked responses).
 *
 *  1. A patient watching their request history sees a hospital's
 *     allocation appear ("1/3 units fulfilled") without reloading. That page
 *     does not poll at all, so the update can only have come from the event
 *     stream.
 *  2. A donor sees the bell's unread count rise when a patient files a
 *     matching request, well inside the bell's 30 s fallback poll interval.
 */
import { expect, request as pwRequest, test } from "@playwright/test";

import { loginAsUi } from "./helpers";
import { API_BASE_URL, apiLogin, E2E_CITY, ensureDonorAndHospitalProfiles, USERS } from "./setup";

const ORIGIN = { Origin: "http://localhost:5173" };

/** The seeded *demo* hospital (scripts/seed_demo_data.py) is used as the
 * allocating facility so this spec never changes the shared e2e hospital's
 * profile or stock that other specs depend on. */
async function demoHospital() {
  const ctx = await pwRequest.newContext({ baseURL: API_BASE_URL });
  const res = await ctx.post("/auth/login", {
    data: { email: "demo.hospital@lifelink-demo.example.com", password: "LifeLink-Demo-2026!" },
    headers: ORIGIN,
  });
  expect(res.ok(), "seed_demo_data provides the demo hospital").toBeTruthy();
  return ctx;
}

test.beforeAll(async () => {
  await ensureDonorAndHospitalProfiles();
});

test("patient sees a hospital allocation live, without reloading", async ({ browser }) => {
  // Request filed in the hospital's own city so it can see/allocate it.
  const hospitalCtx = await demoHospital();
  const city = (await (await hospitalCtx.get("/hospitals/me")).json()).city as string;
  expect(city).toBeTruthy();
  await hospitalCtx.post("/hospitals/me/inventory/add", {
    data: { blood_type: "AB_POS", units: 3 }, headers: ORIGIN,
  });
  const patientCtx = await apiLogin(USERS.patient);
  const created = await patientCtx.post("/blood-requests", {
    data: { blood_type: "AB_POS", city, units_needed: 3, note: `live-${Date.now()}` },
    headers: ORIGIN,
  });
  expect(created.ok()).toBeTruthy();
  const rid = (await created.json()).id as string;
  await patientCtx.dispose();

  const context = await browser.newContext();
  const page = await context.newPage();
  await loginAsUi(page, USERS.patient);
  await page.goto("/patient/history");
  await expect(page.getByText("Loading your requests…")).not.toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("1/3 units fulfilled")).toHaveCount(0);

  const alloc = await hospitalCtx.post(`/hospitals/me/requests/${rid}/allocate`, {
    data: { blood_type: "AB_POS", units: 1 }, headers: ORIGIN,
  });
  expect(alloc.ok()).toBeTruthy();
  await hospitalCtx.dispose();

  await expect(page.getByText("1/3 units fulfilled").first()).toBeVisible({ timeout: 8_000 });
  await context.close();
});

test("donor's bell updates live when a matching request is filed", async ({ browser }) => {
  const donorContext = await browser.newContext();
  const donorPage = await donorContext.newPage();
  await loginAsUi(donorPage, USERS.donor);
  const bell = donorPage.getByRole("button", { name: "Notifications" }).first();
  await expect(bell).toBeVisible();
  // Live indicator is shown in the bell panel once the stream is connected.
  await bell.click();
  await expect(donorPage.getByTestId("realtime-status")).toHaveText(/Live/, { timeout: 10_000 });
  await bell.click();
  const before = Number((await bell.textContent())?.replace(/\D/g, "") || "0");

  const patientCtx = await apiLogin(USERS.patient);
  const r = await patientCtx.post("/blood-requests", {
    // Audit D4: alerts need a location in scope; the donor lives in E2E_CITY.
    data: { blood_type: "O_NEG", units_needed: 1, city: E2E_CITY, note: `live-bell-${Date.now()}` },
    headers: ORIGIN,
  });
  expect(r.ok()).toBeTruthy();
  await patientCtx.dispose();

  await expect
    .poll(async () => Number((await bell.textContent())?.replace(/\D/g, "") || "0"), { timeout: 8_000 })
    .toBeGreaterThan(before);
  await donorContext.close();
});
