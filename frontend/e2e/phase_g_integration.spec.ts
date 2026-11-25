/**
 * Phase G E2E -- frontend integration closures through the real UI and the
 * real backend:
 *  1. A hospital adds plasma through the inventory console's (accessible)
 *     action dialog and sees it under "Stock by component"; an admin then
 *     sees the component on that facility's detail.
 *  2. The inventory console (with the dialog open) has no WCAG 2.1 A/AA
 *     axe violations, and has no horizontal overflow at phone, tablet and
 *     desktop widths.
 *  3. Keyboard: the dialog takes focus, Tab stays inside it, Escape closes it.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

import { loginAsUi, logout } from "./helpers";
import { ensureDonorAndHospitalProfiles, USERS } from "./setup";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const axeSource = fs.readFileSync(
  path.join(__dirname, "..", "node_modules", "axe-core", "axe.min.js"),
  "utf-8",
);

async function axeViolations(page: Page) {
  await page.evaluate(axeSource);
  return page.evaluate(async () => {
    const axe = (window as unknown as { axe: { run: typeof import("axe-core").run } }).axe;
    const r = await axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] },
    });
    return r.violations.map((v) => ({ id: v.id, nodes: v.nodes.length }));
  });
}

async function openAddDialog(page: Page) {
  await page.goto("/hospital/inventory");
  await expect(page.getByTestId("component-stock").or(page.getByText("No stock recorded for any component yet."))).toBeVisible({ timeout: 15_000 });
  await page.getByTitle("New stock received — increases units on hand.").first().click();
  return page.getByRole("dialog");
}

test.beforeAll(async () => {
  await ensureDonorAndHospitalProfiles();
});

test("hospital adds plasma via the console; admin sees the component", async ({ page }) => {
  await loginAsUi(page, USERS.hospital);
  const dialog = await openAddDialog(page);
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("heading")).toContainText("Add stock");
  await dialog.getByLabel("Component").selectOption("PLASMA");
  await dialog.getByLabel("Units").fill("2");
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(dialog).toBeHidden();
  const table = page.getByTestId("component-stock");
  await expect(table.getByRole("row", { name: /Plasma/ }).first()).toBeVisible({ timeout: 10_000 });
  await logout(page);

  await loginAsUi(page, USERS.admin);
  await page.goto("/admin/facilities");
  await page.getByText("E2E Metro General").first().click();
  await expect(page.getByRole("cell", { name: "Plasma" }).first()).toBeVisible({ timeout: 10_000 });
});

test("inventory console + dialog: no axe violations, no overflow at 3 widths", async ({ page }) => {
  await loginAsUi(page, USERS.hospital);
  for (const width of [375, 768, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/hospital/inventory");
    await expect(page.getByRole("heading", { name: "Stock by component" })).toBeVisible({ timeout: 15_000 });
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth + 2,
    );
    expect(overflow, `horizontal overflow at ${width}px`).toBe(false);
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  const dialog = await openAddDialog(page);
  await expect(dialog).toBeVisible();
  expect(await axeViolations(page)).toEqual([]);
});

test("action dialog is keyboard-operable: focus inside, Tab trapped, Escape closes", async ({ page }) => {
  await loginAsUi(page, USERS.hospital);
  const dialog = await openAddDialog(page);
  await expect(dialog).toBeVisible();
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press("Tab");
    const inside = await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'));
    expect(inside, `focus left the dialog after ${i + 1} Tab(s)`).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});
