import { request as pwRequest } from "@playwright/test";

export const API_BASE_URL = "http://127.0.0.1:8000";
export const E2E_PASSWORD = "e2e decent passphrase 2026";

export const USERS = {
  donor: "e2e.donor@example.com",
  patient: "e2e.patient@example.com",
  hospital: "e2e.hospital@example.com",
  family: "e2e.family@example.com",
  admin: "e2e.admin@example.com",
} as const;

/** Log in as one of the seeded E2E users via the real API and return an
 * APIRequestContext carrying that session's cookie -- used only to set up
 * profile fields (donor blood type/availability, hospital name/location)
 * that would otherwise need their own dedicated UI flows to reach; every
 * notification-producing action in the actual specs goes through the real
 * browser UI, not this helper. */
/** Log in as one of the seeded E2E users via the real API and return an
 * APIRequestContext carrying that session's cookie. Exported (Phase 31.1)
 * so specs that need to create real backend state as a *different* role
 * than the one driving the browser (e.g. a donor's donation+certificate,
 * set up before an admin UI test revokes it) can do so without faking any
 * response -- every state change still goes through the real API. */
export async function apiLogin(email: string) {
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

/** Phase E: certificates are only issued for VERIFIED donations, and a
 * donor can never verify their own -- so specs that need a certificate
 * have the seeded admin verify the donation first, through the real
 * review endpoint (POST /donations/{id}/verify), exactly as a reviewer
 * would. */
export async function verifyDonationAsAdmin(donationId: string, note = "E2E: verified by admin") {
  const adminCtx = await apiLogin(USERS.admin);
  const res = await adminCtx.post(`/donations/${donationId}/verify`, {
    data: { note },
    headers: { Origin: "http://localhost:5173" },
  });
  if (!res.ok()) {
    throw new Error(`verify donation failed: ${res.status()} ${await res.text()}`);
  }
  await adminCtx.dispose();
}

/** One-time real-API setup for the profile fields the seed script (which
 * only creates bare, verified User rows) doesn't set -- run from each spec
 * file's own beforeAll so specs remain independently runnable. */
/** The city the E2E donor lives in and the alerting specs file requests for. */
export const E2E_CITY = "Bengaluru";

export async function ensureDonorAndHospitalProfiles() {
  const donorCtx = await apiLogin(USERS.donor);
  // Audit D4 (2026-10-31): donors are alerted only within the distance limit
  // or in the request's city, so the E2E donor lives in the specs' city.
  await donorCtx.put("/donors/me", {
    data: { blood_type: "O_NEG", city: E2E_CITY },
    headers: { Origin: "http://localhost:5173" },
  });
  await donorCtx.put("/donors/me/availability", {
    data: { state: "AVAILABLE" },
    headers: { Origin: "http://localhost:5173" },
  });
  await donorCtx.dispose();

  const hospitalCtx = await apiLogin(USERS.hospital);
  await hospitalCtx.put("/hospitals/me", {
    data: { name: "E2E Metro General", latitude: 12.9716, longitude: 77.5946 },
    headers: { Origin: "http://localhost:5173" },
  });
  await hospitalCtx.dispose();
}
