#!/usr/bin/env node
/**
 * Final QA -- visual/functional crawl of EVERY frontend route.
 *
 *   node scripts/qa-crawl.mjs <out-dir> <params.json>
 *
 * Needs the API on :8000 and the built app (`vite preview`) on :4173, with
 * the demo seed loaded (scripts/seed_demo_data.py). For every route in
 * src/App.tsx it signs in as an allowed role (or none for public pages)
 * and, at 375 / 768 / 1280 px, records:
 *   - console errors and uncaught page errors
 *   - failed API responses (status >= 400) and network failures
 *   - horizontal overflow (document wider than the viewport)
 *   - broken images (loaded with naturalWidth 0)
 *   - blank page (almost no visible text)
 *   - axe-core violations (serious/critical) at 1280 px
 * and saves a screenshot. Then, for every protected route, it checks the
 * unauthenticated and wrong-role behaviour (must not render the page).
 * Results: <out-dir>/crawl-results.json. Nothing here changes data except
 * signing in.
 */
/* global document, window, location */
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const [, , OUT = "qa-crawl", PARAMS = "params.json"] = process.argv;
const BASE = process.env.QA_BASE ?? "http://localhost:4173";
const API = process.env.QA_API ?? "http://localhost:8000";
const PW = process.env.QA_PASSWORD ?? "LifeLink-Demo-2026!";
const params = JSON.parse(fs.readFileSync(PARAMS, "utf8"));
const AXE = fs.readFileSync(new URL("../node_modules/axe-core/axe.min.js", import.meta.url), "utf8");
const WIDTHS = [375, 768, 1280];
const EMAIL = (role) => `demo.${role.toLowerCase()}@lifelink-demo.example.com`;

// route -> roles allowed (from src/App.tsx; [] + auth = any signed-in role)
const ROUTES = JSON.parse(fs.readFileSync(path.join(OUT, "fe_routes.json"), "utf8"));
const fill = (p) => p
  .replace(":code", params.verify_code).replace(":section", "privacy")
  .replace(":patientId", params.patient_id).replace(":requestId", params.request_id)
  .replace(":id", params.cert_id);

fs.mkdirSync(path.join(OUT, "shots"), { recursive: true });
const browser = await chromium.launch();
const results = [];

// One real sign-in per role (the login endpoint is rate-limited); every
// page visit then reuses that session's cookie in a fresh browser context.
const states = {};
async function context(role, width) {
  if (role && !states[role]) {
    const c = await browser.newContext();
    const r = await c.request.post(`${API}/auth/login`, {
      data: { email: EMAIL(role), password: PW },
      headers: { Origin: "http://localhost:5173" },
    });
    if (!r.ok()) throw new Error(`login ${role}: ${r.status()}`);
    states[role] = await c.storageState();
    await c.close();
  }
  return browser.newContext({
    viewport: { width, height: width < 800 ? 900 : 860 },
    storageState: role ? states[role] : undefined,
  });
}

async function visit(ctx, url) {
  const page = await ctx.newPage();
  const consoleErrors = [];
  const failed = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 300)); });
  page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${String(e).slice(0, 300)}`));
  page.on("response", (r) => {
    if (r.status() >= 400 && r.url().startsWith(API)) failed.push(`${r.status()} ${r.request().method()} ${r.url().replace(API, "")}`);
  });
  page.on("requestfailed", (r) => {
    const u = r.url();
    // An EventSource closed by navigation is not a failure.
    if (!u.includes("/events/stream")) failed.push(`FAILED ${u.replace(API, "").replace(BASE, "")} ${r.failure()?.errorText}`);
  });
  await page.goto(BASE + url, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2200);
  const facts = await page.evaluate(() => {
    const el = document.scrollingElement;
    const broken = [...document.images].filter((i) => i.complete && i.naturalWidth === 0 && i.src).map((i) => i.src);
    return {
      finalPath: location.pathname,
      overflowPx: Math.max(0, el.scrollWidth - el.clientWidth),
      brokenImages: broken,
      textLength: document.body.innerText.trim().length,
      title: document.title,
      h1: document.querySelector("h1")?.innerText?.slice(0, 80) ?? null,
    };
  });
  return { page, consoleErrors, failed, facts };
}

function slug(s) { return s.replace(/^\//, "").replace(/[/:*]+/g, "_") || "root"; }

for (const r of ROUTES) {
  if (r.path === "*" ) continue;
  const roles = !r.auth ? [null] : r.roles.length ? [r.roles[0]] : ["DONOR"];
  const url = fill(r.path);
  for (const role of roles) {
    for (const width of WIDTHS) {
      const ctx = await context(role, width);
      const { page, consoleErrors, failed, facts } = await visit(ctx, url);
      let axe = null;
      if (width === 1280) {
        await page.evaluate(AXE); // CDP evaluation: not subject to the page CSP
        axe = await page.evaluate(async () => {
          const res = await window.axe.run(document, { resultTypes: ["violations"] });
          return res.violations.filter((v) => ["serious", "critical"].includes(v.impact))
            .map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, sample: v.nodes[0]?.target?.join(" ") }));
        });
      }
      const file = `shots/${slug(r.path)}__${role ?? "public"}__${width}.jpg`;
      await page.screenshot({ path: path.join(OUT, file), type: "jpeg", quality: 70 });
      results.push({ route: r.path, url, role, width, ...facts, consoleErrors, failed, axe, screenshot: file });
      process.stdout.write(`${r.path} ${role ?? "public"} ${width}: overflow=${facts.overflowPx} errors=${consoleErrors.length} failed=${failed.length}${axe ? ` axe=${axe.length}` : ""}\n`);
      await ctx.close();
    }
  }
}

// Authorization UI: protected routes unauthenticated and with a wrong role.
const ALL = ["PATIENT", "FAMILY", "DONOR", "HOSPITAL", "ADMIN"];
const authz = [];
for (const r of ROUTES) {
  if (!r.auth || r.path === "*") continue;
  const url = fill(r.path);
  const cases = [null];
  if (r.roles.length) cases.push(ALL.find((x) => !r.roles.includes(x)));
  for (const role of cases) {
    const ctx = await context(role, 1280);
    const { page, facts, failed } = await visit(ctx, url);
    const body = await page.evaluate(() => document.body.innerText.slice(0, 400));
    const file = `shots/authz_${slug(r.path)}__${role ?? "anonymous"}.jpg`;
    await page.screenshot({ path: path.join(OUT, file), type: "jpeg", quality: 60 });
    // Pass = the page is not rendered for this caller: sent to sign-in /
    // verify / 403, or the in-place Access Denied screen is shown.
    const denied = /\/(login|403|session-expired|verify-email)/.test(facts.finalPath)
      || facts.finalPath === "/" || /access denied|not authori[sz]ed|don.t have access|permission/i.test(body);
    authz.push({ route: r.path, as: role ?? "anonymous", landedOn: facts.finalPath, h1: facts.h1,
      denied, apiErrors: failed.filter((f) => !f.includes("tile.openstreetmap")), screenshot: file });
    await page.close();
    await ctx.close();
  }
}
fs.writeFileSync(path.join(OUT, "crawl-results.json"), JSON.stringify({ results, authz }, null, 1));
await browser.close();
console.log(`done: ${results.length} page visits, ${authz.length} authorization checks`);
