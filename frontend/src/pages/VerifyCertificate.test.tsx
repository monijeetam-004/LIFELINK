/**
 * Phase 32 -- component tests for VerifyCertificate's domain contract:
 * a genuine valid / revoked / not-found / network-error distinction, per
 * Phase 31.1's fix (never conflate a revoked certificate with "not found",
 * and never conflate a network/server failure with either).
 *
 * Mocking boundary: only `../lib/domain`'s `verifyCertificate` (the network
 * call) is mocked. `toApiError` (imported directly from the real,
 * un-mocked `../lib/api`) is what the test also uses to compute the exact
 * message a genuine server/network failure should render -- never a second,
 * hand-written copy of that mapping.
 */
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import VerifyCertificate from "./VerifyCertificate";
import { renderWithProviders } from "../test/test-utils";
import { makeCertificateVerification } from "../test/fixtures";
import { networkError, notFoundError, serverError } from "../test/apiErrors";
import { toApiError } from "../lib/api";

const { verifyCertificateMock } = vi.hoisted(() => ({ verifyCertificateMock: vi.fn() }));

vi.mock("../lib/domain", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/domain")>();
  return { ...actual, verifyCertificate: verifyCertificateMock };
});

function renderPage(code = "abc123") {
  return renderWithProviders(<VerifyCertificate />, {
    route: `/verify/${code}`,
    path: "/verify/:code",
  });
}

describe("VerifyCertificate", () => {
  it("shows the authentic state for a valid certificate", async () => {
    verifyCertificateMock.mockResolvedValue(makeCertificateVerification({ valid: true }));
    renderPage();
    expect(await screen.findByText("Authentic certificate")).toBeInTheDocument();
    expect(screen.queryByText("Certificate revoked")).not.toBeInTheDocument();
  });

  it("shows the revoked state for a revoked certificate", async () => {
    verifyCertificateMock.mockResolvedValue(
      makeCertificateVerification({ valid: false, revoked_at: "2026-02-01T00:00:00Z" }),
    );
    renderPage();
    expect(await screen.findByText("Certificate revoked")).toBeInTheDocument();
    expect(screen.queryByText("Authentic certificate")).not.toBeInTheDocument();
  });

  it("shows the revocation reason when one is present", async () => {
    verifyCertificateMock.mockResolvedValue(
      makeCertificateVerification({
        valid: false,
        revoked_at: "2026-02-01T00:00:00Z",
        revocation_reason: "Donation could not be confirmed by the hospital.",
      }),
    );
    renderPage();
    expect(
      await screen.findByText(/Donation could not be confirmed by the hospital\./),
    ).toBeInTheDocument();
  });

  it("shows the revoked_at date via the component's own existing date formatting", async () => {
    const revokedAt = "2026-02-01T00:00:00Z";
    verifyCertificateMock.mockResolvedValue(makeCertificateVerification({ valid: false, revoked_at: revokedAt }));
    renderPage();
    const expectedDate = `Revoked ${new Date(revokedAt).toLocaleDateString()}`;
    expect(await screen.findByText(expectedDate)).toBeInTheDocument();
  });

  it("shows a genuine not-found state for a 404, never conflated with revoked", async () => {
    verifyCertificateMock.mockRejectedValue(notFoundError());
    renderPage();
    expect(await screen.findByText("Not verified")).toBeInTheDocument();
    expect(screen.getByText("No certificate matches this code. Double-check it was copied correctly.")).toBeInTheDocument();
    expect(screen.queryByText("Certificate revoked")).not.toBeInTheDocument();
  });

  it("shows an honest 'couldn't check' state for a server error, via the real toApiError mapping", async () => {
    const error = serverError();
    verifyCertificateMock.mockRejectedValue(error);
    renderPage();
    expect(await screen.findByText("Couldn't check this certificate")).toBeInTheDocument();
    expect(screen.getByText(toApiError(error).detail)).toBeInTheDocument();
  });

  it("never renders a network failure as 'not found', 'revoked', or 'authentic'", async () => {
    verifyCertificateMock.mockRejectedValue(networkError());
    renderPage();
    expect(await screen.findByText("Couldn't check this certificate")).toBeInTheDocument();
    expect(screen.queryByText("Not verified")).not.toBeInTheDocument();
    expect(screen.queryByText("Certificate revoked")).not.toBeInTheDocument();
    expect(screen.queryByText("Authentic certificate")).not.toBeInTheDocument();
  });

  it("keeps the certificate's factual details visible in the revoked state", async () => {
    verifyCertificateMock.mockResolvedValue(
      makeCertificateVerification({ valid: false, donor_name: "Dana Donor", serial: "LL-2026-0099" }),
    );
    renderPage();
    await screen.findByText("Certificate revoked");
    expect(screen.getByText("Dana Donor")).toBeInTheDocument();
    expect(screen.getByText("LL-2026-0099")).toBeInTheDocument();
  });
});
