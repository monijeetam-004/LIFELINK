/**
 * Phase E -- frontend state honesty for the new domain states.
 *
 * Mocking boundary: only `../lib/domain`'s network-calling functions. The
 * components' own rendering/validation logic runs for real.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import FulfillmentProgress from "./FulfillmentProgress";
import DonationVerificationBadge, { canRequestCertificate } from "./DonationVerificationBadge";
import DonationReviewQueue from "./DonationReviewQueue";
import LogDonationModal from "./LogDonationModal";
import { ReconcilePanel } from "../pages/hospital/Shipments";
import { renderWithProviders } from "../test/test-utils";
import { makeHospitalDirectoryItem } from "../test/fixtures";
import {
  OPEN_REQUEST_STATUSES,
  isOpenRequestStatus,
  componentLabel,
  type Donation,
  type Shipment,
} from "../lib/domain";

const mocks = vi.hoisted(() => ({
  reconcileShipment: vi.fn(),
  listDonationsForReview: vi.fn(),
  verifyDonation: vi.fn(),
  rejectDonation: vi.fn(),
  getHospitalDirectory: vi.fn(),
  listDonorResponses: vi.fn(),
  logDonation: vi.fn(),
}));

vi.mock("../lib/domain", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/domain")>();
  return { ...actual, ...mocks };
});

beforeEach(() => {
  Object.values(mocks).forEach((m) => m.mockReset());
  mocks.listDonorResponses.mockResolvedValue([]);
  mocks.getHospitalDirectory.mockResolvedValue([]);
});

function donation(overrides: Partial<Donation> = {}): Donation {
  return {
    id: "don-1", donor_user_id: "u1", blood_request_id: null, units: 1,
    donated_on: "2026-09-01", hospital_name: "St Jude", hospital_id: "h1", note: null,
    verification_status: "DONOR_REPORTED", verified_by_user_id: null, verified_at: null,
    verification_note: null, created_at: "2026-09-01T10:00:00Z", ...overrides,
  };
}

function failedShipment(): Shipment {
  return {
    id: "s1", shipment_code: "SHP-1", origin_facility_id: "o", destination_facility_id: "d",
    origin_facility_name: "O", destination_facility_name: "D", transport_route_id: null,
    status: "FAILED", created_by_user_id: null, dispatched_at: null, in_transit_at: null,
    arrived_at: null, received_at: null, cancelled_at: null, failed_at: "2026-09-01T00:00:00Z",
    estimated_arrival_at: null, delayed_at: null, delay_reason: null, cancel_reason: null,
    failure_reason: "crash", note: null, created_at: "", updated_at: "", allowed_transitions: [],
    route_status: null, route_disrupted_reason: null,
    items: [
      { id: "i1", blood_unit_id: "b1", is_active: false, unit_code: "U-1", blood_type: "O_NEG", component: "PLASMA" },
      { id: "i2", blood_unit_id: "b2", is_active: false, unit_code: "U-2", blood_type: "O_NEG", component: "PLASMA" },
    ],
    reconciliation_status: "REQUIRED", reconciled_at: null, reconciled_by_user_id: null,
    reconciliation_note: null, redirected_to_facility_id: null,
  };
}

describe("open request statuses", () => {
  it("treats PARTIALLY_FULFILLED as still open (it still needs blood)", () => {
    expect(OPEN_REQUEST_STATUSES).toContain("PARTIALLY_FULFILLED");
    expect(isOpenRequestStatus("PARTIALLY_FULFILLED")).toBe(true);
    expect(isOpenRequestStatus("FULFILLED")).toBe(false);
  });
});

describe("FulfillmentProgress", () => {
  it("shows server quantities and the derived remaining amount", () => {
    renderWithProviders(<FulfillmentProgress fulfilled={1} needed={4} />);
    expect(screen.getByText("1 of 4 unit(s) fulfilled · 3 still needed")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "1");
  });

  it("does not claim units are still needed once complete", () => {
    renderWithProviders(<FulfillmentProgress fulfilled={4} needed={4} />);
    expect(screen.getByText("4 of 4 unit(s) fulfilled")).toBeInTheDocument();
  });
});

describe("DonationVerificationBadge", () => {
  it("never labels a self-reported donation as verified", () => {
    renderWithProviders(<DonationVerificationBadge status="DONOR_REPORTED" />);
    const badge = screen.getByTestId("donation-verification-badge");
    expect(badge).toHaveTextContent(/self-reported/i);
    expect(badge).toHaveTextContent(/not yet verified/i);
  });

  it("only offers certificates for verified donations", () => {
    expect(canRequestCertificate({ verification_status: "VERIFIED" })).toBe(true);
    expect(canRequestCertificate({ verification_status: "DONOR_REPORTED" })).toBe(false);
    expect(canRequestCertificate({ verification_status: "REJECTED" })).toBe(false);
  });
});

describe("componentLabel", () => {
  it("labels known components and never invents one for unknown values", () => {
    expect(componentLabel("RED_BLOOD_CELLS")).toBe("Red blood cells");
    expect(componentLabel("SOMETHING_NEW")).toBe("SOMETHING_NEW");
    expect(componentLabel(null)).toBe("Unknown component");
  });
});

describe("ReconcilePanel", () => {
  it("cannot restock until every unit is confirmed and cold chain attested", async () => {
    mocks.reconcileShipment.mockResolvedValue({ ...failedShipment(), reconciliation_status: "RETURNED_TO_ORIGIN" });
    const onDone = vi.fn();
    renderWithProviders(<ReconcilePanel shipment={failedShipment()} onDone={onDone} />);

    fireEvent.click(screen.getByLabelText(/Physically returned to us intact/));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Driver brought cooler back" } });
    const submit = screen.getByRole("button", { name: "Record reconciliation" });
    expect(submit).toBeDisabled();

    fireEvent.click(screen.getByLabelText("U-1"));
    expect(submit).toBeDisabled(); // partial confirmation
    fireEvent.click(screen.getByLabelText("U-2"));
    expect(submit).toBeDisabled(); // no cold-chain attestation yet
    fireEvent.click(screen.getByLabelText(/cold chain was maintained/));
    expect(submit).toBeEnabled();

    fireEvent.click(submit);
    await waitFor(() =>
      expect(mocks.reconcileShipment).toHaveBeenCalledWith("s1", {
        outcome: "RETURNED_TO_ORIGIN",
        note: "Driver brought cooler back",
        unit_codes: ["U-1", "U-2"],
        cold_chain_maintained: true,
      }),
    );
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it("quarantine needs only a real note and sends no unit list", async () => {
    mocks.reconcileShipment.mockResolvedValue(failedShipment());
    renderWithProviders(<ReconcilePanel shipment={failedShipment()} onDone={vi.fn()} />);
    const submit = screen.getByRole("button", { name: "Record reconciliation" });
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "short" } });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Cooler breached on NH48" } });
    fireEvent.click(submit);
    await waitFor(() =>
      expect(mocks.reconcileShipment).toHaveBeenCalledWith("s1", {
        outcome: "QUARANTINED", note: "Cooler breached on NH48",
      }),
    );
  });
});

describe("DonationReviewQueue", () => {
  it("lists the queue and verifies with the reviewer's note", async () => {
    mocks.listDonationsForReview.mockResolvedValue([donation()]);
    mocks.verifyDonation.mockResolvedValue(donation({ verification_status: "VERIFIED" }));
    renderWithProviders(<DonationReviewQueue />);
    await screen.findByTestId("donation-review-row");
    expect(mocks.listDonationsForReview).toHaveBeenCalledWith({ verification_status: "DONOR_REPORTED" });
    fireEvent.change(screen.getByLabelText("Review note"), { target: { value: "Register #42" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() => expect(mocks.verifyDonation).toHaveBeenCalledWith("don-1", "Register #42"));
  });

  it("surfaces a backend authorization error instead of pretending success", async () => {
    mocks.listDonationsForReview.mockResolvedValue([donation()]);
    mocks.rejectDonation.mockRejectedValue({
      isAxiosError: true,
      response: { status: 403, data: { detail: "Only the facility this donation is linked to (or an admin) can review it" } },
    });
    renderWithProviders(<DonationReviewQueue />);
    await screen.findByTestId("donation-review-row");
    fireEvent.click(screen.getByRole("button", { name: "Dispute" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/linked to/);
  });
});

describe("LogDonationModal facility link", () => {
  it("sends hospital_id only when the donor picks a real facility", async () => {
    mocks.getHospitalDirectory.mockResolvedValue([makeHospitalDirectoryItem({ id: "fac-9", name: "Metro General" })]);
    mocks.logDonation.mockResolvedValue(donation());
    renderWithProviders(<LogDonationModal open onClose={vi.fn()} />);
    const select = await screen.findByLabelText("Facility on LifeLink (optional)");
    fireEvent.change(select, { target: { value: "fac-9" } });
    fireEvent.click(screen.getByRole("button", { name: "Log donation" }));
    await waitFor(() => expect(mocks.logDonation).toHaveBeenCalled());
    expect(mocks.logDonation.mock.calls[0][0]).toMatchObject({ hospital_id: "fac-9" });
  });
});
