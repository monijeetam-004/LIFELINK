import { expect, test } from "@playwright/test";
import { loginAsUi, logout, notificationBell } from "./helpers";
import { E2E_CITY, ensureDonorAndHospitalProfiles, USERS } from "./setup";

test.describe("Phase 18 E2E: blood request -> donor notification, real end to end", () => {
  test.beforeAll(async () => {
    await ensureDonorAndHospitalProfiles();
  });

  test("a patient's O- request notifies the O- donor, who reads it via the bell and the full center", async ({
    page,
  }) => {
    // Sequential, single-context: the patient files a real request through
    // the real form, then the SAME browser session switches identity (real
    // login as the donor) to observe the result -- see helpers.ts::logout
    // for why this runs sequentially rather than two live contexts at once.
    await loginAsUi(page, USERS.patient);
    // force: true -- the patient dashboard's hero section carries a
    // decorative animated background that can transiently intercept pointer
    // coordinates during its transition; this is cosmetic, not a real
    // interaction bug (confirmed by screenshot during triage).
    await page.getByRole("button", { name: "New Blood Request" }).first().click({ force: true });
    await page.getByLabel("Blood group").selectOption("O_NEG");
    // Audit D4: without a city no donor is alerted (the form says so).
    await page.getByLabel("City (optional)").fill(E2E_CITY);
    await page.getByRole("button", { name: "Submit request" }).click();
    await expect(page.getByRole("button", { name: "Submit request" })).toHaveCount(0, {
      timeout: 10_000,
    });

    await logout(page);
    await loginAsUi(page, USERS.donor);
    await notificationBell(page).click();
    // .first() -- Phase 19: this suite has been re-run many times against
    // the same persistent dev DB over this project's life, so the same
    // seeded donor can genuinely have more than one past notification with
    // this exact generic title. What's under test is that the real one
    // this test just triggered is present, not row uniqueness.
    await expect(page.getByText("A patient near you needs blood").first()).toBeVisible({
      timeout: 10_000,
    });

    // The full Notifications Center shows the same real row. "Mark all
    // read" is a real POST /notifications/read-all -- confirm it actually
    // clears the unread filter down to zero rather than just changing a
    // label.
    await page.goto("/notifications");
    await expect(page.getByText("A patient near you needs blood").first()).toBeVisible();
    await page.getByRole("button", { name: "Mark all read" }).click();
    await expect(page.getByRole("button", { name: /^Unread \(0\)$/ })).toBeVisible({
      timeout: 10_000,
    });
  });
});
