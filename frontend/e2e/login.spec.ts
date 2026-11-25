import { expect, test } from "@playwright/test";
import { loginAsUi, notificationBell } from "./helpers";
import { USERS } from "./setup";

test.describe("Phase 18 E2E: login + notification surface renders for every role", () => {
  for (const [role, email] of Object.entries(USERS)) {
    if (role === "admin") continue; // admin has its own inline panel, not the shared bell -- see below
    test(`${role} can sign in through the real UI and sees the notifications bell`, async ({
      page,
    }) => {
      await loginAsUi(page, email);
      await expect(notificationBell(page)).toBeVisible();
    });
  }

  test("admin has no shared NotificationsBell in the header -- it uses its own inline 'Your Notifications' panel", async ({
    page,
  }) => {
    // A real, previously-undocumented finding from this sweep: AdminOverview
    // does not render the shared NotificationsBell component at all (grep
    // confirms it isn't imported there) -- it builds its own inline panel
    // instead. Not a bug (the panel is real, wired to GET /notifications,
    // and was audited separately), just an inconsistency worth recording
    // rather than asserting a bell that was never meant to be there.
    await loginAsUi(page, USERS.admin);
    await expect(page.getByText("Your Notifications")).toBeVisible();
  });
});
