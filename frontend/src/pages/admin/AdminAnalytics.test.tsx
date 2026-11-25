/**
 * Phase 32 -- component tests for AdminAnalytics' certificate registry and
 * revoke flow (Phase 31.1). This file is the only place `RevokeCertificateModal`
 * exists (a local, non-exported component) -- no separate `AdminCertificates.tsx`
 * exists in this codebase, so this page IS "the admin certificate registry"
 * component the master prompt's checklist E refers to.
 *
 * Mocking boundary: `../../lib/domain`'s network-calling functions, and
 * `../../lib/auth`'s `useAuth` (its only real logic is calling /auth/*).
 * `NotificationsBell` (rendered in this page's header) is NOT mocked as a
 * component -- it's a real component whose own network calls
 * (`unreadCount`/`listNotifications`) are covered by the same domain mock,
 * so this stays "mock the transport, not the component."
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import AdminAnalytics from "./AdminAnalytics";
import { renderWithProviders } from "../../test/test-utils";
import { makeAdminCertificate } from "../../test/fixtures";
import { forbiddenError } from "../../test/apiErrors";
import { toApiError } from "../../lib/api";
import type { AdminCertificateList } from "../../lib/domain";

const {
  getAdminAnalyticsOverviewMock,
  listAdminCertificatesMock,
  revokeAdminCertificateMock,
} = vi.hoisted(() => ({
  getAdminAnalyticsOverviewMock: vi.fn(),
  listAdminCertificatesMock: vi.fn(),
  revokeAdminCertificateMock: vi.fn(),
}));

vi.mock("../../lib/domain", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/domain")>();
  return {
    ...actual,
    getAdminAnalyticsOverview: getAdminAnalyticsOverviewMock,
    listAdminCertificates: listAdminCertificatesMock,
    revokeAdminCertificate: revokeAdminCertificateMock,
    verifyCertificate: vi.fn(),
    unreadCount: vi.fn().mockResolvedValue(0),
    listNotifications: vi.fn().mockResolvedValue([]),
  };
});

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    user: {
      id: "admin-1",
      email: "admin@example.com",
      full_name: "Ada Admin",
      role: "ADMIN",
      is_email_verified: true,
      is_active: true,
    },
    loading: false,
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn(),
  }),
}));

const EMPTY_OVERVIEW = {
  request_status_breakdown: {},
  request_blood_type_breakdown: {},
  emergency_status_breakdown: {},
  donor_response_breakdown: {},
  hospital_response_breakdown: {},
  verification_breakdown: {},
  requests_per_day: [],
};

function certList(items: AdminCertificateList["items"]): AdminCertificateList {
  return { items, total: items.length, page: 1, page_size: 20 };
}

function registryRow(serial: string) {
  const table = screen.getByRole("table");
  return within(table).getByText(serial).closest("tr")!;
}

describe("AdminAnalytics certificate registry", () => {
  it("renders 'Valid' for a non-revoked certificate row", async () => {
    getAdminAnalyticsOverviewMock.mockResolvedValue(EMPTY_OVERVIEW);
    listAdminCertificatesMock.mockResolvedValue(certList([makeAdminCertificate({ revoked_at: null })]));
    renderWithProviders(<AdminAnalytics />);
    await screen.findByText("LL-2026-0001");
    expect(within(registryRow("LL-2026-0001")).getByText("Valid")).toBeInTheDocument();
  });

  it("renders 'Revoked' for a revoked certificate row", async () => {
    getAdminAnalyticsOverviewMock.mockResolvedValue(EMPTY_OVERVIEW);
    listAdminCertificatesMock.mockResolvedValue(
      certList([makeAdminCertificate({ revoked_at: "2026-02-01T00:00:00Z" })]),
    );
    renderWithProviders(<AdminAnalytics />);
    await screen.findByText("LL-2026-0001");
    expect(within(registryRow("LL-2026-0001")).getByText("Revoked")).toBeInTheDocument();
  });

  it("shows a Revoke action for a non-revoked certificate", async () => {
    getAdminAnalyticsOverviewMock.mockResolvedValue(EMPTY_OVERVIEW);
    listAdminCertificatesMock.mockResolvedValue(certList([makeAdminCertificate({ revoked_at: null })]));
    renderWithProviders(<AdminAnalytics />);
    await screen.findByText("LL-2026-0001");
    expect(within(registryRow("LL-2026-0001")).getByRole("button", { name: "Revoke" })).toBeInTheDocument();
  });

  it("hides the Revoke action once a certificate is revoked", async () => {
    getAdminAnalyticsOverviewMock.mockResolvedValue(EMPTY_OVERVIEW);
    listAdminCertificatesMock.mockResolvedValue(
      certList([makeAdminCertificate({ revoked_at: "2026-02-01T00:00:00Z" })]),
    );
    renderWithProviders(<AdminAnalytics />);
    await screen.findByText("LL-2026-0001");
    expect(within(registryRow("LL-2026-0001")).queryByRole("button", { name: "Revoke" })).not.toBeInTheDocument();
  });

  it("opens the revoke dialog from the row action", async () => {
    getAdminAnalyticsOverviewMock.mockResolvedValue(EMPTY_OVERVIEW);
    listAdminCertificatesMock.mockResolvedValue(certList([makeAdminCertificate({ revoked_at: null })]));
    renderWithProviders(<AdminAnalytics />);
    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    expect(await screen.findByRole("heading", { name: "Revoke certificate" })).toBeInTheDocument();
  });

  it("keeps the confirm action disabled for a too-short reason", async () => {
    getAdminAnalyticsOverviewMock.mockResolvedValue(EMPTY_OVERVIEW);
    listAdminCertificatesMock.mockResolvedValue(certList([makeAdminCertificate({ revoked_at: null })]));
    renderWithProviders(<AdminAnalytics />);
    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    await screen.findByRole("heading", { name: "Revoke certificate" });
    fireEvent.change(screen.getByLabelText(/Reason for revocation/), { target: { value: "no" } });
    expect(screen.getByRole("button", { name: "Revoke certificate" })).toBeDisabled();
  });

  it("enables the confirm action once a valid (>=3 char) reason is entered", async () => {
    getAdminAnalyticsOverviewMock.mockResolvedValue(EMPTY_OVERVIEW);
    listAdminCertificatesMock.mockResolvedValue(certList([makeAdminCertificate({ revoked_at: null })]));
    renderWithProviders(<AdminAnalytics />);
    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    await screen.findByRole("heading", { name: "Revoke certificate" });
    fireEvent.change(screen.getByLabelText(/Reason for revocation/), {
      target: { value: "Donation could not be confirmed." },
    });
    expect(screen.getByRole("button", { name: "Revoke certificate" })).toBeEnabled();
  });

  it("disables repeat submission while the revoke mutation is pending", async () => {
    getAdminAnalyticsOverviewMock.mockResolvedValue(EMPTY_OVERVIEW);
    listAdminCertificatesMock.mockResolvedValue(certList([makeAdminCertificate({ revoked_at: null })]));
    revokeAdminCertificateMock.mockReturnValue(new Promise(() => {})); // never resolves
    renderWithProviders(<AdminAnalytics />);
    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    await screen.findByRole("heading", { name: "Revoke certificate" });
    fireEvent.change(screen.getByLabelText(/Reason for revocation/), {
      target: { value: "Donation could not be confirmed." },
    });
    const confirmBtn = screen.getByRole("button", { name: "Revoke certificate" });
    fireEvent.click(confirmBtn);
    await waitFor(() => expect(confirmBtn).toBeDisabled());
    expect(revokeAdminCertificateMock).toHaveBeenCalledTimes(1);
  });

  it("surfaces a real API error through the existing normalized error mechanism, not a fabricated success", async () => {
    getAdminAnalyticsOverviewMock.mockResolvedValue(EMPTY_OVERVIEW);
    listAdminCertificatesMock.mockResolvedValue(certList([makeAdminCertificate({ revoked_at: null })]));
    const error = forbiddenError();
    revokeAdminCertificateMock.mockRejectedValue(error);
    renderWithProviders(<AdminAnalytics />);
    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    await screen.findByRole("heading", { name: "Revoke certificate" });
    fireEvent.change(screen.getByLabelText(/Reason for revocation/), {
      target: { value: "Donation could not be confirmed." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Revoke certificate" }));
    expect(await screen.findByText(toApiError(error).detail)).toBeInTheDocument();
    // The dialog stays open and the row is NOT optimistically flipped to Revoked.
    expect(screen.getByRole("heading", { name: "Revoke certificate" })).toBeInTheDocument();
    expect(within(registryRow("LL-2026-0001")).getByText("Valid")).toBeInTheDocument();
  });

  it("only shows Revoked after the mutation genuinely succeeds and the list re-fetches -- never optimistically", async () => {
    getAdminAnalyticsOverviewMock.mockResolvedValue(EMPTY_OVERVIEW);
    listAdminCertificatesMock
      .mockResolvedValueOnce(certList([makeAdminCertificate({ revoked_at: null })]))
      .mockResolvedValueOnce(
        certList([makeAdminCertificate({ revoked_at: "2026-02-01T00:00:00Z", revocation_reason: "Confirmed bad." })]),
      );
    revokeAdminCertificateMock.mockResolvedValue(
      makeAdminCertificate({ revoked_at: "2026-02-01T00:00:00Z", revocation_reason: "Confirmed bad." }),
    );
    renderWithProviders(<AdminAnalytics />);
    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    await screen.findByRole("heading", { name: "Revoke certificate" });
    fireEvent.change(screen.getByLabelText(/Reason for revocation/), { target: { value: "Confirmed bad." } });
    fireEvent.click(screen.getByRole("button", { name: "Revoke certificate" }));

    // Dialog closes only once the real backend response comes back.
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Revoke certificate" })).not.toBeInTheDocument(),
    );
    expect(within(registryRow("LL-2026-0001")).getByText("Revoked")).toBeInTheDocument();
    expect(listAdminCertificatesMock).toHaveBeenCalledTimes(2);
  });

  it("gives the reason textarea an accessible label", async () => {
    getAdminAnalyticsOverviewMock.mockResolvedValue(EMPTY_OVERVIEW);
    listAdminCertificatesMock.mockResolvedValue(certList([makeAdminCertificate({ revoked_at: null })]));
    renderWithProviders(<AdminAnalytics />);
    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    expect(await screen.findByLabelText(/Reason for revocation/)).toBeInstanceOf(HTMLTextAreaElement);
  });

  it("shares the Modal primitive's dialog/focus/close semantics", async () => {
    getAdminAnalyticsOverviewMock.mockResolvedValue(EMPTY_OVERVIEW);
    listAdminCertificatesMock.mockResolvedValue(certList([makeAdminCertificate({ revoked_at: null })]));
    renderWithProviders(<AdminAnalytics />);
    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    const dialog = await screen.findByRole("dialog", { name: "Revoke certificate" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Revoke certificate" })).not.toBeInTheDocument(),
    );
  });
});
