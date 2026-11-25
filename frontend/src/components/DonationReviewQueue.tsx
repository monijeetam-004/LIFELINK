/**
 * Phase E -- the donation review queue (GET /donations + verify/reject).
 *
 * A HOSPITAL sees only donations a donor linked to ITS facility; an ADMIN
 * sees all, and can narrow to unlinked donations (free-text hospital only),
 * which nobody but an admin can review. The backend enforces all of that
 * (app/api/routes/donation_review.py); this list never decides authority.
 * Verifying confirms the donation happened -- it is not a clinical test
 * result, and the copy says so.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { toApiError } from "../lib/api";
import {
  listDonationsForReview,
  rejectDonation,
  verifyDonation,
  type Donation,
  type DonationVerificationStatus,
} from "../lib/domain";
import DonationVerificationBadge from "./DonationVerificationBadge";

const FILTERS: { label: string; value: DonationVerificationStatus }[] = [
  { label: "Awaiting review", value: "DONOR_REPORTED" },
  { label: "Verified", value: "VERIFIED" },
  { label: "Disputed", value: "REJECTED" },
];

export default function DonationReviewQueue({ isAdmin = false }: { isAdmin?: boolean }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<DonationVerificationStatus>("DONOR_REPORTED");
  const [unlinkedOnly, setUnlinkedOnly] = useState(false);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [error, setError] = useState("");

  const queue = useQuery({
    queryKey: ["donation-reviews", status, unlinkedOnly],
    queryFn: () =>
      listDonationsForReview({
        verification_status: status,
        ...(isAdmin && unlinkedOnly ? { unlinked_only: true } : {}),
      }),
  });

  const decide = useMutation({
    mutationFn: ({ d, action }: { d: Donation; action: "verify" | "reject" }) =>
      action === "verify" ? verifyDonation(d.id, notes[d.id]) : rejectDonation(d.id, notes[d.id]),
    onSuccess: () => {
      setError("");
      qc.invalidateQueries({ queryKey: ["donation-reviews"] });
    },
    onError: (e) => setError(toApiError(e).detail),
  });

  return (
    <section className="bg-surface rounded-xl border border-border-subtle shadow-sm p-5" aria-labelledby="donation-review-heading">
      <h2 id="donation-review-heading" className="font-title-lg text-title-lg text-on-surface">
        Donation verification
      </h2>
      <p className="text-body-sm text-on-surface-variant mt-1">
        {isAdmin
          ? "All self-reported donations. Donations not linked to a facility can only be reviewed by an admin."
          : "Donations donors linked to your facility. Verify only what your own records confirm — this is a record check, not a clinical result."}
      </p>

      <div className="flex flex-wrap items-center gap-2 mt-3">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => setStatus(f.value)}
            aria-pressed={status === f.value}
            className={`px-3 py-1.5 rounded-full text-label-sm font-label-sm ${
              status === f.value ? "bg-primary text-on-primary" : "bg-surface-container text-on-surface-variant"
            }`}
          >
            {f.label}
          </button>
        ))}
        {isAdmin && (
          <label className="flex items-center gap-2 text-body-sm ml-2">
            <input type="checkbox" checked={unlinkedOnly} onChange={(e) => setUnlinkedOnly(e.target.checked)} />
            Unlinked only
          </label>
        )}
      </div>

      {queue.isLoading && (
        <div className="flex items-center gap-2 text-on-surface-variant py-6 justify-center">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading…
        </div>
      )}
      {queue.isError && (
        <p role="alert" className="text-error text-body-sm mt-3">{toApiError(queue.error).detail}</p>
      )}
      {queue.data && queue.data.length === 0 && (
        <p className="text-body-sm text-on-surface-variant mt-4">Nothing here.</p>
      )}
      {queue.data && queue.data.length > 0 && (
        <ul className="divide-y divide-border-subtle mt-3">
          {queue.data.map((d) => (
            <li key={d.id} className="py-3 flex flex-col gap-2" data-testid="donation-review-row">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="font-label-md text-label-md text-on-surface">
                    {d.donated_on} · {d.units} unit(s) · {d.hospital_name || "Hospital not specified"}
                  </p>
                  {d.note && <p className="text-body-sm text-on-surface-variant">Donor note: {d.note}</p>}
                  {d.verification_note && (
                    <p className="text-body-sm text-on-surface-variant">Review note: {d.verification_note}</p>
                  )}
                </div>
                <DonationVerificationBadge status={d.verification_status} />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="text"
                  aria-label="Review note"
                  placeholder="Note (e.g. matched register entry #)"
                  className="flex-1 min-w-[12rem] px-3 py-2 rounded-lg border border-outline-variant bg-surface text-body-sm"
                  value={notes[d.id] ?? ""}
                  onChange={(e) => setNotes({ ...notes, [d.id]: e.target.value })}
                />
                {d.verification_status !== "VERIFIED" && (
                  <button
                    type="button"
                    disabled={decide.isPending}
                    onClick={() => decide.mutate({ d, action: "verify" })}
                    className="h-9 px-3 rounded-lg bg-success text-on-primary text-label-sm font-label-md disabled:opacity-50"
                  >
                    Verify
                  </button>
                )}
                {d.verification_status !== "REJECTED" && (
                  <button
                    type="button"
                    disabled={decide.isPending}
                    onClick={() => decide.mutate({ d, action: "reject" })}
                    className="h-9 px-3 rounded-lg bg-error/10 text-error text-label-sm font-label-md disabled:opacity-50"
                  >
                    Dispute
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {error && <p role="alert" className="text-error text-body-sm mt-2">{error}</p>}
    </section>
  );
}
