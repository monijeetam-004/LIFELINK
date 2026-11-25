/**
 * Phase E -- a donation's verification state, exactly as the backend
 * reports it. DONOR_REPORTED is the donor's own statement and is labelled
 * as such -- never "verified", never a generic "logged" that reads as
 * confirmed.
 */
import type { Donation, DonationVerificationStatus } from "../lib/domain";

const LABEL: Record<DonationVerificationStatus, string> = {
  DONOR_REPORTED: "Self-reported · not yet verified",
  VERIFIED: "Verified by facility",
  REJECTED: "Disputed by facility",
};

const STYLE: Record<DonationVerificationStatus, string> = {
  DONOR_REPORTED: "bg-surface-container-high text-on-surface-variant",
  VERIFIED: "bg-success/15 text-success",
  REJECTED: "bg-error/10 text-error",
};

export function donationVerificationLabel(s: DonationVerificationStatus): string {
  return LABEL[s];
}

/** Certificates are only issued for VERIFIED donations (backend-enforced,
 * app/api/routes/certificates.py) -- the UI mirrors that instead of
 * offering a button that will always 409. */
export function canRequestCertificate(d: Pick<Donation, "verification_status">): boolean {
  return d.verification_status === "VERIFIED";
}

export default function DonationVerificationBadge({ status }: { status: DonationVerificationStatus }) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold whitespace-nowrap ${STYLE[status]}`}
      data-testid="donation-verification-badge"
    >
      {LABEL[status]}
    </span>
  );
}
