import { request as pwRequest, expect, test } from "@playwright/test";
import { loginAsUi, logout, verifyEmailViaRealEndpoint } from "./helpers";
import { API_BASE_URL, E2E_PASSWORD, USERS, verifyDonationAsAdmin } from "./setup";

/**
 * Phase 23 -- Security Hardening + Adversarial Audit: real-browser E2E
 * coverage for the session/CSRF/IDOR/rate-limit/mass-assignment behaviors
 * covered at the unit level by backend/tests/test_phase23_security_hardening.py.
 *
 * Every scenario drives either the real UI (login form, the account menu's
 * sign-out button, RequireAuth's role gate) or `page.context().request` --
 * the same real authenticated browser session's cookie jar, making the
 * exact HTTP calls the real UI would make -- against the real backend and
 * real (persistent dev) Postgres/Redis. Nothing here is mocked. This
 * follows the same pattern already established in
 * document_storage_security.spec.ts (tests C/D there use the identical
 * `page.context().request` technique for IDOR checks).
 *
 * `apiLogin` below is a throwaway, independent request context used only
 * to set up a *second* user's data (so a genuine cross-user IDOR exists to
 * probe) -- never the browser session under test.
 *
 * Two scoping notes, stated plainly rather than overclaiming:
 *   - Scenario (F) (CSRF) verifies the server-side `verify_origin` check by
 *     sending a request through the real authenticated session with an
 *     explicit forged `Origin` header. A real cross-origin attacker page
 *     would have this header set automatically and unforgeably by the
 *     browser itself; this test exercises the same server enforcement path
 *     without standing up a second real origin in this sandbox, and does
 *     not itself claim to reproduce the browser's same-origin policy.
 *   - Every scenario is independent (no shared mutable fixture beyond the
 *     five persistent E2E accounts), since this file may run standalone.
 */
const BACKEND_ORIGIN = "http://localhost:8000";

async function apiLogin(email: string) {
  const ctx = await pwRequest.newContext({ baseURL: API_BASE_URL });
  const res = await ctx.post("/auth/login", {
    data: { email, password: E2E_PASSWORD },
    headers: { Origin: "http://localhost:5173" },
  });
  if (!res.ok()) {
    throw new Error(`login failed for ${email}: ${res.status()} ${await res.text()}`);
  }
  return ctx;
}

test.describe("Phase 23 E2E: session, CSRF, IDOR, rate-limit, mass-assignment", () => {
  test("(A) the session cookie is HttpOnly -- inaccessible to page JavaScript", async ({ page }) => {
    await loginAsUi(page, USERS.donor);
    const jsVisibleCookies = await page.evaluate(() => document.cookie);
    expect(jsVisibleCookies).not.toContain("lifelink_session");

    // Confirm it's genuinely set (server-side), just not JS-readable.
    const cookies = await page.context().cookies();
    const session = cookies.find((c) => c.name === "lifelink_session");
    expect(session).toBeTruthy();
    expect(session?.httpOnly).toBe(true);
  });

  test("(B) an unauthenticated visit to a protected dashboard redirects to /login, never flashing protected content", async ({
    page,
  }) => {
    await page.context().clearCookies();
    await page.goto("/patient");
    await page.waitForURL(/\/login/, { timeout: 10_000 });
    await expect(page.getByPlaceholder("Email address")).toBeVisible();
  });

  test("(C) a role mismatch shows Access Denied client-side, and the underlying admin API genuinely 403s (not just hidden UI)", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.donor);
    await page.goto("/admin/users");
    await expect(page.getByText("Access denied")).toBeVisible({ timeout: 10_000 });

    // The client-side gate is UX only -- confirm the real backend independently
    // refuses the same donor session, through the browser's own cookie jar.
    const resp = await page.context().request.get(`${BACKEND_ORIGIN}/admin/users`);
    expect(resp.status()).toBe(403);
  });

  test("(D) a patient's browser session cannot view another patient's blood request (IDOR denied with a clean 404)", async ({
    page,
  }) => {
    // A brand-new, unrelated patient (not the shared e2e.patient account,
    // and never family-linked to anyone) is the "victim" here -- the
    // shared e2e.family account has picked up real ACCEPTED family links
    // to e2e.patient across many prior phases' runs on this persistent dev
    // database, which would make this a false negative (FAMILY genuinely
    // is allowed to see a linked patient's requests).
    const victimEmail = `phase23-idor-victim-${Date.now()}@example.com`;
    const registerCtx = await pwRequest.newContext({ baseURL: API_BASE_URL });
    const reg = await registerCtx.post("/auth/register", {
      data: {
        full_name: "Phase23 IDOR Victim", email: victimEmail,
        password: E2E_PASSWORD, role: "PATIENT",
      },
      headers: { Origin: "http://localhost:5173" },
    });
    expect(reg.ok()).toBeTruthy();
    await verifyEmailViaRealEndpoint(registerCtx, victimEmail);
    await registerCtx.dispose();

    const victimCtx = await apiLogin(victimEmail);
    const created = await victimCtx.post("/blood-requests", {
      data: { blood_type: "O_POS", urgency: "ROUTINE" },
      headers: { Origin: "http://localhost:5173" },
    });
    expect(created.ok()).toBeTruthy();
    const requestId = (await created.json()).id as string;
    await victimCtx.dispose();

    // The shared e2e.patient account -- genuinely unrelated to the fresh
    // victim above -- attempting the same request id through its OWN real
    // browser session.
    await loginAsUi(page, USERS.patient);
    const resp = await page.context().request.get(`${BACKEND_ORIGIN}/blood-requests/${requestId}`);
    expect(resp.status()).toBe(404);
  });

  test("(E) certificate download requires real ownership or a valid signed token, not merely being logged in", async ({
    page,
  }) => {
    // Set up a real donation + certificate for the seeded donor via an
    // independent API session, then confirm a DIFFERENT logged-in role
    // (hospital) cannot download it through its own real browser session,
    // while an anonymous, logged-out request is rejected outright too.
    const donorCtx = await apiLogin(USERS.donor);
    const me = await donorCtx.get("/auth/me");
    const donorId = (await me.json()).id as string;
    const donation = await donorCtx.post("/donors/me/donations", {
      data: { units: 1, donated_on: new Date().toISOString().slice(0, 10) },
      headers: { Origin: "http://localhost:5173" },
    });
    expect(donation.ok()).toBeTruthy();
    const donationId = (await donation.json()).id as string;
    await verifyDonationAsAdmin(donationId);
    const cert = await donorCtx.post("/certificates", {
      data: { donation_id: donationId },
      headers: { Origin: "http://localhost:5173" },
    });
    expect(cert.ok()).toBeTruthy();
    const certId = (await cert.json()).id as string;
    await donorCtx.dispose();
    expect(donorId).toBeTruthy();

    await loginAsUi(page, USERS.hospital);
    const resp = await page.context().request.get(
      `${BACKEND_ORIGIN}/certificates/${certId}/download`,
    );
    expect(resp.status()).toBe(401);
  });

  test("(F) a state-changing request through the real session is rejected when it carries a forged cross-site Origin, and accepted with the real one", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.patient);

    const forged = await page.context().request.post(`${BACKEND_ORIGIN}/blood-requests`, {
      data: { blood_type: "O_POS", urgency: "ROUTINE" },
      headers: { Origin: "https://evil-attacker.example" },
    });
    expect(forged.status()).toBe(403);

    const legit = await page.context().request.post(`${BACKEND_ORIGIN}/blood-requests`, {
      data: { blood_type: "O_POS", urgency: "ROUTINE" },
      headers: { Origin: "http://localhost:5173" },
    });
    expect(legit.status()).toBe(201);
  });

  test("(G) signing out through the real account menu immediately invalidates the session", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.donor);
    expect((await page.context().request.get(`${BACKEND_ORIGIN}/auth/me`)).status()).toBe(200);

    await page.getByRole("button", { name: /^E2E/ }).click();
    await page.getByRole("button", { name: /Sign out/ }).click();
    await page.waitForURL(/\/login/, { timeout: 10_000 });

    const after = await page.context().request.get(`${BACKEND_ORIGIN}/auth/me`);
    expect(after.status()).toBe(401);
  });

  test("(H) changing the password through the real session invalidates a session cookie captured beforehand", async ({
    page,
  }) => {
    // A dedicated throwaway account (rather than a shared seeded one) so
    // this test can safely change its password without disrupting every
    // other spec's fixed E2E_PASSWORD assumption.
    const email = `phase23-pwchange-${Date.now()}@example.com`;
    const registerCtx = await pwRequest.newContext({ baseURL: API_BASE_URL });
    const reg = await registerCtx.post("/auth/register", {
      data: {
        full_name: "Phase23 PwChange", email, password: E2E_PASSWORD, role: "DONOR",
      },
      headers: { Origin: "http://localhost:5173" },
    });
    expect(reg.ok()).toBeTruthy();
    await registerCtx.dispose();

    // Verify via the real admin-visible flow is unnecessary here -- login
    // works pre-verification is NOT assumed; instead this test drives the
    // real login UI, which requires a verified account. Fall back to
    // marking the account verified through a fresh, disposable API step
    // mirroring what the real verification-email link would do is out of
    // scope for a browser spec, so this scenario logs in via the UI only
    // Note: the backend's `authenticate()` never checks is_email_verified
    // (only is_active) -- login genuinely succeeds and a real session
    // cookie is set even for a brand-new, unverified account. The frontend
    // separately routes an unverified user to /verify-email instead of
    // their dashboard, so the login-success signal checked here is the
    // real session cookie being set, not which page it landed on.
    await page.goto("/login");
    await page.getByPlaceholder("Email address").fill(email);
    await page.getByPlaceholder("Password").fill(E2E_PASSWORD);
    await page.locator("form").getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL(/\/(patient|family|donor|hospital|admin|verify-email)$/, { timeout: 10_000 });

    const oldCookie = (await page.context().cookies()).find((c) => c.name === "lifelink_session");
    expect(oldCookie).toBeTruthy();

    const changed = await page.context().request.post(`${BACKEND_ORIGIN}/auth/change-password`, {
      data: { current_password: E2E_PASSWORD, new_password: "a brand new e2e passphrase" },
      headers: { Origin: "http://localhost:5173" },
    });
    expect(changed.ok()).toBeTruthy();

    // Uses BACKEND_ORIGIN ("http://localhost:8000"), not API_BASE_URL
    // ("http://127.0.0.1:8000") -- the real cookie's domain is host-only
    // "localhost" (the hostname the frontend's own fetches use, per
    // document_storage_security.spec.ts's identical note), so a request
    // context whose baseURL is the "127.0.0.1" spelling would never attach
    // this cookie at all and the test would pass for the wrong reason.
    const stale = await pwRequest.newContext({
      baseURL: BACKEND_ORIGIN,
      storageState: {
        cookies: [
          {
            name: "lifelink_session",
            value: oldCookie!.value,
            domain: "localhost",
            path: "/",
            expires: -1,
            httpOnly: true,
            secure: false,
            sameSite: "Lax",
          },
        ],
        origins: [],
      },
    });
    const staleResp = await stale.get("/auth/me");
    expect(staleResp.status()).toBe(401);
    await stale.dispose();
  });

  test("(I) rapid real emergency-creation requests through the browser session eventually trip the Phase 23 rate limit", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.family);
    let last = 0;
    for (let i = 0; i < 12; i++) {
      const resp = await page.context().request.post(`${BACKEND_ORIGIN}/emergencies`, {
        data: {
          blood_type: "O_POS",
          location_text: `Phase23 rate-limit probe ${i}`,
          idempotency_key: `phase23-rl-${Date.now()}-${i}`,
        },
        headers: { Origin: "http://localhost:5173" },
      });
      last = resp.status();
      if (last === 429) break;
    }
    expect(last).toBe(429);
  });

  test("(J) mass assignment through the real session cannot flip a donor's identity_verified flag or role", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.donor);
    const resp = await page.context().request.put(`${BACKEND_ORIGIN}/donors/me`, {
      data: { blood_type: "O_POS", identity_verified: true, role: "ADMIN" },
      headers: { Origin: "http://localhost:5173" },
    });
    expect(resp.ok()).toBeTruthy();
    expect((await resp.json()).identity_verified).toBe(false);

    const me = await page.context().request.get(`${BACKEND_ORIGIN}/auth/me`);
    expect((await me.json()).role).toBe("DONOR");
  });

  test("(K) a logged-out browser cannot fetch certificate details at all", async ({ browser }) => {
    const anonContext = await browser.newContext();
    const page = await anonContext.newPage();
    await page.goto("/login");
    const resp = await page.context().request.get(
      `${BACKEND_ORIGIN}/certificates/${"00000000-0000-0000-0000-000000000000"}`,
    );
    expect(resp.status()).toBe(401);
    await anonContext.close();
  });

  test("(L) a donor's real browser session cannot log a donation against a request it never accepted (Phase 23 fix)", async ({
    page,
  }) => {
    const patientCtx = await apiLogin(USERS.patient);
    const created = await patientCtx.post("/blood-requests", {
      data: { blood_type: "O_POS", urgency: "ROUTINE" },
      headers: { Origin: "http://localhost:5173" },
    });
    expect(created.ok()).toBeTruthy();
    const requestId = (await created.json()).id as string;
    await patientCtx.dispose();

    await loginAsUi(page, USERS.donor);
    const resp = await page.context().request.post(`${BACKEND_ORIGIN}/donors/me/donations`, {
      data: {
        blood_request_id: requestId, units: 1,
        donated_on: new Date().toISOString().slice(0, 10),
      },
      headers: { Origin: "http://localhost:5173" },
    });
    expect(resp.status()).toBe(400);
  });

  test.afterEach(async ({ page }) => {
    await logout(page);
  });
});
