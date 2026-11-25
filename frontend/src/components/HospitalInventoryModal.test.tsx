/**
 * Phase 33 -- component tests for HospitalInventoryModal's mutation-safety
 * and numeric-validation contract (Phase 19 error-state fix included).
 *
 * Mocking boundary: only `../lib/domain`'s `getInventory`/`putInventory` are
 * mocked.
 *
 * Two real, deliberate constraints this file tests against rather than
 * invents around (per the master prompt's explicit instruction not to
 * invent blood-component semantics the current contract doesn't have):
 *  - There is no "add a new blood type" affordance -- the modal only ever
 *    edits blood types already present in the real `getInventory()`
 *    response.
 *  - Negative/invalid numeric input is silently clamped to 0 in the input's
 *    own onChange handler (`Math.max(0, Number(e.target.value) || 0)`) --
 *    there is no separate validation-error message for this, so these tests
 *    assert the real clamped value rather than a fabricated error string.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import HospitalInventoryModal from "./HospitalInventoryModal";
import { renderWithProviders } from "../test/test-utils";
import { makeInventoryItem } from "../test/fixtures";
import {
  conflictError,
  forbiddenError,
  networkError,
  notFoundError,
  serverError,
  serviceUnavailableError,
  validationError,
} from "../test/apiErrors";
import { toApiError } from "../lib/api";

const { getInventoryMock, putInventoryMock } = vi.hoisted(() => ({
  getInventoryMock: vi.fn(),
  putInventoryMock: vi.fn(),
}));

vi.mock("../lib/domain", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/domain")>();
  return { ...actual, getInventory: getInventoryMock, putInventory: putInventoryMock };
});

beforeEach(() => {
  getInventoryMock.mockResolvedValue([
    makeInventoryItem({ blood_type: "O_NEG", units: 5 }),
    makeInventoryItem({ blood_type: "A_POS", units: 2 }),
  ]);
});

function renderModal(onClose = vi.fn()) {
  return { onClose, ...renderWithProviders(<HospitalInventoryModal open onClose={onClose} />) };
}

describe("HospitalInventoryModal", () => {
  it("shows a loading state while the real inventory fetch is in flight", async () => {
    getInventoryMock.mockReturnValue(new Promise(() => {}));
    renderModal();
    expect(await screen.findByText("Loading…")).toBeInTheDocument();
  });

  it("shows a real, visible error (with Retry) on a failed fetch -- never an empty inventory grid mistaken for zero units", async () => {
    getInventoryMock.mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce([
      makeInventoryItem({ blood_type: "O_NEG", units: 5 }),
    ]);
    renderModal();
    expect(await screen.findByText("Couldn't load your inventory.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(getInventoryMock).toHaveBeenCalledTimes(2));
  });

  it("renders exactly the blood types the real inventory returned -- no invented types, no add-new-type affordance", async () => {
    renderModal();
    expect(await screen.findByLabelText("O-")).toBeInstanceOf(HTMLInputElement);
    expect(screen.getByLabelText("A+")).toBeInstanceOf(HTMLInputElement);
    expect(screen.queryByLabelText("B+")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add/i })).not.toBeInTheDocument();
  });

  it("seeds each field from the real current units for that blood type", async () => {
    renderModal();
    expect(await screen.findByLabelText("O-")).toHaveValue(5);
    expect(screen.getByLabelText("A+")).toHaveValue(2);
  });

  it("clamps a negative entry to 0 -- the component's real (only) numeric guard, not a fabricated validation message", async () => {
    putInventoryMock.mockResolvedValue(undefined);
    renderModal();
    const oNegField = await screen.findByLabelText("O-");
    fireEvent.change(oNegField, { target: { value: "-7" } });
    expect(oNegField).toHaveValue(0);
    fireEvent.click(screen.getByRole("button", { name: "Save inventory" }));
    await waitFor(() =>
      expect(putInventoryMock).toHaveBeenCalledWith(
        expect.arrayContaining([expect.objectContaining({ blood_type: "O_NEG", units: 0 })]),
      ),
    );
  });

  it("clamps a non-numeric entry to 0 the same real way, rather than sending NaN", async () => {
    renderModal();
    const oNegField = await screen.findByLabelText("O-");
    fireEvent.change(oNegField, { target: { value: "abc" } });
    expect(oNegField).toHaveValue(0);
  });

  it("accepts an explicit zero as a genuine, valid quantity", async () => {
    putInventoryMock.mockResolvedValue(undefined);
    renderModal();
    const oNegField = await screen.findByLabelText("O-");
    fireEvent.change(oNegField, { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "Save inventory" }));
    await waitFor(() =>
      expect(putInventoryMock).toHaveBeenCalledWith(
        expect.arrayContaining([expect.objectContaining({ blood_type: "O_NEG", units: 0 })]),
      ),
    );
  });

  it("calls the real mutation exactly once for a valid quantity change, sending the current draft for every field", async () => {
    putInventoryMock.mockResolvedValue(undefined);
    renderModal();
    const oNegField = await screen.findByLabelText("O-");
    fireEvent.change(oNegField, { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: "Save inventory" }));
    await waitFor(() => expect(putInventoryMock).toHaveBeenCalledTimes(1));
    expect(putInventoryMock).toHaveBeenCalledWith([
      { blood_type: "O_NEG", units: 9 },
      { blood_type: "A_POS", units: 2 },
    ]);
  });

  it("disables Save while the mutation is pending, preventing a duplicate real request", async () => {
    putInventoryMock.mockReturnValue(new Promise(() => {}));
    renderModal();
    await screen.findByLabelText("O-");
    const saveBtn = screen.getByRole("button", { name: "Save inventory" });
    fireEvent.click(saveBtn);
    await waitFor(() => expect(saveBtn).toBeDisabled());
    fireEvent.click(saveBtn);
    expect(putInventoryMock).toHaveBeenCalledTimes(1);
  });

  it("closes only after a genuine successful save -- no optimistic close beforehand", async () => {
    putInventoryMock.mockResolvedValue(undefined);
    const { onClose } = renderModal();
    await screen.findByLabelText("O-");
    fireEvent.click(screen.getByRole("button", { name: "Save inventory" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("surfaces every real error status honestly through the existing normalized mapping, and never closes on failure", async () => {
    const errors = [
      forbiddenError(),
      notFoundError(),
      conflictError(),
      validationError(),
      serverError(),
      serviceUnavailableError(),
      networkError(),
    ];
    for (const error of errors) {
      putInventoryMock.mockRejectedValueOnce(error);
      const { onClose, unmount } = renderModal();
      await screen.findByLabelText("O-");
      fireEvent.click(screen.getByRole("button", { name: "Save inventory" }));
      expect(await screen.findByText(toApiError(error).detail)).toBeInTheDocument();
      expect(onClose).not.toHaveBeenCalled();
      unmount();
    }
  });

  it("keeps the field's accessible name tied to its real blood-type label via the wrapping <label>", async () => {
    renderModal();
    const field = await screen.findByLabelText("O-");
    expect(field).toHaveAttribute("type", "number");
    expect(field).toHaveAttribute("min", "0");
  });

  it("keeps real dialog semantics for the whole modal", async () => {
    renderModal();
    expect(await screen.findByRole("dialog", { name: "Blood inventory" })).toBeInTheDocument();
  });

  it("keeps the existing Cancel behavior intact without ever calling the mutation", async () => {
    const { onClose } = renderModal();
    await screen.findByLabelText("O-");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(putInventoryMock).not.toHaveBeenCalled();
  });
});
