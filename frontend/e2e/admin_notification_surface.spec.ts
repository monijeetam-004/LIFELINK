import { request as pwRequest, expect, test } from "@playwright/test";
import { loginAsUi, notificationBell } from "./helpers";
import { API_BASE_URL, E2E_PASSWORD, USERS } from "./setup";

/**
 * Phase 19 -- the shared admin notification surface (section 1's Phase 18
 * limitation, explicitly listed "MUST FIX": admin sub-pages beyond
 * Overview had no notification surface at all -- Overview's own inline
 * "Your Notifications" panel, covered by admin_verification_notifications
 * .spec.ts, never appeared on /admin/users, /admin/facilities, etc.).
 * This drives a real verification submission through the API, then proves
 * the same shared NotificationsBell (dropped into all 8 admin pages this
 * phase) shows it consistently across non-Overview sub-pages, and that its
 * deep link actually lands somewhere the admin is authorized to see.
 */
test.describe("Phase 19 E2E: shared admin notification surface", () => {
  test("a new verification submission is visible from the bell on non-Overview admin pages, and its link opens the verification queue", async ({
    page,
  }) => {
    // This spec's seeded donor may already have a pending DONOR_IDENTITY
    // verification left over from an earlier run of this same suite (only
    // one kind exists per role, and a second submission while one is still
    // PENDING is a real, correct 409 -- not something to work around by
    // picking a different kind). Clear it via the real admin
    // reject endpoint first so this run starts from a clean, known state.
    const adminCtx = await pwRequest.newContext({ baseURL: API_BASE_URL });
    const adminLogin = await adminCtx.post("/auth/login", {
      data: { email: USERS.admin, password: E2E_PASSWORD },
      headers: { Origin: "http://localhost:5173" },
    });
    expect(adminLogin.ok()).toBeTruthy();
    const pending = await adminCtx
      .get("/admin/verifications", { params: { status: "PENDING", query: USERS.donor } })
      .then((r) => r.json());
    for (const v of pending) {
      await adminCtx.post(`/admin/verifications/${v.id}/reject`, {
        data: { reason: "phase19 e2e cleanup" },
        headers: { Origin: "http://localhost:5173" },
      });
    }
    await adminCtx.dispose();

    const ctx = await pwRequest.newContext({ baseURL: API_BASE_URL });
    const login = await ctx.post("/auth/login", {
      data: { email: USERS.donor, password: E2E_PASSWORD },
      headers: { Origin: "http://localhost:5173" },
    });
    expect(login.ok()).toBeTruthy();
    const submit = await ctx.post("/verifications", {
      multipart: {
        kind: "DONOR_IDENTITY",
        files: {
          name: "id.pdf",
          mimeType: "application/pdf",
          buffer: Buffer.from("%PDF-1.4 phase19 e2e admin-notification-surface donor id"),
        },
      },
      headers: { Origin: "http://localhost:5173" },
    });
    expect(submit.ok()).toBeTruthy();
    await ctx.dispose();

    await loginAsUi(page, USERS.admin);
    // Land directly on a non-Overview sub-page -- the exact gap Phase 18
    // left open.
    await page.goto("/admin/users");
    await expect(page.getByText("User Identity & Role Access Governance")).toBeVisible();
    await notificationBell(page).click();
    // .first() -- earlier runs of this same suite can leave more than one
    // notification with this generic title; the surface showing at least
    // one is what's under test, not row uniqueness.
    const item = page.getByText(/New donor_identity verification submitted/i).first();
    await expect(item).toBeVisible({ timeout: 10_000 });

    // The same shared bell, with the same underlying data, is present on a
    // second non-Overview sub-page too -- this is a consistent surface, not
    // a one-off added to a single page.
    await page.goto("/admin/facilities");
    await notificationBell(page).click();
    await expect(page.getByText(/New donor_identity verification submitted/i).first()).toBeVisible({
      timeout: 10_000,
    });

    // The notification's title is a real link (verification.submitted ->
    // /admin/verifications) and actually lands on a page this admin is
    // authorized to see -- not a dead link or a 403.
    await page.getByText(/New donor_identity verification submitted/i).first().click();
    await page.waitForURL(/\/admin\/verifications$/, { timeout: 10_000 });
    await expect(page.getByText("Couldn't load", { exact: false })).not.toBeVisible();

    // Cleanup: leave the shared seeded donor account without a dangling
    // PENDING verification, the same way this test found it -- otherwise a
    // later spec run (e.g. admin_verification_notifications.spec.ts, which
    // submits its own DONOR_IDENTITY verification for the same donor)
    // would get a real, correct 409 for a reason that has nothing to do
    // with what it's testing.
    const cleanupCtx = await pwRequest.newContext({ baseURL: API_BASE_URL });
    await cleanupCtx.post("/auth/login", {
      data: { email: USERS.admin, password: E2E_PASSWORD },
      headers: { Origin: "http://localhost:5173" },
    });
    const stillPending = await cleanupCtx
      .get("/admin/verifications", { params: { status: "PENDING", query: USERS.donor } })
      .then((r) => r.json());
    for (const v of stillPending) {
      await cleanupCtx.post(`/admin/verifications/${v.id}/reject`, {
        data: { reason: "phase19 e2e cleanup" },
        headers: { Origin: "http://localhost:5173" },
      });
    }
    await cleanupCtx.dispose();
  });
});
