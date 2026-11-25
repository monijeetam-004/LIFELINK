import { expect, test } from "@playwright/test";
import { loginAsUi, logout } from "./helpers";
import { USERS } from "./setup";

/**
 * Phase 22 -- Responsive Design + Accessibility + Mobile UX Hardening.
 *
 * REAL-BACKEND E2E scenarios (no network interception) confirming the two
 * concrete, highest-severity bugs this phase found and fixed (see
 * LIFELINK_PHASE_22_AUDIT.md / LIFELINK_PHASE_22_REPORT.md):
 *
 *  A/B/C. 16 hand-rolled dashboard pages (all 8 Admin screens, Patient
 *     Overview, Family Dashboard, and 4 patient sub-pages) had a
 *     `hidden lg:flex` / `hidden md:flex` desktop-only sidebar and NO mobile
 *     equivalent at all -- a real phone user landing on any of them had no
 *     way to reach any other page in the app. Confirms, at a real mobile
 *     viewport, that a hamburger button is now visible, opens a working
 *     navigation drawer with real links, Escape closes it, and a link
 *     navigates to the right page. One test per portal family (Admin,
 *     Patient, Family) rather than all 14 fixed pages, since all pages
 *     within a family share the exact same fix.
 *  D. The shared `Modal` component (BloodRequestModal, SosModal,
 *     HospitalInventoryModal, ConfirmationDialog, RequestTrackDrawer,
 *     EmergencyTrackDrawer all render through it) had no dialog semantics,
 *     no focus-on-open, and no focus trap -- Tab could silently escape to
 *     the page behind the overlay. Confirms the fixed dialog: real
 *     role="dialog"/aria-modal, focus lands inside it on open, and Escape
 *     closes it.
 */
test.describe("Phase 22 E2E: responsive mobile navigation + dialog accessibility", () => {
  test("(A) Admin: a mobile viewport gets a working hamburger menu instead of no navigation at all", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await loginAsUi(page, USERS.admin);
    // loginAsUi waits for /admin -- AdminOverview.tsx, one of the 8 patched pages.
    await expect(page).toHaveURL(/\/admin$/);

    // The desktop sidebar is `hidden lg:flex` -- at 390px wide it must not
    // be rendered/visible at all.
    await expect(page.locator("aside").first()).toBeHidden();

    const menuButton = page.getByRole("button", { name: "Open navigation menu" });
    await expect(menuButton).toBeVisible();
    await menuButton.click();

    const drawer = page.getByRole("dialog", { name: "LifeLink navigation" });
    await expect(drawer).toBeVisible();
    const auditLink = drawer.getByRole("link", { name: /Audit Feed/ });
    await expect(auditLink).toBeVisible();

    // Escape closes it without navigating.
    await page.keyboard.press("Escape");
    await expect(drawer).not.toBeVisible();

    // Reopen and actually navigate -- the drawer must be a real, working nav,
    // not just a decorative panel.
    await menuButton.click();
    await drawer.getByRole("link", { name: /Audit Feed/ }).click();
    await expect(page).toHaveURL(/\/admin\/audit-feed$/);
    await expect(drawer).not.toBeVisible();
  });

  test("(B) Patient: a mobile viewport gets a working hamburger menu instead of no navigation at all", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await loginAsUi(page, USERS.patient);
    await expect(page).toHaveURL(/\/patient$/);

    const menuButton = page.getByRole("button", { name: "Open navigation menu" });
    await expect(menuButton).toBeVisible();
    await menuButton.click();

    const drawer = page.getByRole("dialog", { name: "LifeLink navigation" });
    await expect(drawer).toBeVisible();
    await drawer.getByRole("link", { name: "Find Donor" }).click();
    await expect(page).toHaveURL(/\/patient\/find-donor$/);
    await expect(drawer).not.toBeVisible();

    // The destination page (one of the 4 patient sub-pages fixed the same
    // way) also has a working hamburger, not just the dashboard itself.
    const subPageMenuButton = page.getByRole("button", { name: "Open navigation menu" });
    await expect(subPageMenuButton).toBeVisible();
  });

  test("(C) Family: a mobile viewport gets a working hamburger menu instead of no navigation at all", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await loginAsUi(page, USERS.family);
    await expect(page).toHaveURL(/\/family$/);

    const menuButton = page.getByRole("button", { name: "Open navigation menu" });
    await expect(menuButton).toBeVisible();
    await menuButton.click();

    const drawer = page.getByRole("dialog", { name: "LifeLink navigation" });
    await expect(drawer).toBeVisible();
    await drawer.getByRole("link", { name: "Linked Patients" }).click();
    await expect(page).toHaveURL(/\/family\/patients$/);
  });

  test("(D) the shared Modal has real dialog semantics, moves focus in, and Escape closes it", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.patient);
    await expect(page).toHaveURL(/\/patient$/);

    await page.getByRole("button", { name: "Emergency SOS" }).first().click();

    const dialog = page.getByRole("dialog", { name: "Emergency SOS" });
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAttribute("aria-modal", "true");

    // Focus lands inside the dialog on open (the close button) rather than
    // silently staying on <body> or the trigger button behind the overlay.
    await expect(dialog.getByRole("button", { name: "Close" })).toBeFocused();

    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible();
  });

  test.afterEach(async ({ page }) => {
    await logout(page);
  });
});
