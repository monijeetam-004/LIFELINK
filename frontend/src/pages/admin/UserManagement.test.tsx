/**
 * Phase 33 -- component tests for UserManagement's suspend/reactivate
 * account-status flow specifically (the master prompt's own scoping
 * language) -- not the whole page's role-governance/search/pagination
 * surface, which is unchanged existing behavior outside Phase 33's scope.
 *
 * Mocking boundary: `../../lib/domain`'s network-calling functions
 * (including `unreadCount`/`listNotifications`, since this page renders a
 * real `NotificationsBell` in its header -- same pattern as
 * AdminAnalytics.test.tsx from Phase 32), and `../../lib/auth`'s `useAuth`.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import UserManagement from "./UserManagement";
import { renderWithProviders } from "../../test/test-utils";
import { makeAdminUser } from "../../test/fixtures";
import { conflictError, forbiddenError, notFoundError, serverError } from "../../test/apiErrors";
import { toApiError } from "../../lib/api";
import type { AdminUserList } from "../../lib/domain";

const { listAdminUsersMock, suspendAdminUserMock, reactivateAdminUserMock } = vi.hoisted(() => ({
  listAdminUsersMock: vi.fn(),
  suspendAdminUserMock: vi.fn(),
  reactivateAdminUserMock: vi.fn(),
}));

vi.mock("../../lib/domain", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/domain")>();
  return {
    ...actual,
    listAdminUsers: listAdminUsersMock,
    suspendAdminUser: suspendAdminUserMock,
    reactivateAdminUser: reactivateAdminUserMock,
    changeAdminUserRole: vi.fn(),
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

function listResult(items: AdminUserList["items"]): AdminUserList {
  return { items, total: items.length, page: 1, page_size: 25 };
}

async function selectUser(name: string) {
  fireEvent.click(await screen.findByText(name));
}

describe("UserManagement -- suspend/reactivate flow", () => {
  it("opens the suspend console (not the reactivate one) when an active user is selected", async () => {
    listAdminUsersMock.mockResolvedValue(listResult([makeAdminUser({ id: "u1", full_name: "Active Alice", is_active: true })]));
    renderWithProviders(<UserManagement />);
    await selectUser("Active Alice");
    expect(screen.getByLabelText("Reason for suspension (min. 3 characters)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reactivate" })).not.toBeInTheDocument();
  });

  it("opens the reactivate console (not the suspend one) when a suspended user is selected", async () => {
    listAdminUsersMock.mockResolvedValue(
      listResult([makeAdminUser({ id: "u2", full_name: "Suspended Sam", is_active: false, suspended_reason: "Policy violation" })]),
    );
    renderWithProviders(<UserManagement />);
    await selectUser("Suspended Sam");
    expect(screen.getByRole("button", { name: "Reactivate" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Reason for suspension (min. 3 characters)")).not.toBeInTheDocument();
    // "Policy violation" legitimately appears twice (the directory row's own
    // status tooltip text and the status console's own message) -- assert
    // presence rather than a single unique match.
    expect(screen.getAllByText(/Policy violation/).length).toBeGreaterThan(0);
  });

  it("keeps Suspend disabled below the real 3-character minimum, including whitespace-only input", async () => {
    listAdminUsersMock.mockResolvedValue(listResult([makeAdminUser({ id: "u1", full_name: "Active Alice", is_active: true })]));
    renderWithProviders(<UserManagement />);
    await selectUser("Active Alice");
    const reasonField = screen.getByLabelText("Reason for suspension (min. 3 characters)");
    const suspendBtn = screen.getByRole("button", { name: "Suspend" });

    fireEvent.change(reasonField, { target: { value: "ab" } });
    expect(suspendBtn).toBeDisabled();

    fireEvent.change(reasonField, { target: { value: "     " } });
    expect(suspendBtn).toBeDisabled();
    expect(suspendAdminUserMock).not.toHaveBeenCalled();
  });

  it("enables Suspend at exactly the 3-character (trimmed) minimum and calls the real mutation with the trimmed reason", async () => {
    suspendAdminUserMock.mockResolvedValue(makeAdminUser({ id: "u1", is_active: false }));
    listAdminUsersMock.mockResolvedValue(listResult([makeAdminUser({ id: "u1", full_name: "Active Alice", is_active: true })]));
    renderWithProviders(<UserManagement />);
    await selectUser("Active Alice");
    fireEvent.change(screen.getByLabelText("Reason for suspension (min. 3 characters)"), {
      target: { value: "  abc  " },
    });
    const suspendBtn = screen.getByRole("button", { name: "Suspend" });
    expect(suspendBtn).toBeEnabled();
    fireEvent.click(suspendBtn);
    await waitFor(() => expect(suspendAdminUserMock).toHaveBeenCalledWith("u1", "abc"));
  });

  it("disables Suspend while its own mutation is pending, preventing a duplicate real request", async () => {
    suspendAdminUserMock.mockReturnValue(new Promise(() => {}));
    listAdminUsersMock.mockResolvedValue(listResult([makeAdminUser({ id: "u1", full_name: "Active Alice", is_active: true })]));
    renderWithProviders(<UserManagement />);
    await selectUser("Active Alice");
    fireEvent.change(screen.getByLabelText("Reason for suspension (min. 3 characters)"), {
      target: { value: "Repeated abuse" },
    });
    const suspendBtn = screen.getByRole("button", { name: "Suspend" });
    fireEvent.click(suspendBtn);
    await waitFor(() => expect(suspendBtn).toBeDisabled());
    fireEvent.click(suspendBtn);
    expect(suspendAdminUserMock).toHaveBeenCalledTimes(1);
  });

  it("keeps a real 403 visible on suspend -- it never claims success", async () => {
    const error = forbiddenError();
    suspendAdminUserMock.mockRejectedValue(error);
    listAdminUsersMock.mockResolvedValue(listResult([makeAdminUser({ id: "u1", full_name: "Active Alice", is_active: true })]));
    renderWithProviders(<UserManagement />);
    await selectUser("Active Alice");
    fireEvent.change(screen.getByLabelText("Reason for suspension (min. 3 characters)"), {
      target: { value: "Repeated abuse" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Suspend" }));
    expect(await screen.findByText(toApiError(error).detail)).toBeInTheDocument();
    // Still showing the suspend console for an (unchanged) active user --
    // no optimistic flip to "Suspended" on a denied request.
    expect(screen.getByRole("button", { name: "Suspend" })).toBeInTheDocument();
  });

  it("surfaces a real 404/409/server error on suspend through the existing normalized error mechanism, not a bespoke one", async () => {
    for (const error of [notFoundError(), conflictError(), serverError()]) {
      suspendAdminUserMock.mockRejectedValueOnce(error);
      listAdminUsersMock.mockResolvedValue(listResult([makeAdminUser({ id: "u1", full_name: "Active Alice", is_active: true })]));
      const { unmount } = renderWithProviders(<UserManagement />);
      await selectUser("Active Alice");
      fireEvent.change(screen.getByLabelText("Reason for suspension (min. 3 characters)"), {
        target: { value: "Repeated abuse" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Suspend" }));
      expect(await screen.findByText(toApiError(error).detail)).toBeInTheDocument();
      unmount();
    }
  });

  it("only reflects a successful suspend after the mutation genuinely resolves and the directory refetches -- no optimistic row update", async () => {
    suspendAdminUserMock.mockResolvedValue(makeAdminUser({ id: "u1", is_active: false }));
    listAdminUsersMock
      .mockResolvedValueOnce(listResult([makeAdminUser({ id: "u1", full_name: "Active Alice", is_active: true })]))
      .mockResolvedValueOnce(
        listResult([makeAdminUser({ id: "u1", full_name: "Active Alice", is_active: false, suspended_reason: "Repeated abuse" })]),
      );
    renderWithProviders(<UserManagement />);
    await selectUser("Active Alice");
    fireEvent.change(screen.getByLabelText("Reason for suspension (min. 3 characters)"), {
      target: { value: "Repeated abuse" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Suspend" }));
    await waitFor(() => expect(listAdminUsersMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Suspended")).toBeInTheDocument();
  });

  it("calls the real reactivate mutation with just the user's id, separately from suspend", async () => {
    reactivateAdminUserMock.mockResolvedValue(makeAdminUser({ id: "u2", is_active: true }));
    listAdminUsersMock.mockResolvedValue(
      listResult([makeAdminUser({ id: "u2", full_name: "Suspended Sam", is_active: false })]),
    );
    renderWithProviders(<UserManagement />);
    await selectUser("Suspended Sam");
    fireEvent.click(screen.getByRole("button", { name: "Reactivate" }));
    await waitFor(() => expect(reactivateAdminUserMock).toHaveBeenCalledWith("u2"));
    expect(suspendAdminUserMock).not.toHaveBeenCalled();
  });

  it("disables Reactivate while pending, preventing a duplicate real request", async () => {
    reactivateAdminUserMock.mockReturnValue(new Promise(() => {}));
    listAdminUsersMock.mockResolvedValue(
      listResult([makeAdminUser({ id: "u2", full_name: "Suspended Sam", is_active: false })]),
    );
    renderWithProviders(<UserManagement />);
    await selectUser("Suspended Sam");
    const reactivateBtn = screen.getByRole("button", { name: "Reactivate" });
    fireEvent.click(reactivateBtn);
    await waitFor(() => expect(reactivateBtn).toBeDisabled());
    fireEvent.click(reactivateBtn);
    expect(reactivateAdminUserMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the suspend textarea properly labeled for assistive tech", async () => {
    listAdminUsersMock.mockResolvedValue(listResult([makeAdminUser({ id: "u1", full_name: "Active Alice", is_active: true })]));
    renderWithProviders(<UserManagement />);
    await selectUser("Active Alice");
    expect(screen.getByLabelText("Reason for suspension (min. 3 characters)")).toBeInstanceOf(HTMLTextAreaElement);
  });
});
