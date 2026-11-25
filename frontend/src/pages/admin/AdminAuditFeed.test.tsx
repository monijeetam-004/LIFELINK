/**
 * Hardening -- the audit-chain panel reports signed checkpoints honestly:
 * verified count when they pass, the server's reason when they fail, and a
 * checkpoint failure (no entry number) is not shown as "entry #?".
 * Mocking boundary: only `../../lib/domain` network functions and useAuth.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";

import AdminAuditFeed from "./AdminAuditFeed";
import { renderWithProviders } from "../../test/test-utils";
import type { AuditChainVerifyResult } from "../../lib/domain";

const { verifyMock } = vi.hoisted(() => ({ verifyMock: vi.fn() }));

vi.mock("../../lib/domain", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/domain")>();
  return {
    ...actual,
    verifyAuditChain: verifyMock,
    listAdminAuditLog: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
    unreadCount: vi.fn().mockResolvedValue(0),
    listNotifications: vi.fn().mockResolvedValue([]),
  };
});

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "a1", email: "a@example.com", full_name: "Ada Admin", role: "ADMIN", is_email_verified: true, is_active: true },
    loading: false, login: vi.fn(), register: vi.fn(), logout: vi.fn(), refresh: vi.fn(),
  }),
}));

const base: AuditChainVerifyResult = {
  ok: true, checked: 12, head_seq: 12, head_hash: "a".repeat(64), unsealed: 0,
  first_break: null, anchored: false, mode: "full",
  checkpoints: { enabled: true, ok: true, checked: 3, latest_seq: 10, reason: null },
};

async function verify(result: AuditChainVerifyResult) {
  verifyMock.mockResolvedValueOnce(result);
  renderWithProviders(<AdminAuditFeed />);
  fireEvent.click(await screen.findByRole("button", { name: "Verify chain" }));
  return screen.findByRole("status");
}

describe("AuditChainPanel signed checkpoints", () => {
  it("shows verified checkpoints", async () => {
    await verify(base);
    expect(screen.getByTestId("audit-checkpoints")).toHaveTextContent(
      "3 signed checkpoints kept outside the database verified (latest at entry #10).",
    );
  });

  it("shows a checkpoint failure with its reason, not 'entry #?'", async () => {
    const status = await verify({
      ...base,
      ok: false,
      first_break: { seq: null, audit_id: null, reason: "signed checkpoint check failed: x" },
      checkpoints: { enabled: true, ok: false, checked: 0, latest_seq: 10, reason: "x" },
    });
    expect(status).toHaveTextContent("Verification failed: signed checkpoint check failed: x");
    expect(status).not.toHaveTextContent("#?");
    expect(screen.getByTestId("audit-checkpoints")).toHaveTextContent("Signed checkpoints failed: x");
  });

  it("says nothing about checkpoints when they are not configured", async () => {
    await verify({ ...base, checkpoints: { enabled: false, ok: null, checked: 0, latest_seq: null, reason: null } });
    expect(screen.queryByTestId("audit-checkpoints")).toBeNull();
  });
});
