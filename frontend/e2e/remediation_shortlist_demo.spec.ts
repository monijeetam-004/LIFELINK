import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { expect, test, type Page } from "@playwright/test";

/**
 * Audit remediation 2026-10-31 -- the donor shortlist panel in the browser,
 * after `scripts/demo_walkthrough.py` has played scenarios A-D against a
 * DEMO_MODE API (docs/remediation/DEMO_GUIDE.md).
 *
 * For each scenario hospital it opens the real request page, checks that the
 * panel tells the truth (state text, confirmed vs. still needed, SYNTHETIC
 * and simulated labels), takes screenshots at 375 / 768 / 1280 px, checks
 * horizontal overflow and runs axe-core (WCAG 2.1 A/AA).
 *
 * Runs only when pointed at such a stack:
 *   DEMO_FRONTEND_URL=http://127.0.0.1:4500 DEMO_RESULTS=<demo_results.json>
 *   [DEMO_SHOTS=<dir>]
 * Otherwise it is skipped (it is not part of the CI-identical run).
 */
const FRONT = process.env.DEMO_FRONTEND_URL;
const RESULTS = process.env.DEMO_RESULTS;
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SHOTS = process.env.DEMO_SHOTS ?? path.join(__dirname, "..", "..", "docs", "QA", "evidence",
  "remediation-2026-10-31", "screenshots");
const PASSWORD = "LifeLink-Demo-2026!";
const WIDTHS = [375, 768, 1280];

test.skip(!FRONT || !RESULTS, "DEMO_FRONTEND_URL / DEMO_RESULTS not set");
test.use({ baseURL: FRONT });
test.describe.configure({ mode: "serial" });

const EXPECT: Record<string, { state: RegExp; extra: RegExp[] }> = {
  A: { state: /Enough donors confirmed/, extra: [/SYNTHETIC/, /simulated answer/] },
  B: { state: /Partially covered — still short/, extra: [/still needed/, /SYNTHETIC/] },
  C: { state: /No eligible donors found/, extra: [/beyond.distance.limit|distance limit/i] },
  D: { state: /Enough donors confirmed/, extra: [/Timed out|TIMED_OUT|timed out/i] },
};

async function login(page: Page, email: string) {
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByPlaceholder("Email address").fill(email);
  await page.getByPlaceholder("Password").fill(PASSWORD);
  await page.locator("form").getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/hospital$/, { timeout: 20_000 });
}

for (const key of ["A", "B", "C", "D"]) {
  test(`scenario ${key}: the shortlist panel states what really happened`, async ({ page }) => {
    const results = JSON.parse(fs.readFileSync(RESULTS as string, "utf8"));
    const rid: string = results[key].subject_id;
    fs.mkdirSync(SHOTS, { recursive: true });
    await login(page, `shortlist.hospital.${key.toLowerCase()}@lifelink-demo.example.com`);
    await page.goto(`/hospital/requests/${rid}`);
    const panel = page.getByTestId("donor-shortlist");
    await expect(panel).toBeVisible({ timeout: 20_000 });
    await expect(panel).toContainText(EXPECT[key].state);
    for (const re of EXPECT[key].extra) await expect(panel).toContainText(re);
    await expect(panel).toContainText(/DEMO MODE/);
    // Never a promise of availability: the panel carries the disclaimer and
    // no "certain"/"definitely available" wording.
    await expect(panel).toContainText(/Ranking is not confirmation/);
    await expect(panel).not.toContainText(/100% certain|definitely available/i);
    // Once satisfied, nothing reads as still unmet.
    if (key === "A" || key === "D") await expect(panel).not.toContainText(/donors still needed [1-9]/);
    // Unified priority (2026-10-31): selectable ordering, honest labelling,
    // and the strategy/model recorded per contacted donor.
    const strategy = panel.getByTestId("ranking-strategy");
    await expect(strategy).toHaveValue("UNIFIED_PRIORITY");
    await expect(panel).toContainText(/not a probability of donation/);
    if (key !== "C") {
      await expect(panel).toContainText(/ranked by Unified priority score, priority \d+\/100 at contact/);
    }
    if (key === "B") {
      await strategy.selectOption("NEAREST");
      await expect(panel).toContainText(/Original lexicographic strategy/);
      await strategy.selectOption("UNIFIED_PRIORITY");
      await expect(panel).toContainText(/not a probability of donation/);
    }
    const axeSource = fs.readFileSync(path.join(__dirname, "..", "node_modules", "axe-core", "axe.min.js"), "utf8");
    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: 900 });
      await page.waitForTimeout(300);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow, `overflow at ${width}px`).toBeLessThanOrEqual(1);
      await page.screenshot({ path: path.join(SHOTS, `shortlist-${key}-${width}.png`), fullPage: true });
      await page.evaluate(axeSource);
      const res = await page.evaluate(async () => {
        const axe = (window as unknown as { axe: { run: typeof import("axe-core").run } }).axe;
        return await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
      });
      fs.appendFileSync(path.join(SHOTS, "axe.jsonl"), JSON.stringify({
        scenario: key, width, violations: res.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length })),
      }) + "\n");
      expect(res.violations.map((v) => `${v.id} (${v.nodes.length})`), `axe @${width}`).toEqual([]);
    }
  });
}

test("unified priority breakdown reconciles and is accessible before anyone is contacted", async ({ page }) => {
  const api = process.env.DEMO_API_URL ?? "http://127.0.0.1:8500";
  fs.mkdirSync(SHOTS, { recursive: true });
  await login(page, "shortlist.hospital.b@lifelink-demo.example.com");
  const created = await page.request.post(`${api}/blood-requests`, {
    data: { blood_type: "A_POS", units_needed: 2, urgency: "URGENT", note: "[DEMO] breakdown check" },
    headers: { Origin: FRONT as string },
  });
  expect(created.ok()).toBeTruthy();
  const rid = (await created.json()).id as string;
  try {
    await page.goto(`/hospital/requests/${rid}`);
    const panel = page.getByTestId("donor-shortlist");
    await expect(panel).toBeVisible({ timeout: 20_000 });
    await expect(panel).toContainText(/Not contacted yet/);
    const first = panel.getByTestId("priority-score").first();
    await expect(first).toHaveText(/priority (\d+\/100|not scored)/);
    await panel.getByText("Why this rank").first().click();
    const table = panel.getByTestId("priority-breakdown").first();
    await expect(table).toBeVisible();
    // The explanation is generated from the calculation: the factor
    // contributions plus the missing-data baseline add up to the score.
    const shown = await table.evaluate((t) => {
      const cells = [...t.querySelectorAll("tbody tr")].map((r) =>
        [...r.querySelectorAll("td")].map((d) => d.textContent ?? ""));
      return cells;
    });
    expect(shown.length).toBeGreaterThan(7);
    const pts = shown.map((r) => Number((r[2] ?? "").replace("+", "")));
    const total = pts[pts.length - 1];
    const sum = pts.slice(0, -1).reduce((x, y) => x + y, 0);
    expect(Math.abs(sum - total), `rows ${JSON.stringify(shown)}`).toBeLessThanOrEqual(0.5);
    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: 900 });
      await page.waitForTimeout(300);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow, `overflow at ${width}px`).toBeLessThanOrEqual(1);
      await table.screenshot({ path: path.join(SHOTS, `priority-breakdown-${width}.png`) });
      const axeSource = fs.readFileSync(path.join(__dirname, "..", "node_modules", "axe-core", "axe.min.js"), "utf8");
      await page.evaluate(axeSource);
      const res = await page.evaluate(async () => {
        const axe = (window as unknown as { axe: { run: typeof import("axe-core").run } }).axe;
        return await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
      });
      fs.appendFileSync(path.join(SHOTS, "axe.jsonl"), JSON.stringify({
        scenario: "breakdown", width, violations: res.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length })),
      }) + "\n");
      expect(res.violations.map((v) => `${v.id} (${v.nodes.length})`), `axe @${width}`).toEqual([]);
    }
  } finally {
    await page.request.post(`${api}/blood-requests/${rid}/cancel`, {
      data: { reason: "demo breakdown check done" }, headers: { Origin: FRONT as string },
    });
  }
});
