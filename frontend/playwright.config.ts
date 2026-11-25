import { existsSync } from "node:fs";
import { defineConfig, devices } from "@playwright/test";

/**
 * Phase 18 E2E config, made CI-portable in Phase 29.
 *
 * Deliberately does NOT use Playwright's `webServer` option to start the
 * frontend/backend -- both are already running as long-lived local
 * processes for this phase's manual verification (a real FastAPI/uvicorn
 * process against the real Postgres `lifelink` dev database + Redis, and
 * `vite preview` serving the real production build), started the same way
 * a developer running this suite locally would. `reuseExistingServer`-style
 * auto-start isn't needed here and would just add startup-ordering risk.
 * The Phase 29 CI workflow (.github/workflows/ci.yml) starts both the same
 * way, then polls /health/ready and / before running this suite -- see that
 * workflow's `e2e` job for the exact readiness-wait logic (no arbitrary
 * sleeps).
 *
 * Phase 18's sandbox has a single pinned Chromium at a fixed path (see
 * SANDBOX_CHROMIUM below) because its network egress doesn't allow reaching
 * cdn.playwright.dev to download a browser. A real CI runner (GitHub
 * Actions-hosted or otherwise) has neither that path nor that restriction,
 * and runs `npx playwright install --with-deps chromium` instead -- so
 * `executablePath` is only set when the sandbox's pinned path actually
 * exists on disk; otherwise this falls through to Playwright's own default
 * browser resolution (the one `playwright install` downloads).
 */
const SANDBOX_CHROMIUM = "/opt/pw-browsers/chromium";
const isCI = !!process.env.CI;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: isCI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: "http://localhost:4173",
    trace: "retain-on-failure",
    // Phase 29: captured only on failure, and only meaningfully consumed in
    // CI (see ci.yml's artifact-upload step) -- local runs already have a
    // human watching, so this stays off there to avoid needless disk I/O.
    // QA: PW_SCREENSHOT_ALL=1 keeps an end-of-test screenshot for EVERY test
    // (evidence runs); otherwise only failures are captured in CI.
    screenshot: process.env.PW_SCREENSHOT_ALL ? "on" : isCI ? "only-on-failure" : "off",
    video: isCI ? "retain-on-failure" : "off",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: existsSync(SANDBOX_CHROMIUM)
          ? { executablePath: SANDBOX_CHROMIUM }
          : {},
      },
    },
  ],
});
