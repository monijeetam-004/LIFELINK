import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { expect, request as pwRequest, test, type Page } from "@playwright/test";
import { resetLoginLimitForE2E } from "./helpers";

/**
 * Gap elimination -- the new and rewritten screens, against the real backend
 * and the synthetic demo seed (scripts/seed_demo_data.py):
 *   * every screen is opened at 375 / 768 / 1280 px, screenshotted into
 *     docs/final-verification/screenshots/, checked for horizontal overflow
 *     and scanned with axe-core (WCAG 2.1 A/AA);
 *   * the live planner pages actually run the engine (POST /pathways/simulate)
 *     and show its answer.
 */
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SHOTS = path.join(__dirname, "..", "..", "docs", "final-verification", "screenshots");
const API = "http://127.0.0.1:8000";
const ORIGIN = { Origin: "http://localhost:5173" };
const PASSWORD = "LifeLink-Demo-2026!";
const DOMAIN = "lifelink-demo.example.com";
const WIDTHS = [375, 768, 1280];
const axeSource = fs.readFileSync(path.join(__dirname, "..", "node_modules", "axe-core", "axe.min.js"), "utf8");
const axeLog: { route: string; width: number; violations: { id: string; nodes: number }[] }[] = [];

async function api(email: string) {
  resetLoginLimitForE2E(); // this spec signs in ~12 times; see helpers.ts
  const ctx = await pwRequest.newContext({ baseURL: API });
  const r = await ctx.post("/auth/login", { data: { email, password: PASSWORD }, headers: ORIGIN });
  if (!r.ok()) throw new Error(`login ${email}: ${r.status()}`);
  return ctx;
}

async function login(page: Page, email: string) {
  resetLoginLimitForE2E();
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByPlaceholder("Email address").fill(email);
  await page.getByPlaceholder("Password").fill(PASSWORD);
  await page.locator("form").getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/(patient|family|donor|hospital|admin)$/, { timeout: 20_000 });
}

async function audit(page: Page, route: string, name: string) {
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    await page.waitForTimeout(250);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `${route} overflows horizontally at ${width}px`).toBeLessThanOrEqual(1);
    await page.screenshot({ path: path.join(SHOTS, `${name}-${width}.png`), fullPage: true });
    await page.evaluate(axeSource);
    const res = await page.evaluate(async () => {
      const axe = (window as unknown as { axe: { run: typeof import("axe-core").run } }).axe;
      return await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
    });
    axeLog.push({ route, width, violations: res.violations.map((v) => ({ id: v.id, nodes: v.nodes.length })) });
    expect(res.violations.map((v) => `${v.id} (${v.nodes.length})`), `${route} @${width}`).toEqual([]);
  }
}

test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  // Put the demo hospital on the transport network: two routes to seeded
  // Mumbai facilities, so the planner has real routes to choose between.
  const admin = await api(`demo.admin@${DOMAIN}`);
  const dir = await (await admin.get("/hospitals/directory")).json();
  const demo = dir.find((d: { name: string }) => d.name === "LifeLink Demo Hospital");
  const mumbai = dir.filter((d: { city: string | null; verified: boolean; id: string }) =>
    d.city?.startsWith("Mumbai") && d.verified && d.id !== demo.id).slice(0, 2);
  for (const [i, f] of mumbai.entries()) {
    await admin.post("/admin/transport-routes", {
      data: { origin_facility_id: demo.id, destination_facility_id: f.id, distance_km: 8 + i * 6, transit_minutes: 15 + i * 10 },
      headers: ORIGIN,
    });
  }
});

test.afterAll(() => {
  resetLoginLimitForE2E(); // leave the next spec the full budget
  fs.writeFileSync(path.join(SHOTS, "axe-gap-pages.json"), JSON.stringify(axeLog, null, 1));
});

test("graph decision engine runs the live planner", async ({ page }) => {
  await login(page, `demo.hospital@${DOMAIN}`);
  await page.goto("/graph-engine");
  await expect(page.getByRole("heading", { name: "Graph Decision Engine" })).toBeVisible();
  await page.getByRole("button", { name: "Run planner" }).click();
  await expect(page.getByRole("heading", { name: "Primary plan" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Every facility evaluated")).toBeVisible();
  await audit(page, "/graph-engine", "gap-graph-engine");
});

test("simulation compares before / after a simulated route failure", async ({ page }) => {
  await login(page, `demo.hospital@${DOMAIN}`);
  await page.goto("/simulation");
  const boxes = page.getByRole("checkbox");
  await expect(boxes.first()).toBeVisible({ timeout: 15_000 });
  await boxes.first().check();
  await page.getByRole("button", { name: "Compare before / after" }).click();
  await expect(page.getByRole("heading", { name: /With 1 route\(s\) failed/ })).toBeVisible({ timeout: 20_000 });
  await audit(page, "/simulation", "gap-simulation");
});

test("research mode compares the three strategies", async ({ page }) => {
  await login(page, `demo.hospital@${DOMAIN}`);
  await page.goto("/research-mode");
  await page.getByRole("button", { name: "Compare strategies" }).click();
  await expect(page.getByRole("rowheader", { name: "ROUTINE_EFFICIENT" })).toBeVisible({ timeout: 20_000 });
  await audit(page, "/research-mode", "gap-research-mode");
});

test("mathematical model shows the configured parameters", async ({ page }) => {
  await login(page, `demo.hospital@${DOMAIN}`);
  await page.goto("/mathematical-model");
  await expect(page.getByText(/handling = 30 min/)).toBeVisible();
  await audit(page, "/mathematical-model", "gap-mathematical-model");
});

test("decision transparency plans and explains a real emergency", async ({ page }) => {
  const patient = await api(`demo.patient@${DOMAIN}`);
  const dir = await (await patient.get("/hospitals/directory")).json();
  const demo = dir.find((d: { name: string }) => d.name === "LifeLink Demo Hospital");
  const em = await (await patient.post("/emergencies", {
    data: { blood_type: "O_NEG", units_needed: 2, target_facility_id: demo.id, latitude: 19.07, longitude: 72.88 },
    headers: ORIGIN,
  })).json();
  await login(page, `demo.hospital@${DOMAIN}`);
  await page.goto("/decision-transparency");
  await page.getByLabel("Emergency").selectOption(em.id);
  await page.getByRole("button", { name: "Plan delivery" }).click();
  await expect(page.getByRole("heading", { name: "Current plan and backups" })).toBeVisible({ timeout: 20_000 });
  await audit(page, "/decision-transparency", "gap-decision-transparency");
  await page.goto("/recommendation");
  await page.getByLabel("Emergency").selectOption(em.id);
  await expect(page.getByText(/distance limit/)).toBeVisible({ timeout: 15_000 });
  await audit(page, "/recommendation", "gap-donor-recommendation");
});

test("analytics runs a labelled forecast", async ({ page }) => {
  await login(page, `demo.hospital@${DOMAIN}`);
  await page.goto("/analytics");
  await page.getByRole("button", { name: "Run forecast" }).click();
  await expect(page.getByText(/SYNTHETIC data/)).toBeVisible({ timeout: 20_000 });
  await audit(page, "/analytics", "gap-analytics");
});

test("hospital unit lifecycle, resource pathways and shipments", async ({ page }) => {
  await login(page, `demo.hospital@${DOMAIN}`);
  await page.goto("/hospital/units");
  await expect(page.getByRole("table")).toBeVisible({ timeout: 15_000 });
  await audit(page, "/hospital/units", "gap-hospital-units");
  await page.goto("/hospital/resource-pathways");
  await expect(page.getByText(/Recorded transport routes|Straight-line|Own stock/).first()).toBeVisible({ timeout: 20_000 });
  await audit(page, "/hospital/resource-pathways", "gap-resource-pathways");
  await page.goto("/hospital/shipments");
  await audit(page, "/hospital/shipments", "gap-shipments");
});

test("admin facilities shows the revoke-verification control", async ({ page }) => {
  await login(page, `demo.admin@${DOMAIN}`);
  await page.goto("/admin/facilities");
  await expect(page.getByRole("table")).toBeVisible({ timeout: 15_000 });
  await audit(page, "/admin/facilities", "gap-admin-facilities");
});

test("map shows an explicit fallback when tiles cannot load", async ({ page }) => {
  // Tile requests are forced to fail (in the sandbox they fail anyway);
  // the map must say so instead of showing a silent grey box.
  await page.route(/tile\.openstreetmap\.org/, (r) => r.abort());
  await login(page, `demo.hospital@${DOMAIN}`);
  await page.goto("/hospital");
  await expect(page.getByText(/Map background tiles could not be loaded/)).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: path.join(SHOTS, "gap-map-tile-fallback-1280.png") });
});
