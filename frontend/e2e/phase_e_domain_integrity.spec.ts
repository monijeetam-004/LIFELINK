/**
 * Phase E E2E -- the new domain states through the real UI against the real
 * backend (no mocked responses):
 *
 *  1. A self-reported donation is shown as such, offers no certificate,
 *     is verified by the linked hospital in its review queue, and only then
 *     lets the donor get a certificate.
 *  2. A failed shipment shows "Reconciliation required" and the origin
 *     records an attested outcome through the reconcile panel; the create
 *     form sends the chosen blood component.
 *  3. An admin runs the tamper-evident audit-chain verification and sees an
 *     honest result -- explicitly "tamper-evident", never claimed immutable.
 *
 * API calls are used only to create starting state as a role other than the
 * one driving the browser, exactly like the Phase 31.1 spec.
 */
import { expect, test } from "@playwright/test";

import { loginAsUi, logout } from "./helpers";
import { apiLogin, ensureDonorAndHospitalProfiles, USERS } from "./setup";

const ORIGIN = { Origin: "http://localhost:5173" };
const PAST_DONATION_DATE = new Date(Date.now() - 200 * 24 * 3600 * 1000).toISOString().slice(0, 10);

test.beforeAll(async () => {
  await ensureDonorAndHospitalProfiles();
});

test("donation verification gates the certificate end to end", async ({ page }) => {
  const hospitalCtx = await apiLogin(USERS.hospital);
  const hospitalId = (await (await hospitalCtx.get("/hospitals/me")).json()).id as string;
  await hospitalCtx.dispose();

  const donorCtx = await apiLogin(USERS.donor);
  const note = `phase-e-${Date.now()}`;
  const res = await donorCtx.post("/donors/me/donations", {
    // Dated well outside the donation-interval window so this spec never
    // changes the shared e2e donor's eligibility for specs that run after it.
    data: { donated_on: PAST_DONATION_DATE, units: 1, hospital_id: hospitalId, note },
    headers: ORIGIN,
  });
  expect(res.ok()).toBeTruthy();
  await donorCtx.dispose();

  // Donor: self-reported, no certificate offered.
  await loginAsUi(page, USERS.donor);
  await page.goto("/donor/milestones");
  const row = page.locator("tr", { hasText: note });
  await expect(row).toBeVisible({ timeout: 15_000 });
  await expect(row.getByTestId("donation-verification-badge")).toHaveText(/Self-reported/);
  await expect(row.getByRole("button", { name: "Get certificate" })).toHaveCount(0);
  await logout(page);

  // Hospital verifies it from its own queue.
  await loginAsUi(page, USERS.hospital);
  await page.goto("/hospital/donation-reviews");
  const item = page.getByTestId("donation-review-row").filter({ hasText: note });
  await expect(item).toBeVisible({ timeout: 15_000 });
  await item.getByLabel("Review note").fill("Matched blood-bank register");
  await item.getByRole("button", { name: "Verify" }).click();
  await expect(item).toHaveCount(0, { timeout: 15_000 });
  await logout(page);

  // Donor: now verified, certificate available.
  await loginAsUi(page, USERS.donor);
  await page.goto("/donor/milestones");
  const verifiedRow = page.locator("tr", { hasText: note });
  await expect(verifiedRow.getByTestId("donation-verification-badge")).toHaveText(/Verified by facility/, {
    timeout: 15_000,
  });
  await verifiedRow.getByRole("button", { name: "Get certificate" }).click();
  await page.waitForURL(/\/certificate\//, { timeout: 15_000 });
});

test("failed shipment requires an explicit, attested reconciliation", async ({ page }) => {
  const hospitalCtx = await apiLogin(USERS.hospital);
  const me = await (await hospitalCtx.get("/hospitals/me")).json();
  const directory = (await (await hospitalCtx.get("/hospitals/directory")).json()) as {
    id: string; verified: boolean;
  }[];
  const destination = directory.find((h) => h.verified && h.id !== me.id);
  expect(destination, "seed data provides a verified destination facility").toBeTruthy();
  await hospitalCtx.post("/hospitals/me/inventory/add", {
    data: { blood_type: "O_NEG", units: 2, component: "PLASMA" }, headers: ORIGIN,
  });
  const created = await hospitalCtx.post("/shipments", {
    data: {
      origin_facility_id: me.id, destination_facility_id: destination!.id,
      items: [{ blood_type: "O_NEG", units: 1, component: "PLASMA" }],
    },
    headers: ORIGIN,
  });
  expect(created.ok()).toBeTruthy();
  const shipment = await created.json();
  expect(shipment.items[0].component).toBe("PLASMA");
  await hospitalCtx.post(`/shipments/${shipment.id}/dispatch`, { data: {}, headers: ORIGIN });
  await hospitalCtx.post(`/shipments/${shipment.id}/fail`, { data: { reason: "E2E breakdown" }, headers: ORIGIN });
  await hospitalCtx.dispose();

  await loginAsUi(page, USERS.hospital);
  await page.goto(`/hospital/shipments?shipment=${shipment.id}`);
  await expect(page.getByTestId("reconciliation-status")).toContainText("Reconciliation required", {
    timeout: 15_000,
  });
  await expect(page.getByText(/Plasma/).first()).toBeVisible();

  const panel = page.getByTestId("reconcile-panel");
  await panel.getByLabel(/Quarantined/).check();
  await panel.getByRole("textbox").fill("Cooler breached; bags destroyed on site");
  await panel.getByRole("button", { name: "Record reconciliation" }).click();
  await expect(page.getByTestId("reconciliation-status")).toContainText("Quarantined", { timeout: 15_000 });
  await expect(page.getByTestId("reconcile-panel")).toHaveCount(0);
});

test("admin audit-chain verification reports an honest result", async ({ page }) => {
  await loginAsUi(page, USERS.admin);
  await page.goto("/admin/audit-feed");
  const panel = page.getByTestId("audit-chain-panel");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await panel.getByRole("button", { name: "Verify chain" }).click();
  await expect(panel.getByRole("status")).toContainText("No evidence of tampering", { timeout: 20_000 });
  await expect(panel).toContainText("does not make the log immutable");
});
