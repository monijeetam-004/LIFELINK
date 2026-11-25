/**
 * Phase 33 -- component tests for LogDonationModal's mutation-safety and
 * idempotency-key contract (Phase 26). Mirrors BloodRequestModal/SosModal's
 * "mint once per attempt, reuse across retries of that attempt, re-mint only
 * after a real 409" pattern -- see lib/idempotency.ts.
 *
 * Mocking boundary: `../lib/domain`'s network-calling functions, and
 * `../lib/idempotency`'s `newIdempotencyKey` (a pure key generator, mocked
 * here only so the test can assert *how many times* and *in what order* a
 * fresh key is minted -- never to fake the component's own submit/error
 * logic, which runs for real).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import LogDonationModal from "./LogDonationModal";
import { renderWithProviders } from "../test/test-utils";
import { conflictError, serverError } from "../test/apiErrors";
import { toApiError } from "../lib/api";

const { listDonorResponsesMock, logDonationMock, newIdempotencyKeyMock } = vi.hoisted(() => ({
  listDonorResponsesMock: vi.fn(),
  logDonationMock: vi.fn(),
  newIdempotencyKeyMock: vi.fn(),
}));

vi.mock("../lib/domain", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/domain")>();
  return { ...actual, listDonorResponses: listDonorResponsesMock, logDonation: logDonationMock };
});

vi.mock("../lib/idempotency", () => ({ newIdempotencyKey: newIdempotencyKeyMock }));

let keyCounter = 0;

beforeEach(() => {
  keyCounter = 0;
  newIdempotencyKeyMock.mockImplementation(() => `key-${++keyCounter}`);
  listDonorResponsesMock.mockResolvedValue([]);
});

function renderModal(onClose = vi.fn()) {
  return { onClose, ...renderWithProviders(<LogDonationModal open onClose={onClose} />) };
}

function fillMinimalValidForm() {
  fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2026-01-05" } });
}

describe("LogDonationModal", () => {
  it("mints a fresh idempotency key when the modal opens for a new attempt", async () => {
    renderModal();
    await waitFor(() => expect(newIdempotencyKeyMock).toHaveBeenCalled());
  });

  it("blocks submission when the donation date is cleared, without calling the mutation", () => {
    renderModal();
    const dateInput = screen.getByLabelText("Date");
    fireEvent.change(dateInput, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Log donation" }));
    // The date field is HTML-`required`, so jsdom's own constraint
    // validation blocks the submit event before it ever reaches the
    // component's onSubmit handler -- the real, observable outcome is
    // "field marked invalid, mutation never called," which this asserts
    // directly rather than reaching for the component's own (unreached,
    // in this exact path) hand-written "Enter the donation date." message.
    expect(dateInput).toBeInvalid();
    expect(logDonationMock).not.toHaveBeenCalled();
  });

  it("calls the real domain mutation exactly once for a valid submission, carrying the minted key", async () => {
    logDonationMock.mockResolvedValue({ id: "donation-1" });
    renderModal();
    fillMinimalValidForm();
    fireEvent.click(screen.getByRole("button", { name: "Log donation" }));
    await waitFor(() => expect(logDonationMock).toHaveBeenCalledTimes(1));
    expect(logDonationMock).toHaveBeenCalledWith(
      expect.objectContaining({ donated_on: "2026-01-05", idempotency_key: expect.stringMatching(/^key-/) }),
    );
  });

  it("disables the submit button while the mutation is pending, preventing a duplicate click from firing a second mutation", async () => {
    logDonationMock.mockReturnValue(new Promise(() => {})); // never resolves
    renderModal();
    fillMinimalValidForm();
    const submitBtn = screen.getByRole("button", { name: "Log donation" });
    fireEvent.click(submitBtn);
    await waitFor(() => expect(submitBtn).toBeDisabled());
    fireEvent.click(submitBtn); // a second real click while disabled/pending
    expect(logDonationMock).toHaveBeenCalledTimes(1);
  });

  it("closes (calls onClose) only once, only after the mutation genuinely succeeds", async () => {
    logDonationMock.mockResolvedValue({ id: "donation-1" });
    const { onClose } = renderModal();
    fillMinimalValidForm();
    fireEvent.click(screen.getByRole("button", { name: "Log donation" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("keeps a real mutation error visible and does NOT close the modal (no fabricated success)", async () => {
    const error = serverError();
    logDonationMock.mockRejectedValue(error);
    const { onClose } = renderModal();
    fillMinimalValidForm();
    fireEvent.click(screen.getByRole("button", { name: "Log donation" }));
    expect(await screen.findByText(toApiError(error).detail)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("surfaces a real 409 honestly and mints a new idempotency key for the next attempt, without reimplementing the backend's conflict rule", async () => {
    const error = conflictError("That conflicts with the current state — refresh and try again.");
    logDonationMock.mockRejectedValueOnce(error).mockResolvedValueOnce({ id: "donation-1" });
    renderModal();
    fillMinimalValidForm();

    fireEvent.click(screen.getByRole("button", { name: "Log donation" }));
    expect(await screen.findByText(toApiError(error).detail)).toBeInTheDocument();
    const firstKey = logDonationMock.mock.calls[0][0].idempotency_key;

    // Re-attempt: the component mints a fresh key after a real 409 (asserted
    // via the actual mutation payload, not by inspecting internal state).
    fireEvent.click(screen.getByRole("button", { name: "Log donation" }));
    await waitFor(() => expect(logDonationMock).toHaveBeenCalledTimes(2));
    const secondKey = logDonationMock.mock.calls[1][0].idempotency_key;
    expect(secondKey).not.toBe(firstKey);
  });

  it("does not invent a backend constraint beyond what the form already enforces (blank optional fields submit fine)", async () => {
    logDonationMock.mockResolvedValue({ id: "donation-1" });
    renderModal();
    fillMinimalValidForm();
    // Hospital and note are left blank -- the component's own validation
    // only requires a date; nothing here should block submission on their
    // account.
    fireEvent.click(screen.getByRole("button", { name: "Log donation" }));
    await waitFor(() =>
      expect(logDonationMock).toHaveBeenCalledWith(
        expect.objectContaining({ hospital_name: null, note: null }),
      ),
    );
  });

  it("keeps real dialog semantics and label associations for every field", () => {
    renderModal();
    expect(screen.getByRole("dialog", { name: "Log a donation" })).toBeInTheDocument();
    expect(screen.getByLabelText("Date")).toBeInstanceOf(HTMLInputElement);
    expect(screen.getByLabelText("Units")).toBeInstanceOf(HTMLInputElement);
    expect(screen.getByLabelText("Hospital (optional)")).toBeInstanceOf(HTMLInputElement);
    expect(screen.getByLabelText("Note (optional)")).toBeInstanceOf(HTMLTextAreaElement);
  });
});
