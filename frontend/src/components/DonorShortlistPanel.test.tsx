/**
 * Confidence shortlist panel (2026-10-31). Only lib/gapApi's network calls
 * are mocked; rendering and wording run for real.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";

import DonorShortlistPanel from "./DonorShortlistPanel";
import { renderWithProviders } from "../test/test-utils";
import type { Shortlist, ShortlistCandidate, ShortlistContact } from "../lib/gapApi";

const api = vi.hoisted(() => ({
  getShortlist: vi.fn(),
  contactNextBatch: vi.fn(),
  confirmShortlistContact: vi.fn(),
  simulateShortlist: vi.fn(),
}));
vi.mock("../lib/gapApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/gapApi")>();
  return { ...actual, ...api };
});

function contact(over: Partial<ShortlistContact>): ShortlistContact {
  return {
    id: Math.random().toString(36).slice(2), donor_ref: "abcd1234", donor_name: "Asha",
    blood_type: "O_NEG", status: "AWAITING_RESPONSE", batch: 1, rank_at_contact: 1,
    score_at_contact: 0.8, contacted_at: null, timeout_at: "2026-10-31T10:00:00Z",
    responded_at: null, eligibility_rechecked_at: null, confirmed_at: null, status_reason: null,
    notification_state: "SENT_UNREAD", sms_status: null, simulated: false, synthetic: false, ...over,
  };
}

function shortlist(over: Partial<Shortlist>): Shortlist {
  return {
    policy: "UNIFIED_PRIORITY", strategy: "UNIFIED_PRIORITY",
    scoring_model: "UNIFIED_PRIORITY/1.0.0#abc123def456", explanation_level: "FULL",
    subject: "BLOOD_REQUEST", subject_id: "r1", is_open: true,
    urgency: "URGENT", units_needed: 3, units_fulfilled: 0, units_reserved_inventory: 0,
    donors_still_needed: 3, remaining_unmet: 1, state: "PARTIAL",
    summary: "2 confirmed and 0 accepted (awaiting confirmation) of 3 donor(s) required; The request is still 1 donor(s) short.",
    counts: {
      compatible_pool: 20, eligible_candidates: 15, not_yet_contacted: 5, contacted: 10,
      awaiting_response: 0, accepted: 0, confirmed_for_coordination: 2, declined: 4, unavailable: 0,
      timed_out: 4, notification_failed: 0, withdrawn: 0, eligibility_recheck_required: 0,
    },
    excluded: {}, next_to_contact: [], contacts: [], escalation: ["4 contacted donor(s) did not answer in time."],
    demo_mode: false, disclaimer: "Ranking is not confirmation.", ...over,
  };
}

function candidate(over: Partial<ShortlistCandidate> = {}): ShortlistCandidate {
  return {
    donor_ref: "c0ffee00", donor_name: "Ravi", blood_type: "O_NEG", rank: 1, score: 0.62,
    components: { availability: null, distance: 0.8 },
    next_action: "contact (invitation)", distance_basis: "MEASURED", distance_km: 10,
    travel_minutes_estimate: 13, location_stale: false, explanation: ["10.0 km away"],
    synthetic: false,
    priority: {
      model: "UNIFIED_PRIORITY/1.0.0#abc123def456", score: 62.0, score_available: true,
      evidence_coverage: 0.6, evidence_level: "MEDIUM", missing_data_baseline: 20.0,
      availability_status: "UNCONFIRMED",
      factors: [
        { name: "availability", label: "Explicit availability / willingness", weight: 0.25,
          value: null, contribution: 0, status: "MISSING", detail: "availability never explicitly confirmed" },
        { name: "distance", label: "Distance (travel feasibility)", weight: 0.2, value: 0.8,
          contribution: 16.0, status: "OBSERVED", detail: "10.0 km away (limit 50 km)" },
        { name: "verification", label: "Verification", weight: 0.15, value: 1.0,
          contribution: 15.0, status: "OBSERVED", detail: "identity verified" },
        { name: "freshness", label: "Location freshness", weight: 0.1, value: 1.0,
          contribution: 10.0, status: "OBSERVED", detail: "location shared 3 day(s) ago" },
      ],
    },
    ...over,
  };
}

beforeEach(() => Object.values(api).forEach((m) => m.mockReset()));

describe("DonorShortlistPanel -- unified priority score", () => {
  it("shows the score as a ranking measure with evidence and an explanation that adds up", async () => {
    api.getShortlist.mockResolvedValue(shortlist({ next_to_contact: [candidate()] }));
    renderWithProviders(<DonorShortlistPanel kind="BLOOD_REQUEST" subjectId="r1" canManage />);
    await waitFor(() => expect(screen.getByTestId("priority-score")).toHaveTextContent("priority 62/100"));
    expect(screen.getByText(/medium evidence/)).toBeInTheDocument();
    expect(screen.getByText(/not a probability of donation/)).toBeInTheDocument();
    expect(screen.getByText(/availability not confirmed \(unknown\)/)).toBeInTheDocument();
    const table = screen.getByTestId("priority-breakdown");
    // the missing factor contributes 0 and is labelled "no data", never positive
    expect(table).toHaveTextContent("no data");
    expect(table).toHaveTextContent("Missing-data baseline");
    expect(table).toHaveTextContent("+20.0");
    expect(table).toHaveTextContent("62.0");
  });

  it("offers the four original strategies and passes the chosen one to the API", async () => {
    api.getShortlist.mockResolvedValue(shortlist({ next_to_contact: [candidate()] }));
    api.contactNextBatch.mockResolvedValue(shortlist({ strategy: "NEAREST" }));
    renderWithProviders(<DonorShortlistPanel kind="BLOOD_REQUEST" subjectId="r1" canManage />);
    const select = await screen.findByTestId("ranking-strategy");
    const options = Array.from(select.querySelectorAll("option")).map((o) => o.value);
    expect(options).toEqual(["UNIFIED_PRIORITY", "NEAREST", "TIME_AWARE", "TIME_RESILIENCE", "EVENT_DRIVEN"]);
    fireEvent.change(select, { target: { value: "NEAREST" } });
    await waitFor(() => expect(api.getShortlist).toHaveBeenLastCalledWith("BLOOD_REQUEST", "r1", "NEAREST"));
    fireEvent.click(await screen.findByRole("button", { name: /Contact next ranked donors/ }));
    await waitFor(() => expect(api.contactNextBatch).toHaveBeenCalledWith("BLOOD_REQUEST", "r1", "NEAREST"));
  });

  it("never shows a made-up number when the evidence is insufficient", async () => {
    const thin = candidate({
      priority: { ...candidate().priority, score_available: false, evidence_level: "INSUFFICIENT" },
    });
    api.getShortlist.mockResolvedValue(shortlist({ next_to_contact: [thin] }));
    renderWithProviders(<DonorShortlistPanel kind="BLOOD_REQUEST" subjectId="r1" canManage />);
    await waitFor(() =>
      expect(screen.getByTestId("priority-score")).toHaveTextContent("not scored (too little data)"),
    );
  });
});

describe("DonorShortlistPanel", () => {
  it("separates found, contacted, accepted, confirmed and still-needed", async () => {
    api.getShortlist.mockResolvedValue(shortlist({
      contacts: [contact({ status: "CONFIRMED_FOR_COORDINATION" }), contact({ status: "TIMED_OUT", donor_name: "Ravi" })],
    }));
    renderWithProviders(<DonorShortlistPanel kind="BLOOD_REQUEST" subjectId="r1" canManage />);
    await waitFor(() => expect(screen.getByTestId("donor-shortlist")).toBeInTheDocument());
    expect(screen.getByTestId("shortlist-state")).toHaveTextContent("still short");
    expect(screen.getByText("eligible found").previousSibling).toHaveTextContent("15");
    expect(screen.getByText("confirmed").previousSibling).toHaveTextContent("2");
    expect(screen.getByText("still needed").previousSibling).toHaveTextContent("1");
    expect(screen.getByText("Confirmed for coordination")).toBeInTheDocument();
    expect(screen.getByText("No answer (timed out)")).toBeInTheDocument();
    expect(screen.getByTestId("shortlist-escalation")).toHaveTextContent("did not answer");
    expect(screen.getByText("Ranking is not confirmation.")).toBeInTheDocument();
  });

  it("never labels an accepted donor as confirmed and lets a manager confirm after re-check", async () => {
    const acc = contact({ status: "ACCEPTED" });
    api.getShortlist.mockResolvedValue(shortlist({ contacts: [acc] }));
    api.confirmShortlistContact.mockResolvedValue(shortlist({ contacts: [{ ...acc, status: "CONFIRMED_FOR_COORDINATION" }] }));
    renderWithProviders(<DonorShortlistPanel kind="BLOOD_REQUEST" subjectId="r1" canManage />);
    await waitFor(() => expect(screen.getByText("Accepted — not yet confirmed")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Confirm after re-check"));
    await waitFor(() => expect(api.confirmShortlistContact).toHaveBeenCalledWith("BLOOD_REQUEST", "r1", acc.id));
  });

  it("shows an honest empty state with the exclusion reasons", async () => {
    api.getShortlist.mockResolvedValue(shortlist({
      state: "NO_ELIGIBLE", remaining_unmet: 1, units_needed: 1, donors_still_needed: 1,
      counts: { ...shortlist({}).counts, eligible_candidates: 0, contacted: 0, confirmed_for_coordination: 0, not_yet_contacted: 0 },
      excluded: { beyond_distance_limit: 3, donation_interval_not_elapsed: 1 },
      escalation: ["No eligible donor was found within the distance limit."],
    }));
    renderWithProviders(<DonorShortlistPanel kind="EMERGENCY" subjectId="e1" canManage />);
    await waitFor(() => expect(screen.getByTestId("shortlist-state")).toHaveTextContent("No eligible donors found"));
    expect(screen.getByTestId("shortlist-excluded")).toHaveTextContent("3 beyond the distance limit");
    expect(screen.getByText("Contact next ranked donors")).toBeDisabled();
  });

  it("labels demo mode and synthetic, simulated rows; the simulator is admin-only", async () => {
    api.getShortlist.mockResolvedValue(shortlist({
      demo_mode: true, counts: { ...shortlist({}).counts, awaiting_response: 1 },
      contacts: [contact({ status: "ACCEPTED", synthetic: true, simulated: true })],
    }));
    const { unmount } = renderWithProviders(<DonorShortlistPanel kind="BLOOD_REQUEST" subjectId="r1" canManage />);
    await waitFor(() => expect(screen.getByTestId("demo-mode-banner")).toBeInTheDocument());
    expect(screen.getByText("SYNTHETIC")).toBeInTheDocument();
    expect(screen.getByText("(simulated answer)")).toBeInTheDocument();
    expect(screen.queryByText(/Simulate synthetic answers/)).toBeNull();
    unmount();
    renderWithProviders(<DonorShortlistPanel kind="BLOOD_REQUEST" subjectId="r1" canManage isAdmin />);
    await waitFor(() => expect(screen.getByText(/Simulate synthetic answers/)).toBeInTheDocument());
  });

  it("hides management actions from viewers who do not manage the request", async () => {
    api.getShortlist.mockResolvedValue(shortlist({ contacts: [contact({ status: "ACCEPTED", donor_name: null })] }));
    renderWithProviders(<DonorShortlistPanel kind="BLOOD_REQUEST" subjectId="r1" canManage={false} />);
    await waitFor(() => expect(screen.getByTestId("donor-shortlist")).toBeInTheDocument());
    expect(screen.queryByText("Confirm after re-check")).toBeNull();
    expect(screen.queryByText("Contact next ranked donors")).toBeNull();
    expect(screen.getByText(/Donor abcd1234/)).toBeInTheDocument();
  });

  it("renders a SUMMARY explanation without weights or points (release gate C)", async () => {
    const base = candidate();
    const c = candidate({
      components: {},
      priority: {
        ...base.priority,
        factors: base.priority.factors.map((f) => ({
          ...f, weight: null, value: null, contribution: null,
          detail: "how far the donor is",
        })),
      },
    });
    api.getShortlist.mockResolvedValue(shortlist({ explanation_level: "SUMMARY", next_to_contact: [c] }));
    renderWithProviders(<DonorShortlistPanel kind="BLOOD_REQUEST" subjectId="r1" canManage />);
    fireEvent.click(await screen.findByText("Why this rank"));
    const table = await screen.findByTestId("priority-breakdown");
    expect(table).not.toHaveTextContent("+16.0");
    expect(table).toHaveTextContent("—");
    expect(table).toHaveTextContent("62.0");
  });

  it("labels a banded SUMMARY score as approximate and has no baseline line (privacy round 2)", async () => {
    const base = candidate();
    const c = candidate({
      components: {}, score: 0.6, distance_km: 4, travel_minutes_estimate: 5,
      explanation: ["about 4 km away"],
      priority: {
        ...base.priority, score: 60, missing_data_baseline: null,
        factors: base.priority.factors.map((f) => ({
          ...f, weight: null, value: null, contribution: null, detail: "how far the donor is",
        })),
      },
    });
    api.getShortlist.mockResolvedValue(shortlist({ explanation_level: "SUMMARY", next_to_contact: [c] }));
    renderWithProviders(<DonorShortlistPanel kind="BLOOD_REQUEST" subjectId="r1" canManage />);
    expect(await screen.findByTestId("priority-score")).toHaveTextContent("priority about 60/100");
    fireEvent.click(await screen.findByText("Why this rank"));
    const table = await screen.findByTestId("priority-breakdown");
    expect(table).toHaveTextContent("about 60");
    expect(table).not.toHaveTextContent("60.0");
    expect(table).not.toHaveTextContent("Missing-data baseline");
    expect(screen.getByText(/about 4 km away · ~5 min/)).toBeInTheDocument();
  });

  it("keeps the exact score and the baseline line for FULL viewers", async () => {
    api.getShortlist.mockResolvedValue(shortlist({ next_to_contact: [candidate()] }));
    renderWithProviders(<DonorShortlistPanel kind="BLOOD_REQUEST" subjectId="r1" canManage />);
    expect(await screen.findByTestId("priority-score")).toHaveTextContent("priority 62/100");
    expect(screen.getByTestId("priority-score")).not.toHaveTextContent("about");
    fireEvent.click(await screen.findByText("Why this rank"));
    const table = await screen.findByTestId("priority-breakdown");
    expect(table).toHaveTextContent("Missing-data baseline");
    expect(table).toHaveTextContent("+20.0");
    expect(table).toHaveTextContent("62.0");
  });
});
