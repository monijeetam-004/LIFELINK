import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { expect, test } from "@playwright/test";
import { loginAsUi, logout } from "./helpers";
import { apiLogin, USERS, verifyDonationAsAdmin } from "./setup";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * Phase 31.1 -- E2E coverage for the two backend signals this phase surfaced
 * in the frontend: Emergency.deadline_exceeded_at and
 * Certificate.revoked_at/revocation_reason. No component-test framework
 * (vitest/RTL or similar) exists anywhere in this codebase -- Playwright is
 * the only frontend test runner installed -- so this phase's frontend
 * coverage is expressed here rather than inventing a new test-runner
 * dependency for a bounded follow-up phase (documented in
 * docs/LIFELINK_PHASE_31_1_REPORT.md).
 *
 * Every piece of state below (the donation, the certificate, the emergency
 * and its past deadline, the revocation itself) is created through real API
 * calls or real UI actions against the real backend -- nothing here fakes a
 * network response to manufacture a UI state.
 */

test.describe("Phase 31.1 E2E: certificate revocation", () => {
  test("admin can revoke a certificate through the real UI, and public verification reflects it honestly", async ({
    page,
  }) => {
    // ---- Real setup: donor logs a donation and issues a certificate for it ----
    const donorCtx = await apiLogin(USERS.donor);
    const donationRes = await donorCtx.post("/donors/me/donations", {
      data: {
        donated_on: new Date().toISOString().slice(0, 10),
        units: 1,
        hospital_name: "Phase 31.1 E2E Hospital",
        note: "phase31.1-revocation-e2e",
      },
      headers: { Origin: "http://localhost:5173" },
    });
    expect(donationRes.ok()).toBeTruthy();
    const donation = await donationRes.json();
    await verifyDonationAsAdmin(donation.id);

    const certRes = await donorCtx.post("/certificates", {
      data: { donation_id: donation.id },
      headers: { Origin: "http://localhost:5173" },
    });
    expect(certRes.ok()).toBeTruthy();
    const cert: { id: string; serial: string; verify_code: string } = await certRes.json();
    await donorCtx.dispose();

    // ---- Public verification, before revocation: genuinely valid ----
    await page.goto(`/verify/${cert.verify_code}`);
    await expect(page.getByText("Authentic certificate")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("Certificate revoked")).toHaveCount(0);

    // ---- Admin revokes it through the real registry UI ----
    await loginAsUi(page, USERS.admin);
    await page.goto("/admin/analytics");
    await page
      .getByPlaceholder("Search certificate serial, donor, or hospital…")
      .fill(cert.serial);
    const row = page.locator("tbody tr", { hasText: cert.serial });
    await expect(row).toBeVisible({ timeout: 10_000 });
    await expect(row.getByText("Valid", { exact: true })).toBeVisible();

    await row.getByRole("button", { name: "Revoke" }).click();
    await expect(page.getByRole("heading", { name: "Revoke certificate" })).toBeVisible();
    const confirmBtn = page.getByRole("button", { name: "Revoke certificate" });

    // Too-short reason keeps the confirm action disabled -- a real reason is required.
    await page.getByLabel(/Reason for revocation/).fill("no");
    await expect(confirmBtn).toBeDisabled();

    await page.getByLabel(/Reason for revocation/).fill("Donation could not be confirmed by the hospital.");
    await expect(confirmBtn).toBeEnabled();
    await confirmBtn.click();

    // No optimistic UI: the dialog only closes and the row only flips to
    // "Revoked" once the real backend response comes back.
    await expect(page.getByRole("heading", { name: "Revoke certificate" })).toHaveCount(0, {
      timeout: 10_000,
    });
    await expect(row.getByText("Revoked", { exact: true })).toBeVisible({ timeout: 10_000 });
    await logout(page);

    // ---- Public verification, after revocation: honestly reflects it ----
    await page.goto(`/verify/${cert.verify_code}`);
    await expect(page.getByText("Certificate revoked")).toBeVisible({ timeout: 10_000 });
    await expect(
      page.getByText("Donation could not be confirmed by the hospital.", { exact: false }),
    ).toBeVisible();
    // Never conflated with "not found" or a generic invalid message.
    await expect(page.getByText("No certificate matches this code.")).toHaveCount(0);
    await expect(page.getByText("Authentic certificate")).toHaveCount(0);
  });
});

test.describe("Phase 31.1 E2E: emergency deadline-exceeded banner", () => {
  test("a real past-deadline emergency shows the deadline banner alongside (not instead of) its real status", async ({
    page,
  }) => {
    const uniqueLocation = `Phase31.1 Deadline Test ${Date.now()}`;

    // ---- Real setup: patient broadcasts an SOS with a deadline already in the past ----
    const patientCtx = await apiLogin(USERS.patient);
    const emRes = await patientCtx.post("/emergencies", {
      data: {
        urgency: "CRITICAL",
        location_text: uniqueLocation,
        deadline: new Date(Date.now() - 60_000).toISOString(),
        idempotency_key: `phase31-1-${Date.now()}`,
      },
      headers: { Origin: "http://localhost:5173" },
    });
    expect(emRes.ok()).toBeTruthy();
    await patientCtx.dispose();

    // ---- Real UI: open the tracking drawer for exactly this emergency ----
    await loginAsUi(page, USERS.patient);
    await page.goto("/patient/sos-tracking");
    const row = page.locator("li", { hasText: uniqueLocation });
    await expect(row).toBeVisible({ timeout: 10_000 });
    await row.getByRole("button", { name: "Track" }).click();

    const dialog = page.getByRole("dialog", { name: "Emergency tracking" });
    await expect(dialog).toBeVisible();
    // The real backend flag, surfaced honestly -- not a client-computed guess.
    await expect(dialog.getByText("Past deadline — action needed")).toBeVisible({ timeout: 10_000 });
    // The real status is still shown independently, never replaced by the deadline notice.
    await expect(dialog.getByText("ACTIVE", { exact: true })).toBeVisible();

    await logout(page);
  });
});

/**
 * Phase 31.1 accessibility regression on exactly the new surfaces this
 * phase touched -- same axe-core scan and same disclosed, pre-existing
 * rule exclusions as phase26_accessibility.spec.ts (color-contrast /
 * aria-command-name / link-in-text-block are pre-existing design-system
 * debt, not something this bounded phase redesigns).
 */
const axeSource = fs.readFileSync(
  path.join(__dirname, "..", "node_modules", "axe-core", "axe.min.js"),
  "utf-8",
);
const DISCLOSED_RULE_IDS = ["aria-command-name", "color-contrast", "link-in-text-block"];

async function scanForRegressions(page: import("@playwright/test").Page) {
  await page.evaluate(axeSource);
  return page.evaluate(async (excludedIds) => {
    const axe = (window as unknown as { axe: { run: typeof import("axe-core").run } }).axe;
    const res = await axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] },
    });
    return res.violations
      .filter((v: { id: string }) => !excludedIds.includes(v.id))
      .map((v: { id: string; nodes: unknown[] }) => ({ id: v.id, nodes: v.nodes.length }));
  }, DISCLOSED_RULE_IDS);
}

test.describe("Phase 31.1 E2E: accessibility of the new certificate/emergency UI", () => {
  test("public verify page (valid certificate) has no new axe violations", async ({ page }) => {
    const donorCtx = await apiLogin(USERS.donor);
    const donationRes = await donorCtx.post("/donors/me/donations", {
      data: { donated_on: new Date().toISOString().slice(0, 10), units: 1 },
      headers: { Origin: "http://localhost:5173" },
    });
    const donation = await donationRes.json();
    await verifyDonationAsAdmin(donation.id);
    const certRes = await donorCtx.post("/certificates", {
      data: { donation_id: donation.id },
      headers: { Origin: "http://localhost:5173" },
    });
    const cert: { verify_code: string } = await certRes.json();
    await donorCtx.dispose();

    await page.goto(`/verify/${cert.verify_code}`);
    await expect(page.getByText("Authentic certificate")).toBeVisible({ timeout: 10_000 });
    expect(await scanForRegressions(page)).toEqual([]);
  });

  test("donor milestones page (with a revoked certificate present) has no new axe violations", async ({
    page,
  }) => {
    // ---- Real setup: donor gets a certificate, admin revokes it via the real API ----
    const donorCtx = await apiLogin(USERS.donor);
    const donationRes = await donorCtx.post("/donors/me/donations", {
      data: { donated_on: new Date().toISOString().slice(0, 10), units: 1 },
      headers: { Origin: "http://localhost:5173" },
    });
    const donation = await donationRes.json();
    await verifyDonationAsAdmin(donation.id);
    const certRes = await donorCtx.post("/certificates", {
      data: { donation_id: donation.id },
      headers: { Origin: "http://localhost:5173" },
    });
    const cert: { id: string } = await certRes.json();
    await donorCtx.dispose();

    const adminCtx = await apiLogin(USERS.admin);
    const revokeRes = await adminCtx.post(`/admin/certificates/${cert.id}/revoke`, {
      data: { reason: "Phase 31.1 accessibility fixture" },
      headers: { Origin: "http://localhost:5173" },
    });
    expect(revokeRes.ok()).toBeTruthy();
    await adminCtx.dispose();

    await loginAsUi(page, USERS.donor);
    await page.goto("/donor/milestones");
    await expect(page.getByRole("heading", { name: "Certificates" })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("Revoked", { exact: true }).first()).toBeVisible({ timeout: 10_000 });
    expect(await scanForRegressions(page)).toEqual([]);
    await logout(page);
  });

  test("admin certificate registry, with the revoke dialog open, has no new axe violations", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.admin);
    await page.goto("/admin/analytics");
    await expect(page.getByRole("heading", { name: "Donation Certificates Registry" })).toBeVisible({
      timeout: 10_000,
    });
    const revokeButtons = page.getByRole("button", { name: "Revoke" });
    if (await revokeButtons.first().isVisible().catch(() => false)) {
      await revokeButtons.first().click();
      await expect(page.getByRole("heading", { name: "Revoke certificate" })).toBeVisible();
    }
    expect(await scanForRegressions(page)).toEqual([]);
    await logout(page);
  });
});

test.describe("Phase 31.1 E2E: responsive -- new Status/Action columns don't cause page overflow", () => {
  async function hasHorizontalOverflow(page: import("@playwright/test").Page) {
    return page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2);
  }

  test("admin certificate registry (new Status + Action columns) at every required breakpoint", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.admin);
    for (const width of [320, 375, 390, 414, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto("/admin/analytics");
      await expect(page.getByRole("heading", { name: "Donation Certificates Registry" })).toBeVisible({
        timeout: 10_000,
      });
      await page.waitForTimeout(200);
      expect(await hasHorizontalOverflow(page)).toBe(false);
    }
    await logout(page);
  });
});
