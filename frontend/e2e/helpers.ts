import type { APIRequestContext, Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { E2E_PASSWORD } from "./setup";

const BACKEND_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "backend");

/** Production hardening: email verification is mandatory server-side, so a
 * spec that registers a brand-new account verifies it the way a user would
 * -- through the REAL `POST /auth/verify-email` endpoint. Only "read the
 * token from the mailbox" is replaced (there is no SMTP in E2E): the token
 * is issued by `backend/scripts/e2e_issue_verification_token.py`, which
 * needs the server's SECRET_KEY / DATABASE_URL in the environment (CI sets
 * them at job level). Override the interpreter with E2E_PYTHON. */
export async function verifyEmailViaRealEndpoint(ctx: APIRequestContext, email: string) {
  const token = execFileSync(
    process.env.E2E_PYTHON ?? "python",
    ["-m", "scripts.e2e_issue_verification_token", email],
    { cwd: BACKEND_DIR, encoding: "utf8" },
  ).trim();
  const r = await ctx.post("/auth/verify-email", {
    data: { token },
    headers: { Origin: "http://localhost:5173" },
  });
  if (!r.ok()) throw new Error(`verify-email failed for ${email}: ${r.status()} ${await r.text()}`);
}

/** E2E only: clear the per-IP registration counter (5/hour by design) so a
 * full-suite run, where every spec registers from 127.0.0.1, can still
 * create the fresh accounts a later spec needs. The limit itself is covered
 * by its own tests. Uses the backend's own Redis client and REDIS_URL. */
export function resetRegistrationLimitForE2E() {
  execFileSync(
    process.env.E2E_PYTHON ?? "python",
    ["-c", "from app.core.redis import redis_client as r\nfor k in r.scan_iter('rl:register:ip:*'): r.delete(k)"],
    { cwd: BACKEND_DIR, encoding: "utf8" },
  );
}

/** E2E only: clear the per-IP LOGIN counter (10 per 5 min by design).
 * Every browser and API context in the suite signs in from 127.0.0.1, so a
 * spec that signs in many times would otherwise exhaust the budget of the
 * specs after it. The limit itself is covered by its own tests
 * (backend/tests/test_gap_rate_limit.py at the production values, and
 * phase23_security_hardening.spec.ts). Never used outside E2E. */
export function resetLoginLimitForE2E() {
  execFileSync(
    process.env.E2E_PYTHON ?? "python",
    ["-c", "from app.core.redis import redis_client as r\nfor k in r.scan_iter('rl:login:ip:*'): r.delete(k)"],
    { cwd: BACKEND_DIR, encoding: "utf8" },
  );
}

/** Logs in through the real login form -- the same one every real user
 * hits -- and waits for the resulting role dashboard to load. */
export async function loginAsUi(page: Page, email: string) {
  await page.goto("/login");
  await page.getByPlaceholder("Email address").fill(email);
  await page.getByPlaceholder("Password").fill(E2E_PASSWORD);
  await page.locator("form").getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/(patient|family|donor|hospital|admin)$/, { timeout: 15_000 });
}

export const notificationBell = (page: Page) => page.getByRole("button", { name: "Notifications" }).first();

/** Switches the current page to a fresh, logged-out state so the same
 * single browser context/page can be reused for the next user in a
 * sequential multi-role flow -- avoids running two live browser contexts
 * side by side, which this sandboxed environment's single pinned Chromium
 * instance doesn't handle reliably (see the Phase 18 report's Playwright
 * section). Every notification-producing action in these specs still goes
 * through a real login and the real UI; only the "how do I hand the
 * browser to the next role" mechanic is simplified. */
export async function logout(page: Page) {
  await page.context().clearCookies();
}
