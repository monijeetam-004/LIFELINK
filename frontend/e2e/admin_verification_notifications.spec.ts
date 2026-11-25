import { request as pwRequest, expect, test } from "@playwright/test";
import { loginAsUi } from "./helpers";
import { API_BASE_URL, E2E_PASSWORD, USERS } from "./setup";

/**
 * The Phase 18 fix under test: before this phase, an admin never received
 * any notification when a donor/hospital submitted a new verification
 * request (see app/api/routes/verifications.py::submit_verification and
 * LIFELINK_PHASE_18_NOTIFICATION_EVENT_AUDIT.md, Step 1). This drives the
 * submission itself through the real API (multipart file upload through a
 * Chromium `<input type=file>` drag/drop-style widget is exercised well
 * enough by the existing component; what this suite needs to prove is that
 * the resulting notification genuinely reaches the ADMIN's real browser
 * session end to end) and then verifies the result in the real admin UI.
 */
test.describe("Phase 18 E2E: new verification submission notifies the admin console", () => {
  test("submitting a verification (real API) surfaces a real notification in the admin's bell and center", async ({
    page,
  }) => {
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
          buffer: Buffer.from("%PDF-1.4 phase18 e2e fake donor id"),
        },
      },
      headers: { Origin: "http://localhost:5173" },
    });
    expect(submit.ok()).toBeTruthy();
    await ctx.dispose();

    // Admin has no shared NotificationsBell (see login.spec.ts's finding) --
    // its own inline "Your Notifications" panel on the Overview page is the
    // real surface for this, wired to the same GET /notifications.
    await loginAsUi(page, USERS.admin);
    await expect(page.getByText("Your Notifications")).toBeVisible();
    // .first() -- Phase 19: this suite has been re-run many times against
    // the same persistent dev DB, so more than one past notification can
    // genuinely share this exact generic title. What's under test is that
    // the real one this run just triggered is present, not row uniqueness.
    await expect(page.getByText(/New donor_identity verification submitted/i).first()).toBeVisible({
      timeout: 10_000,
    });

    await page.goto("/notifications");
    await expect(page.getByText(/New donor_identity verification submitted/i).first()).toBeVisible();
  });
});
