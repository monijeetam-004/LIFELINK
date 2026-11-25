/**
 * Phase 32 -- component tests for EmergencyTrackDrawer's domain contract:
 * `Emergency.deadline_exceeded_at` (backend-provided, Phase 31.1) must be
 * shown honestly, alongside (never instead of) the real `status`, and must
 * never be derived client-side from `slack_seconds`.
 *
 * Mocking boundary: only the network/API-calling layer is mocked --
 * `../lib/domain`'s query/mutation functions (thin wrappers around the
 * shared `api` axios instance) and `../lib/auth`'s `useAuth` (whose only
 * real logic is calling /auth/me et al.). Everything else -- Modal, the
 * component's own rendering/derivation logic, `toApiError` -- runs for
 * real. Responsive/layout concerns are explicitly out of scope here (see
 * docs/LIFELINK_PHASE_32_COMPONENT_TESTING_REPORT.md, section 10) --
 * Playwright's real-browser suite owns those.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import EmergencyTrackDrawer from "./EmergencyTrackDrawer";
import { renderWithProviders } from "../test/test-utils";
import { makeEmergencyDetail } from "../test/fixtures";
import { serverError } from "../test/apiErrors";
import { toApiError } from "../lib/api";

const { getEmergencyMock, cancelEmergencyMock } = vi.hoisted(() => ({
  getEmergencyMock: vi.fn(),
  cancelEmergencyMock: vi.fn(),
}));

vi.mock("../lib/domain", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/domain")>();
  return {
    ...actual,
    getEmergency: getEmergencyMock,
    getEmergencyCandidates: vi.fn().mockResolvedValue({
      emergency_id: "em-1",
      recipient_blood_type: "O_NEG",
      compatible_donor_types: ["O_NEG"],
      candidates: [],
    }),
    getHospitalProfile: vi.fn(),
    cancelEmergency: cancelEmergencyMock,
    resolveEmergency: vi.fn(),
    emergencyAssistanceFound: vi.fn(),
    acknowledgeEmergency: vi.fn(),
    respondToEmergency: vi.fn(),
    selectEmergencyPathway: vi.fn(),
  };
});

vi.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "user-1", email: "patient@example.com", full_name: "Pat Patient", role: "PATIENT", is_email_verified: true, is_active: true },
    loading: false,
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn(),
  }),
}));

function renderDrawer() {
  return renderWithProviders(<EmergencyTrackDrawer emergencyId="em-1" onClose={vi.fn()} mode="creator" />);
}

describe("EmergencyTrackDrawer", () => {
  it("renders a loading state while the emergency is being fetched", () => {
    getEmergencyMock.mockReturnValue(new Promise(() => {})); // never resolves
    renderDrawer();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("renders an error state, via the real toApiError mapping, when the fetch fails", async () => {
    const error = serverError();
    getEmergencyMock.mockRejectedValue(error);
    renderDrawer();
    const expectedMessage = toApiError(error).detail;
    expect(await screen.findByText(expectedMessage)).toBeInTheDocument();
  });

  it("keeps the existing status badge visible when there is no deadline signal", async () => {
    getEmergencyMock.mockResolvedValue(makeEmergencyDetail({ status: "ACTIVE", deadline_exceeded_at: null }));
    renderDrawer();
    expect(await screen.findByText("ACTIVE")).toBeInTheDocument();
    expect(screen.queryByText("Past deadline — action needed")).not.toBeInTheDocument();
  });

  it("shows no deadline alert when deadline_exceeded_at is null", async () => {
    getEmergencyMock.mockResolvedValue(makeEmergencyDetail({ deadline_exceeded_at: null }));
    renderDrawer();
    await screen.findByText("ACTIVE");
    expect(screen.queryByText("Past deadline — action needed")).not.toBeInTheDocument();
  });

  it("shows the honest 'Past deadline — action needed' notice when the backend sets deadline_exceeded_at", async () => {
    getEmergencyMock.mockResolvedValue(
      makeEmergencyDetail({ deadline_exceeded_at: "2026-01-01T12:00:00Z" }),
    );
    renderDrawer();
    expect(await screen.findByText("Past deadline — action needed")).toBeInTheDocument();
  });

  it("shows the deadline alert ALONGSIDE the status, never replacing it", async () => {
    getEmergencyMock.mockResolvedValue(
      makeEmergencyDetail({ status: "ACKNOWLEDGED", deadline_exceeded_at: "2026-01-01T12:00:00Z" }),
    );
    renderDrawer();
    expect(await screen.findByText("Past deadline — action needed")).toBeInTheDocument();
    expect(screen.getByText("ACKNOWLEDGED")).toBeInTheDocument();
  });

  it("never derives the deadline notice from slack_seconds being negative", async () => {
    // Genuinely overdue by slack_seconds, but the backend has NOT (yet, or
    // ever) set deadline_exceeded_at -- the contract is
    // "backend-provided deadline_exceeded_at -> frontend displays it", not
    // "slack_seconds < 0 -> frontend invents a warning".
    getEmergencyMock.mockResolvedValue(
      makeEmergencyDetail({ slack_seconds: -600, deadline_exceeded_at: null }),
    );
    renderDrawer();
    await screen.findByText("ACTIVE");
    expect(screen.getByText("10m overdue")).toBeInTheDocument();
    expect(screen.queryByText("Past deadline — action needed")).not.toBeInTheDocument();
  });

  it("never invents a deadline state when slack_seconds itself is missing", async () => {
    getEmergencyMock.mockResolvedValue(
      makeEmergencyDetail({ slack_seconds: null, deadline_exceeded_at: null, units_needed: null }),
    );
    renderDrawer();
    await screen.findByText("ACTIVE");
    expect(screen.queryByText("Past deadline — action needed")).not.toBeInTheDocument();
  });

  it("gives the deadline alert accessible alert semantics", async () => {
    getEmergencyMock.mockResolvedValue(
      makeEmergencyDetail({ deadline_exceeded_at: "2026-01-01T12:00:00Z" }),
    );
    renderDrawer();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Past deadline — action needed");
  });

  it("still lets existing creator actions (Cancel) call the real mutation", async () => {
    getEmergencyMock.mockResolvedValue(makeEmergencyDetail({ status: "ACTIVE" }));
    cancelEmergencyMock.mockResolvedValue(makeEmergencyDetail({ status: "CANCELLED" }));
    renderDrawer();
    const cancelBtn = await screen.findByRole("button", { name: "Cancel" });
    fireEvent.click(cancelBtn);
    await waitFor(() => expect(cancelEmergencyMock).toHaveBeenCalledWith("em-1"));
  });
});
