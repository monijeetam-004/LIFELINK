/**
 * Gap elimination -- the live research pages and new operational screens.
 *
 * Mocking boundary: only the network-calling functions (lib/gapApi) and the
 * signed-in user (lib/auth). Rendering, gating and wording run for real.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";

import PlanView from "./PlanView";
import LivePageShell from "./LivePageShell";
import Analytics from "../../pages/Analytics";
import UnitLifecycle from "../../pages/hospital/UnitLifecycle";
import MathematicalModel from "../../pages/MathematicalModel";
import { renderWithProviders } from "../../test/test-utils";
import type { BloodUnitRow, ForecastRun, PlanResult } from "../../lib/gapApi";

const auth = vi.hoisted(() => ({ user: null as null | { role: string; full_name: string } }));
vi.mock("../../lib/auth", () => ({ useAuth: () => ({ user: auth.user }) }));

const api = vi.hoisted(() => ({
  listForecasts: vi.fn(),
  createForecast: vi.fn(),
  listMyUnits: vi.fn(),
  getPathwayConfig: vi.fn(),
}));
vi.mock("../../lib/gapApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/gapApi")>();
  return { ...actual, ...api };
});
vi.mock("../NotificationsBell", () => ({ default: () => null }));

beforeEach(() => {
  Object.values(api).forEach((m) => m.mockReset());
  auth.user = null;
});

function plan(): PlanResult {
  return {
    strategy: "CRITICAL_FASTEST_FEASIBLE",
    strategy_description: "fastest first",
    strategy_keys: ["meets_deadline", "eta_minutes"],
    pathway_source: "RELATIONAL_ROUTES",
    graph_available: false,
    degraded_reason: "Neo4j is not configured; routes computed from PostgreSQL",
    recipient_blood_type: "A_POS",
    component: "PLASMA",
    units_needed: 2,
    urgency: "CRITICAL",
    deadline: null,
    destination_facility_id: "dest",
    compatible_types: ["A_POS", "AB_POS"],
    disrupted_routes_excluded: 1,
    simulated_failed_route_ids: [],
    assumptions: { handling_minutes: 30, deadline_risk_minutes: 15, max_hops: 6, straight_line_speed_kmph: 45 },
    primary: {
      feasibility: "DEADLINE_RISK", units_requested: 2, units_planned: 2, eta_minutes: 70,
      arrival_at: null, slack_minutes: 5, explanation: ["Leg 1: NO recorded transport route"],
      legs: [{ facility_id: "far", facility_name: "Far Bank", units: 2, units_by_type: { A_POS: 2 },
        route_basis: "STRAIGHT_LINE_ESTIMATE", route_ids: [], node_ids: ["far", "dest"], eta_minutes: 70, distance_km: 30 }],
    },
    backups: [],
    options: [
      { facility_id: "far", facility_name: "Far Bank", city: null, verified: true, route_basis: "STRAIGHT_LINE_ESTIMATE",
        node_ids: [], route_ids: [], distance_km: 30, straight_line_km: 30, transit_minutes: 40, eta_minutes: 70,
        slack_minutes: 5, usable_by_type: { A_POS: 2 }, usable_total: 2, rejected_reason: null },
      { facility_id: "unv", facility_name: "Shady Clinic", city: null, verified: false, route_basis: null,
        node_ids: [], route_ids: [], distance_km: null, straight_line_km: null, transit_minutes: null, eta_minutes: null,
        slack_minutes: null, usable_by_type: {}, usable_total: 0, rejected_reason: "facility is not verified" },
    ],
  };
}

describe("PlanView", () => {
  it("shows the engine, the estimate label and every rejected facility with its reason", () => {
    renderWithProviders(<PlanView result={plan()} />);
    expect(screen.getByText(/RELATIONAL_ROUTES/)).toBeInTheDocument();
    expect(screen.getAllByText(/Straight-line ESTIMATE/).length).toBeGreaterThan(0);
    expect(screen.getByText("Rejected: facility is not verified")).toBeInTheDocument();
    expect(screen.getByText("DEADLINE RISK")).toBeInTheDocument();
    expect(screen.getByText(/1 disrupted route\(s\) excluded/)).toBeInTheDocument();
  });
});

describe("LivePageShell", () => {
  it("asks visitors to sign in and always discloses synthetic data", () => {
    renderWithProviders(<LivePageShell title="T" intro="i" roles={["HOSPITAL"]}><p>secret</p></LivePageShell>);
    expect(screen.getByText("Sign in to run this page")).toBeInTheDocument();
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(/synthetic/i);
  });

  it("gates by role", () => {
    auth.user = { role: "DONOR", full_name: "D" };
    renderWithProviders(<LivePageShell title="T" intro="i" roles={["HOSPITAL"]}><p>secret</p></LivePageShell>);
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
    auth.user = { role: "HOSPITAL", full_name: "H" };
    renderWithProviders(<LivePageShell title="T2" intro="i" roles={["HOSPITAL"]}><p>secret</p></LivePageShell>);
    expect(screen.getByText("secret")).toBeInTheDocument();
  });
});

describe("Analytics (forecasts)", () => {
  it("reports honestly when the model does not beat the naive baseline", async () => {
    auth.user = { role: "HOSPITAL", full_name: "H" };
    const run: ForecastRun = {
      id: "r1", facility_id: "f", method: "simple_exponential_smoothing", history_days: 56,
      horizon_days: 7, parameters: { signal: "units allocated at facility" },
      data_is_synthetic: true, created_at: "2026-10-01T00:00:00Z", disclaimer: "Not a clinical prediction.",
      results: [{
        blood_type: "O_POS", component: "WHOLE_BLOOD", status: "OK", total_units: 40, active_days: 20, alpha: 0.3,
        history: [{ day: "2026-09-30", units: 3 }],
        forecast: [{ h: 1, day: "2026-10-01", mean: 3, lo80: 1, hi80: 5 }],
        backtest: { holdout_days: 7, ses_mae: 2, naive_mae: 1.5, seasonal_naive_mae: 1.8, beats_naive: false, beats_seasonal_naive: false },
      }],
    };
    api.listForecasts.mockResolvedValue([run]);
    renderWithProviders(<Analytics />);
    // Audit F9 (2026-10-31): BOTH baselines now carry their own verdict.
    await waitFor(() => expect(screen.getAllByText(/does NOT beat/)).toHaveLength(2));
    expect(screen.getByText(/Seasonal-naive baseline/)).toBeInTheDocument();
    expect(screen.getByText(/did not beat every baseline/)).toBeInTheDocument();
    expect(screen.getByText(/SYNTHETIC data/)).toBeInTheDocument();
  });

  it("states a mixed result per baseline instead of one overall claim", async () => {
    auth.user = { role: "HOSPITAL", full_name: "H" };
    const run: ForecastRun = {
      id: "r2", facility_id: "f", method: "simple_exponential_smoothing", history_days: 56,
      horizon_days: 7, parameters: { signal: "units" }, data_is_synthetic: true,
      created_at: "2026-10-01T00:00:00Z", disclaimer: "Not a clinical prediction.",
      results: [{
        blood_type: "O_POS", component: "WHOLE_BLOOD", status: "OK", total_units: 40, active_days: 20, alpha: 0.3,
        history: [{ day: "2026-09-30", units: 3 }],
        forecast: [{ h: 1, day: "2026-10-01", mean: 3, lo80: 1, hi80: 5 }],
        backtest: { holdout_days: 7, ses_mae: 1.6, naive_mae: 1.9, seasonal_naive_mae: 1.4, beats_naive: true, beats_seasonal_naive: false },
      }],
    };
    api.listForecasts.mockResolvedValue([run]);
    renderWithProviders(<Analytics />);
    await waitFor(() => expect(screen.getByTestId("backtest-verdict")).toBeInTheDocument());
    const verdict = screen.getByTestId("backtest-verdict");
    expect(verdict).toHaveTextContent("model beats it");
    expect(verdict).toHaveTextContent("model does NOT beat it");
    expect(verdict).toHaveTextContent("did not beat every baseline");
  });
});

describe("UnitLifecycle", () => {
  function unit(over: Partial<BloodUnitRow>): BloodUnitRow {
    return {
      id: Math.random().toString(36), unit_code: "LL-X", blood_type: "A_POS", component: "PLASMA", state: "AVAILABLE",
      provenance: "REAL", received_at: "2026-10-01T00:00:00Z", expires_at: null, thawed_at: null,
      thaw_expires_at: null, effective_expires_at: null, status_reason: null, ...over,
    } as BloodUnitRow;
  }

  it("offers only the actions each state allows", async () => {
    api.listMyUnits.mockResolvedValue([
      unit({ unit_code: "FROZEN-1" }),
      unit({ unit_code: "Q-1", state: "QUARANTINED", component: "WHOLE_BLOOD" }),
      unit({ unit_code: "GONE-1", state: "DISCARDED", component: "WHOLE_BLOOD" }),
    ]);
    renderWithProviders(<UnitLifecycle />);
    await waitFor(() => expect(screen.getByText("FROZEN-1")).toBeInTheDocument());
    const row = (code: string) => screen.getByText(code).closest("tr")!;
    expect(row("FROZEN-1")).toHaveTextContent("Thaw");
    expect(row("Q-1")).toHaveTextContent("Release");
    expect(row("Q-1")).not.toHaveTextContent("Thaw");
    expect(row("GONE-1")).not.toHaveTextContent("Discard");
  });
});

describe("MathematicalModel (QA round)", () => {
  it("does not call the authenticated config endpoint for an anonymous visitor", async () => {
    renderWithProviders(<MathematicalModel />);
    expect(await screen.findByText("Sign in to run this page")).toBeInTheDocument();
    expect(api.getPathwayConfig).not.toHaveBeenCalled();
  });

  it("loads the configured values once signed in", async () => {
    auth.user = { role: "DONOR", full_name: "Donor" };
    api.getPathwayConfig.mockResolvedValue({
      max_hops: 6, straight_line_speed_kmph: 45, handling_minutes: 30, deadline_risk_minutes: 15,
      strategies: {}, max_backups: 3, max_donor_distance_km: 50, hospital_alert_radius_km: 25,
    });
    renderWithProviders(<MathematicalModel />);
    await waitFor(() => expect(api.getPathwayConfig).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/H = 6 edges/)).toBeInTheDocument();
  });
});
