/**
 * Phase 33 -- component tests for BloodRequestModal's mutation-safety,
 * idempotency, and honest-error contract (Phase 19).
 *
 * Mocking boundary: `../lib/domain`'s network-calling functions, `../lib/auth`'s
 * `useAuth` (its only real logic is calling /auth/*), and `../lib/idempotency`'s
 * key generator (mocked only to observe how many fresh keys are minted and
 * when -- never to fake the component's own submit/error logic).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import BloodRequestModal from "./BloodRequestModal";
import { renderWithProviders } from "../test/test-utils";
import { makePatientProfile } from "../test/fixtures";
import {
  conflictError,
  forbiddenError,
  networkError,
  rateLimitError,
} from "../test/apiErrors";
import { toApiError } from "../lib/api";

const { getPatientProfileMock, createBloodRequestMock, newIdempotencyKeyMock } = vi.hoisted(() => ({
  getPatientProfileMock: vi.fn(),
  createBloodRequestMock: vi.fn(),
  newIdempotencyKeyMock: vi.fn(),
}));

vi.mock("../lib/domain", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/domain")>();
  return { ...actual, getPatientProfile: getPatientProfileMock, createBloodRequest: createBloodRequestMock };
});

vi.mock("../lib/idempotency", () => ({ newIdempotencyKey: newIdempotencyKeyMock }));

vi.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "patient-1", email: "pat@example.com", full_name: "Pat Patient", role: "PATIENT", is_email_verified: true, is_active: true },
    loading: false,
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn(),
  }),
}));

let keyCounter = 0;

beforeEach(() => {
  keyCounter = 0;
  newIdempotencyKeyMock.mockImplementation(() => `key-${++keyCounter}`);
  getPatientProfileMock.mockResolvedValue(makePatientProfile({ blood_type: null }));
});

function renderModal(onClose = vi.fn()) {
  return { onClose, ...renderWithProviders(<BloodRequestModal open onClose={onClose} />) };
}

function chooseBloodGroup() {
  fireEvent.change(screen.getByLabelText("Blood group"), { target: { value: "O_NEG" } });
}

describe("BloodRequestModal", () => {
  it("blocks submission with no blood group chosen, without calling the mutation", () => {
    renderModal();
    const bloodGroupSelect = screen.getByLabelText("Blood group");
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    // The blood-group field is HTML-`required`, so jsdom's own constraint
    // validation blocks the submit event before it ever reaches the
    // component's onSubmit handler -- assert the real, reachable outcome
    // directly instead of the component's own (unreached, in this exact
    // path) hand-written "Choose a blood group." message.
    expect(bloodGroupSelect).toBeInvalid();
    expect(createBloodRequestMock).not.toHaveBeenCalled();
  });

  it("submits with the chosen blood group and units, calling the real mutation once", async () => {
    createBloodRequestMock.mockResolvedValue({ id: "req-1" });
    renderModal();
    chooseBloodGroup();
    fireEvent.change(screen.getByLabelText("Units needed"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    await waitFor(() => expect(createBloodRequestMock).toHaveBeenCalledTimes(1));
    expect(createBloodRequestMock).toHaveBeenCalledWith(
      expect.objectContaining({ blood_type: "O_NEG", units_needed: 3, idempotency_key: expect.stringMatching(/^key-/) }),
    );
  });

  it("disables the submit button while pending, so a second real click cannot fire a duplicate request", async () => {
    createBloodRequestMock.mockReturnValue(new Promise(() => {}));
    renderModal();
    chooseBloodGroup();
    const submitBtn = screen.getByRole("button", { name: "Submit request" });
    fireEvent.click(submitBtn);
    await waitFor(() => expect(submitBtn).toBeDisabled());
    fireEvent.click(submitBtn);
    expect(createBloodRequestMock).toHaveBeenCalledTimes(1);
  });

  it("surfaces a real 409 honestly and mints a fresh key for the next attempt", async () => {
    const error = conflictError();
    createBloodRequestMock.mockRejectedValueOnce(error).mockResolvedValueOnce({ id: "req-1" });
    renderModal();
    chooseBloodGroup();

    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    expect(await screen.findByText(toApiError(error).detail)).toBeInTheDocument();
    const firstKey = createBloodRequestMock.mock.calls[0][0].idempotency_key;

    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    await waitFor(() => expect(createBloodRequestMock).toHaveBeenCalledTimes(2));
    expect(createBloodRequestMock.mock.calls[1][0].idempotency_key).not.toBe(firstKey);
  });

  it("surfaces a real 429 through the existing normalized error mechanism, not a second status-message system", async () => {
    const error = rateLimitError(30);
    createBloodRequestMock.mockRejectedValue(error);
    renderModal();
    chooseBloodGroup();
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    expect(await screen.findByText(toApiError(error).detail)).toBeInTheDocument();
  });

  it("surfaces a real 403 honestly -- it never looks like a successful submission", async () => {
    const error = forbiddenError();
    createBloodRequestMock.mockRejectedValue(error);
    const { onClose } = renderModal();
    chooseBloodGroup();
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    expect(await screen.findByText(toApiError(error).detail)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("keeps a network failure distinguishable from a successful creation", async () => {
    const error = networkError();
    createBloodRequestMock.mockRejectedValue(error);
    const { onClose } = renderModal();
    chooseBloodGroup();
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    expect(await screen.findByText(toApiError(error).detail)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes only after a genuine successful creation", async () => {
    createBloodRequestMock.mockResolvedValue({ id: "req-1" });
    const { onClose } = renderModal();
    chooseBloodGroup();
    fireEvent.click(screen.getByRole("button", { name: "Submit request" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("keeps real form label associations and dialog semantics", () => {
    renderModal();
    expect(screen.getByRole("dialog", { name: "New blood request" })).toBeInTheDocument();
    expect(screen.getByLabelText("Blood group")).toBeInstanceOf(HTMLSelectElement);
    expect(screen.getByLabelText("Units needed")).toBeInstanceOf(HTMLInputElement);
  });
});
