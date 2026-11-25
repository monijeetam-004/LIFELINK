/**
 * Phase 33 -- component tests for SosModal's mutation-safety, idempotency,
 * and honest-error contract. This is the highest-consequence of the three
 * idempotency-using modals (Phase 12/26), so these tests lean hardest on
 * "never fabricate an active-emergency UI state" and "never derive backend
 * business logic client-side."
 *
 * Mocking boundary: `../lib/domain`'s network-calling functions, `../lib/auth`'s
 * `useAuth`, and `../lib/idempotency`'s key generator (mocked only to observe
 * key behavior -- never to fake the component's own submit/error logic).
 *
 * Important asymmetry vs. LogDonationModal/BloodRequestModal: SosModal's
 * `onError` handler does NOT re-mint the idempotency key on a 409 -- there is
 * no such branch in the real component (see SosModal.tsx). These tests
 * verify the actual, asymmetric behavior rather than assuming the other two
 * modals' re-mint pattern applies here too.
 *
 * SosModal also has no <form> element and its "Broadcast emergency" button is
 * a plain onClick handler, not type="submit" -- so unlike LogDonationModal/
 * BloodRequestModal, jsdom's native HTML5 constraint validation never gates
 * this component. The only structural "required field" gating is the
 * button's own `disabled` expression (FAMILY role + blank patient email).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import SosModal from "./SosModal";
import { renderWithProviders } from "../test/test-utils";
import {
  conflictError,
  forbiddenError,
  networkError,
  rateLimitError,
} from "../test/apiErrors";
import { toApiError } from "../lib/api";
import type { Emergency } from "../lib/domain";
import type { Role } from "../lib/types";

const { getHospitalDirectoryMock, createEmergencyMock, newIdempotencyKeyMock } = vi.hoisted(() => ({
  getHospitalDirectoryMock: vi.fn(),
  createEmergencyMock: vi.fn(),
  newIdempotencyKeyMock: vi.fn(),
}));

vi.mock("../lib/domain", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/domain")>();
  return { ...actual, getHospitalDirectory: getHospitalDirectoryMock, createEmergency: createEmergencyMock };
});

vi.mock("../lib/idempotency", () => ({ newIdempotencyKey: newIdempotencyKeyMock }));

const { authState } = vi.hoisted(() => ({
  authState: {
    user: {
      id: "patient-1",
      email: "pat@example.com",
      full_name: "Pat Patient",
      role: "PATIENT",
      is_email_verified: true,
      is_active: true,
    } as { id: string; email: string; full_name: string; role: Role; is_email_verified: boolean; is_active: boolean },
  },
}));

vi.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: authState.user,
    loading: false,
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn(),
  }),
}));

function makeEmergency(overrides: Partial<Emergency> = {}): Emergency {
  return {
    id: "em-1",
    created_by_user_id: "patient-1",
    patient_user_id: "patient-1",
    created_by_name: "Pat Patient",
    patient_name: "Pat Patient",
    filed_by_family: false,
    blood_type: "O_NEG",
    urgency: "CRITICAL",
    status: "ACTIVE",
    location_text: "Test General Hospital",
    note: null,
    resolved_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    units_needed: null,
    deadline: null,
    target_facility_id: null,
    slack_seconds: null,
    deadline_exceeded_at: null,
    allowed_transitions: ["ACKNOWLEDGED", "CANCELLED", "RESOLVED"],
    possible_duplicate_of: null,
    ...overrides,
  };
}

let keyCounter = 0;

beforeEach(() => {
  keyCounter = 0;
  newIdempotencyKeyMock.mockImplementation(() => `key-${++keyCounter}`);
  getHospitalDirectoryMock.mockResolvedValue([]);
  authState.user = {
    id: "patient-1",
    email: "pat@example.com",
    full_name: "Pat Patient",
    role: "PATIENT",
    is_email_verified: true,
    is_active: true,
  };
});

function renderModal(onClose = vi.fn()) {
  return { onClose, ...renderWithProviders(<SosModal open onClose={onClose} />) };
}

function broadcastButton() {
  return screen.getByRole("button", { name: /Broadcast emergency/ });
}

describe("SosModal", () => {
  it("requires a patient email for a FAMILY member -- the broadcast button stays disabled until one is entered", () => {
    authState.user = { ...authState.user, role: "FAMILY" };
    renderModal();
    // FAMILY + blank email is the component's only structural "required
    // field" gate -- there's no <form>, so this is asserted via the real
    // `disabled` state, not a native-validation or custom-error-text path.
    expect(broadcastButton()).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Patient email"), { target: { value: "patient@example.com" } });
    expect(broadcastButton()).toBeEnabled();
  });

  it("does not gate submission on patient email for non-FAMILY roles", () => {
    renderModal();
    expect(screen.queryByLabelText("Patient email")).not.toBeInTheDocument();
    expect(broadcastButton()).toBeEnabled();
  });

  it("calls the real emergency-creation mutation exactly once, carrying the minted idempotency key", async () => {
    createEmergencyMock.mockResolvedValue(makeEmergency());
    renderModal();
    fireEvent.click(broadcastButton());
    await waitFor(() => expect(createEmergencyMock).toHaveBeenCalledTimes(1));
    expect(createEmergencyMock).toHaveBeenCalledWith(
      expect.objectContaining({ idempotency_key: expect.stringMatching(/^key-/) }),
    );
  });

  it("converts a chosen deadline into the ISO string the backend expects, using the form's own conversion -- never inventing deadline/status logic", async () => {
    createEmergencyMock.mockResolvedValue(makeEmergency());
    renderModal();
    fireEvent.change(screen.getByLabelText("Deadline (optional)"), { target: { value: "2026-02-01T10:30" } });
    fireEvent.click(broadcastButton());
    await waitFor(() => expect(createEmergencyMock).toHaveBeenCalledTimes(1));
    const payload = createEmergencyMock.mock.calls[0][0];
    expect(payload.deadline).toBe(new Date("2026-02-01T10:30").toISOString());
  });

  it("leaves deadline null when none is chosen, rather than fabricating one", async () => {
    createEmergencyMock.mockResolvedValue(makeEmergency());
    renderModal();
    fireEvent.click(broadcastButton());
    await waitFor(() => expect(createEmergencyMock).toHaveBeenCalledTimes(1));
    expect(createEmergencyMock.mock.calls[0][0].deadline).toBeNull();
  });

  it("disables the broadcast button while pending, so a second real click cannot fire a duplicate emergency", async () => {
    createEmergencyMock.mockReturnValue(new Promise(() => {}));
    renderModal();
    const btn = broadcastButton();
    fireEvent.click(btn);
    await waitFor(() => expect(btn).toBeDisabled());
    fireEvent.click(btn);
    expect(createEmergencyMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the same idempotency key across a retry after a real 409 -- this component has no re-mint-on-conflict branch, unlike LogDonationModal/BloodRequestModal", async () => {
    const error = conflictError();
    createEmergencyMock.mockRejectedValueOnce(error).mockResolvedValueOnce(makeEmergency());
    renderModal();

    fireEvent.click(broadcastButton());
    expect(await screen.findByText(toApiError(error).detail)).toBeInTheDocument();
    const firstKey = createEmergencyMock.mock.calls[0][0].idempotency_key;

    fireEvent.click(broadcastButton());
    await waitFor(() => expect(createEmergencyMock).toHaveBeenCalledTimes(2));
    expect(createEmergencyMock.mock.calls[1][0].idempotency_key).toBe(firstKey);
  });

  it("surfaces a real 429 through the existing normalized error mechanism", async () => {
    const error = rateLimitError(30);
    createEmergencyMock.mockRejectedValue(error);
    renderModal();
    fireEvent.click(broadcastButton());
    expect(await screen.findByText(toApiError(error).detail)).toBeInTheDocument();
  });

  it("surfaces a real 403 honestly -- it never fabricates an active-emergency UI state", async () => {
    const error = forbiddenError();
    createEmergencyMock.mockRejectedValue(error);
    const { onClose } = renderModal();
    fireEvent.click(broadcastButton());
    expect(await screen.findByText(toApiError(error).detail)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.queryByText(/you already have another open emergency/)).not.toBeInTheDocument();
  });

  it("keeps a network/server failure distinguishable from a successful broadcast -- no fake active-emergency state", async () => {
    const error = networkError();
    createEmergencyMock.mockRejectedValue(error);
    const { onClose } = renderModal();
    fireEvent.click(broadcastButton());
    expect(await screen.findByText(toApiError(error).detail)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes only after a genuine successful broadcast with no duplicate flag", async () => {
    createEmergencyMock.mockResolvedValue(makeEmergency({ possible_duplicate_of: null }));
    const { onClose } = renderModal();
    fireEvent.click(broadcastButton());
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("shows the real duplicate-notice screen instead of auto-closing when the backend flags a possible duplicate -- and Close still works", async () => {
    createEmergencyMock.mockResolvedValue(makeEmergency({ possible_duplicate_of: "em-0" }));
    const { onClose } = renderModal();
    fireEvent.click(broadcastButton());
    expect(await screen.findByText(/you already have another open emergency/)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    // Two buttons share the accessible name "Close" here: the Modal's own
    // header icon button (aria-label="Close") and this screen's own visible
    // "Close" button -- both call resetAndClose, so either would prove the
    // behavior, but click the visible text one to match what a sighted user
    // actually presses on this confirmation screen.
    const closeButtons = screen.getAllByRole("button", { name: "Close" });
    fireEvent.click(closeButtons[closeButtons.length - 1]);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("keeps the existing Cancel behavior intact -- closing without ever having submitted", () => {
    const { onClose } = renderModal();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(createEmergencyMock).not.toHaveBeenCalled();
  });

  it("keeps real dialog semantics and label associations", () => {
    renderModal();
    expect(screen.getByRole("dialog", { name: "Emergency SOS" })).toBeInTheDocument();
    expect(screen.getByLabelText("Blood group needed (optional)")).toBeInstanceOf(HTMLSelectElement);
    expect(screen.getByLabelText("Location")).toBeInstanceOf(HTMLInputElement);
    expect(screen.getByLabelText("Units needed (optional)")).toBeInstanceOf(HTMLInputElement);
  });
});
