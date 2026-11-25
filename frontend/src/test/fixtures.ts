/**
 * Phase 32 -- fixture factories for the domain shapes the mandatory
 * component tests need. Every field the real backend can return is present
 * (not a partial `Partial<T>` stub) so a test can never accidentally pass
 * TypeScript by omitting a field the component actually reads -- the same
 * discipline `toApiError`'s callers get from the real `Emergency`/
 * `Certificate` types in lib/domain.ts. Overrides are shallow-merged.
 */
import type {
  AdminCertificate,
  AdminUser,
  AppNotification,
  Certificate,
  CertificateVerification,
  DonorMatch,
  EmergencyDetail,
  HospitalDirectoryItem,
  InventoryItem,
  PatientProfile,
} from "../lib/domain";

export function makeEmergencyDetail(overrides: Partial<EmergencyDetail> = {}): EmergencyDetail {
  return {
    id: "em-1",
    created_by_user_id: "user-1",
    patient_user_id: "user-1",
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
    units_needed: 2,
    deadline: null,
    target_facility_id: null,
    slack_seconds: null,
    deadline_exceeded_at: null,
    allowed_transitions: ["ACKNOWLEDGED", "CANCELLED", "RESOLVED"],
    possible_duplicate_of: null,
    events: [],
    responders: [],
    accepted_count: 0,
    my_response: null,
    current_pathway: null,
    pathway_history: [],
    ...overrides,
  };
}

export function makeCertificate(overrides: Partial<Certificate> = {}): Certificate {
  return {
    id: "cert-1",
    donation_id: "donation-1",
    donor_user_id: "donor-1",
    serial: "LL-2026-0001",
    verify_code: "abc123",
    created_at: "2026-01-01T00:00:00Z",
    donor_name: "Dana Donor",
    donated_on: "2026-01-01",
    units: 1,
    hospital_name: "Test General Hospital",
    verify_path: "/verify/abc123",
    download_token: "tok-1",
    revoked_at: null,
    revocation_reason: null,
    ...overrides,
  };
}

export function makeCertificateVerification(
  overrides: Partial<CertificateVerification> = {},
): CertificateVerification {
  return {
    valid: true,
    serial: "LL-2026-0001",
    donor_name: "Dana Donor",
    donated_on: "2026-01-01",
    units: 1,
    hospital_name: "Test General Hospital",
    issued_at: "2026-01-01T00:00:00Z",
    revoked_at: null,
    revocation_reason: null,
    ...overrides,
  };
}

export function makeAdminCertificate(overrides: Partial<AdminCertificate> = {}): AdminCertificate {
  return {
    id: "cert-1",
    serial: "LL-2026-0001",
    verify_code: "abc123",
    donor_user_id: "donor-1",
    donor_name: "Dana Donor",
    donor_email: "dana@example.com",
    donated_on: "2026-01-01",
    units: 1,
    hospital_name: "Test General Hospital",
    created_at: "2026-01-01T00:00:00Z",
    revoked_at: null,
    revocation_reason: null,
    ...overrides,
  };
}

/** Phase 33 -- fixture factories for the high-risk mutation surfaces. */

export function makeDonorMatch(overrides: Partial<DonorMatch> = {}): DonorMatch {
  return {
    id: "req-1",
    requester_user_id: "patient-1",
    patient_user_id: "patient-1",
    patient_name: "Pat Patient",
    requester_name: "Pat Patient",
    filed_by_family: false,
    blood_type: "O_NEG",
    units_needed: 2,
    fulfilled_units: 0,
    urgency: "URGENT",
    status: "MATCHING",
    hospital_name: "Test General Hospital",
    city: "Testville",
    note: null,
    needed_by: null,
    fulfilled_at: null,
    cancelled_at: null,
    expired_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    accepted_count: 1,
    allowed_transitions: [],
    my_response: {
      id: "resp-1",
      donor_user_id: "donor-1",
      status: "ACCEPTED",
      note: null,
      created_at: "2026-01-01T00:00:00Z",
    },
    distance_km: null,
    ...overrides,
  };
}

export function makePatientProfile(overrides: Partial<PatientProfile> = {}): PatientProfile {
  return {
    user_id: "patient-1",
    blood_type: "O_NEG",
    date_of_birth: null,
    phone: null,
    address: null,
    city: null,
    medical_notes: null,
    emergency_contact_name: null,
    emergency_contact_phone: null,
    ...overrides,
  };
}

export function makeHospitalDirectoryItem(
  overrides: Partial<HospitalDirectoryItem> = {},
): HospitalDirectoryItem {
  return {
    id: "facility-1",
    name: "Test General Hospital",
    city: "Testville",
    verified: true,
    ...overrides,
  };
}

export function makeAppNotification(overrides: Partial<AppNotification> = {}): AppNotification {
  return {
    id: "notif-1",
    type: "verification.submitted",
    title: "New verification submitted",
    body: null,
    data: {},
    read_at: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export function makeAdminUser(overrides: Partial<AdminUser> = {}): AdminUser {
  return {
    id: "user-1",
    email: "user1@example.com",
    full_name: "Uma User",
    role: "DONOR",
    is_active: true,
    is_email_verified: true,
    suspended_reason: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export function makeInventoryItem(overrides: Partial<InventoryItem> = {}): InventoryItem {
  return {
    blood_type: "O_NEG",
    units: 5,
    reserved_units: 1,
    available_units: 4,
    ...overrides,
  };
}
