/**
 * API client for the gap-elimination features: the pathway engine
 * (dry-run simulation, stored plans with full routes, reservations,
 * overrides), cold-chain telemetry and per-unit receipt outcomes, the
 * per-unit lifecycle (thaw / quarantine / discard), demand forecasts and
 * facility-verification revocation. Every call hits a real endpoint.
 */
import { api } from "./api";
import type { BloodType } from "./domain";

export type Component = "WHOLE_BLOOD" | "RED_BLOOD_CELLS" | "PLASMA" | "PLATELETS";
export type Urgency = "ROUTINE" | "URGENT" | "CRITICAL";
export type Feasibility = "VIABLE" | "DEADLINE_RISK" | "PARTIAL" | "IMPOSSIBLE";

export const COMPONENTS: Component[] = ["WHOLE_BLOOD", "RED_BLOOD_CELLS", "PLASMA", "PLATELETS"];
export const COMPONENT_LABEL: Record<Component, string> = {
  WHOLE_BLOOD: "Whole blood",
  RED_BLOOD_CELLS: "Red cells",
  PLASMA: "Plasma",
  PLATELETS: "Platelets",
};
export const URGENCIES: Urgency[] = ["CRITICAL", "URGENT", "ROUTINE"];

export const FEASIBILITY_STYLE: Record<Feasibility, string> = {
  VIABLE: "bg-green-100 text-green-900 border-green-300",
  DEADLINE_RISK: "bg-amber-100 text-amber-900 border-amber-300",
  PARTIAL: "bg-orange-100 text-orange-900 border-orange-300",
  IMPOSSIBLE: "bg-red-100 text-red-900 border-red-300",
};

// ---------------------------------------------------------------- pathways
export interface FacilityOption {
  facility_id: string;
  facility_name: string | null;
  city: string | null;
  verified: boolean;
  route_basis: string | null;
  node_ids: string[];
  route_ids: string[];
  distance_km: number | null;
  straight_line_km: number | null;
  transit_minutes: number | null;
  eta_minutes: number | null;
  slack_minutes: number | null;
  usable_by_type: Record<string, number>;
  usable_total: number;
  rejected_reason: string | null;
}

export interface PlanLeg {
  facility_id: string;
  facility_name: string | null;
  units: number;
  units_by_type: Record<string, number>;
  route_basis: string | null;
  route_ids: string[];
  node_ids: string[];
  eta_minutes: number | null;
  distance_km: number | null;
}

export interface CandidatePlan {
  feasibility: Feasibility;
  units_requested: number;
  units_planned: number;
  eta_minutes: number | null;
  arrival_at: string | null;
  slack_minutes: number | null;
  explanation: string[];
  legs: PlanLeg[];
}

export interface PlanResult {
  strategy: string;
  strategy_description: string;
  strategy_keys: string[];
  pathway_source: string;
  graph_available: boolean;
  degraded_reason: string | null;
  recipient_blood_type: BloodType;
  component: Component;
  units_needed: number;
  urgency: Urgency;
  deadline: string | null;
  destination_facility_id: string;
  compatible_types: BloodType[];
  disrupted_routes_excluded: number;
  simulated_failed_route_ids: string[];
  assumptions: Record<string, unknown>;
  primary: CandidatePlan;
  backups: CandidatePlan[];
  options: FacilityOption[];
}

export interface SimulateBody {
  recipient_blood_type: BloodType;
  component: Component;
  units_needed: number;
  urgency: Urgency;
  deadline_minutes: number | null;
  destination_facility_id: string;
  failed_route_ids: string[];
  max_backups?: number | null;
}

export const simulatePathway = (body: SimulateBody) =>
  api.post<PlanResult>("/pathways/simulate", body).then((r) => r.data);

export interface PathwayConfig {
  handling_minutes: number;
  deadline_risk_minutes: number;
  max_hops: number;
  max_backups: number;
  max_donor_distance_km: number;
  hospital_alert_radius_km: number;
  straight_line_speed_kmph: number;
  data_is_synthetic: boolean;
  strategies: Record<string, { name: string; objective: string; keys: string[]; description: string }>;
}
export const getPathwayConfig = () =>
  api.get<PathwayConfig>("/pathways/config").then((r) => r.data);

export interface PathwayStep {
  step_index: number;
  route_id: string;
  from_facility_id: string;
  to_facility_id: string;
  distance_km: number;
  transit_minutes: number;
  route_status_at_plan: string;
  route_status_now: string | null;
}
export interface PathwayLeg {
  leg_index: number;
  source_facility_id: string;
  source_facility_name: string | null;
  units: number;
  units_by_type: Record<string, number>;
  route_basis: string;
  node_ids: string[];
  distance_km: number;
  straight_line_km: number | null;
  transit_minutes: number;
  eta_minutes: number;
  hops: number;
  steps: PathwayStep[];
}
export interface StoredPathway {
  id: string;
  version: number;
  plan_round: number;
  backup_rank: number;
  status: "ACTIVE" | "BACKUP" | "SUPERSEDED" | "INVALIDATED";
  feasibility: Feasibility | null;
  origin_facility_id: string;
  selected_facility_id: string | null;
  pathway_source: string;
  component: Component | null;
  recipient_blood_type: BloodType | null;
  urgency: Urgency | null;
  units_requested: number | null;
  units_planned: number | null;
  eta_minutes: number | null;
  arrival_at: string | null;
  deadline_at: string | null;
  slack_minutes: number | null;
  strategy: string | null;
  explanation: string[] | null;
  assumptions: Record<string, unknown> | null;
  reason: string;
  created_at: string;
  promoted_at: string | null;
  superseded_reason: string | null;
  invalidated_reason: string | null;
  reserved_units: number;
  // Audit C15/C21 (2026-10-31): never infer "secured" from status alone.
  reservation_status?: "RESERVED" | "HANDED_OFF" | "RECOVERY_REQUIRED" | "UNRESERVED";
  recovery_required_at?: string | null;
  recovery_required_reason?: string | null;
  shipments?: {
    id: string;
    shipment_code: string;
    status: string;
    origin_facility_id: string;
    destination_facility_id: string;
    units: number;
  }[];
  legs: PathwayLeg[];
}
export const handOffEmergencyPathway = (emergencyId: string) =>
  api.post<StoredPathway>(`/emergencies/${emergencyId}/pathway/handoff`).then((r) => r.data);
export const listEmergencyPathways = (emergencyId: string) =>
  api.get<StoredPathway[]>(`/emergencies/${emergencyId}/pathways`).then((r) => r.data);
export const planEmergencyPathway = (emergencyId: string, originFacilityId?: string) =>
  api
    .post(`/emergencies/${emergencyId}/pathway`, { origin_facility_id: originFacilityId ?? null })
    .then((r) => r.data);
export const reserveEmergencyPathway = (emergencyId: string) =>
  api.post<StoredPathway>(`/emergencies/${emergencyId}/pathway/reserve`).then((r) => r.data);
export const releaseEmergencyPathway = (emergencyId: string, note?: string) =>
  api
    .post<StoredPathway>(`/emergencies/${emergencyId}/pathway/release`, { note: note ?? null })
    .then((r) => r.data);
export const grantOverride = (
  emergencyId: string,
  kind: "DONOR_DISTANCE" | "UNVERIFIED_FACILITY",
  reason: string,
) => api.post(`/emergencies/${emergencyId}/overrides`, { kind, reason }).then((r) => r.data);

// ---------------------------------------------------------------- units
export interface BloodUnitRow {
  id: string;
  unit_code: string;
  blood_type: BloodType;
  component: Component;
  state: "AVAILABLE" | "RESERVED" | "ALLOCATED" | "EXPIRED" | "QUARANTINED" | "DISCARDED";
  provenance: string;
  received_at: string;
  expires_at: string | null;
  thawed_at: string | null;
  thaw_expires_at: string | null;
  effective_expires_at: string | null;
  status_reason: string | null;
}
export const listMyUnits = (params: { state?: string; component?: string } = {}) =>
  api.get<BloodUnitRow[]>("/hospitals/me/units", { params }).then((r) => r.data);
export const thawUnit = (id: string, note?: string) =>
  api.post<BloodUnitRow>(`/hospitals/me/units/${id}/thaw`, { note: note ?? null }).then((r) => r.data);
export const quarantineUnit = (id: string, reason: string) =>
  api.post<BloodUnitRow>(`/hospitals/me/units/${id}/quarantine`, { reason }).then((r) => r.data);
export const releaseQuarantine = (id: string, reason: string) =>
  api
    .post<BloodUnitRow>(`/hospitals/me/units/${id}/release-quarantine`, { reason })
    .then((r) => r.data);
export const discardUnit = (id: string, reason: string) =>
  api.post<BloodUnitRow>(`/hospitals/me/units/${id}/discard`, { reason }).then((r) => r.data);

// ---------------------------------------------------------------- telemetry
export interface ColdChainReport {
  status: string;
  readings: number;
  valid_readings: number;
  failed_readings: number;
  min_c: number | null;
  max_c: number | null;
  range_min_c: number | null;
  range_max_c: number | null;
  excursions: { start: string; end: string; readings: number; min_c: number; max_c: number }[];
  gaps_minutes: number[];
  findings: string[];
  sources: Record<string, number>;
}
export interface Telemetry {
  report: ColdChainReport;
  readings: {
    recorded_at: string;
    temperature_c: number | null;
    sensor_id: string;
    source: string;
    error_code: string | null;
  }[];
  inserted?: number | null;
  duplicates?: number | null;
}
export const getTelemetry = (shipmentId: string) =>
  api.get<Telemetry>(`/shipments/${shipmentId}/telemetry`).then((r) => r.data);
export const addTelemetry = (
  shipmentId: string,
  readings: { recorded_at: string; temperature_c: number | null; sensor_id: string; error_code?: string | null }[],
  source: "SENSOR" | "MANUAL" = "MANUAL",
) => api.post<Telemetry>(`/shipments/${shipmentId}/telemetry`, { source, readings }).then((r) => r.data);
export const receiveShipmentWithOutcomes = (
  shipmentId: string,
  items: { unit_code: string; outcome: string; reason?: string | null }[],
  reason?: string,
) => api.post(`/shipments/${shipmentId}/receive`, { reason: reason ?? null, items }).then((r) => r.data);

// ---------------------------------------------------------------- forecasts
export interface ForecastPoint { h: number; day: string; mean: number; lo80: number; hi80: number }
export interface ForecastSeries {
  blood_type: string;
  component: string;
  status: "OK" | "INSUFFICIENT_HISTORY";
  history: { day: string; units: number }[];
  forecast: ForecastPoint[];
  total_units: number;
  active_days: number;
  alpha?: number;
  backtest?: {
    holdout_days: number;
    ses_mae: number;
    naive_mae: number;
    seasonal_naive_mae: number;
    beats_naive: boolean;
    beats_seasonal_naive: boolean;
  } | null;
  note?: string;
}
export interface ForecastRun {
  id: string;
  facility_id: string | null;
  method: string;
  history_days: number;
  horizon_days: number;
  parameters: Record<string, unknown>;
  results: ForecastSeries[];
  data_is_synthetic: boolean;
  created_at: string;
  disclaimer: string;
}
export const createForecast = (body: { history_days: number; horizon_days: number; network?: boolean }) =>
  api.post<ForecastRun>("/forecasts", body).then((r) => r.data);
export const listForecasts = () => api.get<ForecastRun[]>("/forecasts").then((r) => r.data);

// ---------------------------------------------------------------- admin
export const revokeFacilityVerification = (facilityId: string, reason: string) =>
  api
    .post(`/admin/facilities/${facilityId}/revoke-verification`, { reason })
    .then((r) => r.data);

// ---------------------------------------------------------------- donor shortlist (2026-10-31)
export type ShortlistSubjectKind = "BLOOD_REQUEST" | "EMERGENCY";
export type DonorContactStatus =
  | "QUEUED"
  | "AWAITING_RESPONSE"
  | "NOTIFICATION_FAILED"
  | "ACCEPTED"
  | "DECLINED"
  | "UNAVAILABLE"
  | "TIMED_OUT"
  | "ELIGIBILITY_RECHECK_REQUIRED"
  | "CONFIRMED_FOR_COORDINATION"
  | "WITHDRAWN";
/** Unified Donor Priority Score (backend app/core/unified_priority.py).
 * A ranking measure on 0-100 -- NOT a probability of donation. */
export const RANKING_STRATEGIES = [
  "UNIFIED_PRIORITY",
  "NEAREST",
  "TIME_AWARE",
  "TIME_RESILIENCE",
  "EVENT_DRIVEN",
] as const;
export type RankingStrategy = (typeof RANKING_STRATEGIES)[number];
export interface PriorityFactor {
  name: string;
  label: string;
  /** null in SUMMARY explanations (patients, family): weights are restricted
   * configuration and factor values are other donors' history. */
  weight: number | null;
  value: number | null;
  contribution: number | null;
  status: "OBSERVED" | "COARSE" | "MISSING";
  detail: string;
}
export interface Priority {
  model: string;
  score: number;
  score_available: boolean;
  evidence_coverage: number;
  evidence_level: "HIGH" | "MEDIUM" | "LOW" | "INSUFFICIENT";
  /** null in SUMMARY explanations; there `score` is also rounded to a
   * 10-point band (privacy round 2), so neither can be used to back out a
   * donor's distance or history. */
  missing_data_baseline: number | null;
  availability_status:
    | "ACCEPTED_THIS_REQUEST"
    | "CONFIRMED_RECENTLY"
    | "CONFIRMED_STALE"
    | "UNCONFIRMED";
  factors: PriorityFactor[];
}
export interface ShortlistCandidate {
  donor_ref: string;
  donor_name: string | null;
  blood_type: BloodType | null;
  rank: number;
  score: number;
  components: Record<string, number | null>;
  priority: Priority;
  next_action: string;
  distance_basis: "MEASURED" | "SAME_CITY" | "UNKNOWN" | "NO_REQUEST_LOCATION";
  distance_km: number | null;
  travel_minutes_estimate: number | null;
  location_stale: boolean | null;
  explanation: string[];
  synthetic: boolean;
}
export interface ShortlistContact {
  id: string;
  donor_ref: string;
  donor_name: string | null;
  blood_type: BloodType | null;
  status: DonorContactStatus;
  batch: number;
  rank_at_contact: number;
  score_at_contact: number;
  ranking_strategy?: string | null;
  scoring_model?: string | null;
  contacted_at: string | null;
  timeout_at: string | null;
  responded_at: string | null;
  eligibility_rechecked_at: string | null;
  confirmed_at: string | null;
  status_reason: string | null;
  notification_state: "SENT_UNREAD" | "READ" | null;
  sms_status: string | null;
  simulated: boolean;
  synthetic: boolean;
}
export interface Shortlist {
  policy: string;
  strategy: RankingStrategy;
  scoring_model: string;
  explanation_level: "FULL" | "SUMMARY";
  subject: ShortlistSubjectKind;
  subject_id: string;
  is_open: boolean;
  urgency: Urgency;
  units_needed: number;
  units_fulfilled: number;
  units_reserved_inventory: number;
  donors_still_needed: number;
  remaining_unmet: number;
  state: "SATISFIED" | "PARTIAL" | "AWAITING" | "NO_ELIGIBLE" | "NOT_STARTED" | "CLOSED";
  summary: string;
  counts: {
    compatible_pool: number;
    eligible_candidates: number;
    not_yet_contacted: number;
    contacted: number;
    awaiting_response: number;
    accepted: number;
    confirmed_for_coordination: number;
    declined: number;
    unavailable: number;
    timed_out: number;
    notification_failed: number;
    withdrawn: number;
    eligibility_recheck_required: number;
  };
  excluded: Record<string, number>;
  next_to_contact: ShortlistCandidate[];
  contacts: ShortlistContact[];
  escalation: string[];
  demo_mode: boolean;
  disclaimer: string;
}
const shortlistBase = (kind: ShortlistSubjectKind, id: string) =>
  kind === "BLOOD_REQUEST" ? `/blood-requests/${id}/shortlist` : `/emergencies/${id}/shortlist`;
export const getShortlist = (kind: ShortlistSubjectKind, id: string, strategy?: RankingStrategy) =>
  api
    .get<Shortlist>(shortlistBase(kind, id), { params: strategy ? { strategy } : undefined })
    .then((r) => r.data);
export const contactNextBatch = (kind: ShortlistSubjectKind, id: string, strategy?: RankingStrategy) =>
  api
    .post<Shortlist>(`${shortlistBase(kind, id)}/contact-next`, undefined, {
      params: strategy ? { strategy } : undefined,
    })
    .then((r) => r.data);
export const confirmShortlistContact = (kind: ShortlistSubjectKind, id: string, contactId: string) =>
  api
    .post<Shortlist>(`${shortlistBase(kind, id)}/contacts/${contactId}/confirm`)
    .then((r) => r.data);
export const simulateShortlist = (kind: ShortlistSubjectKind, id: string) =>
  api
    .post<{ simulated: Record<string, number>; skipped_non_synthetic: number; shortlist: Shortlist }>(
      `/demo/shortlist/${kind}/${id}/simulate`,
      { force_timeouts: true },
    )
    .then((r) => r.data);
