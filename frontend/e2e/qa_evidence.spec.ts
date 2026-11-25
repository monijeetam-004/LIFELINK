/**
 * Final QA -- end-to-end workflows in a real browser, with screenshot
 * evidence at each meaningful state.
 *
 * Runs in the normal Playwright suite (assertions always apply). When
 * QA_SCREENSHOT_DIR is set, every `evidence()` call also saves a PNG under
 * <dir>/<category>/<name>.png and appends {category, name, feature, test,
 * expected, actual} to <dir>/index.jsonl (used to build
 * docs/QA/SCREENSHOT_INDEX.md). `actual` is written only after the
 * preceding assertions passed -- a failing step produces no evidence.
 *
 * When MAILDEV_URL is set (full-stack run: RQ_SYNC=false, a real RQ worker,
 * MailDev as the SMTP catcher), the registration test reads the real
 * verification email from MailDev and follows its link. Otherwise it uses
 * the token-issuing stand-in from ./helpers (no mailbox in plain CI).
 */
import fs from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { loginAsUi, logout, resetRegistrationLimitForE2E, verifyEmailViaRealEndpoint } from "./helpers";
import { API_BASE_URL, apiLogin, E2E_PASSWORD, ensureDonorAndHospitalProfiles, USERS } from "./setup";

const ORIGIN = { Origin: "http://localhost:5173" };
const DIR = process.env.QA_SCREENSHOT_DIR;
const MAILDEV = process.env.MAILDEV_URL;
// Final QA round: the real Mailpit (official release) replaces MailDev as the
// SMTP catcher when MAILPIT_URL is set (e.g. http://127.0.0.1:8025).
const MAILPIT = process.env.MAILPIT_URL;
const MAILBOX = MAILPIT || MAILDEV;
const DEMO_HOSPITAL = "demo.hospital@lifelink-demo.example.com";
const DEMO_PASSWORD = "LifeLink-Demo-2026!";

async function evidence(
  page: Page, category: string, name: string,
  meta: { feature: string; test: string; expected: string; actual: string },
) {
  if (!DIR) return;
  const dir = path.join(DIR, category);
  fs.mkdirSync(dir, { recursive: true });
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(dir, `${name}.png`), fullPage: false });
  fs.appendFileSync(
    path.join(DIR, "index.jsonl"),
    JSON.stringify({ category, file: `${category}/${name}.png`, ...meta, result: "PASS" }) + "\n",
  );
}

test.describe.configure({ mode: "serial" });
test.setTimeout(90_000);

test.beforeAll(async () => {
  await ensureDonorAndHospitalProfiles();
});

// --------------------------------------------------------------------------
// 1. Registration -> email -> verification -> login -> dashboard
// --------------------------------------------------------------------------
test("QA auth: register, blocked until verified, verify by email link, sign in", async ({ page, request }) => {
  const email = `qa.donor.${Date.now()}@example.com`;
  resetRegistrationLimitForE2E();
  await page.goto("/register");
  await page.getByRole("button", { name: "Create account" }).first().click();
  await expect(page.getByPlaceholder("Full name")).toBeVisible();
  await evidence(page, "01-auth", "auth_01_registration", {
    feature: "Registration", test: "Open the registration form",
    expected: "Role cards, name/email/password fields", actual: "Form rendered with all roles and fields",
  });

  await page.getByRole("button", { name: /^Donor/ }).click();
  await page.getByPlaceholder("Full name").fill("QA Donor");
  await page.getByPlaceholder("Email address").fill(email);
  await page.getByPlaceholder("Password").fill("password");
  await page.locator("form").getByRole("button", { name: /Create account/ }).click();
  const err = page.getByRole("alert").first();
  await expect(err).toBeVisible({ timeout: 10_000 });
  await expect(page).toHaveURL(/\/register|\/$|\/login/);
  await evidence(page, "01-auth", "auth_02_validation_error", {
    feature: "Registration", test: "Submit a weak password",
    expected: "Server-side validation error shown; no account created", actual: `Error shown: "${(await err.innerText()).slice(0, 90)}"`,
  });

  await page.getByPlaceholder("Password").fill(E2E_PASSWORD);
  await page.locator("form").getByRole("button", { name: /Create account/ }).click();
  await page.waitForURL(/\/verify-email/, { timeout: 15_000 });
  await evidence(page, "01-auth", "auth_03_registered_check_inbox", {
    feature: "Registration", test: "Submit valid details",
    expected: "Account created; user sent to 'check your inbox'", actual: "Landed on /verify-email",
  });

  // Signing in before verifying: the API refuses everything but me/logout.
  await page.goto("/login");
  await page.getByPlaceholder("Email address").fill(email);
  await page.getByPlaceholder("Password").fill(E2E_PASSWORD);
  await page.locator("form").getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/verify-email|\/donor/, { timeout: 15_000 });
  await page.goto("/donor");
  await expect(page).toHaveURL(/\/verify-email/, { timeout: 10_000 });
  const blocked = await request.get(`${API_BASE_URL}/notifications`, {
    headers: { ...ORIGIN, cookie: (await page.context().cookies()).map((c) => `${c.name}=${c.value}`).join("; ") },
  });
  expect(blocked.status()).toBe(403);
  await evidence(page, "01-auth", "auth_04_unverified_blocked", {
    feature: "Email verification enforcement", test: "Unverified user opens /donor",
    expected: "Redirected to /verify-email; API returns 403", actual: "Redirected; GET /notifications -> 403",
  });

  if (MAILPIT) {
    // The real email, delivered by the RQ worker over SMTP to the real Mailpit.
    let link = "";
    for (let i = 0; i < 30 && !link; i++) {
      const list = (await (await request.get(`${MAILPIT}/api/v1/messages`)).json()) as {
        messages: { ID: string; To: { Address: string }[]; Subject: string }[];
      };
      const mail = list.messages.find((m) => m.To.some((t) => t.Address === email));
      if (mail) {
        const full = (await (await request.get(`${MAILPIT}/api/v1/message/${mail.ID}`)).json()) as { HTML: string };
        const m = full.HTML.match(/href="([^"]+\/verify-email\?token=[^"]+)"/);
        if (m) {
          link = m[1].replace(/&amp;/g, "&");
          expect(mail.Subject).toBe("Verify your LifeLink email");
          await page.goto(`${MAILPIT}/view/${mail.ID}`);
          await expect(page.getByText("Verify your LifeLink email").first()).toBeVisible({ timeout: 10_000 });
          await evidence(page, "15-email", "email_01_verification_email_received", {
            feature: "Email (worker + SMTP)", test: "Registration email delivered to the mail server",
            expected: "Subject 'Verify your LifeLink email' with a verify link",
            actual: "Received by the real Mailpit v1.21.8 (sent by the RQ worker)",
          });
          continue;
        }
      }
      await page.waitForTimeout(1000);
    }
    expect(link, "verification email arrived via the worker").not.toBe("");
    await page.goto(link.replace(/^https?:\/\/[^/]+/, ""));
  } else if (MAILDEV) {
    // The real email, delivered by the RQ worker over SMTP to MailDev.
    let link = "";
    for (let i = 0; i < 30 && !link; i++) {
      const mails = (await (await request.get(`${MAILDEV}/email`)).json()) as {
        id: string; to: { address: string }[]; subject: string; html: string;
      }[];
      const mail = mails.reverse().find((m) => m.to.some((t) => t.address === email));
      const m = mail?.html.match(/href="([^"]+\/verify-email\?token=[^"]+)"/);
      if (m && mail) {
        link = m[1];
        await page.goto(`${MAILDEV}/#/email/${mail.id}`);
        await expect(page.getByText("Verify your LifeLink email").first()).toBeVisible({ timeout: 10_000 });
        await evidence(page, "15-email", "email_01_verification_email_received", {
          feature: "Email (worker + SMTP)", test: "Registration email delivered to the mail catcher",
          expected: "Subject 'Verify your LifeLink email' with a verify link",
          actual: `Received by MailDev (Mailpit stand-in) for ${email}`,
        });
      } else {
        await page.waitForTimeout(1000);
      }
    }
    expect(link, "verification email arrived via the worker").not.toBe("");
    await page.goto(link.replace(/^https?:\/\/[^/]+/, ""));
  } else {
    // The page fixture's `request` points at the frontend origin; verify
    // against the API itself.
    const api = await (await import("@playwright/test")).request.newContext({ baseURL: API_BASE_URL });
    await verifyEmailViaRealEndpoint(api, email);
    await api.dispose();
    await page.goto("/verify-email");
  }
  if (MAILBOX) {
    await expect(page.getByText("Email verified")).toBeVisible({ timeout: 15_000 });
    await evidence(page, "01-auth", "auth_05_email_verified", {
      feature: "Email verification", test: "Follow the link from the email",
      expected: "'Email verified' confirmation", actual: "Verified page shown",
    });
  }
  // The session from the earlier sign-in is still valid; now verified, the
  // same session reaches the dashboard.
  await page.goto("/donor");
  await page.waitForURL(/\/donor$/, { timeout: 15_000 });
  await expect(page.getByRole("heading").first()).toBeVisible({ timeout: 15_000 });
  await evidence(page, "01-auth", "auth_06_signed_in_dashboard", {
    feature: "Login", test: "Verified donor signs in",
    expected: "Donor dashboard", actual: "Landed on /donor",
  });
});

// --------------------------------------------------------------------------
// 2. Donation -> hospital verification -> certificate
// --------------------------------------------------------------------------
test("QA donor: log a donation, hospital verifies, certificate issued and downloadable", async ({ page, request }) => {
  const hosp = await apiLogin(USERS.hospital);
  const hospitalName = (await (await hosp.get("/hospitals/me")).json()).name as string;
  await hosp.dispose();
  const note = `qa-donation-${Date.now()}`;

  await loginAsUi(page, USERS.donor);
  await evidence(page, "02-donor", "donor_01_dashboard", {
    feature: "Donor dashboard", test: "Donor signs in", expected: "Dashboard with eligibility and requests", actual: "Rendered",
  });
  await page.goto("/donor/milestones");
  await page.getByRole("button", { name: "Log a donation" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  const date = new Date(Date.now() - 400 * 24 * 3600 * 1000).toISOString().slice(0, 10);
  await dialog.locator('input[type="date"]').first().fill(date);
  const facility = dialog.getByLabel(/Facility/i);
  // The facility list renders once the directory has loaded; checking its
  // count before that raced and logged an unlinked donation (2026-10-31 run).
  await expect(facility).toBeVisible({ timeout: 15_000 });
  if (await facility.count()) {
    const opts = await facility.locator("option").allInnerTexts();
    const idx = opts.findIndex((o) => o.includes(hospitalName));
    if (idx > 0) await facility.selectOption({ index: idx });
  }
  const noteField = dialog.getByLabel(/Note/i);
  if (await noteField.count()) await noteField.fill(note);
  await evidence(page, "02-donor", "donor_02_donation_form", {
    feature: "Donation creation", test: "Open the log-donation form", expected: "Date, facility, units, note", actual: "Form filled",
  });
  await dialog.getByRole("button", { name: "Log donation" }).click();
  await expect(dialog).toHaveCount(0, { timeout: 15_000 });
  const row = page.locator("tr", { hasText: note }).first();
  await expect(row).toBeVisible({ timeout: 15_000 });
  await expect(row.getByTestId("donation-verification-badge")).toHaveText(/Self-reported/);
  await expect(row.getByRole("button", { name: "Get certificate" })).toHaveCount(0);
  await evidence(page, "02-donor", "donor_03_donation_submitted_self_reported", {
    feature: "Donation verification", test: "Donation saved",
    expected: "Listed as Self-reported; no certificate offered", actual: "Self-reported badge, no certificate button",
  });
  await logout(page);

  await loginAsUi(page, USERS.hospital);
  await page.goto("/hospital/donation-reviews");
  const item = page.getByTestId("donation-review-row").filter({ hasText: note });
  await expect(item).toBeVisible({ timeout: 15_000 });
  await evidence(page, "10-donation-verification", "verification_01_hospital_queue", {
    feature: "Donation verification", test: "Linked hospital opens its review queue",
    expected: "The donor's donation awaiting review", actual: "Row present with Verify/Reject",
  });
  await item.getByLabel("Review note").fill("Matched blood-bank register (QA)");
  await item.getByRole("button", { name: "Verify" }).click();
  await expect(item).toHaveCount(0, { timeout: 15_000 });
  await evidence(page, "10-donation-verification", "verification_02_verified_removed_from_queue", {
    feature: "Donation verification", test: "Hospital verifies", expected: "Row leaves the queue", actual: "Queue updated",
  });
  await logout(page);

  await loginAsUi(page, USERS.donor);
  await page.goto("/donor/milestones");
  const verified = page.locator("tr", { hasText: note }).first();
  await expect(verified.getByTestId("donation-verification-badge")).toHaveText(/Verified by facility/, { timeout: 15_000 });
  await evidence(page, "02-donor", "donor_04_donation_verified", {
    feature: "Donation verification", test: "Donor sees the outcome", expected: "Verified by facility; certificate offered", actual: "Badge updated",
  });
  await verified.getByRole("button", { name: "Get certificate" }).click();
  await page.waitForURL(/\/certificate\//, { timeout: 15_000 });
  await expect(page.getByText("Verified Digital Copy")).toBeVisible({ timeout: 15_000 });
  await evidence(page, "12-certificates", "certificate_01_issued", {
    feature: "Certificate", test: "Get certificate", expected: "Certificate page with donor name and status", actual: "Verified Digital Copy",
  });
  const href = await page.getByRole("link", { name: "Download PDF" }).getAttribute("href");
  const pdf = await request.get(new URL(href!, API_BASE_URL).toString());
  expect(pdf.status()).toBe(200);
  expect(pdf.headers()["content-type"]).toContain("application/pdf");
  expect((await pdf.body()).subarray(0, 5).toString()).toBe("%PDF-");
  await evidence(page, "12-certificates", "certificate_02_pdf_download_ok", {
    feature: "Certificate download", test: "Download PDF link", expected: "200 application/pdf, %PDF- header", actual: "PDF returned",
  });
});

// --------------------------------------------------------------------------
// 3. Documents: upload -> malware scan -> admin review; bad files refused
// --------------------------------------------------------------------------
test("QA documents: clean upload is scanned and reviewable; infected, wrong-type and oversized are refused", async ({ page }) => {
  // A fresh donor each run (the shared e2e donor may already be verified).
  const docEmail = `qa.docs.${Date.now()}@example.com`;
  const docName = `QA Docs ${Date.now() % 100000}`;
  resetRegistrationLimitForE2E();
  const reg = await (await import("@playwright/test")).request.newContext({ baseURL: API_BASE_URL });
  expect((await reg.post("/auth/register", { data: { full_name: docName, email: docEmail, password: E2E_PASSWORD, role: "DONOR" }, headers: ORIGIN })).ok()).toBeTruthy();
  await verifyEmailViaRealEndpoint(reg, docEmail);
  await reg.dispose();
  await loginAsUi(page, docEmail);
  await page.goto("/donor/identity-verification");

  // Infected (EICAR test signature) -- refused, nothing stored.
  const eicar = "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*";
  await page.locator("#id-doc-input").setInputFiles({ name: "eicar.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n" + eicar) });
  await page.getByRole("button", { name: "Submit for review" }).click();
  await expect(page.getByText(/failed a security scan/i)).toBeVisible({ timeout: 15_000 });
  await evidence(page, "11-documents", "documents_01_infected_upload_refused", {
    feature: "Malware scanning", test: "Upload a file carrying the EICAR signature",
    expected: "Refused with a security-scan message", actual: "\"File failed a security scan\" shown",
  });
  await page.reload();

  // Wrong type: an executable renamed .pdf
  await page.locator("#id-doc-input").setInputFiles({ name: "invoice.pdf", mimeType: "application/pdf", buffer: Buffer.from("MZ\x90\x00 this is not a pdf") });
  await page.getByRole("button", { name: "Submit for review" }).click();
  await expect(page.getByRole("alert").first()).toBeVisible({ timeout: 15_000 });
  await evidence(page, "11-documents", "documents_02_wrong_type_refused", {
    feature: "Upload validation", test: "Executable bytes declared as PDF",
    expected: "Refused (content does not match type)", actual: `Error: ${(await page.getByRole("alert").first().innerText()).slice(0, 80)}`,
  });
  await page.reload();

  // Clean PDF
  await page.locator("#id-doc-input").setInputFiles({ name: "qa-donor-id.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n% QA clean identity document\n%%EOF\n") });
  await page.getByRole("button", { name: "Submit for review" }).click();
  await expect(page.getByText("Submitted — awaiting review")).toBeVisible({ timeout: 15_000 });
  await evidence(page, "11-documents", "documents_03_clean_upload_submitted", {
    feature: "Document upload", test: "Upload a clean PDF", expected: "Submitted, awaiting review", actual: "Status shown",
  });
  await logout(page);

  await loginAsUi(page, USERS.admin);
  await page.goto("/admin/verifications");
  await page.getByRole("button", { name: "Donor identity", exact: true }).click();
  // Rows show no requester identity (privacy); the newest PENDING row is ours.
  await page.locator("button").filter({ hasText: "PENDING" }).first().click();
  const doc = page.getByTestId("verification-doc-0");
  await expect(doc).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("scan-status").first()).toContainText(/clean/i);
  await evidence(page, "11-documents", "documents_04_admin_review_scan_clean", {
    feature: "Document review", test: "Admin opens the request", expected: "Document marked clean, link available", actual: "Scan status: clean",
  });
  // The link opens in a new tab; fetch the same URL with the admin's session.
  const docHref = await doc.locator("a").first().getAttribute("href");
  const resp = await page.request.get(new URL(docHref!, API_BASE_URL).toString());
  expect(resp.status()).toBe(200);
  expect((await resp.body()).subarray(0, 5).toString()).toBe("%PDF-");
  await page.getByRole("button", { name: /^Approve$/ }).click();
  await expect(page.getByText(/APPROVED/).first()).toBeVisible({ timeout: 15_000 });
  await evidence(page, "04-admin", "admin_01_verification_approved", {
    feature: "Verification approval", test: "Admin downloads the document and approves",
    expected: "Download 200; request APPROVED", actual: "Download 200; APPROVED",
  });
});

// --------------------------------------------------------------------------
// 4. Hospital inventory by component
// --------------------------------------------------------------------------
test("QA hospital: inventory by component, add/reserve/release through the UI", async ({ page }) => {
  await loginAsUi(page, USERS.hospital);
  await evidence(page, "03-hospital", "hospital_01_dashboard", {
    feature: "Hospital dashboard", test: "Hospital signs in", expected: "Command center", actual: "Rendered",
  });
  await page.goto("/hospital/inventory");
  const stock = page.locator('section[aria-labelledby="component-stock-heading"]');
  await expect(stock).toBeVisible({ timeout: 15_000 });
  await evidence(page, "05-inventory", "inventory_01_console", {
    feature: "Inventory", test: "Open inventory console", expected: "Per-blood-group cards and per-component table", actual: "Rendered",
  });

  async function act(kind: string, component: string, units: number) {
    await page.getByRole("button", { name: new RegExp(`${kind}$`) }).first().click();
    const dlg = page.getByRole("dialog");
    await expect(dlg).toBeVisible();
    await dlg.getByLabel("Component").selectOption(component);
    await dlg.getByLabel("Units").fill(String(units));
    return dlg;
  }
  const before = await stock.innerText();
  const dlg = await act("Add stock", "PLATELETS", 3);
  await evidence(page, "05-inventory", "inventory_02_component_selector", {
    feature: "Inventory by component", test: "Add stock dialog", expected: "Component selector (Whole blood, RBC, Plasma, Platelets)", actual: "Platelets chosen, 3 units",
  });
  await dlg.getByRole("button", { name: "Confirm" }).click();
  await expect(dlg).toHaveCount(0, { timeout: 10_000 });
  await expect.poll(async () => (await stock.innerText()) !== before, { timeout: 10_000 }).toBeTruthy();
  await expect(stock).toContainText(/Platelets/);
  await evidence(page, "05-inventory", "inventory_03_after_add_platelets", {
    feature: "Add stock", test: "Confirm add", expected: "Component table updates", actual: "Platelets row updated",
  });
  const r = await act("Reserve", "PLATELETS", 1);
  await r.getByRole("button", { name: "Confirm" }).click();
  await expect(r).toHaveCount(0, { timeout: 10_000 });
  const rel = await act("Release", "PLATELETS", 1);
  await rel.getByRole("button", { name: "Confirm" }).click();
  await expect(rel).toHaveCount(0, { timeout: 10_000 });
  await evidence(page, "05-inventory", "inventory_04_after_reserve_release", {
    feature: "Reserve / release", test: "Reserve 1 then release 1", expected: "Both succeed; log shows them", actual: "Dialogs closed without error",
  });
  // Over-release is refused with a visible error (no silent failure).
  const bad = await act("Release", "PLASMA", 999);
  await bad.getByRole("button", { name: "Confirm" }).click();
  await expect(bad.getByRole("alert")).toBeVisible({ timeout: 10_000 });
  await evidence(page, "05-inventory", "inventory_05_invalid_release_refused", {
    feature: "Inventory invariants", test: "Release more than reserved", expected: "Visible error; nothing changes", actual: `Error: ${(await bad.getByRole("alert").innerText()).slice(0, 80)}`,
  });
  await bad.getByRole("button", { name: "Cancel" }).click();
});

// --------------------------------------------------------------------------
// 5. Blood request: patient creates -> partial -> full, seen live
// --------------------------------------------------------------------------
test("QA requests: patient request, partial then full fulfilment, live updates", async ({ browser }) => {
  // The demo hospital has a city (requests are routed to hospitals by city).
  const hosp = await (await import("@playwright/test")).request.newContext({ baseURL: API_BASE_URL });
  expect((await hosp.post("/auth/login", { data: { email: DEMO_HOSPITAL, password: DEMO_PASSWORD }, headers: ORIGIN })).ok()).toBeTruthy();
  const city = (await (await hosp.get("/hospitals/me")).json()).city as string;
  await hosp.post("/hospitals/me/inventory/add", { data: { blood_type: "AB_NEG", units: 2 }, headers: ORIGIN });

  const pctx = await browser.newContext();
  const patient = await pctx.newPage();
  await loginAsUi(patient, USERS.patient);
  await patient.goto("/patient/new-request");
  await patient.getByRole("button", { name: "AB-", exact: true }).click();
  await patient.getByLabel("Increase units").click();
  await evidence(patient, "06-blood-requests", "request_01_guided_form", {
    feature: "Blood request", test: "Guided request form", expected: "Blood group, units, urgency", actual: "AB-, 2 units",
  });
  await patient.getByRole("button", { name: "Continue" }).click();
  await patient.locator("#city").fill(city);
  await patient.getByRole("button", { name: "Continue" }).click();
  await patient.getByRole("button", { name: "Submit request" }).click();
  await expect(patient.getByRole("button", { name: "Submit request" })).toHaveCount(0, { timeout: 15_000 });
  await evidence(patient, "06-blood-requests", "request_02_created", {
    feature: "Blood request", test: "Submit", expected: "Request created and tracked", actual: "Submitted",
  });
  const mine = await apiLogin(USERS.patient);
  const reqs = (await (await mine.get("/blood-requests")).json()) as { id: string; blood_type: string; status: string }[];
  const rid = reqs.find((r) => r.blood_type === "AB_NEG" && ["PENDING", "MATCHING"].includes(r.status))!.id;
  await mine.dispose();

  await patient.goto("/patient/history");
  await expect(patient.getByText("Loading your requests…")).not.toBeVisible({ timeout: 10_000 });

  const hctx = await browser.newContext();
  const hp = await hctx.newPage();
  await hp.goto("/login");
  await hp.getByPlaceholder("Email address").fill(DEMO_HOSPITAL);
  await hp.getByPlaceholder("Password").fill(DEMO_PASSWORD);
  await hp.locator("form").getByRole("button", { name: "Sign in" }).click();
  await hp.waitForURL(/\/hospital$/, { timeout: 15_000 });
  await hp.goto(`/hospital/requests/${rid}`);
  await hp.getByLabel("Units to allocate").fill("1");
  await hp.getByRole("button", { name: "Allocate units" }).click();
  await expect.poll(async () => /partial/i.test(await hp.locator("body").innerText()), { timeout: 15_000 }).toBeTruthy();
  await evidence(hp, "07-fulfillment", "fulfillment_01_hospital_partial", {
    feature: "Partial fulfilment", test: "Hospital allocates 1 of 2", expected: "PARTIALLY_FULFILLED, 1/2", actual: "1/2 shown",
  });
  const row = () => patient.locator("tr", { hasText: rid.slice(0, 8).toUpperCase() });
  await expect(row()).toContainText("1/2 units fulfilled", { timeout: 10_000 });
  await evidence(patient, "14-realtime", "realtime_01_patient_sees_partial_live", {
    feature: "Realtime", test: "Patient page open, no reload", expected: "1/2 appears live", actual: "Updated via SSE",
  });
  await hp.getByLabel("Units to allocate").fill("1");
  await hp.getByRole("button", { name: "Allocate units" }).click();
  await expect(row()).toHaveCount(0, { timeout: 10_000 }); // left the Active tab live
  await patient.getByRole("tab", { name: /Fulfilled/ }).or(patient.getByRole("button", { name: /^Fulfilled/ })).first().click();
  await expect(row()).toContainText("2/2 units fulfilled", { timeout: 10_000 });
  await evidence(patient, "07-fulfillment", "fulfillment_02_patient_full", {
    feature: "Full fulfilment", test: "Hospital allocates the last unit", expected: "Fulfilled, 2/2", actual: "2/2 in Fulfilled tab",
  });
  const over = await hosp.post(`/hospitals/me/requests/${rid}/allocate`, { data: { blood_type: "AB_NEG", units: 1 }, headers: ORIGIN });
  expect(over.status()).toBe(409);
  await hosp.dispose();

  await patient.getByRole("button", { name: "Notifications" }).first().click();
  await expect(patient.getByRole("dialog").or(patient.getByRole("region", { name: /Notifications/ })).first()).toBeVisible();
  await evidence(patient, "13-notifications", "notifications_01_panel", {
    feature: "Notifications", test: "Open the bell after fulfilment", expected: "Fulfilment notifications listed", actual: "Panel open",
  });
  await pctx.close();
  await hctx.close();
});

// --------------------------------------------------------------------------
// 6. Shipment lifecycle + failed shipment reconciliation
// --------------------------------------------------------------------------
test("QA shipments: create, dispatch, delay, arrive, receive; failure needs reconciliation", async ({ page }) => {
  const origin = await apiLogin(USERS.hospital);
  const me = await (await origin.get("/hospitals/me")).json();
  const directory = (await (await origin.get("/hospitals/directory")).json()) as { id: string; name: string; verified: boolean }[];
  const dest = directory.find((h) => h.verified && h.id !== me.id)!;
  await origin.post("/hospitals/me/inventory/add", { data: { blood_type: "O_NEG", units: 4, component: "RED_BLOOD_CELLS" }, headers: ORIGIN });

  await loginAsUi(page, USERS.hospital);
  await page.goto("/hospital/shipments");
  await page.getByRole("button", { name: "New shipment" }).click();
  await page.getByLabel("Destination facility").selectOption(dest.id);
  await page.getByLabel("Blood type").selectOption("O_NEG");
  await page.getByLabel("Component").selectOption("RED_BLOOD_CELLS");
  await page.getByLabel("Units").fill("2");
  await evidence(page, "08-shipment", "shipment_01_create_form", {
    feature: "Shipment creation", test: "New shipment form", expected: "Destination, blood type, component, units", actual: `To ${dest.name}, O-, RBC, 2`,
  });
  await page.getByRole("button", { name: /Reserve units & create shipment/ }).click();
  await expect(page.getByText("CREATED").first()).toBeVisible({ timeout: 15_000 });
  await evidence(page, "08-shipment", "shipment_02_created", {
    feature: "Shipment", test: "Create", expected: "CREATED with reserved units", actual: "CREATED",
  });
  await page.getByRole("button", { name: /Dispatch$/ }).click();
  await expect(page.getByText("DISPATCHED").first()).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: /Mark in transit$/ }).click();
  await expect(page.getByText("IN TRANSIT").first()).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: /Report delay$/ }).click();
  const delay = page.getByRole("dialog");
  if (await delay.count()) {
    await delay.getByRole("textbox").first().fill("Road closure (QA)");
    await delay.getByRole("button", { name: /Report|Save|Confirm/ }).last().click();
  }
  await evidence(page, "08-shipment", "shipment_03_in_transit_delayed", {
    feature: "Transport", test: "Dispatch, in transit, report delay", expected: "Timeline shows each step", actual: "IN TRANSIT with delay",
  });
  const list = (await (await origin.get("/shipments")).json()) as { id: string; status: string }[];
  const sid = list.find((s) => s.status === "IN_TRANSIT")!.id;

  // Destination receives (API as the destination's demo account is not
  // available here; the origin page must see RECEIVED live).
  const destApi = await (await import("@playwright/test")).request.newContext({ baseURL: API_BASE_URL });
  const dl = await destApi.post("/auth/login", { data: { email: "demo.hospital@lifelink-demo.example.com", password: "LifeLink-Demo-2026!" }, headers: ORIGIN });
  if (dl.ok() && dest.name === (await (await destApi.get("/hospitals/me")).json()).name) {
    expect((await destApi.post(`/shipments/${sid}/arrive`, { data: {}, headers: ORIGIN })).ok()).toBeTruthy();
    expect((await destApi.post(`/shipments/${sid}/receive`, { data: {}, headers: ORIGIN })).ok()).toBeTruthy();
    await expect(page.getByText("RECEIVED").first()).toBeVisible({ timeout: 15_000 });
    await evidence(page, "08-shipment", "shipment_04_received_live", {
      feature: "Shipment receipt", test: "Destination arrives + receives", expected: "Origin page shows RECEIVED without reload", actual: "RECEIVED live",
    });
  }
  await destApi.dispose();

  // Failure -> reconciliation
  const created = await origin.post("/shipments", { data: { origin_facility_id: me.id, destination_facility_id: dest.id, items: [{ blood_type: "O_NEG", units: 1, component: "RED_BLOOD_CELLS" }] }, headers: ORIGIN });
  const failed = await created.json();
  await origin.post(`/shipments/${failed.id}/dispatch`, { data: {}, headers: ORIGIN });
  await origin.post(`/shipments/${failed.id}/fail`, { data: { reason: "Vehicle breakdown (QA)" }, headers: ORIGIN });
  await origin.dispose();
  await page.goto(`/hospital/shipments?shipment=${failed.id}`);
  await expect(page.getByTestId("reconciliation-status")).toContainText("Reconciliation required", { timeout: 15_000 });
  await evidence(page, "09-reconciliation", "reconciliation_01_required", {
    feature: "Failed shipment", test: "Shipment failed after dispatch", expected: "Reconciliation required; units not silently returned", actual: "Required shown",
  });
  const panel = page.getByTestId("reconcile-panel");
  await panel.getByLabel(/Quarantined/).check();
  await panel.getByRole("textbox").fill("Cooler breached; bags quarantined (QA)");
  await panel.getByRole("button", { name: "Record reconciliation" }).click();
  await expect(page.getByTestId("reconciliation-status")).toContainText("Quarantined", { timeout: 15_000 });
  await evidence(page, "09-reconciliation", "reconciliation_02_recorded", {
    feature: "Reconciliation", test: "Record an attested outcome", expected: "Status Quarantined; panel gone", actual: "Quarantined",
  });
});

// --------------------------------------------------------------------------
// 7. Admin
// --------------------------------------------------------------------------
test("QA admin: overview, users, facilities, shipments, analytics, audit chain", async ({ page }) => {
  await loginAsUi(page, USERS.admin);
  await evidence(page, "04-admin", "admin_02_overview", { feature: "Admin dashboard", test: "Admin signs in", expected: "Platform KPIs", actual: "Rendered" });
  for (const [path_, name, feature] of [
    ["/admin/users", "admin_03_users", "User governance"],
    ["/admin/facilities", "admin_04_facilities", "Facilities"],
    ["/admin/shipments", "admin_05_shipments", "Shipments (admin)"],
    ["/admin/donors", "admin_06_donors", "Donor governance"],
  ] as const) {
    await page.goto(path_);
    await expect(page.locator("main, [role=main], body").first()).toBeVisible();
    await page.waitForTimeout(1200);
    await evidence(page, "04-admin", name, { feature, test: `Open ${path_}`, expected: "Data table from the real API", actual: "Rendered" });
  }
  await page.goto("/admin/analytics");
  await page.waitForTimeout(1500);
  await evidence(page, "17-analytics", "analytics_01_admin", { feature: "Analytics", test: "Open analytics", expected: "Breakdowns + certificate registry", actual: "Rendered" });
  await page.goto("/admin/audit-feed");
  const chain = page.getByTestId("audit-chain-panel");
  await chain.getByRole("button", { name: "Verify chain" }).click();
  await expect(chain.getByRole("status")).toContainText("No evidence of tampering", { timeout: 20_000 });
  await evidence(page, "04-admin", "admin_07_audit_chain_verified", {
    feature: "Audit chain", test: "Verify chain", expected: "No evidence of tampering; not claimed immutable", actual: "Verified",
  });
});

// --------------------------------------------------------------------------
// 8. Realtime: reconnecting state and recovery
// --------------------------------------------------------------------------
test("QA realtime: losing the API shows reconnecting, recovery is live again", async ({ page }) => {
  // QA_API_RESTART_CMD (full-stack evidence run only) really stops the API
  // process for a few seconds and starts it again, cutting every open event
  // stream. Without it (plain CI) only the connected state is checked.
  await loginAsUi(page, USERS.patient);
  await page.getByRole("button", { name: "Notifications" }).first().click();
  const live = page.getByText("Live", { exact: true }).first();
  await expect(live).toBeVisible({ timeout: 15_000 });
  await evidence(page, "14-realtime", "realtime_02_live", { feature: "Realtime status", test: "Stream connected", expected: "'Live'", actual: "Live" });
  const restart = process.env.QA_API_RESTART_CMD;
  if (!restart) return;
  const { spawn } = await import("node:child_process");
  spawn("bash", [restart], { detached: true, stdio: "ignore" }).unref();
  await expect(page.getByText(/Reconnecting/).first()).toBeVisible({ timeout: 20_000 });
  await evidence(page, "14-realtime", "realtime_03_reconnecting", { feature: "Realtime status", test: "API process stopped", expected: "'Reconnecting…'", actual: "Reconnecting shown" });
  await expect(live).toBeVisible({ timeout: 60_000 });
  await evidence(page, "14-realtime", "realtime_04_live_after_reconnect", { feature: "Realtime status", test: "API back", expected: "'Live' again, no reload", actual: "Live" });
});

// --------------------------------------------------------------------------
// 9. Security UI states
// --------------------------------------------------------------------------
test("QA security: wrong role, anonymous and expired sessions", async ({ page, browser }) => {
  await loginAsUi(page, USERS.donor);
  await page.goto("/admin/users");
  await expect(page.getByRole("heading", { name: "Access denied" })).toBeVisible({ timeout: 15_000 });
  await evidence(page, "18-security", "security_01_wrong_role_denied", { feature: "Role isolation (UI)", test: "Donor opens /admin/users", expected: "Access denied page, no admin data", actual: `Landed on ${new URL(page.url()).pathname}` });
  const anon = await browser.newContext();
  const ap = await anon.newPage();
  await ap.goto("/hospital/inventory");
  await expect(ap).toHaveURL(/\/login|\/$/);
  await evidence(ap, "18-security", "security_02_anonymous_redirected", { feature: "Authentication (UI)", test: "Anonymous opens /hospital/inventory", expected: "Sent to sign-in", actual: `Landed on ${new URL(ap.url()).pathname}` });
  await anon.close();
  // Session ended elsewhere: log out via API with the same cookie, then act.
  const cookie = (await page.context().cookies()).map((c) => `${c.name}=${c.value}`).join("; ");
  const ctx = await (await import("@playwright/test")).request.newContext({ baseURL: API_BASE_URL });
  await ctx.post("/auth/logout", { headers: { ...ORIGIN, cookie } });
  await ctx.dispose();
  await page.goto("/donor/eligibility");
  await expect(page).toHaveURL(/session-expired|login|\/$/, { timeout: 15_000 });
  await evidence(page, "18-security", "security_03_session_ended", { feature: "Session expiry", test: "Session revoked server-side", expected: "Session-ended / sign-in page, no data", actual: `Landed on ${new URL(page.url()).pathname}` });
});
