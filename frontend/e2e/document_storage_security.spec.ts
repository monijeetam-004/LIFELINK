import { request as pwRequest, expect, test } from "@playwright/test";
import { loginAsUi, logout } from "./helpers";
import { API_BASE_URL, E2E_PASSWORD, USERS, ensureDonorAndHospitalProfiles } from "./setup";

/**
 * Phase 20 -- Secure Document & File Storage Hardening: real-browser E2E
 * coverage for the upload/download/authorization UX (spec section 18,
 * items A-F). Every scenario here drives the real UI against the real
 * backend and the real (local-disk, in this sandbox) storage backend --
 * nothing is mocked at the browser-API level. Network *failures* are real
 * aborted requests via `page.route(...).abort("failed")`, the same
 * technique `offline_network_failure.spec.ts` already established.
 *
 * This suite runs against a persistent dev database shared with every
 * other manual/E2E run of this project (see playwright.config.ts's own
 * comment on why `webServer` isn't used) -- across many prior phases'
 * verification-flow runs, the seeded e2e donor/hospital accounts can
 * already be sitting on a PENDING DONOR_IDENTITY/HOSPITAL_FACILITY request,
 * which would make a fresh submission 409 ("already in review") instead of
 * exercising the real upload path. `resolveAnyPending` below clears that
 * out via the real admin API before each upload test, mirroring the
 * idempotent-cleanup convention the Phase 19 fixture fixes established.
 *
 * Any request made through `page.context().request` (which shares the
 * real browser session's cookie jar, populated by the real UI login) must
 * target the exact same origin the frontend itself calls the API on --
 * `VITE_API_URL` (frontend/.env) is `http://localhost:8000`, not
 * `API_BASE_URL` from ./setup ("127.0.0.1") -- "localhost" and "127.0.0.1"
 * are different origins to a browser's cookie jar, so a hostname mismatch
 * here silently drops the session cookie and turns an authorization test
 * into an authentication test. `apiLogin`/`resolveAnyPending` below are
 * unaffected -- they use their own self-contained request contexts, never
 * the browser's cookie jar.
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

/** Rejects every PENDING verification of `kind` belonging to `email`, via
 * the real admin API -- so a fresh UI submission in this run always finds
 * a clean slate, the same guarantee `_clean` gives the backend pytest
 * suite, just done by hand against a persistent dev DB instead of a
 * TRUNCATE. */
async function resolveAnyPending(kind: "DONOR_IDENTITY" | "HOSPITAL_FACILITY", email: string) {
  const userCtx = await apiLogin(email);
  const me = await userCtx.get("/auth/me");
  const userId = (await me.json()).id as string;
  await userCtx.dispose();

  const adminCtx = await apiLogin(USERS.admin);
  const list = await adminCtx.get("/admin/verifications", {
    params: { kind, status: "PENDING" },
  });
  const rows = (await list.json()) as { id: string; user_id: string }[];
  for (const row of rows.filter((r) => r.user_id === userId)) {
    await adminCtx.post(`/admin/verifications/${row.id}/reject`, {
      data: { reason: "phase20 e2e cleanup: superseded by a fresh run" },
      headers: { Origin: "http://localhost:5173" },
    });
  }
  await adminCtx.dispose();
}

test.describe("Phase 20 E2E: secure document upload / download", () => {
  test.beforeAll(async () => {
    await ensureDonorAndHospitalProfiles();
  });

  test("(A) donor uploads a verification document through the real UI and it moves to awaiting review", async ({
    page,
  }) => {
    await resolveAnyPending("DONOR_IDENTITY", USERS.donor);
    await loginAsUi(page, USERS.donor);
    await page.goto("/donor/identity-verification");

    await page
      .locator("#id-doc-input")
      .setInputFiles({
        name: "phase20-donor-id.pdf",
        mimeType: "application/pdf",
        buffer: Buffer.from("%PDF-1.4 phase20 e2e real donor id document"),
      });
    await expect(page.getByText("phase20-donor-id.pdf")).toBeVisible();

    await page.getByRole("button", { name: "Submit for review" }).click();
    await expect(
      page.getByText("Your documents were submitted for review. We'll notify you once a decision is made."),
    ).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("Submitted — awaiting review")).toBeVisible({ timeout: 10_000 });
  });

  test("(B) hospital uploads a facility verification document through the real UI and it moves to awaiting review", async ({
    page,
  }) => {
    await resolveAnyPending("HOSPITAL_FACILITY", USERS.hospital);
    await loginAsUi(page, USERS.hospital);
    await page.goto("/hospital/verification");

    await page
      .locator("#facility-doc-input")
      .setInputFiles({
        name: "phase20-facility-license.pdf",
        mimeType: "application/pdf",
        buffer: Buffer.from("%PDF-1.4 phase20 e2e real facility license"),
      });
    await expect(page.getByText("phase20-facility-license.pdf")).toBeVisible();

    await page.getByRole("button", { name: "Submit for review" }).click();
    await expect(page.getByText("Submitted — awaiting review")).toBeVisible({ timeout: 10_000 });
  });

  test("(C) an authorized admin can download the submitted document, privately (never cached)", async ({
    page,
  }) => {
    // The upload from test (A) is still PENDING (this describe block runs
    // serially -- playwright.config.ts sets fullyParallel:false, workers:1).
    await loginAsUi(page, USERS.admin);
    await page.goto("/admin/verifications");
    await page.getByRole("button", { name: "Donor identity", exact: true }).click();
    // Open the most recent donor-identity item in the queue -- whichever
    // row test (A) just created. Filtered on "PENDING" (not just the kind
    // label) to avoid also matching the type-filter tab button above,
    // which shares the same "Donor identity" text but no status badge.
    await page
      .locator("button")
      .filter({ hasText: "Donor identity" })
      .filter({ hasText: "PENDING" })
      .first()
      .click();

    const docLink = page.getByRole("link", { name: /phase20-donor-id\.pdf/ });
    await expect(docLink).toBeVisible({ timeout: 10_000 });
    const href = await docLink.getAttribute("href");
    expect(href).toBeTruthy();

    // Fetch the real document URL using the admin's own authenticated
    // browser session (shared cookies via the page's request context) --
    // a genuine authorized download through the real backend/storage,
    // without the flakiness of asserting on a new browser tab's rendered
    // PDF viewer.
    const resp = await page.context().request.get(href as string);
    expect(resp.status()).toBe(200);
    expect(resp.headers()["content-type"]).toContain("application/pdf");
    expect(resp.headers()["cache-control"]).toBe("private, no-store");
    expect((await resp.body()).toString()).toContain("phase20 e2e real donor id document");
  });

  test("(D) a logged-in non-admin cannot fetch another user's verification document (IDOR denied, not a fake 200)", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.hospital);
    // The hospital's own session has no admin document route access at
    // all -- even a syntactically well-formed guess at the admin endpoint
    // must come back denied, never a document.
    const adminCtx = await apiLogin(USERS.admin);
    const list = await adminCtx.get("/admin/verifications", {
      params: { kind: "DONOR_IDENTITY", status: "PENDING" },
    });
    const rows = (await list.json()) as { id: string }[];
    await adminCtx.dispose();
    test.skip(rows.length === 0, "no PENDING donor_identity request currently exists to probe");

    const resp = await page.context().request.get(
      `${BACKEND_ORIGIN}/admin/verifications/${rows[0].id}/documents/0`,
    );
    expect(resp.status()).toBe(403);
  });

  test("(E) a verification upload failure shows an honest error and keeps the picked files, and a retry after recovery succeeds", async ({
    page,
  }) => {
    await resolveAnyPending("DONOR_IDENTITY", USERS.donor);
    await loginAsUi(page, USERS.donor);
    await page.goto("/donor/identity-verification");

    await page
      .locator("#id-doc-input")
      .setInputFiles({
        name: "phase20-retry-id.pdf",
        mimeType: "application/pdf",
        buffer: Buffer.from("%PDF-1.4 phase20 e2e retry document"),
      });

    await page.route("**/verifications", (route) => {
      if (route.request().method() === "POST") return route.abort("failed");
      return route.continue();
    });
    await page.getByRole("button", { name: "Submit for review" }).click();
    await expect(
      page.getByText("Can't reach the server. Check your connection and try again."),
    ).toBeVisible({ timeout: 10_000 });
    // The picked file wasn't silently dropped by the failed attempt.
    await expect(page.getByText("phase20-retry-id.pdf")).toBeVisible();
    await expect(page.getByRole("button", { name: "Submit for review" })).toBeEnabled();

    await page.unroute("**/verifications");
    await page.getByRole("button", { name: "Submit for review" }).click();
    await expect(
      page.getByText("Your documents were submitted for review. We'll notify you once a decision is made."),
    ).toBeVisible({ timeout: 10_000 });
  });

  test("(F) a nonexistent document index returns a clean not-found from the API, never a broken/blank viewer", async ({
    page,
  }) => {
    await loginAsUi(page, USERS.admin);
    const list = await page.context().request.get(`${BACKEND_ORIGIN}/admin/verifications`, {
      params: { kind: "DONOR_IDENTITY", status: "PENDING" },
    });
    const rows = (await list.json()) as { id: string }[];
    test.skip(rows.length === 0, "no PENDING donor_identity request currently exists to probe");

    const resp = await page.context().request.get(
      `${BACKEND_ORIGIN}/admin/verifications/${rows[0].id}/documents/99`,
    );
    expect(resp.status()).toBe(404);
    const body = await resp.json();
    expect(body.detail).toBe("Document not found");
  });

  test.afterEach(async ({ page }) => {
    await logout(page);
  });
});
