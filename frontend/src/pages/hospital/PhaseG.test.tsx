/**
 * Phase G -- frontend integration closures, against mocked network calls
 * only (components run for real):
 *  - the inventory console shows every real component row and sends the
 *    chosen component with an action, from an accessible dialog;
 *  - the hospital sub-nav "Analytics" link goes to real analytics, not the
 *    conceptual-illustration page;
 *  - the admin facility detail distinguishes components of one blood type.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import InventoryConsole from "./InventoryConsole";
import HospitalSubNav from "./HospitalSubNav";
import AdminFacilities from "../admin/AdminFacilities";
import { renderWithProviders } from "../../test/test-utils";
import { makeInventoryItem } from "../../test/fixtures";

const m = vi.hoisted(() => ({
  getInventory: vi.fn(),
  getInventoryLog: vi.fn(),
  getComponentInventory: vi.fn(),
  inventoryAction: vi.fn(),
  reconcileInventoryOne: vi.fn(),
  unreadCount: vi.fn(),
  listNotifications: vi.fn(),
  listAdminFacilities: vi.fn(),
  getAdminFacility: vi.fn(),
}));

vi.mock("../../lib/domain", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/domain")>();
  return { ...actual, ...m };
});

vi.mock("../../lib/auth", () => ({
  useAuth: () => ({ user: { id: "a1", full_name: "Admin", email: "a@x", role: "ADMIN" }, logout: vi.fn() }),
}));

beforeEach(() => {
  Object.values(m).forEach((f) => f.mockReset());
  m.getInventory.mockResolvedValue([makeInventoryItem({ blood_type: "O_NEG", units: 5 })]);
  m.getInventoryLog.mockResolvedValue([]);
  m.getComponentInventory.mockResolvedValue([
    { blood_type: "O_NEG", component: "WHOLE_BLOOD", units: 5, reserved_units: 1, available_units: 4 },
    { blood_type: "O_NEG", component: "PLASMA", units: 2, reserved_units: 0, available_units: 2 },
  ]);
  m.unreadCount.mockResolvedValue(0);
  m.listNotifications.mockResolvedValue([]);
});

describe("InventoryConsole (Phase G)", () => {
  it("lists every component row the facility holds", async () => {
    renderWithProviders(<InventoryConsole />);
    const table = await screen.findByTestId("component-stock");
    expect(within(table).getByText("Plasma")).toBeInTheDocument();
    expect(within(table).getByText("Whole blood")).toBeInTheDocument();
  });

  it("sends the chosen component with an action from an accessible dialog", async () => {
    m.inventoryAction.mockResolvedValue(makeInventoryItem());
    renderWithProviders(<InventoryConsole />);
    const addButtons = await screen.findAllByTitle("New stock received — increases units on hand.");
    fireEvent.click(addButtons[0]);
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Component"), { target: { value: "PLATELETS" } });
    fireEvent.change(within(dialog).getByLabelText("Units"), { target: { value: "3" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(m.inventoryAction).toHaveBeenCalledWith("add", expect.objectContaining({
        blood_type: "O_NEG", units: 3, component: "PLATELETS",
      })),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("closes the action dialog with Escape", async () => {
    renderWithProviders(<InventoryConsole />);
    const addButtons = await screen.findAllByTitle("New stock received — increases units on hand.");
    fireEvent.click(addButtons[0]);
    const dialog = await screen.findByRole("dialog");
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});

describe("HospitalSubNav (Phase G)", () => {
  it("points Analytics at the real hospital analytics", () => {
    renderWithProviders(<HospitalSubNav title="T" />);
    expect(screen.getByRole("link", { name: "Analytics" })).toHaveAttribute("href", "/hospital/analytics-mobile");
  });
});

describe("AdminFacilities detail (Phase G)", () => {
  it("shows the component so two lines of one blood type are distinguishable", async () => {
    m.listAdminFacilities.mockResolvedValue({
      items: [{
        id: "f1", user_id: "u1", name: "Metro General", license_no: null, phone: null, address: null,
        city: "Metro", latitude: null, longitude: null, verified: true, facility_type: "HOSPITAL",
        is_active: true, total_units: 7, total_reserved_units: 0, created_at: "2026-01-01T00:00:00Z",
      }],
      total: 1, page: 1, page_size: 25,
    });
    m.getAdminFacility.mockImplementation(async () => ({
      facility: (await m.listAdminFacilities.mock.results[0].value).items[0],
      inventory: [
        { blood_type: "O_NEG", component: "WHOLE_BLOOD", units: 5, reserved_units: 0, available_units: 5 },
        { blood_type: "O_NEG", component: "PLASMA", units: 2, reserved_units: 0, available_units: 2 },
      ],
    }));
    renderWithProviders(<AdminFacilities />, { route: "/admin/facilities" });
    fireEvent.click(await screen.findByText("Metro General"));
    expect(await screen.findByText("Plasma")).toBeInTheDocument();
    expect(screen.getByText("Whole blood")).toBeInTheDocument();
  });
});
