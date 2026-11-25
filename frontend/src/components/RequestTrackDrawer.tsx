import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import Modal, { ghostButtonClass, primaryButtonClass } from "./Modal";
import { toApiError } from "../lib/api";
import {
  BLOOD_LABEL,
  cancelBloodRequest,
  fulfillBloodRequest,
  getBloodRequest,
  getMatchCandidates,
  hospitalRespond,
  type BloodRequestStatus,
  type HospitalResponseStatus,
  isOpenRequestStatus,
} from "../lib/domain";
import FulfillmentProgress from "./FulfillmentProgress";
import DonorShortlistPanel from "./DonorShortlistPanel";
import { useAuth } from "../lib/auth";

const STATUS_STYLE: Record<BloodRequestStatus, string> = {
  PENDING: "bg-surface-container-high text-on-surface",
  MATCHING: "bg-warning/15 text-warning",
  PARTIALLY_FULFILLED: "bg-secondary/15 text-secondary",
  FULFILLED: "bg-success/15 text-success",
  CANCELLED: "bg-error/10 text-error",
  EXPIRED: "bg-error/10 text-error",
};

const EVENT_LABEL: Record<string, string> = {
  created: "Request created",
  donor_accepted: "A donor accepted",
  donor_declined: "A donor declined",
  donor_not_now: "A donor said not now",
  donor_withdrew: "A donor withdrew",
  reopened: "Reopened for matching",
  cancelled: "Request cancelled",
  fulfilled: "Request fulfilled",
  hospital_acknowledged: "Hospital acknowledged",
  hospital_preparing: "Hospital preparing units",
  hospital_dispatched: "Hospital dispatched units",
  hospital_declined: "Hospital declined",
  shortlist_contacted: "Ranked donors contacted",
  shortlist_timed_out: "Contacted donors did not answer in time",
  shortlist_confirmation: "Donor confirmation re-check",
  donor_alerts_withheld: "Donor alerts withheld (no location)",
};

const HOSPITAL_ACTIONS: HospitalResponseStatus[] = [
  "ACKNOWLEDGED", "PREPARING", "DISPATCHED", "DECLINED",
];

export default function RequestTrackDrawer({
  requestId,
  onClose,
  mode = "manager",
}: {
  requestId: string | null;
  onClose: () => void;
  mode?: "manager" | "hospital";
}) {
  const qc = useQueryClient();
  const { user } = useAuth();
  const q = useQuery({
    queryKey: ["blood-requests", requestId],
    queryFn: () => getBloodRequest(requestId!),
    enabled: !!requestId,
  });
  const candidates = useQuery({
    queryKey: ["blood-requests", requestId, "match"],
    queryFn: () => getMatchCandidates(requestId!),
    enabled: !!requestId,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["blood-requests"] });
  const cancel = useMutation({ mutationFn: () => cancelBloodRequest(requestId!), onSuccess: invalidate });
  const fulfill = useMutation({ mutationFn: () => fulfillBloodRequest(requestId!), onSuccess: invalidate });
  const respond = useMutation({
    mutationFn: (s: HospitalResponseStatus) => hospitalRespond(requestId!, s),
    onSuccess: () => {
      invalidate();
      qc.invalidateQueries({ queryKey: ["hospital"] });
    },
  });

  const req = q.data;
  const isOpen = req ? isOpenRequestStatus(req.status) : false;

  return (
    <Modal
      open={!!requestId}
      onClose={onClose}
      title={mode === "hospital" ? "Incoming request" : "Request tracking"}
      width="max-w-xl"
    >
      {q.isLoading && (
        <div className="flex items-center gap-2 text-on-surface-variant">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading…
        </div>
      )}
      {q.isError && <p className="text-error text-body-sm">{toApiError(q.error).detail}</p>}
      {req && (
        <div className="flex flex-col gap-5">
          <div className="flex items-center justify-between">
            <div>
              <span className="bg-primary/10 text-primary px-3 py-1 rounded font-bold text-sm">
                {BLOOD_LABEL[req.blood_type]}
              </span>
              <span className="ml-2 text-on-surface-variant text-sm">
                {req.units_needed} unit(s) · {req.urgency.toLowerCase()}
              </span>
            </div>
            <span className={`px-3 py-1 rounded-full text-xs font-bold ${STATUS_STYLE[req.status]}`}>
              {req.status}
            </span>
          </div>

          <FulfillmentProgress fulfilled={req.fulfilled_units} needed={req.units_needed} />

          {(req.hospital_name || req.city) && (
            <p className="text-sm text-on-surface-variant">
              {[req.hospital_name, req.city].filter(Boolean).join(" · ")}
            </p>
          )}

          {req.filed_by_family && (
            <p className="flex items-center gap-1.5 text-xs font-semibold text-secondary bg-secondary/10 rounded-full px-3 py-1 w-fit">
              <span aria-hidden="true" className="material-symbols-outlined text-[14px]">group</span>
              Requested by family member{req.requester_name ? ` · ${req.requester_name}` : ""}
            </p>
          )}

          {mode === "manager" && (
            <div>
              <h4 className="font-label-md text-label-md mb-2">
                Donor responses ({req.accepted_count} accepted)
              </h4>
              {req.responses.length === 0 ? (
                <p className="text-sm text-on-surface-variant">No responses yet.</p>
              ) : (
                <ul className="space-y-1.5">
                  {req.responses.map((r) => (
                    <li key={r.id} className="flex items-center justify-between text-sm">
                      <span>
                        {r.donor_name ?? "Donor"}
                        {r.donor_blood_type && (
                          <span className="text-on-surface-variant"> ({BLOOD_LABEL[r.donor_blood_type]})</span>
                        )}
                      </span>
                      <span
                        className={
                          r.status === "ACCEPTED"
                            ? "text-success font-semibold"
                            : r.status === "NOT_NOW"
                              ? "text-on-surface-variant"
                              : "text-error"
                        }
                      >
                        {r.status === "NOT_NOW" ? "Not now" : r.status === "ACCEPTED" ? "Accepted" : "Declined"}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* Confidence shortlist: eligible -> contacted -> answered -> confirmed. */}
          <DonorShortlistPanel
            kind="BLOOD_REQUEST"
            subjectId={req.id}
            canManage={mode === "manager"}
            isAdmin={user?.role === "ADMIN"}
          />

          {/* Matching foundation — factual, no score. Kept as a collapsed
              ranking view: these donors were NOT contacted or confirmed. */}
          <details>
            <summary className="cursor-pointer text-xs text-on-surface-variant">
              Show the full ranking view (not contacted, not confirmed)
            </summary>
          <div>
            <h4 className="font-label-md text-label-md mb-2">
              Compatible donors{" "}
              {candidates.data && (
                <span className="text-on-surface-variant font-normal">
                  ({candidates.data.candidates.length} · types{" "}
                  {candidates.data.compatible_donor_types.map((b) => BLOOD_LABEL[b]).join(", ")})
                </span>
              )}
            </h4>
            {candidates.isLoading && <p className="text-sm text-on-surface-variant">Scanning…</p>}
            {candidates.data && candidates.data.candidates.length === 0 && (
              <p className="text-sm text-on-surface-variant">No available compatible donors right now.</p>
            )}
            <ul className="space-y-1.5">
              {(candidates.data?.candidates ?? []).slice(0, 8).map((c) => (
                <li key={c.donor_ref} className="flex items-center justify-between text-sm">
                  <span>
                    {c.donor_name ?? `Donor ${c.donor_ref}`}
                    <span className="text-on-surface-variant"> · {BLOOD_LABEL[c.blood_type]}</span>
                    {c.city && <span className="text-on-surface-variant"> · {c.city}</span>}
                  </span>
                  <span className="text-on-surface-variant text-xs flex items-center gap-2">
                    {c.has_accepted && <span className="text-success font-semibold">accepted</span>}
                    {c.distance_km != null && <span>{c.distance_km} km</span>}
                  </span>
                </li>
              ))}
            </ul>
          </div>
          </details>

          <div>
            <h4 className="font-label-md text-label-md mb-2">Timeline</h4>
            <ol className="relative border-l border-border-subtle ml-1 space-y-3">
              {req.events.map((e) => (
                <li key={e.id} className="ml-4">
                  <span className="absolute -left-1.5 h-3 w-3 rounded-full bg-primary" />
                  <p className="text-sm text-on-surface">{EVENT_LABEL[e.event_type] ?? e.event_type}</p>
                  {e.note && <p className="text-xs text-on-surface-variant">{e.note}</p>}
                  <p className="text-[11px] text-on-surface-variant">
                    {new Date(e.created_at).toLocaleString()}
                  </p>
                </li>
              ))}
            </ol>
          </div>

          {mode === "manager" && isOpen && (
            <div className="flex justify-end gap-3 pt-1">
              <button className={ghostButtonClass} onClick={() => cancel.mutate()} disabled={cancel.isPending}>
                Cancel request
              </button>
              <button className={primaryButtonClass} onClick={() => fulfill.mutate()} disabled={fulfill.isPending}>
                {fulfill.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                Mark fulfilled
              </button>
            </div>
          )}

          {mode === "hospital" && (
            <div className="pt-1">
              <h4 className="font-label-md text-label-md mb-2">Update handling status</h4>
              <div className="grid grid-cols-2 gap-2">
                {HOSPITAL_ACTIONS.map((s) => (
                  <button
                    key={s}
                    disabled={respond.isPending}
                    onClick={() => respond.mutate(s)}
                    className="py-2 rounded-lg text-label-sm font-label-md border border-border-subtle text-on-surface-variant hover:bg-surface-container-low disabled:opacity-50"
                  >
                    {s[0] + s.slice(1).toLowerCase()}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
