/** Phase 2 domain types + API calls (patient / family / donor flows). */
import { api, API_BASE_URL } from "./api";

export type BloodType =
  | "A_POS" | "A_NEG" | "B_POS" | "B_NEG"
  | "AB_POS" | "AB_NEG" | "O_POS" | "O_NEG";

export const BLOOD_TYPES: BloodType[] = [
  "O_NEG", "O_POS", "A_NEG", "A_POS", "B_NEG", "B_POS", "AB_NEG", "AB_POS",
];

// Kept in sync with the backend's single authoritative mapping
// (app.core.blood_compatibility.UI_LABELS) -- fetch getBloodCompatibility()
// for the full compatibility matrix rather than deriving it from this map.
export const BLOOD_LABEL: Record<BloodType, string> = {
  A_POS: "A+", A_NEG: "A-", B_POS: "B+", B_NEG: "B-",
  AB_POS: "AB+", AB_NEG: "AB-", O_POS: "O+", O_NEG: "O-",
};

export type Urgency = "ROUTINE" | "URGENT" | "CRITICAL";
export type BloodRequestStatus =
  | "PENDING" | "MATCHING" | "PARTIALLY_FULFILLED" | "FULFILLED" | "CANCELLED" | "EXPIRED";

/** Phase E: every status in which a request still needs blood -- mirrors
 * backend `app.core.request_lifecycle.OPEN_STATUSES`. A PARTIALLY_FULFILLED
 * request is still open; before Phase E several screens hardcoded
 * ["PENDING", "MATCHING"] and would have shown it as closed. Use this, never
 * a local copy. */
export const OPEN_REQUEST_STATUSES: readonly BloodRequestStatus[] = [
  "PENDING", "MATCHING", "PARTIALLY_FULFILLED",
];
export const isOpenRequestStatus = (s: BloodRequestStatus): boolean =>
  OPEN_REQUEST_STATUSES.includes(s);

/** Phase E: mirrors backend `BloodComponent` exactly. Before Phase E the
 * frontend had no notion of components at all, so the shipment form could
 * only ever ship the backend's WHOLE_BLOOD default. */
export type BloodComponent = "WHOLE_BLOOD" | "RED_BLOOD_CELLS" | "PLASMA" | "PLATELETS";
export const BLOOD_COMPONENTS: BloodComponent[] = [
  "WHOLE_BLOOD", "RED_BLOOD_CELLS", "PLASMA", "PLATELETS",
];
export const COMPONENT_LABEL: Record<BloodComponent, string> = {
  WHOLE_BLOOD: "Whole blood",
  RED_BLOOD_CELLS: "Red blood cells",
  PLASMA: "Plasma",
  PLATELETS: "Platelets",
};
/** Label for a component string from the API; falls back to the raw value
 * rather than guessing if the backend ever sends one this build doesn't know. */
export const componentLabel = (c: string | null | undefined): string =>
  c ? (COMPONENT_LABEL as Record<string, string>)[c] ?? c : "Unknown component";
export type DonorResponseStatus = "ACCEPTED" | "DECLINED" | "NOT_NOW";
export type DonorAvailabilityState = "AVAILABLE" | "UNAVAILABLE" | "NOT_NOW";
export type EmergencyStatus =
  | "ACTIVE" | "ACKNOWLEDGED" | "MATCHING" | "ASSISTANCE_FOUND" | "RESOLVED" | "CANCELLED";
export type UserRole = "PATIENT" | "FAMILY" | "DONOR" | "HOSPITAL" | "ADMIN";

export interface PatientProfile {
  user_id: string;
  blood_type: BloodType | null;
  date_of_birth: string | null;
  phone: string | null;
  address: string | null;
  city: string | null;
  medical_notes: string | null;
  emergency_contact_name: string | null;
  emergency_contact_phone: string | null;
}

export interface DonorProfile {
  user_id: string;
  blood_type: BloodType | null;
  phone: string | null;
  city: string | null;
  last_donation_date: string | null;
  is_available: boolean;
  availability_state: DonorAvailabilityState;
  availability_note: string | null;
  weight_kg: number | null;
  medical_conditions: string | null;
  latitude: number | null;
  longitude: number | null;
  share_location: boolean;
  identity_verified: boolean;
  // Alert Watcher preferences (Phase 4) — see notify_matching_donors() server-side.
  alert_min_urgency: Urgency;
  alert_radius_km: number | null;
  eligible: boolean;
  eligible_on: string | null;
}

export interface HospitalProfile {
  /** hospital_profiles.id -- distinct from user_id; the id referenced by
   * Emergency.target_facility_id and EmergencyPathway's facility columns. */
  id: string;
  user_id: string;
  name: string | null;
  license_no: string | null;
  phone: string | null;
  address: string | null;
  city: string | null;
  latitude: number | null;
  longitude: number | null;
  verified: boolean;
}

export interface InventoryItem {
  blood_type: BloodType;
  units: number;
  reserved_units: number;
  available_units: number;
}

export type HospitalResponseStatus =
  | "ACKNOWLEDGED" | "PREPARING" | "DISPATCHED" | "DECLINED" | "ALLOCATED" | "FULFILLED";

export interface HospitalRequestItem extends BloodRequest {
  hospital_status: HospitalResponseStatus | null;
  distance_km: number | null;
}

export type InventoryActionKind = "add" | "reserve" | "allocate" | "release" | "expire";

export interface InventoryActionInput {
  blood_type: BloodType;
  units: number;
  request_id?: string;
  note?: string;
  /** Phase G: omitted = WHOLE_BLOOD (the backend default). */
  component?: BloodComponent;
}

/** Phase G: one real (blood_type, component) inventory row. */
export interface ComponentInventoryItem {
  blood_type: BloodType;
  component: BloodComponent;
  units: number;
  reserved_units: number;
  available_units: number;
}

export interface InventoryReconcileInput {
  blood_type: BloodType;
  units: number;
  note?: string;
}

export type HospitalInventoryAction =
  | "ADD" | "RESERVE" | "ALLOCATE" | "RELEASE" | "EXPIRE" | "RECONCILE";

export interface HospitalInventoryLogEntry {
  id: string;
  blood_type: BloodType;
  action: HospitalInventoryAction;
  delta_units: number;
  delta_reserved: number;
  resulting_units: number;
  resulting_reserved: number;
  request_id: string | null;
  note: string | null;
  created_at: string;
}

export interface HospitalRoute {
  id: string;
  partner_facility_id: string;
  partner_facility_name: string | null;
  partner_facility_city: string | null;
  distance_km: number;
  transit_minutes: number;
  status: "OPERATIONAL" | "DISRUPTED";
  disrupted_reason: string | null;
}

export interface HospitalActivityItem {
  kind: "inventory" | "request";
  created_at: string;
  summary: string;
  request_id: string | null;
  blood_type: BloodType | null;
}

export interface MatchCandidate {
  donor_ref: string;
  donor_name: string | null;
  blood_type: BloodType;
  is_available: boolean;
  has_accepted: boolean;
  distance_km: number | null;
  last_donation_date: string | null;
  city: string | null;
}

export interface MatchCandidatesResult {
  request_id: string;
  recipient_blood_type: BloodType;
  compatible_donor_types: BloodType[];
  candidates: MatchCandidate[];
}

export interface MapDonorPin {
  blood_type: BloodType;
  latitude: number;
  longitude: number;
}

export interface MapData {
  center: { lat: number; lng: number } | null;
  donors: MapDonorPin[];
}

export interface DonorResponseItem {
  id: string;
  donor_user_id: string;
  status: DonorResponseStatus;
  note: string | null;
  created_at: string;
  donor_name?: string | null;
  donor_blood_type?: BloodType | null;
}

export interface BloodRequestEventItem {
  id: string;
  event_type: string;
  note: string | null;
  actor_user_id: string | null;
  created_at: string;
}

export interface BloodRequest {
  id: string;
  requester_user_id: string;
  patient_user_id: string;
  patient_name: string | null;
  requester_name: string | null;
  /** True when a FAMILY member filed this on the patient's behalf. */
  filed_by_family: boolean;
  blood_type: BloodType;
  units_needed: number;
  /** Phase E: the server's single source of truth for how many of
   * `units_needed` have actually been allocated/declared met. The UI
   * derives "remaining" from these two numbers and never sends either. */
  fulfilled_units: number;
  urgency: Urgency;
  status: BloodRequestStatus;
  hospital_name: string | null;
  city: string | null;
  note: string | null;
  needed_by: string | null;
  fulfilled_at: string | null;
  /** Phase 10: set exactly once, the moment the request transitions into
   * CANCELLED / EXPIRED -- mirrors fulfilled_at. */
  cancelled_at: string | null;
  expired_at: string | null;
  created_at: string;
  updated_at: string;
  accepted_count: number;
  /** Phase 10: the state machine's own answer to "what can happen next"
   * (empty for a terminal request). */
  allowed_transitions: BloodRequestStatus[];
}

export interface BloodRequestDetail extends BloodRequest {
  events: BloodRequestEventItem[];
  responses: DonorResponseItem[];
  my_response: DonorResponseItem | null;
}

export interface DonorOpenRequest extends BloodRequest {
  my_response: DonorResponseItem | null;
}

export interface DonorMatch extends BloodRequest {
  my_response: DonorResponseItem;
  distance_km: number | null;
}

export interface Emergency {
  id: string;
  created_by_user_id: string;
  patient_user_id: string | null;
  created_by_name: string | null;
  patient_name: string | null;
  /** True when a FAMILY member started this SOS on the patient's behalf. */
  filed_by_family: boolean;
  blood_type: BloodType | null;
  urgency: Urgency;
  status: EmergencyStatus;
  location_text: string | null;
  note: string | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
  // ---- Phase 12 additions ----
  units_needed: number | null;
  deadline: string | null;
  target_facility_id: string | null;
  /** Computed on read, never stored. Positive: time remaining before
   * `deadline`. Zero: exactly at deadline. Negative: genuinely overdue --
   * never clamped to zero. `null` when there's no deadline to measure. */
  slack_seconds: number | null;
  /** Phase 31.1: set straight from the backend column
   * (Emergency.deadline_exceeded_at) -- the first time any read or mutating
   * request noticed `deadline` had passed on a still-open emergency. `null`
   * means either no deadline was ever set, or it hasn't passed yet. This is
   * a separate fact from `status` and from `slack_seconds` being negative --
   * never derive it client-side, and never let it replace or hide `status`. */
  deadline_exceeded_at: string | null;
  /** Every status this emergency could legally move to right now (empty
   * once it's terminal). */
  allowed_transitions: EmergencyStatus[];
  /** Set only on the just-created response, only when this looked like a
   * possible second SOS for the same patient/creator. Never blocks
   * creation -- purely informational. */
  possible_duplicate_of: string | null;
}

export interface EmergencyResponder {
  id: string;
  responder_user_id: string;
  responder_role: UserRole;
  status: DonorResponseStatus;
  note: string | null;
  created_at: string;
  responder_name: string | null;
  /** Set only when this response just attempted a real inventory
   * reservation (a HOSPITAL responder accepting/withdrawing against an
   * emergency with both a blood type and units_needed) -- reports the
   * real outcome, including failure, never a fabricated success. */
  inventory_note?: string | null;
}

export type EmergencyPathwayStatus = "ACTIVE" | "SUPERSEDED";

export interface EmergencyPathway {
  id: string;
  version: number;
  status: EmergencyPathwayStatus;
  origin_facility_id: string;
  selected_facility_id: string | null;
  pathway_source: GraphPathwaySource;
  distance_km: number | null;
  transit_minutes: number | null;
  hops: number | null;
  graph_available: boolean;
  reason: string;
  superseded_at: string | null;
  superseded_reason: string | null;
  created_at: string;
}

export interface EmergencyDetail extends Emergency {
  events: BloodRequestEventItem[];
  responders: EmergencyResponder[];
  accepted_count: number;
  my_response: EmergencyResponder | null;
  /** The emergency's current resource-pathway plan, if one has ever been
   * selected -- `null` (never fabricated) when none has, or the last
   * attempt genuinely found nothing reachable. */
  current_pathway: EmergencyPathway | null;
  pathway_history: EmergencyPathway[];
}

export interface EmergencyOpen extends Emergency {
  accepted_count: number;
  my_response: EmergencyResponder | null;
  distance_km: number | null;
}

export interface EmergencyMatchResponder {
  responder_ref: string;
  responder_name: string | null;
  role: UserRole;
  blood_type: BloodType | null;
  is_available: boolean;
  has_accepted: boolean;
  distance_km: number | null;
  city: string | null;
  rank?: number;
  explanation?: string;
  /** Hospitals only: whether the facility is verified. */
  facility_verified?: boolean | null;
}

export interface EmergencyMatchResult {
  emergency_id: string;
  recipient_blood_type: BloodType | null;
  compatible_donor_types: BloodType[];
  candidates: EmergencyMatchResponder[];
  /** Gap elimination: the donor distance policy that was applied. */
  component?: string;
  distance_limit_km?: number | null;
  excluded_beyond_limit?: number;
  unknown_distance?: number;
  distance_override_active?: boolean;
  emergency_has_coordinates?: boolean;
}

/** Phase E: DONOR_REPORTED is the donor's own statement and must never be
 * displayed as "verified". */
export type DonationVerificationStatus = "DONOR_REPORTED" | "VERIFIED" | "REJECTED";

export interface Donation {
  id: string;
  donor_user_id: string;
  blood_request_id: string | null;
  units: number;
  donated_on: string;
  hospital_name: string | null;
  /** Phase E: set only when the donor picked a real facility; that
   * facility (or an admin) is who can verify the donation. */
  hospital_id: string | null;
  note: string | null;
  verification_status: DonationVerificationStatus;
  verified_by_user_id: string | null;
  verified_at: string | null;
  verification_note: string | null;
  created_at: string;
}

// Phase 26: mirrors CreateBloodRequestBody.idempotency_key -- the same
// "generate once per submission attempt, reuse across retries of that
// attempt" contract logDonation's caller (LogDonationModal) follows.
export interface CreateDonationBody {
  donated_on: string;
  units: number;
  hospital_name?: string | null;
  hospital_id?: string | null;
  note?: string | null;
  blood_request_id?: string | null;
  idempotency_key?: string;
}

export interface Certificate {
  id: string;
  donation_id: string;
  donor_user_id: string;
  serial: string;
  verify_code: string;
  created_at: string;
  donor_name: string | null;
  donated_on: string | null;
  units: number | null;
  hospital_name: string | null;
  verify_path: string | null;
  download_token: string | null;
  /** Phase 31.1: set only by an admin's revoke action (POST
   * /admin/certificates/{id}/revoke). `null` means never revoked. Never
   * infer revocation from anything else -- these are the only two real
   * fields the backend returns for it. */
  revoked_at: string | null;
  revocation_reason: string | null;
}

export interface CertificateVerification {
  /** Phase 31.1: now honestly reflects revocation, not just "a row with
   * this code exists" -- `false` when `revoked_at` is set. */
  valid: boolean;
  serial: string;
  donor_name: string;
  donated_on: string;
  units: number;
  hospital_name: string | null;
  issued_at: string;
  revoked_at: string | null;
  revocation_reason: string | null;
}

// ---- Family links ------------------------------------------------------
export type FamilyLinkStatus = "PENDING" | "ACCEPTED" | "REJECTED" | "REVOKED" | "EXPIRED";

export interface PersonRef {
  id: string;
  full_name: string;
  email: string;
}

export interface FamilyLink {
  id: string;
  family_user_id: string;
  patient_user_id: string;
  relationship: string;
  status: FamilyLinkStatus;
  requested_by_user_id: string | null;
  created_at: string;
  /** Set while PENDING (an un-actioned request lazily expires past this). */
  expires_at: string | null;
  /** Free-text reason attached to the most recent accept/reject/revoke. */
  reason: string | null;
  /** Set when a PENDING request is actioned (accepted or rejected). */
  responded_at: string | null;
  /** Every state this link could legally move to right now -- server-side
   * truth, not a client-computed guess; drive UI affordances off this. */
  allowed_transitions: FamilyLinkStatus[];
  family: PersonRef | null;
  patient: PersonRef | null;
}

export const listFamilyLinks = () => api.get<FamilyLink[]>("/family/links").then((r) => r.data);
export const requestFamilyLink = (patient_email: string, relationship: string) =>
  api.post<FamilyLink>("/family/links", { patient_email, relationship }).then((r) => r.data);
export const acceptFamilyLink = (linkId: string, reason?: string) =>
  api.post<FamilyLink>(`/family/links/${linkId}/accept`, { reason: reason || undefined }).then((r) => r.data);
/** Patient declines a still-PENDING request outright (distinct from revoke). */
export const rejectFamilyLink = (linkId: string, reason?: string) =>
  api.post<FamilyLink>(`/family/links/${linkId}/reject`, { reason: reason || undefined }).then((r) => r.data);
/** Either party ends a PENDING (cancel) or ACCEPTED (end access) link. */
export const revokeFamilyLink = (linkId: string, reason?: string) =>
  api.post<FamilyLink>(`/family/links/${linkId}/revoke`, { reason: reason || undefined }).then((r) => r.data);

export interface AppNotification {
  id: string;
  type: string;
  title: string;
  body: string | null;
  data: Record<string, unknown>;
  read_at: string | null;
  created_at: string;
}

// ---- Patient / Family -----------------------------------------------------
export const getPatientProfile = () =>
  api.get<PatientProfile>("/patients/me").then((r) => r.data);
export const updatePatientProfile = (body: Partial<PatientProfile>) =>
  api.put<PatientProfile>("/patients/me", body).then((r) => r.data);

export interface CreateBloodRequestBody {
  blood_type: BloodType;
  units_needed?: number;
  urgency?: Urgency;
  hospital_name?: string | null;
  city?: string | null;
  note?: string | null;
  needed_by?: string | null;
  patient_email?: string | null;
  // Phase 19: client-generated once per submission attempt and resent
  // unchanged on any retry (double-click, network retry) so the backend
  // can recognize a replay instead of creating a duplicate request --
  // mirrors CreateEmergencyBody.idempotency_key above.
  idempotency_key?: string;
}
export const listBloodRequests = (status?: BloodRequestStatus) =>
  api
    .get<BloodRequest[]>("/blood-requests", {
      params: status ? { status_filter: status } : undefined,
    })
    .then((r) => r.data);
export const createBloodRequest = (body: CreateBloodRequestBody) =>
  api.post<BloodRequest>("/blood-requests", body).then((r) => r.data);
export const getBloodRequest = (id: string) =>
  api.get<BloodRequestDetail>(`/blood-requests/${id}`).then((r) => r.data);
export const cancelBloodRequest = (id: string, reason?: string) =>
  api.post<BloodRequest>(`/blood-requests/${id}/cancel`, { reason }).then((r) => r.data);
export const fulfillBloodRequest = (id: string) =>
  api.post<BloodRequest>(`/blood-requests/${id}/fulfill`).then((r) => r.data);

// ---- Emergencies --------------------------------------------------------
export interface CreateEmergencyBody {
  blood_type?: BloodType | null;
  urgency?: Urgency;
  location_text?: string | null;
  note?: string | null;
  patient_email?: string | null;
  // ---- Phase 12 additions (all optional; omitting them reproduces
  // pre-Phase-12 behavior exactly) ----
  units_needed?: number | null;
  /** ISO datetime string, e.g. from a <input type="datetime-local"> value. */
  deadline?: string | null;
  /** Client-generated once per SOS attempt and resent unchanged on any
   * retry (double-click, network retry) so the backend can recognize a
   * replay instead of creating a duplicate emergency. */
  idempotency_key?: string;
  target_facility_id?: string | null;
  /** Audit D4 (2026-10-31): donors are alerted only within the distance
   * limit or in the same city, so a structured city scopes the alert. */
  city?: string | null;
}
export const listEmergencies = () =>
  api.get<Emergency[]>("/emergencies").then((r) => r.data);
export const listActiveEmergencies = () =>
  api.get<EmergencyOpen[]>("/emergencies/active").then((r) => r.data);
export const getEmergency = (id: string) =>
  api.get<EmergencyDetail>(`/emergencies/${id}`).then((r) => r.data);
export const getEmergencyCandidates = (id: string) =>
  api.get<EmergencyMatchResult>(`/emergencies/${id}/match-candidates`).then((r) => r.data);
export const createEmergency = (body: CreateEmergencyBody) =>
  api.post<Emergency>("/emergencies", body).then((r) => r.data);
export const cancelEmergency = (id: string, note?: string) =>
  api.post<Emergency>(`/emergencies/${id}/cancel`, { note }).then((r) => r.data);
export const resolveEmergency = (id: string, note?: string) =>
  api.post<Emergency>(`/emergencies/${id}/resolve`, { note }).then((r) => r.data);
export const acknowledgeEmergency = (id: string, note?: string) =>
  api.post<Emergency>(`/emergencies/${id}/acknowledge`, { note }).then((r) => r.data);
export const emergencyAssistanceFound = (id: string, note?: string) =>
  api.post<Emergency>(`/emergencies/${id}/assistance-found`, { note }).then((r) => r.data);
export const respondToEmergency = (
  id: string,
  action: DonorResponseStatus,
  note?: string,
) =>
  api
    .post<EmergencyResponder>(`/emergencies/${id}/respond`, { action, note })
    .then((r) => r.data);
/** Select (or re-select, creating the next version) this emergency's
 * resource pathway from `origin_facility_id` -- HOSPITAL (the owning
 * facility only) or ADMIN. */
export const selectEmergencyPathway = (id: string, origin_facility_id: string) =>
  api
    .post<EmergencyPathway>(`/emergencies/${id}/pathway`, { origin_facility_id })
    .then((r) => r.data);

// ---- Donor ------------------------------------------------------------
export const getDonorProfile = () =>
  api.get<DonorProfile>("/donors/me").then((r) => r.data);
export const updateDonorProfile = (body: Partial<DonorProfile>) =>
  api.put<DonorProfile>("/donors/me", body).then((r) => r.data);
export const setAvailability = (
  state: DonorAvailabilityState,
  availability_note?: string | null,
) =>
  api
    .put<DonorProfile>("/donors/me/availability", { state, availability_note })
    .then((r) => r.data);
export const listDonorOpenRequests = () =>
  api.get<DonorOpenRequest[]>("/donors/me/requests").then((r) => r.data);
export const listDonorResponses = () =>
  api.get<DonorMatch[]>("/donors/me/responses").then((r) => r.data);
export const respondToRequest = (id: string, action: DonorResponseStatus, note?: string) =>
  api
    .post<DonorResponseItem>(`/blood-requests/${id}/respond`, { action, note })
    .then((r) => r.data);
// Phase E: donation review (HOSPITAL linked to the donation's facility, or ADMIN).
export const listDonationsForReview = (params?: {
  verification_status?: DonationVerificationStatus;
  unlinked_only?: boolean;
  limit?: number;
}) => api.get<Donation[]>("/donations", { params }).then((r) => r.data);
export const verifyDonation = (id: string, note?: string) =>
  api.post<Donation>(`/donations/${id}/verify`, { note: note || null }).then((r) => r.data);
export const rejectDonation = (id: string, note?: string) =>
  api.post<Donation>(`/donations/${id}/reject`, { note: note || null }).then((r) => r.data);
export const listDonations = () =>
  api.get<Donation[]>("/donors/me/donations").then((r) => r.data);
export const logDonation = (body: CreateDonationBody) =>
  api.post<Donation>("/donors/me/donations", body).then((r) => r.data);

// ---- Certificates -------------------------------------------------
export const listCertificates = () =>
  api.get<Certificate[]>("/certificates").then((r) => r.data);
export const issueCertificate = (donationId: string) =>
  api.post<Certificate>("/certificates", { donation_id: donationId }).then((r) => r.data);
export const getCertificate = (id: string) =>
  api.get<Certificate>(`/certificates/${id}`).then((r) => r.data);
export const verifyCertificate = (code: string) =>
  api.get<CertificateVerification>(`/certificates/verify/${code}`).then((r) => r.data);
/** Absolute URL for a browser download / new-tab open (signed, no cookie needed). */
export const certificateDownloadUrl = (id: string, token: string | null) =>
  `${API_BASE_URL}/certificates/${id}/download` + (token ? `?token=${encodeURIComponent(token)}` : "");

// ---- Hospital -------------------------------------------------------
export const getHospitalProfile = () =>
  api.get<HospitalProfile>("/hospitals/me").then((r) => r.data);
export const updateHospitalProfile = (body: Partial<HospitalProfile>) =>
  api.put<HospitalProfile>("/hospitals/me", body).then((r) => r.data);
export const getInventory = () =>
  api.get<InventoryItem[]>("/hospitals/me/inventory").then((r) => r.data);
export const putInventory = (items: { blood_type: BloodType; units: number }[]) =>
  api.put<InventoryItem[]>("/hospitals/me/inventory", { items }).then((r) => r.data);
export const listHospitalRequests = (includeClosed = false) =>
  api
    .get<HospitalRequestItem[]>("/hospitals/me/requests", {
      params: includeClosed ? { include_closed: true } : undefined,
    })
    .then((r) => r.data);
export const hospitalRespond = (
  requestId: string,
  status: HospitalResponseStatus,
  note?: string,
) =>
  api
    .post(`/hospitals/me/requests/${requestId}/respond`, { status, note })
    .then((r) => r.data);
export const listHospitalEmergencies = () =>
  api.get<Emergency[]>("/hospitals/me/emergencies").then((r) => r.data);

const INVENTORY_ACTION_PATH: Record<InventoryActionKind, string> = {
  add: "add", reserve: "reserve", allocate: "allocate", release: "release", expire: "expire",
};
export const inventoryAction = (kind: InventoryActionKind, body: InventoryActionInput) =>
  api
    .post<InventoryItem>(`/hospitals/me/inventory/${INVENTORY_ACTION_PATH[kind]}`, body)
    .then((r) => r.data);
export const getComponentInventory = () =>
  api.get<ComponentInventoryItem[]>("/hospitals/me/inventory/components").then((r) => r.data);
export const reconcileInventoryOne = (body: InventoryReconcileInput) =>
  api.post<InventoryItem>("/hospitals/me/inventory/reconcile", body).then((r) => r.data);
export const getInventoryLog = (limit = 50) =>
  api
    .get<HospitalInventoryLogEntry[]>("/hospitals/me/inventory/log", { params: { limit } })
    .then((r) => r.data);
export const allocateToRequest = (
  requestId: string,
  body: { blood_type: BloodType; units: number; note?: string },
) =>
  api
    .post<HospitalRequestResponseOut>(`/hospitals/me/requests/${requestId}/allocate`, body)
    .then((r) => r.data);

/** Phase E: this hospital's own response row for one request.
 * `units_allocated` is THIS hospital's contribution only; the request-wide
 * total is `BloodRequest.fulfilled_units`. */
export interface HospitalRequestResponseOut {
  id: string;
  request_id: string;
  status: HospitalResponseStatus;
  note: string | null;
  units_allocated: number;
  created_at: string;
  updated_at: string;
}
export const fulfillFromHospital = (requestId: string) =>
  api.post(`/hospitals/me/requests/${requestId}/fulfill`).then((r) => r.data);
export const getHospitalRoutes = () =>
  api.get<HospitalRoute[]>("/hospitals/me/routes").then((r) => r.data);
export const getHospitalActivity = (limit = 25) =>
  api
    .get<HospitalActivityItem[]>("/hospitals/me/activity", { params: { limit } })
    .then((r) => r.data);

// ---- Phase D: Shipment / transport / chain-of-custody lifecycle --------
//
// `GET /shipments/{id}` (and every list/detail response) returns real
// backend truth only -- `allowed_transitions` is the state machine's own
// answer to "what can happen next" (app.core.shipment_lifecycle), the same
// idea as BloodRequest's own field; every timestamp below is server-
// written, never client-manufactured (mirrors backend Section 23).
export type ShipmentStatus =
  | "CREATED" | "DISPATCHED" | "IN_TRANSIT" | "ARRIVED" | "RECEIVED" | "CANCELLED" | "FAILED";

export interface ShipmentItemOut {
  id: string;
  blood_unit_id: string;
  is_active: boolean;
  unit_code: string | null;
  blood_type: BloodType | null;
  component: string | null;
  /** Gap elimination: what happened to this unit at receipt/reconciliation. */
  outcome?: string;
  outcome_reason?: string | null;
}

export interface Shipment {
  id: string;
  shipment_code: string;
  origin_facility_id: string;
  destination_facility_id: string;
  origin_facility_name: string | null;
  destination_facility_name: string | null;
  transport_route_id: string | null;
  status: ShipmentStatus;
  created_by_user_id: string | null;
  dispatched_at: string | null;
  in_transit_at: string | null;
  arrived_at: string | null;
  received_at: string | null;
  cancelled_at: string | null;
  failed_at: string | null;
  estimated_arrival_at: string | null;
  delayed_at: string | null;
  delay_reason: string | null;
  cancel_reason: string | null;
  failure_reason: string | null;
  note: string | null;
  created_at: string;
  updated_at: string;
  allowed_transitions: ShipmentStatus[];
  /** Route condition (OPERATIONAL/DISRUPTED), looked up separately from
   * `status` and never an authority over it -- see TransportRoute. */
  route_status: "OPERATIONAL" | "DISRUPTED" | null;
  route_disrupted_reason: string | null;
  items: ShipmentItemOut[];
  /** Phase E: null unless FAILED. REQUIRED means a human decision about
   * the stranded units is still owed -- nothing happens automatically. */
  reconciliation_status: ShipmentReconciliationStatus | null;
  reconciled_at: string | null;
  reconciled_by_user_id: string | null;
  reconciliation_note: string | null;
  redirected_to_facility_id: string | null;
  /** Gap elimination: cold chain (range from cited storage conditions). */
  temp_min_c?: number | null;
  temp_max_c?: number | null;
  cold_chain_status?: string | null;
}

export type ShipmentReconciliationStatus =
  | "REQUIRED" | "RETURNED_TO_ORIGIN" | "REDIRECTED" | "QUARANTINED";

export interface ShipmentReconcileInput {
  outcome: Exclude<ShipmentReconciliationStatus, "REQUIRED">;
  note: string;
  unit_codes?: string[];
  cold_chain_maintained?: boolean;
  redirected_to_facility_id?: string;
}

export interface ShipmentEventItem {
  id: string;
  event_type: string;
  note: string | null;
  actor_user_id: string | null;
  created_at: string;
}

export interface ShipmentLineInput {
  blood_type: BloodType;
  units: number;
  /** Phase E: previously absent, so every shipment silently defaulted to
   * WHOLE_BLOOD server-side. */
  component: BloodComponent;
}

export interface ShipmentCreateInput {
  origin_facility_id: string;
  destination_facility_id: string;
  items: ShipmentLineInput[];
  transport_route_id?: string;
  note?: string;
  idempotency_key?: string;
}

export interface UnitTraceOut {
  id: string;
  unit_code: string;
  blood_type: BloodType;
  component: string;
  state: string;
  hospital_id: string;
  hospital_name: string | null;
  donation_id: string | null;
  source_shipment_id: string | null;
  source_shipment_code: string | null;
  source_unit_id: string | null;
  carrying_shipment_id: string | null;
  carrying_shipment_code: string | null;
  received_at: string;
  expires_at: string | null;
}

export const listShipments = (opts?: { status?: ShipmentStatus; direction?: "incoming" | "outgoing" }) =>
  api.get<Shipment[]>("/shipments", { params: opts }).then((r) => r.data);
export const getShipment = (id: string) =>
  api.get<Shipment>(`/shipments/${id}`).then((r) => r.data);
export const getShipmentByCode = (code: string) =>
  api.get<Shipment>(`/shipments/by-code/${encodeURIComponent(code)}`).then((r) => r.data);
export const getShipmentTimeline = (id: string) =>
  api.get<ShipmentEventItem[]>(`/shipments/${id}/timeline`).then((r) => r.data);
export const createShipment = (body: ShipmentCreateInput) =>
  api.post<Shipment>("/shipments", body).then((r) => r.data);
export const dispatchShipment = (id: string, reason?: string) =>
  api.post<Shipment>(`/shipments/${id}/dispatch`, { reason }).then((r) => r.data);
export const markShipmentInTransit = (id: string, reason?: string) =>
  api.post<Shipment>(`/shipments/${id}/in-transit`, { reason }).then((r) => r.data);
export const reportShipmentDelay = (id: string, reason: string, newEstimatedArrivalAt?: string) =>
  api
    .post<Shipment>(`/shipments/${id}/delay`, {
      reason, new_estimated_arrival_at: newEstimatedArrivalAt,
    })
    .then((r) => r.data);
export const markShipmentArrived = (id: string, reason?: string) =>
  api.post<Shipment>(`/shipments/${id}/arrive`, { reason }).then((r) => r.data);
export const receiveShipment = (id: string, reason?: string) =>
  api.post<Shipment>(`/shipments/${id}/receive`, { reason }).then((r) => r.data);
export const cancelShipment = (id: string, reason?: string) =>
  api.post<Shipment>(`/shipments/${id}/cancel`, { reason }).then((r) => r.data);
export const failShipment = (id: string, reason?: string) =>
  api.post<Shipment>(`/shipments/${id}/fail`, { reason }).then((r) => r.data);
export const reconcileShipment = (id: string, body: ShipmentReconcileInput) =>
  api.post<Shipment>(`/shipments/${id}/reconcile`, body).then((r) => r.data);
export const traceUnitByCode = (unitCode: string) =>
  api.get<UnitTraceOut>(`/shipments/units/${encodeURIComponent(unitCode)}`).then((r) => r.data);

// ---- Matching + map ------------------------------------------------
export const getMatchCandidates = (requestId: string) =>
  api
    .get<MatchCandidatesResult>(`/blood-requests/${requestId}/match-candidates`)
    .then((r) => r.data);
export const getMapData = (bloodType?: BloodType) =>
  api
    .get<MapData>("/map/data", { params: bloodType ? { blood_type: bloodType } : undefined })
    .then((r) => r.data);

/** Central compatibility engine, over the wire (app.core.blood_compatibility).
 * The frontend must never hardcode this table -- see BloodCompatibilityChart. */
export interface BloodGroupCompatibility {
  blood_type: BloodType;
  ui_label: string;
  can_donate_to: BloodType[];
  can_receive_from: BloodType[];
}
export interface BloodCompatibilityMatrix {
  groups: BloodGroupCompatibility[];
}
export const getBloodCompatibility = () =>
  api.get<BloodCompatibilityMatrix>("/matching/blood-compatibility").then((r) => r.data);

// ---- Matching engine: explainable ranking + research-mode strategies ------
export type MatchStrategy = "NEAREST" | "TIME_AWARE" | "TIME_RESILIENCE" | "EVENT_DRIVEN";

export interface MatchFactors {
  compatible: boolean;
  available: boolean;
  eligible: boolean | null;
  verified: boolean | null;
  distance_km: number | null;
  within_alert_radius: boolean | null;
  has_accepted: boolean;
  days_since_last_donation: number | null;
  stale_availability: boolean | null;
  urgency: Urgency;
}

export interface RankedMatchCandidate {
  donor_ref: string;
  donor_name: string | null;
  blood_type: BloodType;
  rank: number;
  factors: MatchFactors;
  explanation: string;
}

export interface StrategyComparisonResult {
  strategy: MatchStrategy;
  candidates: RankedMatchCandidate[];
}

export interface StrategyComparisonOut {
  recipient_blood_type: BloodType | null;
  strategies: StrategyComparisonResult[];
}

/** Real algorithms behind every strategy -- see app/core/matching_engine.py. */
export const getBloodRequestStrategyComparison = (requestId: string) =>
  api
    .get<StrategyComparisonOut>(`/blood-requests/${requestId}/match-strategies`)
    .then((r) => r.data);
export const getEmergencyStrategyComparison = (emergencyId: string) =>
  api
    .get<StrategyComparisonOut>(`/emergencies/${emergencyId}/match-strategies`)
    .then((r) => r.data);

// ---- Notifications ---------------------------------------------------
export const listNotifications = (unreadOnly = false) =>
  api
    .get<AppNotification[]>("/notifications", {
      params: unreadOnly ? { unread_only: true } : undefined,
    })
    .then((r) => r.data);
export const unreadCount = () =>
  api.get<{ unread: number }>("/notifications/unread-count").then((r) => r.data.unread);
export const markNotificationRead = (id: string) =>
  api.post(`/notifications/${id}/read`).then((r) => r.data);
export const markAllNotificationsRead = () =>
  api.post("/notifications/read-all").then((r) => r.data);

// ---- Notification preferences -----------------------------------------
export type NotificationDigestFrequency = "REALTIME" | "DAILY" | "OFF";

export interface NotificationPreference {
  user_id: string;
  email_enabled: boolean;
  sms_enabled: boolean;
  emergency_alerts_enabled: boolean;
  digest_frequency: NotificationDigestFrequency;
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
}

export const getNotificationPreferences = () =>
  api.get<NotificationPreference>("/notifications/preferences").then((r) => r.data);
export const updateNotificationPreferences = (body: Partial<NotificationPreference>) =>
  api.put<NotificationPreference>("/notifications/preferences", body).then((r) => r.data);

// ---- Auth: change password ---------------------------------------------
export const changePassword = (current_password: string, new_password: string) =>
  api.post("/auth/change-password", { current_password, new_password }).then((r) => r.data);

// ---- Hospital directory (any signed-in role) ---------------------------
export interface HospitalDirectoryItem {
  id: string;
  name: string | null;
  city: string | null;
  verified: boolean;
}

export const getHospitalDirectory = () =>
  api.get<HospitalDirectoryItem[]>("/hospitals/directory").then((r) => r.data);

// ---- Donor appointments (pre-scheduling) --------------------------------
export type AppointmentStatus = "REQUESTED" | "CONFIRMED" | "CANCELLED" | "COMPLETED";

export interface DonorAppointment {
  id: string;
  donor_user_id: string;
  hospital_id: string | null;
  hospital_name: string | null;
  preferred_date: string;
  status: AppointmentStatus;
  note: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateAppointmentBody {
  hospital_id?: string | null;
  hospital_name?: string | null;
  preferred_date: string;
  note?: string | null;
}

export const listMyAppointments = () =>
  api.get<DonorAppointment[]>("/donors/me/appointments").then((r) => r.data);
export const requestAppointment = (body: CreateAppointmentBody) =>
  api.post<DonorAppointment>("/donors/me/appointments", body).then((r) => r.data);
export const cancelAppointment = (id: string) =>
  api.post<DonorAppointment>(`/donors/me/appointments/${id}/cancel`).then((r) => r.data);

// ---- Verification (donor identity / hospital facility) ------------------
export type VerificationKind = "DONOR_IDENTITY" | "HOSPITAL_FACILITY";
export type VerificationStatus = "PENDING" | "APPROVED" | "REJECTED";

export interface VerificationDocument {
  filename: string;
  content_type: string;
  size: number;
  /** Phase E: SHA-256 recorded at submission; null for pre-Phase-E files. */
  sha256: string | null;
  /** Hardening: malware-scan state. Only CLEAN documents can be opened or approved. */
  scan_status: "PENDING_SCAN" | "CLEAN" | "INFECTED" | "SCAN_FAILED";
  /** False when scanning is disabled in this environment (never inspected). */
  malware_scanned: boolean;
}

export interface VerificationRequest {
  id: string;
  user_id: string;
  kind: VerificationKind;
  status: VerificationStatus;
  documents: VerificationDocument[];
  note: string | null;
  reviewer_user_id: string | null;
  review_note: string | null;
  /** Phase E: 1, 2, 3... per (user, kind); each resubmission is a new
   * version that supersedes -- never overwrites -- the previous one. */
  version: number;
  supersedes_request_id: string | null;
  created_at: string;
  updated_at: string;
}

export const submitVerification = (kind: VerificationKind, files: File[], note?: string) => {
  const form = new FormData();
  form.append("kind", kind);
  if (note) form.append("note", note);
  files.forEach((f) => form.append("files", f));
  return api
    .post<VerificationRequest>("/verifications", form, {
      headers: { "Content-Type": "multipart/form-data" },
    })
    .then((r) => r.data);
};
export const listMyVerifications = () =>
  api.get<VerificationRequest[]>("/verifications/me").then((r) => r.data);

// ---- Admin ---------------------------------------------------------------
export interface AdminUser {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  is_email_verified: boolean;
  suspended_reason: string | null;
  created_at: string;
}

export interface AdminUserList {
  items: AdminUser[];
  total: number;
  page: number;
  page_size: number;
}

export interface AdminUserStats {
  total_users: number;
  by_role: Record<string, number>;
  active_count: number;
  suspended_count: number;
  unverified_email_count: number;
}

export interface AdminAuditLogEntry {
  id: string;
  actor_user_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  ip: string | null;
  meta: Record<string, unknown>;
  created_at: string;
  actor_email: string | null;
  actor_role: UserRole | null;
  severity: "INFO" | "WARNING" | "CRITICAL";
  /** Phase E: hash-chain position; null = committed but not yet sealed. */
  chain_seq: number | null;
  entry_hash: string | null;
}

/** Phase E: result of recomputing the tamper-EVIDENT audit chain. `ok`
 * means no evidence of tampering was found among sealed entries -- not
 * that the log is immutable. */
export interface AuditChainVerifyResult {
  ok: boolean;
  checked: number;
  head_seq: number | null;
  head_hash: string | null;
  unsealed: number;
  first_break: { seq: number | null; audit_id: string | null; reason: string } | null;
  anchored: boolean;
  /** Hardening: "full" | "incremental" and the HMAC-signed checkpoints
   * kept outside the database (absent on the head endpoint). */
  mode?: "full" | "incremental";
  checkpoints?: {
    enabled: boolean;
    ok: boolean | null;
    checked: number;
    latest_seq: number | null;
    reason: string | null;
  } | null;
}

export interface AdminAuditLogList {
  items: AdminAuditLogEntry[];
  next_cursor: string | null;
}

export interface AdminHealthComponent {
  ok: boolean;
  detail: string | null;
}

export interface AdminGraphHealth {
  configured: boolean;
  connected: boolean;
  status: string;
  reason?: string | null;
}

export interface AdminSmsHealth {
  configured: boolean;
  status: string;
  provider: string;
  recent_failures: number;
  reason?: string | null;
}

export interface AdminRqHealth {
  queued: number;
  failed: number;
  started: number;
  oldest_queued_job_age_seconds: number | null;
  workers: number;
  status: "healthy" | "warning" | "critical";
  reason?: string | null;
}

export interface AdminDbPool {
  size: number;
  checked_out: number;
  overflow: number;
}

export interface AdminVersion {
  app_version: string;
  git_commit_sha: string;
}

export interface AdminHealth {
  status: string;
  db: AdminHealthComponent;
  redis: AdminHealthComponent;
  storage: AdminHealthComponent;
  graph: AdminGraphHealth;
  sms: AdminSmsHealth;
  rq: AdminRqHealth;
  db_pool: AdminDbPool;
  version: AdminVersion;
}

export const listAdminUsers = (params: {
  query?: string;
  role?: UserRole;
  page?: number;
  page_size?: number;
}) => api.get<AdminUserList>("/admin/users", { params }).then((r) => r.data);
export const getAdminUserStats = () =>
  api.get<AdminUserStats>("/admin/users/stats").then((r) => r.data);
export const changeAdminUserRole = (userId: string, role: UserRole) =>
  api.patch<AdminUser>(`/admin/users/${userId}/role`, { role }).then((r) => r.data);
export const suspendAdminUser = (userId: string, reason: string) =>
  api.post<AdminUser>(`/admin/users/${userId}/suspend`, { reason }).then((r) => r.data);
export const reactivateAdminUser = (userId: string) =>
  api.post<AdminUser>(`/admin/users/${userId}/reactivate`).then((r) => r.data);
export const listAdminBloodRequests = (status?: BloodRequestStatus) =>
  api
    .get<BloodRequest[]>("/admin/blood-requests", { params: status ? { status } : undefined })
    .then((r) => r.data);
export const listAdminEmergencies = (status?: EmergencyStatus) =>
  api
    .get<Emergency[]>("/admin/emergencies", { params: status ? { status } : undefined })
    .then((r) => r.data);
export const listAdminAuditLog = (params?: {
  action?: string;
  actor_user_id?: string;
  actor_role?: UserRole;
  target_type?: string;
  severity?: "INFO" | "WARNING" | "CRITICAL";
  since?: string;
  until?: string;
  limit?: number;
  cursor?: string;
}) => api.get<AdminAuditLogList>("/admin/audit-log", { params }).then((r) => r.data);
export const verifyAuditChain = (anchor?: { seq: number; hash: string }) =>
  api
    .post<AuditChainVerifyResult>("/admin/audit-log/verify", null, {
      params: anchor ? { anchor_seq: anchor.seq, anchor_hash: anchor.hash } : undefined,
    })
    .then((r) => r.data);
export const getAuditChainHead = () =>
  api.get<AuditChainVerifyResult>("/admin/audit-log/head").then((r) => r.data);
export const getAdminSystemHealth = () =>
  api.get<AdminHealth>("/admin/system/health").then((r) => r.data);

export const listAdminVerifications = (params?: { kind?: VerificationKind; status?: VerificationStatus; limit?: number }) =>
  api.get<VerificationRequest[]>("/admin/verifications", { params }).then((r) => r.data);
export const getAdminVerification = (id: string) =>
  api.get<VerificationRequest>(`/admin/verifications/${id}`).then((r) => r.data);
export const adminVerificationDocumentUrl = (id: string, index: number) =>
  `${API_BASE_URL}/admin/verifications/${id}/documents/${index}`;
export const approveAdminVerification = (id: string, note?: string) =>
  api.post<VerificationRequest>(`/admin/verifications/${id}/approve`, { note }).then((r) => r.data);
export const rescanAdminVerificationDocument = (id: string, index: number) =>
  api.post<VerificationRequest>(`/admin/verifications/${id}/documents/${index}/rescan`, {}).then((r) => r.data);
export const rejectAdminVerification = (id: string, reason: string) =>
  api.post<VerificationRequest>(`/admin/verifications/${id}/reject`, { reason }).then((r) => r.data);

// ---- Admin: facilities (hospitals / blood banks) --------------------------
export interface AdminFacility {
  id: string;
  user_id: string;
  name: string | null;
  license_no: string | null;
  phone: string | null;
  address: string | null;
  city: string | null;
  latitude: number | null;
  longitude: number | null;
  verified: boolean;
  facility_type: FacilityType;
  is_active: boolean;
  total_units: number;
  total_reserved_units: number;
  created_at: string;
}

export interface AdminFacilityList {
  items: AdminFacility[];
  total: number;
  page: number;
  page_size: number;
}

export interface AdminFacilityInventoryLine {
  blood_type: BloodType;
  /** Phase 35 backend field the admin UI never showed until Phase G --
   * without it, O- whole blood and O- plasma rendered as two identical
   * "O-" rows. */
  component: BloodComponent;
  units: number;
  reserved_units: number;
  available_units: number;
}

export interface AdminFacilityDetail {
  facility: AdminFacility;
  inventory: AdminFacilityInventoryLine[];
}

export const listAdminFacilities = (params?: {
  query?: string;
  facility_type?: FacilityType;
  verified?: boolean;
  page?: number;
  page_size?: number;
}) => api.get<AdminFacilityList>("/admin/facilities", { params }).then((r) => r.data);
export const getAdminFacility = (id: string) =>
  api.get<AdminFacilityDetail>(`/admin/facilities/${id}`).then((r) => r.data);

// ---- Admin: donor governance -----------------------------------------------
// Deliberately excludes medical_conditions/weight_kg/exact lat-lon -- the
// backend never sends them to this endpoint (see AdminDonorOut).
export interface AdminDonor {
  user_id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  blood_type: BloodType | null;
  city: string | null;
  is_available: boolean;
  availability_state: DonorAvailabilityState;
  identity_verified: boolean;
  last_donation_date: string | null;
  created_at: string;
}

export interface AdminDonorList {
  items: AdminDonor[];
  total: number;
  page: number;
  page_size: number;
}

export const listAdminDonors = (params?: {
  query?: string;
  blood_type?: BloodType;
  identity_verified?: boolean;
  is_available?: boolean;
  page?: number;
  page_size?: number;
}) => api.get<AdminDonorList>("/admin/donors", { params }).then((r) => r.data);

// ---- Admin: certificate registry -------------------------------------------
export interface AdminCertificate {
  id: string;
  serial: string;
  verify_code: string;
  donor_user_id: string;
  donor_name: string | null;
  donor_email: string | null;
  donated_on: string | null;
  units: number | null;
  hospital_name: string | null;
  created_at: string;
  /** Phase 31.1 additions -- see Certificate.revoked_at above. */
  revoked_at: string | null;
  revocation_reason: string | null;
}

export interface AdminCertificateList {
  items: AdminCertificate[];
  total: number;
  page: number;
  page_size: number;
}

export const listAdminCertificates = (params?: {
  query?: string;
  page?: number;
  page_size?: number;
}) => api.get<AdminCertificateList>("/admin/certificates", { params }).then((r) => r.data);

/** Admin-only. Uses the existing POST /admin/certificates/{id}/revoke
 * endpoint -- idempotent server-side, requires a real (min. 3 character)
 * reason. Never deletes the certificate or touches the underlying donation. */
export const revokeAdminCertificate = (id: string, reason: string) =>
  api.post<AdminCertificate>(`/admin/certificates/${id}/revoke`, { reason }).then((r) => r.data);

// ---- Admin: real analytics --------------------------------------------------
export interface AdminAnalyticsOverview {
  request_status_breakdown: Record<string, number>;
  request_blood_type_breakdown: Record<string, number>;
  emergency_status_breakdown: Record<string, number>;
  donor_response_breakdown: Record<string, number>;
  hospital_response_breakdown: Record<string, number>;
  verification_breakdown: Record<string, number>;
  requests_per_day: { day: string; count: number }[];
}

export const getAdminAnalyticsOverview = (days?: number) =>
  api
    .get<AdminAnalyticsOverview>("/admin/analytics/overview", { params: days ? { days } : undefined })
    .then((r) => r.data);

// ---- Graph engine: real resource-pathway discovery -----------------------
// Backed by app/services/graph_service.py. `pathway_source` tells you
// honestly whether a result came from a live Neo4j traversal ("GRAPH") or
// the haversine-distance fallback used whenever Neo4j is unreachable
// ("HAVERSINE_FALLBACK") -- always render it, never hide it.
export type FacilityType = "HOSPITAL" | "BLOOD_BANK";
export type TransportRouteStatus = "OPERATIONAL" | "DISRUPTED";
export type GraphPathwaySource = "GRAPH" | "HAVERSINE_FALLBACK" | "RELATIONAL_ROUTES";

export interface TransportRoute {
  id: string;
  origin_facility_id: string;
  destination_facility_id: string;
  distance_km: number;
  transit_minutes: number;
  status: TransportRouteStatus;
  disrupted_reason: string | null;
  disrupted_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateTransportRouteBody {
  origin_facility_id: string;
  destination_facility_id: string;
  // Both optional -- when omitted the backend computes a real distance from
  // the two facilities' recorded coordinates rather than inventing one.
  distance_km?: number | null;
  transit_minutes?: number | null;
}

export interface DisruptionImpact {
  before_best_distance_km: number | null;
  after_best_distance_km: number | null;
  before_candidate_count: number;
  after_candidate_count: number;
  distance_increase_km: number | null;
  became_unreachable: boolean;
  became_reachable: boolean;
  source: GraphPathwaySource;
}

export interface TransportRouteAction {
  route: TransportRoute;
  // None when the impact could not be honestly measured (no recorded stock
  // to probe with) -- never a fabricated zero-impact result.
  sample_blood_type: BloodType | null;
  impact: DisruptionImpact | null;
  graph_synced: boolean;
}

export interface PathwayCandidate {
  facility_id: string;
  facility_name: string | null;
  city: string | null;
  units: number;
  distance_km: number;
  transit_minutes: number | null;
  hops: number | null;
  /** Gap elimination: how the distance was obtained, and the full route. */
  route_basis?: "LOCAL" | "ROUTED" | "STRAIGHT_LINE_ESTIMATE" | null;
  route_ids?: string[];
  node_ids?: string[];
  eta_minutes?: number | null;
  slack_minutes?: number | null;
  units_by_type?: Record<string, number>;
}

export interface ResourcePathway {
  blood_type: BloodType;
  origin_facility_id: string;
  pathway_source: GraphPathwaySource;
  graph_available: boolean;
  disrupted_routes_excluded: number;
  degraded_reason: string | null;
  candidates: PathwayCandidate[];
}

export interface GraphResyncResult {
  facilities: number;
  cities: number;
  stock_edges: number;
  routes: number;
  donor_aggregate_edges: number;
}

export interface GraphPathwayLogEntry {
  id: string;
  requested_by_user_id: string;
  emergency_id: string | null;
  origin_facility_id: string | null;
  blood_type: BloodType;
  pathway_source: GraphPathwaySource;
  best_distance_km: number | null;
  candidate_count: number;
  disrupted_routes_excluded: number;
  graph_available: boolean;
  created_at: string;
}

export interface GraphPathwayLogList {
  items: GraphPathwayLogEntry[];
  next_cursor: string | null;
}

/** Real-time resource discovery from the calling hospital's own facility. */
export const getHospitalResourcePathways = (
  blood_type: BloodType,
  max_results = 5,
  component = "WHOLE_BLOOD",
  units_needed = 1,
) =>
  api
    .get<ResourcePathway>("/hospitals/me/resource-pathways", {
      params: { blood_type, max_results, component, units_needed },
    })
    .then((r) => r.data);

/** Same engine, scoped to a live emergency's blood type + a chosen origin facility. */
export const getEmergencyResourcePathways = (
  emergencyId: string,
  origin_facility_id: string,
  max_results = 5,
) =>
  api
    .get<ResourcePathway>(`/emergencies/${emergencyId}/resource-pathways`, {
      params: { origin_facility_id, max_results },
    })
    .then((r) => r.data);

// ---- Admin: transport-route + graph administration ------------------------
export const listAdminTransportRoutes = (facility_id?: string) =>
  api
    .get<TransportRoute[]>("/admin/transport-routes", {
      params: facility_id ? { facility_id } : undefined,
    })
    .then((r) => r.data);
export const createAdminTransportRoute = (body: CreateTransportRouteBody) =>
  api.post<TransportRoute>("/admin/transport-routes", body).then((r) => r.data);
export const disruptAdminTransportRoute = (routeId: string, reason: string) =>
  api
    .post<TransportRouteAction>(`/admin/transport-routes/${routeId}/disrupt`, { reason })
    .then((r) => r.data);
export const recoverAdminTransportRoute = (routeId: string) =>
  api
    .post<TransportRouteAction>(`/admin/transport-routes/${routeId}/recover`)
    .then((r) => r.data);
/** Wipes and rebuilds the whole graph from Postgres. Rejects (503) rather
 * than claiming success when Neo4j is unreachable. */
export const resyncGraph = () =>
  api.post<GraphResyncResult>("/admin/graph/resync").then((r) => r.data);
export const listGraphPathwayLog = (params?: {
  blood_type?: BloodType;
  limit?: number;
  cursor?: string;
}) => api.get<GraphPathwayLogList>("/admin/graph/pathway-log", { params }).then((r) => r.data);
