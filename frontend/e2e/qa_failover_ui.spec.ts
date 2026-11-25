import fs from "node:fs";
import path from "node:path";

import { expect, request as pwRequest, test, type Page } from "@playwright/test";

import { resetLoginLimitForE2E } from "./helpers";

/**
 * QA round (2026-10-20) -- route failover through the real UI.
 *
 * Planning and failover were covered by backend tests and by the API-level
 * clean-start workflow, but no browser test drove the failure itself:
 *   1. the patient raises an SOS for the demo hospital (API);
 *   2. the hospital plans the delivery in Decision Transparency (UI) and sees
 *      the ACTIVE plan with its backups;
 *   3. the administrator disrupts the route the ACTIVE plan uses, in
 *      Transport Routes (UI), with a reason;
 *   4. the hospital reopens Decision Transparency: the old plan is
 *      INVALIDATED and a new ACTIVE plan avoids the failed route -- a
 *      re-validated backup if one is still usable, otherwise a re-plan --
 *      and the API agrees (persisted state, timeline event);
 *   5. the administrator recovers the route (UI), leaving the seed as it was.
 *
 * Needs the demo seed (scripts/seed_demo_data.py). With QA_SCREENSHOT_DIR set,
 * a screenshot is saved after the assertions of each step pass.
 */
const API = "http://127.0.0.1:8000";
const ORIGIN = { Origin: "http://localhost:5173" };
const PASSWORD = "LifeLink-Demo-2026!"; // documented, non-secret demo seed password
const DOMAIN = "lifelink-demo.example.com";
const DIR = process.env.QA_SCREENSHOT_DIR;

async function shot(page: Page, name: string, meta: { test: string; expected: string; actual: string }) {
  if (!DIR) return;
  const dir = path.join(DIR, "16-route-failover");
  fs.mkdirSync(dir, { recursive: true });
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(dir, `${name}.png`) });
  fs.appendFileSync(
    path.join(DIR, "index.jsonl"),
    JSON.stringify({ category: "16-route-failover", file: `16-route-failover/${name}.png`, feature: "Route failover", ...meta, result: "PASS" }) + "\n",
  );
}

async function api(email: string) {
  resetLoginLimitForE2E();
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

type Step = { route_id: string; from_facility_id: string; to_facility_id: string };
type Pathway = { version: number; status: string; selected_facility_id: string | null; legs: { source_facility_name: string | null; steps: Step[] }[] };

test("QA failover: admin disrupts the planned route in the UI; a new plan that avoids it takes over", async ({ page }) => {
  const admin = await api(`demo.admin@${DOMAIN}`);
  const dir = await (await admin.get("/hospitals/directory")).json();
  const demo = dir.find((d: { name: string }) => d.name === "LifeLink Demo Hospital");
  const mumbai = dir.filter((d: { city: string | null; verified: boolean; id: string }) =>
    d.city?.startsWith("Mumbai") && d.verified && d.id !== demo.id).slice(0, 2);
  expect(mumbai.length).toBe(2);
  // two routes from the demo hospital (idempotent: an existing pair is a 409)
  for (const [i, f] of mumbai.entries()) {
    const r = await admin.post("/admin/transport-routes", {
      data: { origin_facility_id: demo.id, destination_facility_id: f.id, distance_km: 8 + i * 6, transit_minutes: 15 + i * 10 },
      headers: ORIGIN,
    });
    expect([201, 409]).toContain(r.status());
  }

  const patient = await api(`demo.patient@${DOMAIN}`);
  const em = await (await patient.post("/emergencies", {
    // more than the demo hospital holds itself (20 of each group), so the plan
    // must deliver over a route
    data: { blood_type: "O_NEG", units_needed: 23, target_facility_id: demo.id, latitude: 19.07, longitude: 72.88 },
    headers: ORIGIN,
  })).json();

  // 2. the hospital plans in the UI
  await login(page, `demo.hospital@${DOMAIN}`);
  await page.goto("/decision-transparency");
  await page.getByLabel("Emergency").selectOption(em.id);
  await page.getByRole("button", { name: "Plan delivery" }).click();
  await expect(page.getByRole("heading", { name: "Current plan and backups" })).toBeVisible({ timeout: 20_000 });
  const hosp = await api(`demo.hospital@${DOMAIN}`);
  const before = (await (await hosp.get(`/emergencies/${em.id}/pathways`)).json()) as Pathway[];
  const active = before.find((p) => p.status === "ACTIVE")!;
  expect(active, "an ACTIVE plan exists").toBeTruthy();
  expect(before.filter((p) => p.status === "BACKUP").length).toBeGreaterThan(0);
  const routed = active.legs.find((l) => l.steps.length > 0);
  expect(routed, "the ACTIVE plan delivers over at least one route").toBeTruthy();
  const step = routed!.steps[0];
  const routeId = step.route_id;
  const sourceName = routed!.source_facility_name!;
  const nameOf = (id: string) => (dir as { id: string; name: string }[]).find((d) => d.id === id)!.name;
  const [endA, endB] = [nameOf(step.from_facility_id), nameOf(step.to_facility_id)];
  await expect(page.getByText(new RegExp(`v${active.version} · round \\d+ · ACTIVE`))).toBeVisible();
  await shot(page, "failover_01_plan_with_backups", {
    test: "Hospital plans the emergency in Decision Transparency",
    expected: "An ACTIVE plan and at least one BACKUP",
    actual: `ACTIVE v${active.version} from ${sourceName}; ${before.filter((p) => p.status === "BACKUP").length} backup(s)`,
  });

  // 3. the administrator disrupts exactly that route in the UI
  await login(page, `demo.admin@${DOMAIN}`);
  await page.goto("/admin/transport-routes");
  const row = page.locator("tr", { hasText: endA }).filter({ hasText: endB }).first();
  await expect(row).toBeVisible({ timeout: 20_000 });
  await row.getByRole("button", { name: "Disrupt" }).click();
  await row.getByPlaceholder("Reason (min 3 chars)").fill("QA: road closed (simulated)");
  const disrupted = page.waitForResponse((r) => r.url().endsWith(`/admin/transport-routes/${routeId}/disrupt`));
  await row.getByRole("button", { name: "Confirm disrupt" }).click();
  expect((await disrupted).status()).toBe(200);
  await expect(row.getByText("DISRUPTED").first()).toBeVisible({ timeout: 15_000 });
  await shot(page, "failover_02_route_disrupted_by_admin", {
    test: "Admin disrupts the route the ACTIVE plan uses",
    expected: "Route marked DISRUPTED with the reason",
    actual: `Route ${endA} – ${endB} disrupted`,
  });

  // 4. persisted state and the hospital's view after failover
  const after = (await (await hosp.get(`/emergencies/${em.id}/pathways`)).json()) as Pathway[];
  const old = after.find((p) => p.version === active.version)!;
  const now = after.filter((p) => p.status === "ACTIVE");
  expect(old.status).toBe("INVALIDATED");
  expect(now.length).toBe(1);
  expect(now[0].version).not.toBe(active.version);
  expect(now[0].legs.flatMap((l) => l.steps.map((s) => s.route_id))).not.toContain(routeId);
  const events = ((await (await patient.get(`/emergencies/${em.id}`)).json()).events as { event_type: string }[])
    .map((e) => e.event_type);
  const outcome = events.includes("backup_promoted") ? "backup promoted" : events.includes("pathway_replanned") ? "re-planned" : "none";
  expect(outcome, `timeline events: ${events.join(", ")}`).not.toBe("none");
  await login(page, `demo.hospital@${DOMAIN}`);
  await page.goto("/decision-transparency");
  await page.getByLabel("Emergency").selectOption(em.id);
  await expect(page.getByText(new RegExp(`v${now[0].version} · round \\d+ · ACTIVE`))).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(new RegExp(`v${active.version} · round \\d+ · INVALIDATED`))).toBeVisible();
  await shot(page, "failover_03_backup_active_old_invalidated", {
    test: "Hospital reopens the plan after the failure",
    expected: "Old plan INVALIDATED; a new ACTIVE plan (re-validated backup, or a re-plan) that avoids the failed route",
    actual: `v${active.version} INVALIDATED; v${now[0].version} ACTIVE, ${outcome} (persisted state and timeline checked through the API)`,
  });

  // 5. clean up: recover the route through the UI
  await login(page, `demo.admin@${DOMAIN}`);
  await page.goto("/admin/transport-routes");
  const row2 = page.locator("tr", { hasText: endA }).filter({ hasText: endB }).first();
  const recovered = page.waitForResponse((r) => r.url().endsWith(`/admin/transport-routes/${routeId}/recover`));
  await row2.getByRole("button", { name: /Recover/ }).click();
  expect((await recovered).status()).toBe(200);
  await expect(row2.getByText("OPERATIONAL").first()).toBeVisible({ timeout: 15_000 });
  await patient.post(`/emergencies/${em.id}/cancel`, { data: { reason: "QA failover test finished" }, headers: ORIGIN });
});
