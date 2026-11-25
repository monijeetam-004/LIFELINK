/**
 * Phase 32 -- component tests for MilestonesHistory's certificate-list
 * revocation contract (Phase 31.1): each certificate card must show its
 * real Valid/Revoked status and, only when genuinely revoked, its reason --
 * a valid certificate must never show a revocation reason.
 *
 * Mocking boundary: `../../lib/domain`'s network-calling functions only.
 * This page also renders <DonorSectionHeader> (which renders
 * <NotificationsBell>), so `unreadCount`/`listNotifications` -- real
 * network calls that component makes on mount -- are mocked here too,
 * resolved to an inert default (0 unread / no notifications) so this test
 * stays about MilestonesHistory's own contract, not NotificationsBell's.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";

import MilestonesHistory from "./MilestonesHistory";
import { renderWithProviders } from "../../test/test-utils";
import { makeCertificate } from "../../test/fixtures";
import { serverError } from "../../test/apiErrors";
import { toApiError } from "../../lib/api";

const {
  getDonorProfileMock,
  listDonationsMock,
  listCertificatesMock,
} = vi.hoisted(() => ({
  getDonorProfileMock: vi.fn(),
  listDonationsMock: vi.fn(),
  listCertificatesMock: vi.fn(),
}));

vi.mock("../../lib/domain", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/domain")>();
  return {
    ...actual,
    getDonorProfile: getDonorProfileMock,
    listDonations: listDonationsMock,
    listCertificates: listCertificatesMock,
    issueCertificate: vi.fn(),
    unreadCount: vi.fn().mockResolvedValue(0),
    listNotifications: vi.fn().mockResolvedValue([]),
  };
});

function renderPage() {
  return renderWithProviders(<MilestonesHistory />);
}

function certificatesSection() {
  return screen.getByRole("heading", { name: "Certificates" }).closest("section")!;
}

describe("MilestonesHistory", () => {
  it("shows 'Valid' for a non-revoked certificate", async () => {
    getDonorProfileMock.mockResolvedValue({ blood_type: "O_NEG" });
    listDonationsMock.mockResolvedValue([]);
    listCertificatesMock.mockResolvedValue([makeCertificate({ revoked_at: null })]);
    renderPage();
    expect(await screen.findByText("Valid")).toBeInTheDocument();
    expect(within(certificatesSection()).getByText("Valid")).toBeInTheDocument();
  });

  it("shows 'Revoked' for a revoked certificate", async () => {
    getDonorProfileMock.mockResolvedValue({ blood_type: "O_NEG" });
    listDonationsMock.mockResolvedValue([]);
    listCertificatesMock.mockResolvedValue([
      makeCertificate({ revoked_at: "2026-02-01T00:00:00Z" }),
    ]);
    renderPage();
    await screen.findByText("Revoked");
    expect(within(certificatesSection()).getByText("Revoked")).toBeInTheDocument();
  });

  it("shows the revocation reason when available", async () => {
    getDonorProfileMock.mockResolvedValue({ blood_type: "O_NEG" });
    listDonationsMock.mockResolvedValue([]);
    listCertificatesMock.mockResolvedValue([
      makeCertificate({
        revoked_at: "2026-02-01T00:00:00Z",
        revocation_reason: "Donation could not be confirmed by the hospital.",
      }),
    ]);
    renderPage();
    expect(
      await screen.findByText("Donation could not be confirmed by the hospital."),
    ).toBeInTheDocument();
  });

  it("never shows a revocation reason for a valid (non-revoked) certificate", async () => {
    getDonorProfileMock.mockResolvedValue({ blood_type: "O_NEG" });
    listDonationsMock.mockResolvedValue([]);
    listCertificatesMock.mockResolvedValue([
      makeCertificate({ revoked_at: null, revocation_reason: null }),
    ]);
    renderPage();
    await screen.findByText("Valid");
    expect(screen.queryByText("This certificate has been revoked.")).not.toBeInTheDocument();
  });

  it("shows an empty-state message when there are no certificates", async () => {
    getDonorProfileMock.mockResolvedValue({ blood_type: "O_NEG" });
    listDonationsMock.mockResolvedValue([]);
    listCertificatesMock.mockResolvedValue([]);
    renderPage();
    expect(
      await screen.findByText("No certificates issued yet. Get one from a donation above once you've completed it."),
    ).toBeInTheDocument();
  });

  it("shows a loading state for the certificates section before data arrives", () => {
    getDonorProfileMock.mockReturnValue(new Promise(() => {}));
    listDonationsMock.mockReturnValue(new Promise(() => {}));
    listCertificatesMock.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(within(certificatesSection()).getByText("Loading…")).toBeInTheDocument();
  });

  it("shows an honest fetch-error state (via the real toApiError mapping) when certificates fail to load", async () => {
    getDonorProfileMock.mockResolvedValue({ blood_type: "O_NEG" });
    listDonationsMock.mockResolvedValue([]);
    const error = serverError();
    listCertificatesMock.mockRejectedValue(error);
    renderPage();
    expect(await screen.findByText(toApiError(error).detail)).toBeInTheDocument();
  });
});
