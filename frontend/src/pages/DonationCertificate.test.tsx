/**
 * Phase 32 -- component tests for DonationCertificate's revocation contract
 * (Phase 31.1): a revoked certificate is still shown as real, historical
 * evidence of a real donation -- it never disappears -- while its current
 * trust status is unambiguous and never conveyed by color alone.
 *
 * Mocking boundary: only `../lib/domain`'s `listCertificates` (the network
 * call this page makes) is mocked. `certificateDownloadUrl` is a pure
 * string-formatting helper (no network call) and is left real.
 */
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import DonationCertificate from "./DonationCertificate";
import { renderWithProviders } from "../test/test-utils";
import { makeCertificate } from "../test/fixtures";

const { listCertificatesMock } = vi.hoisted(() => ({ listCertificatesMock: vi.fn() }));

vi.mock("../lib/domain", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/domain")>();
  return { ...actual, listCertificates: listCertificatesMock };
});

function renderPage(id = "cert-1") {
  return renderWithProviders(<DonationCertificate />, {
    route: `/certificate/${id}`,
    path: "/certificate/:id",
  });
}

describe("DonationCertificate", () => {
  it("shows the existing valid state for a non-revoked certificate", async () => {
    listCertificatesMock.mockResolvedValue([makeCertificate({ revoked_at: null })]);
    renderPage();
    expect(await screen.findByText("Verified Digital Copy")).toBeInTheDocument();
    expect(screen.queryByText("Revoked", { exact: true })).not.toBeInTheDocument();
  });

  it("shows a revoked banner and status for a revoked certificate", async () => {
    listCertificatesMock.mockResolvedValue([
      makeCertificate({ revoked_at: "2026-02-01T00:00:00Z" }),
    ]);
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("This certificate has been revoked");
    expect(screen.getByText("Revoked", { exact: true })).toBeInTheDocument();
  });

  it("shows the revocation reason when present", async () => {
    listCertificatesMock.mockResolvedValue([
      makeCertificate({
        revoked_at: "2026-02-01T00:00:00Z",
        revocation_reason: "Donation could not be confirmed by the hospital.",
      }),
    ]);
    renderPage();
    expect(
      await screen.findByText(/Donation could not be confirmed by the hospital\./),
    ).toBeInTheDocument();
  });

  it("keeps the certificate viewable as historical evidence when revoked (donor/serial still shown)", async () => {
    listCertificatesMock.mockResolvedValue([
      makeCertificate({ revoked_at: "2026-02-01T00:00:00Z", donor_name: "Dana Donor", serial: "LL-2026-0042" }),
    ]);
    renderPage();
    await screen.findByRole("alert");
    expect(screen.getByText("Dana Donor")).toBeInTheDocument();
    expect(screen.getByText("Document ID: LL-2026-0042")).toBeInTheDocument();
  });

  it("represents revoked status with real text, not color alone", async () => {
    listCertificatesMock.mockResolvedValue([
      makeCertificate({ revoked_at: "2026-02-01T00:00:00Z" }),
    ]);
    renderPage();
    // getByText finds it via its accessible text content, independent of
    // the className that supplies the color -- this is the assertion that
    // the distinction isn't color-only.
    expect(await screen.findByText("Revoked", { exact: true })).toBeInTheDocument();
  });

  it("does not remove existing certificate content merely because trust status changed", async () => {
    listCertificatesMock.mockResolvedValue([
      makeCertificate({
        revoked_at: "2026-02-01T00:00:00Z",
        hospital_name: "Test General Hospital",
      }),
    ]);
    renderPage();
    await screen.findByRole("alert");
    expect(screen.getAllByText(/Test General Hospital/).length).toBeGreaterThan(0);
  });
});
