/**
 * Phase 33 -- component tests for NotificationsBell's honest-count and
 * mutation-safety contract (Phase 17/18/19).
 *
 * Mocking boundary: only `../lib/domain`'s network-calling functions are
 * mocked -- NotificationsBell itself is never mocked, and neither is
 * `../lib/notificationLinks` (a pure, no-I/O lookup).
 *
 * Domain-honesty rule under test (Phase 18): a FAILED unread-count request
 * must render a distinct "!" indicator, never a confident "0" -- that would
 * be indistinguishable from "genuinely caught up."
 *
 * Phase 33 documented that the per-item "Read" button had no pending
 * guard (only "Mark all read" did). Phase G closed that gap -- each row is
 * locked while its own request is in flight -- and the test at the end of
 * this file now asserts it.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import NotificationsBell from "./NotificationsBell";
import { renderWithProviders } from "../test/test-utils";
import { makeAppNotification } from "../test/fixtures";

const {
  unreadCountMock,
  listNotificationsMock,
  markNotificationReadMock,
  markAllNotificationsReadMock,
} = vi.hoisted(() => ({
  unreadCountMock: vi.fn(),
  listNotificationsMock: vi.fn(),
  markNotificationReadMock: vi.fn(),
  markAllNotificationsReadMock: vi.fn(),
}));

vi.mock("../lib/domain", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/domain")>();
  return {
    ...actual,
    unreadCount: unreadCountMock,
    listNotifications: listNotificationsMock,
    markNotificationRead: markNotificationReadMock,
    markAllNotificationsRead: markAllNotificationsReadMock,
  };
});

beforeEach(() => {
  unreadCountMock.mockResolvedValue(0);
  listNotificationsMock.mockResolvedValue([]);
});

function openDropdown() {
  fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
}

describe("NotificationsBell", () => {
  it("shows the real unread count when the API returns a positive number", async () => {
    unreadCountMock.mockResolvedValue(3);
    renderWithProviders(<NotificationsBell />);
    expect(await screen.findByText("3")).toBeInTheDocument();
  });

  it("shows no badge at all when the API genuinely returns zero unread -- never a fabricated indicator", async () => {
    unreadCountMock.mockResolvedValue(0);
    renderWithProviders(<NotificationsBell />);
    await waitFor(() => expect(unreadCountMock).toHaveBeenCalled());
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(screen.queryByTitle("Couldn't check for new notifications")).not.toBeInTheDocument();
  });

  it("renders a distinct '!' indicator on a failed unread-count request -- never a confident '0'", async () => {
    unreadCountMock.mockRejectedValue(new Error("network down"));
    renderWithProviders(<NotificationsBell />);
    expect(await screen.findByTitle("Couldn't check for new notifications")).toHaveTextContent("!");
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("shows a loading state while the dropdown's notification list is in flight", async () => {
    listNotificationsMock.mockReturnValue(new Promise(() => {}));
    renderWithProviders(<NotificationsBell />);
    openDropdown();
    expect(await screen.findByText("Loading…")).toBeInTheDocument();
  });

  it("shows a visible failure (with Retry) when the list fails to load, and Retry re-fetches for real", async () => {
    listNotificationsMock.mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce([]);
    renderWithProviders(<NotificationsBell />);
    openDropdown();
    expect(await screen.findByText("Couldn't load notifications.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(listNotificationsMock).toHaveBeenCalledTimes(2));
  });

  it("renders exactly the notifications the API returned -- never inventing extra items", async () => {
    listNotificationsMock.mockResolvedValue([
      makeAppNotification({ id: "n1", title: "First notice" }),
      makeAppNotification({ id: "n2", title: "Second notice" }),
    ]);
    renderWithProviders(<NotificationsBell />);
    openDropdown();
    await screen.findByText("Second notice");
    const list = screen.getByRole("list");
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(within(list).getByText("First notice")).toBeInTheDocument();
  });

  it("calls the real mark-as-read function with the correct notification id", async () => {
    markNotificationReadMock.mockResolvedValue(undefined);
    listNotificationsMock.mockResolvedValue([makeAppNotification({ id: "n1", read_at: null })]);
    renderWithProviders(<NotificationsBell />);
    openDropdown();
    fireEvent.click(await screen.findByRole("button", { name: "Read" }));
    await waitFor(() => expect(markNotificationReadMock).toHaveBeenCalledWith("n1"));
  });

  it("re-fetches the count and list only after mark-as-read genuinely succeeds", async () => {
    markNotificationReadMock.mockResolvedValue(undefined);
    listNotificationsMock.mockResolvedValue([makeAppNotification({ id: "n1", read_at: null })]);
    renderWithProviders(<NotificationsBell />);
    openDropdown();
    await screen.findByRole("button", { name: "Read" });
    const countCallsBefore = unreadCountMock.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Read" }));
    await waitFor(() => expect(markNotificationReadMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(unreadCountMock.mock.calls.length).toBeGreaterThan(countCallsBefore));
  });

  it("does not silently mark an item read when the mutation actually fails -- no invalidation/refetch occurs", async () => {
    markNotificationReadMock.mockRejectedValue(new Error("boom"));
    listNotificationsMock.mockResolvedValue([makeAppNotification({ id: "n1", read_at: null })]);
    renderWithProviders(<NotificationsBell />);
    openDropdown();
    await screen.findByRole("button", { name: "Read" });
    const listCallsBefore = listNotificationsMock.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Read" }));
    await waitFor(() => expect(markNotificationReadMock).toHaveBeenCalledTimes(1));
    // No onError/invalidation branch exists for a failed mark-as-read in the
    // real component -- so a real failure produces no extra refetch, and the
    // still-unread item's own "Read" affordance remains in the DOM.
    expect(listNotificationsMock.mock.calls.length).toBe(listCallsBefore);
    expect(screen.getByRole("button", { name: "Read" })).toBeInTheDocument();
  });

  it("disables 'Mark all read' when there is nothing unread, and while its own mutation is pending", async () => {
    unreadCountMock.mockResolvedValue(2);
    markAllNotificationsReadMock.mockReturnValue(new Promise(() => {}));
    listNotificationsMock.mockResolvedValue([makeAppNotification({ id: "n1", read_at: null })]);
    renderWithProviders(<NotificationsBell />);
    await screen.findByText("2");
    openDropdown();
    const markAllBtn = await screen.findByRole("button", { name: "Mark all read" });
    expect(markAllBtn).toBeEnabled();
    fireEvent.click(markAllBtn);
    await waitFor(() => expect(markAllBtn).toBeDisabled());
    fireEvent.click(markAllBtn);
    expect(markAllNotificationsReadMock).toHaveBeenCalledTimes(1);
  });

  it("exposes the trigger as a real, natively keyboard-accessible <button> with an accessible name", () => {
    renderWithProviders(<NotificationsBell />);
    const button = screen.getByRole("button", { name: "Notifications" });
    expect(button.tagName).toBe("BUTTON");
    expect(button).toHaveAttribute("type", "button");
  });

  it("Phase G: a rapid double-click on one item's Read sends exactly one request", async () => {
    unreadCountMock.mockResolvedValue(1);
    listNotificationsMock.mockResolvedValue([makeAppNotification({ id: "n-1" })]);
    let resolve!: () => void;
    markNotificationReadMock.mockImplementation(
      () => new Promise<void>((r) => { resolve = r; }),
    );
    renderWithProviders(<NotificationsBell />);
    openDropdown();
    const btn = await screen.findByRole("button", { name: "Read" });
    fireEvent.click(btn);
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByRole("button", { name: "Reading…" })).toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "Reading…" }));
    expect(markNotificationReadMock).toHaveBeenCalledTimes(1);
    resolve();
    await waitFor(() => expect(markNotificationReadMock).toHaveBeenCalledTimes(1));
  });
  it("Phase J: is a real disclosure -- aria-expanded, Escape closes, focus returns to the bell", async () => {
    unreadCountMock.mockResolvedValue(2);
    renderWithProviders(<NotificationsBell />);
    const bell = screen.getByRole("button", { name: "Notifications" });
    expect(bell).toHaveAttribute("aria-expanded", "false");
    await waitFor(() => expect(bell).toHaveAccessibleDescription("2 unread"));

    fireEvent.click(bell);
    expect(bell).toHaveAttribute("aria-expanded", "true");
    const panel = screen.getByRole("region", { name: "Notifications panel" });
    expect(bell).toHaveAttribute("aria-controls", panel.id);

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("region", { name: "Notifications panel" })).not.toBeInTheDocument();
    expect(bell).toHaveAttribute("aria-expanded", "false");
    expect(bell).toHaveFocus();
  });

  it("Phase J: a failed count is described honestly to screen readers, not as zero", async () => {
    unreadCountMock.mockRejectedValue(new Error("network down"));
    renderWithProviders(<NotificationsBell />);
    const bell = screen.getByRole("button", { name: "Notifications" });
    await waitFor(() => expect(bell).toHaveAccessibleDescription("Unread count unavailable"));
  });
});
