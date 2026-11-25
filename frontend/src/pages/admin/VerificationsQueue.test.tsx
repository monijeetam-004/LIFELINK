/**
 * Hardening -- the admin verification queue never offers a document that has
 * not passed malware scanning: no link, Approve disabled with the reason,
 * and a "Retry malware scan" action that calls the real rescan endpoint.
 * Mocking boundary: only `../../lib/domain` network functions and useAuth.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import VerificationsQueue from "./VerificationsQueue";
import { renderWithProviders } from "../../test/test-utils";
import type { VerificationRequest } from "../../lib/domain";

const { listMock, getMock, rescanMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  getMock: vi.fn(),
  rescanMock: vi.fn(),
}));

vi.mock("../../lib/domain", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/domain")>();
  return {
    ...actual,
    listAdminVerifications: listMock,
    getAdminVerification: getMock,
    rescanAdminVerificationDocument: rescanMock,
    approveAdminVerification: vi.fn(),
    rejectAdminVerification: vi.fn(),
    unreadCount: vi.fn().mockResolvedValue(0),
    listNotifications: vi.fn().mockResolvedValue([]),
  };
});

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "a1", email: "a@example.com", full_name: "Ada Admin", role: "ADMIN", is_email_verified: true, is_active: true },
    loading: false,
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn(),
  }),
}));

function request(scan: VerificationRequest["documents"][number]["scan_status"]): VerificationRequest {
  return {
    id: "v1",
    user_id: "u1",
    kind: "DONOR_IDENTITY",
    status: "PENDING",
    documents: [
      { filename: "id.pdf", content_type: "application/pdf", size: 2048, sha256: "ab".repeat(32), scan_status: scan, malware_scanned: true },
    ],
    note: null,
    reviewer_user_id: null,
    review_note: null,
    version: 1,
    supersedes_request_id: null,
    created_at: "2026-10-06T10:00:00Z",
    updated_at: "2026-10-06T10:00:00Z",
  };
}

async function openRequest() {
  renderWithProviders(<VerificationsQueue />);
  const badge = await screen.findByText("PENDING", { selector: "span" });
  const row = badge.closest("button");
  expect(row).not.toBeNull();
  fireEvent.click(row!);
  await screen.findByTestId("verification-doc-0");
}

describe("VerificationsQueue malware-scan states", () => {
  it("withholds a quarantined document and blocks approval, offering a rescan", async () => {
    listMock.mockResolvedValue([request("SCAN_FAILED")]);
    getMock.mockResolvedValue(request("SCAN_FAILED"));
    rescanMock.mockResolvedValue(request("CLEAN"));
    await openRequest();

    const doc = screen.getByTestId("verification-doc-0");
    expect(doc.querySelector("a")).toBeNull();
    expect(screen.getByTestId("scan-status")).toHaveTextContent("quarantined");
    const approve = screen.getByRole("button", { name: /^Approve$/ });
    expect(approve).toBeDisabled();
    expect(approve).toHaveAttribute("title", "Every document must pass malware scanning before approval");

    fireEvent.click(screen.getByRole("button", { name: "Retry malware scan" }));
    await waitFor(() => expect(rescanMock).toHaveBeenCalledWith("v1", 0));
  });

  it("links a clean document and allows approval", async () => {
    listMock.mockResolvedValue([request("CLEAN")]);
    getMock.mockResolvedValue(request("CLEAN"));
    await openRequest();
    expect(screen.getByTestId("verification-doc-0").querySelector("a")).not.toBeNull();
    expect(screen.getByRole("button", { name: /^Approve$/ })).not.toBeDisabled();
    expect(screen.queryByRole("button", { name: "Retry malware scan" })).toBeNull();
  });
});
