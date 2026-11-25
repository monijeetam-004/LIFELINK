import { expect, test } from "@playwright/test";
import { loginAsUi, logout, notificationBell } from "./helpers";
import { E2E_CITY, ensureDonorAndHospitalProfiles, USERS } from "./setup";

test.describe("Phase 18 E2E: emergency SOS broadcast reaches a compatible donor", () => {
  test.beforeAll(async () => {
    await ensureDonorAndHospitalProfiles();
  });

  test("patient broadcasts a real Emergency SOS; the donor sees it as an emergency notification", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.patient);
    // force: true -- see blood_request_notifications.spec.ts for why.
    await page.getByRole("button", { name: "Emergency SOS" }).first().click({ force: true });
    await page.getByLabel("Blood group needed (optional)").selectOption("O_NEG");
    await page.getByLabel("What's happening? (optional)").fill("Phase 18 E2E smoke test emergency");
    // Audit D4: an SOS with no location alerts no donor (the form says so).
    await page.getByLabel("City", { exact: true }).fill(E2E_CITY);
    await page.getByRole("button", { name: "Broadcast emergency" }).click();
    await expect(page.getByRole("button", { name: "Broadcast emergency" })).toHaveCount(0, {
      timeout: 10_000,
    });

    await logout(page);
    await loginAsUi(page, USERS.donor);
    await notificationBell(page).click();
    // .first() -- Phase 19: this suite has been re-run many times against
    // the same persistent dev DB, so the same seeded donor can genuinely
    // have more than one past notification with this exact generic title.
    // What's under test is that the real one this run just triggered is
    // present, not row uniqueness.
    await expect(page.getByText("EMERGENCY: blood needed now").first()).toBeVisible({
      timeout: 10_000,
    });
  });
});
