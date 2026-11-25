/**
 * Family — Manage Linked Patients + Link Request flow
 * Adapted from: lifelink_1b.2_1b.3_manage_linked_patients_link_request_flow/code.html
 *
 * Real data only: GET/POST /family/links. A family member can request a link (pending
 * until the patient accepts or rejects it from their own account) and revoke a request
 * they sent or an active link at any time — "Revoke" here is the family member's own
 * cancel-or-end action; the patient's distinct "decline a pending request" action lives
 * on their dashboard as reject, which produces a separate REJECTED status. An un-actioned
 * request the patient never responds to is lazily marked EXPIRED by the backend after 30
 * days, and can be re-sent from the form above.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, ArrowLeft } from "lucide-react";

import { fieldClass, labelClass, primaryButtonClass, ghostButtonClass } from "../../components/Modal";
import NotificationsBell from "../../components/NotificationsBell";
import { toApiError } from "../../lib/api";
import { listFamilyLinks, requestFamilyLink, revokeFamilyLink, type FamilyLink } from "../../lib/domain";

const STATUS_STYLE: Record<FamilyLink["status"], string> = {
  PENDING: "bg-warning/15 text-warning",
  ACCEPTED: "bg-success/15 text-success",
  REJECTED: "bg-error/10 text-error",
  REVOKED: "bg-error/10 text-error",
  EXPIRED: "bg-surface-container-high text-on-surface-variant",
};

const ENDED_STATUSES: FamilyLink["status"][] = ["REJECTED", "REVOKED", "EXPIRED"];

export default function FamilyLinkedPatients() {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [relationship, setRelationship] = useState("");
  const [error, setError] = useState("");
  const [revokingId, setRevokingId] = useState<string | null>(null);

  const links = useQuery({ queryKey: ["family", "links"], queryFn: listFamilyLinks });

  const create = useMutation({
    mutationFn: () => requestFamilyLink(email, relationship),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["family", "links"] });
      setEmail("");
      setRelationship("");
      setError("");
    },
    onError: (e) => setError(toApiError(e).detail),
  });

  const revoke = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) => revokeFamilyLink(id, reason),
    onMutate: ({ id }) => setRevokingId(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["family", "links"] }),
    onSettled: () => setRevokingId(null),
  });

  const pending = (links.data ?? []).filter((l) => l.status === "PENDING");
  const accepted = (links.data ?? []).filter((l) => l.status === "ACCEPTED");
  const ended = (links.data ?? []).filter((l) => ENDED_STATUSES.includes(l.status));

  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-30 h-16 bg-surface/90 backdrop-blur-md border-b border-border-subtle flex items-center justify-between px-margin-mobile md:px-margin-desktop">
        <div className="flex items-center gap-4">
          <Link
            to="/family"
            aria-label="Back to dashboard"
            className="p-2 rounded-full hover:bg-surface-container-low text-on-surface-variant"
          >
            <ArrowLeft className="h-5 w-5" />
          </Link>
          <div>
            <h1 className="font-headline-md text-headline-md text-on-surface">Linked Patients</h1>
            <p className="text-xs text-on-surface-variant">Manage who you can act on behalf of</p>
          </div>
        </div>
        <NotificationsBell buttonClassName="relative p-2 text-on-surface-variant hover:bg-surface-container rounded-full transition-all" />
      </header>

      <main className="max-w-3xl mx-auto p-margin-mobile md:p-margin-desktop space-y-8">
        <section className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6">
          <h2 className="font-headline-sm text-headline-sm text-on-surface mb-1">Link a new patient</h2>
          <p className="text-sm text-on-surface-variant mb-4">
            They'll need to accept the request from their own LifeLink account before you can file or track
            requests for them.
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setError("");
              if (!email || !relationship) return;
              create.mutate();
            }}
            className="flex flex-col sm:flex-row gap-3"
          >
            <div className="flex-1">
              <label className={labelClass} htmlFor="fl-patient-email">
                Patient email
              </label>
              <input
                id="fl-patient-email"
                type="email"
                required
                className={fieldClass}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="patient@example.com"
              />
            </div>
            <div className="flex-1">
              <label className={labelClass} htmlFor="fl-relationship">
                Relationship
              </label>
              <input
                id="fl-relationship"
                required
                className={fieldClass}
                value={relationship}
                onChange={(e) => setRelationship(e.target.value)}
                placeholder="Daughter, spouse, caregiver…"
              />
            </div>
            <div className="flex items-end">
              <button type="submit" className={primaryButtonClass} disabled={create.isPending}>
                {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                Send request
              </button>
            </div>
          </form>
          {error && <p className="text-error text-sm mt-2">{error}</p>}
        </section>

        {links.isLoading && <p className="text-center text-on-surface-variant">Loading…</p>}
        {links.isError && (
          <div role="alert" className="text-center text-error flex items-center justify-center gap-3">
            <span>Couldn't load your links.</span>
            <button type="button" className="text-sm underline" onClick={() => links.refetch()}>
              Retry
            </button>
          </div>
        )}
        {revoke.isError && (
          <p role="alert" className="text-center text-error text-sm">
            {toApiError(revoke.error).detail}
          </p>
        )}

        {pending.length > 0 && (
          <section>
            <h2 className="font-label-md text-on-surface-variant uppercase tracking-widest text-xs mb-3">
              Pending acceptance ({pending.length})
            </h2>
            <LinkList
              links={pending}
              onRevoke={(id, reason) => revoke.mutate({ id, reason })}
              busyId={revokingId}
            />
          </section>
        )}

        <section>
          <h2 className="font-label-md text-on-surface-variant uppercase tracking-widest text-xs mb-3">
            Active links ({accepted.length})
          </h2>
          {accepted.length === 0 ? (
            <p className="text-sm text-on-surface-variant">No accepted links yet.</p>
          ) : (
            <LinkList
              links={accepted}
              onRevoke={(id, reason) => revoke.mutate({ id, reason })}
              busyId={revokingId}
              linkToDetail
            />
          )}
        </section>

        {ended.length > 0 && (
          <section>
            <h2 className="font-label-md text-on-surface-variant uppercase tracking-widest text-xs mb-3">
              Past links ({ended.length})
            </h2>
            <LinkList links={ended} onRevoke={undefined} busyId={revokingId} />
          </section>
        )}
      </main>
    </div>
  );
}

function LinkList({
  links,
  onRevoke,
  busyId,
  linkToDetail,
}: {
  links: FamilyLink[];
  onRevoke?: (id: string, reason?: string) => void;
  busyId: string | null;
  linkToDetail?: boolean;
}) {
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  return (
    <div className="bg-surface rounded-xl shadow-soft border border-border-subtle divide-y divide-border-subtle overflow-hidden">
      {links.map((l) => {
        // Server-side truth on what's actually still possible for this link
        // right now -- never a client-guessed status transition.
        const canRevoke = onRevoke && l.allowed_transitions.includes("REVOKED");
        return (
          <div key={l.id} className="px-5 py-4">
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                {linkToDetail ? (
                  <Link
                    to={`/family/patients/${l.patient_user_id}`}
                    className="font-label-md text-label-md text-on-surface hover:underline truncate block"
                  >
                    {l.patient?.full_name ?? "Patient"}
                  </Link>
                ) : (
                  <p className="font-label-md text-label-md text-on-surface truncate">{l.patient?.full_name}</p>
                )}
                <p className="text-xs text-on-surface-variant truncate">
                  {l.patient?.email} · {l.relationship}
                </p>
                {l.reason && (
                  <p className="text-xs text-on-surface-variant truncate mt-0.5">Reason: {l.reason}</p>
                )}
              </div>
              <div className="flex items-center gap-3 flex-shrink-0">
                <span className={`px-2.5 py-1 rounded-full text-xs font-bold ${STATUS_STYLE[l.status]}`}>
                  {l.status}
                </span>
                {canRevoke && confirmingId !== l.id && (
                  <button
                    onClick={() => {
                      setConfirmingId(l.id);
                      setReason("");
                    }}
                    disabled={busyId === l.id}
                    className={`${ghostButtonClass} !py-1.5 !px-3 text-xs`}
                  >
                    {busyId === l.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Revoke"}
                  </button>
                )}
              </div>
            </div>
            {canRevoke && confirmingId === l.id && (
              <div className="mt-3 flex flex-col sm:flex-row gap-2 items-stretch sm:items-center">
                <input
                  className={`${fieldClass} flex-1 text-sm`}
                  placeholder="Optional reason (visible to the other party)"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  maxLength={500}
                />
                <div className="flex gap-2">
                  <button
                    onClick={() => {
                      onRevoke?.(l.id, reason.trim() || undefined);
                      setConfirmingId(null);
                    }}
                    disabled={busyId === l.id}
                    className={`${primaryButtonClass} !py-1.5 !px-3 text-xs !bg-error`}
                  >
                    Confirm revoke
                  </button>
                  <button
                    onClick={() => setConfirmingId(null)}
                    className={`${ghostButtonClass} !py-1.5 !px-3 text-xs`}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
