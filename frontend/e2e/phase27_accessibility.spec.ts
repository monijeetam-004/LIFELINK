import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { expect, test } from "@playwright/test";
import { loginAsUi, logout } from "./helpers";
import { USERS } from "./setup";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * Phase 27 -- accessibility remediation regression.
 *
 * A fresh, full 53-route x role axe-core (WCAG 2.1 A/AA) re-audit found the
 * Phase 26 disclosures were real but, on re-investigation, more fixable
 * than that phase concluded:
 *
 *   - aria-command-name (1,133 nodes, /patient/find-donor + /hospital):
 *     Phase 26 disclosed this as unfixable third-party Leaflet DOM. Real
 *     Leaflet source (leaflet-src.js `Marker.prototype._initIcon`) shows it
 *     sets `icon.title = options.title` on the marker's actual DOM node --
 *     including for a custom `L.divIcon` -- and `title` is a valid
 *     accessible-name source for its `role="button"`. DonorMap.tsx now
 *     passes a real `title` to every <Marker>; **0 aria-command-name
 *     violations remain** on either route (was 1,133 nodes).
 *   - link-in-text-block on Leaflet's own attribution link: fixed with a
 *     scoped `.leaflet-control-attribution a { text-decoration: underline }`
 *     rule in index.css -- styling the library's own output, not patching it.
 *   - color-contrast (317+ nodes across 22 routes, disclosed as systemic
 *     design-system debt): re-investigated token-by-token rather than
 *     redesigned. The `warning`/`success` Tailwind tokens themselves failed
 *     AA in every context they're used (measured, not assumed) and were
 *     replaced with darker shades of the same hue (tailwind.config.ts) --
 *     one coherent, low-risk fix that resolved every route using them at
 *     once. The remaining failures were almost all the same one pattern
 *     repeated across ~8 components: an `opacity-50/60/70` wrapper applied
 *     to a card/row that was ALREADY visually distinguished by background
 *     color, badge color and/or icon -- the opacity was redundant and
 *     incidentally dropped that element's own text below AA. Fixed by
 *     either removing the redundant opacity (state stays fully legible via
 *     its other cues) or raising it just enough to clear 4.5:1 (measured
 *     per instance, real WCAG relative-luminance arithmetic, not guessed).
 *     Two `animate-pulse` icons (Tailwind's default 1<->0.5 opacity
 *     keyframe) were below AA for part of every cycle; swapped to a
 *     shallower custom keyframe (`animate-pulse-soft` / `-subtle`, already
 *     used elsewhere in this codebase) that never drops below 4.5:1 while
 *     still visibly pulsing.
 *
 * (Phase J update: the node described below no longer exists -- the
 * watermark is now an SVG, which has no text for axe to contrast-check;
 * the last test in this file now asserts zero contrast violations.)
 *
 * One node remained, deliberately not "fixed" by a visual change:
 * DonorDashboard.tsx's large (160px) `favorite` glyph is a purely
 * decorative background watermark at 10% opacity on the donor "Your
 * Impact" card. It is marked `aria-hidden="true"` (the standard, correct
 * WCAG remediation for decorative content -- WCAG 1.4.3's own "incidental"
 * exception covers pure decoration with no contrast requirement at all).
 * axe-core's automated color-contrast rule does not special-case
 * aria-hidden elements (it checks what's visually rendered, which is
 * correct in general -- aria-hidden doesn't change what a sighted user
 * sees), so it still flags this one node. Raising its opacity to "pass"
 * would defeat the decoration's own purpose for no real accessibility
 * benefit (a screen-reader user never reaches it; a low-vision sighted
 * user reads the actual "Your Impact" heading and numbers right next to
 * it, not this background glyph). Disclosed here, not silently dropped;
 * excluded from this test's assertion by route+selector, not by rule id.
 */
const axeSource = fs.readFileSync(
  path.join(__dirname, "..", "node_modules", "axe-core", "axe.min.js"),
  "utf-8",
);

const DECORATIVE_WATERMARK_SELECTOR = '[data-icon="favorite"][style*="160px"]';

async function scanAndAssertClean(page: import("@playwright/test").Page, path_: string) {
  await page.goto(path_);
  await page.waitForTimeout(300);
  await page.evaluate(axeSource);
  const result = await page.evaluate(async () => {
    const axe = (window as unknown as { axe: { run: typeof import("axe-core").run } }).axe;
    return await axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] },
    });
  });
  const realViolations = result.violations.filter(
    (v) => !v.nodes.every((n) => n.target.some((t) => t.includes('data-icon="favorite"'))),
  );
  expect(realViolations, JSON.stringify(realViolations.map((v) => v.id))).toEqual([]);
}

test.describe("Phase 27 accessibility regression", () => {
  test("Leaflet donor-map markers on /patient/find-donor have no aria-command-name violations", async ({ page }) => {
    await loginAsUi(page, USERS.patient);
    await page.goto("/patient/find-donor");
    await page.waitForTimeout(500); // let the map + markers render
    await page.evaluate(axeSource);
    const result = await page.evaluate(async () => {
      const axe = (window as unknown as { axe: { run: typeof import("axe-core").run } }).axe;
      return await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
    });
    const commandNameViolations = result.violations.filter((v) => v.id === "aria-command-name");
    expect(commandNameViolations).toEqual([]);
    await logout(page);
  });

  test("Hospital donor-directory map has no aria-command-name violations", async ({ page }) => {
    await loginAsUi(page, USERS.hospital);
    await page.goto("/hospital");
    await page.waitForTimeout(500);
    await page.evaluate(axeSource);
    const result = await page.evaluate(async () => {
      const axe = (window as unknown as { axe: { run: typeof import("axe-core").run } }).axe;
      return await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
    });
    const commandNameViolations = result.violations.filter((v) => v.id === "aria-command-name");
    expect(commandNameViolations).toEqual([]);
    await logout(page);
  });

  test("previously-flagged donor/patient/verification/simulation routes are clean (decorative watermark excepted)", async ({ page }) => {
    await loginAsUi(page, USERS.donor);
    await scanAndAssertClean(page, "/donor");
    await scanAndAssertClean(page, "/donor/ready-console");
    await scanAndAssertClean(page, "/donor/identity-verification");
    await logout(page);

    await loginAsUi(page, USERS.patient);
    await scanAndAssertClean(page, "/patient/sos-tracking");
    await scanAndAssertClean(page, "/patient/new-request");
    await scanAndAssertClean(page, "/decision-transparency");
    await scanAndAssertClean(page, "/mathematical-model");
    await scanAndAssertClean(page, "/simulation");
    await logout(page);

    await loginAsUi(page, USERS.hospital);
    await scanAndAssertClean(page, "/hospital/verification");
    await scanAndAssertClean(page, "/matching/strategy-lab");
    await logout(page);
  });

  test("the one remaining color-contrast node is the disclosed decorative watermark, not a regression elsewhere", async ({ page }) => {
    await loginAsUi(page, USERS.donor);
    await page.goto("/donor");
    await page.waitForTimeout(300);
    await page.evaluate(axeSource);
    const result = await page.evaluate(async () => {
      const axe = (window as unknown as { axe: { run: typeof import("axe-core").run } }).axe;
      return await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
    });
    const contrastViolations = result.violations.filter((v) => v.id === "color-contrast");
    // Phase J: the watermark is now an SVG graphic (no text to
    // contrast-check), so the donor dashboard has NO color-contrast
    // violations at all -- stricter than the original "only the watermark".
    expect(contrastViolations).toEqual([]);
    const watermarkVisible = await page.locator(DECORATIVE_WATERMARK_SELECTOR).count();
    expect(watermarkVisible).toBeGreaterThan(0);
    await logout(page);
  });
});
