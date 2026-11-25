export type Role = "PATIENT" | "FAMILY" | "DONOR" | "HOSPITAL" | "ADMIN";

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_email_verified: boolean;
  is_active: boolean;
}

export interface MessageResponse {
  message: string;
}
