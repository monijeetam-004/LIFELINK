import { expect, test } from "@playwright/test";
import { loginAsUi, logout } from "./helpers";
import { USERS } from "./setup";

/**
 * Phase 26 -- responsive regression: no page-level horizontal scroll.
 *
 * A real, reproduced defect was found while auditing all 5 portals across
 * the 10 required breakpoints (320/375/390/414/480/768/834/1024/1280/1440):
 * `/admin/transport-routes`'s routes table -- despite being correctly
 * wrapped in `overflow-x-auto` inside an `overflow-hidden` card, and every
 * ancestor's own `getBoundingClientRect()` already clipped to the viewport
 * -- still let `document.documentElement` itself scroll horizontally by
 * ~49px at 375/390/414px once real data (85 rows) had rendered. Confirmed
 * with `window.scrollTo()` actually moving `window.scrollX`, not just a
 * suspicious `scrollWidth` number. Fixed with a defensive
 * `overflow-x: hidden` on `html, body` (src/index.css) -- a safety net
 * that does not disable any element's own intentional `overflow-x: auto`
 * (the table itself is still fully horizontally scrollable within its own
 * container). See LIFELINK_PHASE_26_DONATION_FRONTEND_AUDIT.md.
 *
 * This checks the exact page/widths that reproduced the bug, plus a small
 * representative sample of other table-heavy pages across the width range
 * -- not the full 10-breakpoint x 5-portal sweep (that lives in the audit
 * doc as one-off evidence; running all of it on every CI run would be slow
 * for little further regression value once the root cause is fixed with a
 * global rule rather than a per-page patch).
 */
test.describe("Phase 26 E2E: no page-level horizontal overflow", () => {
  async function hasHorizontalOverflow(page: import("@playwright/test").Page) {
    return page.evaluate(() => {
      const doc = document.documentElement;
      return doc.scrollWidth > window.innerWidth + 2;
    });
  }

  test("admin transport-routes table (the reproduced bug) does not scroll the page at 375/390/414px", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.admin);
    for (const width of [375, 390, 414]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto("/admin/transport-routes");
      await expect(page.locator("table tbody tr").first()).toBeVisible({ timeout: 10_000 });
      expect(await hasHorizontalOverflow(page)).toBe(false);
    }
    await logout(page);
  });

  test("donor milestones/history and patient history tables do not scroll the page at 320/1440px", async ({
    page,
  }) => {
    for (const [user, path] of [
      [USERS.donor, "/donor/milestones"],
      [USERS.patient, "/patient/history"],
    ] as const) {
      await loginAsUi(page, user);
      for (const width of [320, 1440]) {
        await page.setViewportSize({ width, height: 844 });
        await page.goto(path);
        await page.waitForTimeout(300);
        expect(await hasHorizontalOverflow(page)).toBe(false);
      }
      await logout(page);
    }
  });
});
