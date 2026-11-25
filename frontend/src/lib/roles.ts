import type { Role } from "./types";

/** Where each role lands after signing in. */
export const ROLE_HOME: Record<Role, string> = {
  PATIENT: "/patient",
  FAMILY: "/family",
  DONOR: "/donor",
  HOSPITAL: "/hospital",
  ADMIN: "/admin",
};

export function homeForRole(role: Role): string {
  return ROLE_HOME[role] ?? "/";
}

export const ROLE_LABEL: Record<Role, string> = {
  PATIENT: "Patient",
  FAMILY: "Family",
  DONOR: "Donor",
  HOSPITAL: "Hospital",
  ADMIN: "Admin",
};
