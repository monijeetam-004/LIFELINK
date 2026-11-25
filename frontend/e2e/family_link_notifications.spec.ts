import { request as pwRequest, expect, test } from "@playwright/test";
import { loginAsUi, logout, notificationBell } from "./helpers";
import { API_BASE_URL, E2E_PASSWORD, USERS } from "./setup";

test.describe("Phase 18 E2E: family link request/accept notifies both sides", () => {
  test("family requests a link; patient accepts it through the real UI; family is notified", async ({
    page,
  }) => {
    // A fresh relationship label per run avoids colliding with a prior run's
    // still-PENDING link for the same (family, patient) pair, which the
    // backend's own duplicate-pending-request check would otherwise reject.
    const relationship = `E2E sibling ${Date.now()}`;

    // Phase 19: this suite has been re-run many times against the same
    // persistent dev DB, so the (family, patient) pair can already have a
    // real ACCEPTED link from an earlier run -- the backend correctly
    // refuses "request a new link" while one already exists ("Link already
    // exists"), same class as a duplicate-pending check. Revoke any
    // existing link via the real API first so this run starts from the
    // same clean state the test was written to assume.
    const cleanupCtx = await pwRequest.newContext({ baseURL: API_BASE_URL });
    await cleanupCtx.post("/auth/login", {
      data: { email: USERS.family, password: E2E_PASSWORD },
      headers: { Origin: "http://localhost:5173" },
    });
    const existingLinks = await cleanupCtx.get("/family/links").then((r) => r.json());
    for (const link of existingLinks) {
      if (link.status === "ACCEPTED" || link.status === "PENDING") {
        await cleanupCtx.post(`/family/links/${link.id}/revoke`, {
          data: {},
          headers: { Origin: "http://localhost:5173" },
        });
      }
    }
    await cleanupCtx.dispose();

    await loginAsUi(page, USERS.family);
    await page.goto("/family/patients");
    await page.getByLabel("Patient email").fill(USERS.patient);
    await page.getByLabel("Relationship").fill(relationship);
    await page.getByRole("button", { name: "Send request" }).click();
    await expect(page.getByText(relationship)).toBeVisible({ timeout: 10_000 });

    await logout(page);
    await loginAsUi(page, USERS.patient);
    await expect(page.getByText(`as your ${relationship}`)).toBeVisible({ timeout: 10_000 });
    await page
      .locator("div")
      .filter({ hasText: `as your ${relationship}` })
      .getByRole("button", { name: "Accept" })
      .first()
      .click();
    // The accepted link keeps showing "as your {relationship}" -- it just
    // moves from the pending list ("wants to manage your requests") to the
    // accepted list ("can manage your requests"), so assert on the
    // pending-specific wording disappearing rather than the shared line.
    await expect(page.getByText("wants to manage your requests")).not.toBeVisible({
      timeout: 10_000,
    });
    // Now that the pending copy is gone, the accepted-list row (with the same
    // "as your {relationship}" line) is the only match left.
    await expect(page.getByText(`as your ${relationship}`)).toBeVisible();

    await logout(page);
    await loginAsUi(page, USERS.family);
    await notificationBell(page).click();
    // "Family access granted" also appears as a heading elsewhere on the
    // page once the accepted link renders there; scope to the bell's own
    // dropdown list item to check the notification itself. .first() --
    // Phase 19: repeated runs of this suite can leave more than one past
    // notification with this exact generic title for the same seeded pair.
    await expect(
      page.getByRole("listitem").getByText("Family access granted").first(),
    ).toBeVisible({ timeout: 10_000 });
  });
});
