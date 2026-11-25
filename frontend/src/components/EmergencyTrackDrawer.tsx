import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import Modal, { ghostButtonClass, primaryButtonClass } from "./Modal";
import DonorShortlistPanel from "./DonorShortlistPanel";
import { toApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  BLOOD_LABEL,
  acknowledgeEmergency,
  cancelEmergency,
  emergencyAssistanceFound,
  getEmergency,
  getEmergencyCandidates,
  getHospitalProfile,
  resolveEmergency,
  respondToEmergency,
  selectEmergencyPathway,
  type EmergencyPathway,
  type EmergencyStatus,
} from "../lib/domain";

const PATHWAY_SOURCE_LABEL: Record<string, string> = {
  GRAPH: "graph engine",
  HAVERSINE_FALLBACK: "straight-line fallback (graph unavailable)",
};

function formatSlack(seconds: number): string {
  const overdue = seconds < 0;
  const abs = Math.abs(seconds);
  const mins = Math.round(abs / 60);
  const text = mins < 60 ? `${mins}m` : `${Math.floor(mins / 60)}h ${mins % 60}m`;
  return overdue ? `${text} overdue` : `${text} remaining`;
}

function PathwayRow({ p, label }: { p: EmergencyPathway; label: string }) {
  return (
    <li className="text-sm flex flex-col gap-0.5 border-l-2 border-border-subtle pl-3">
      <span className="font-semibold">
        {label} (v{p.version}) · {p.status === "ACTIVE" ? "current" : "superseded"}
      </span>
      <span className="text-on-surface-variant text-xs">
        {p.selected_facility_id
          ? `Routed via ${PATHWAY_SOURCE_LABEL[p.pathway_source] ?? p.pathway_source}` +
            (p.distance_km != null ? ` · ${p.distance_km.toFixed(1)} km` : "")
          : "No reachable facility was found at this point"}
        {" · "}
        {p.reason.replace(/_/g, " ")}
      </span>
    </li>
  );
}

const STATUS_STYLE: Record<EmergencyStatus, string> = {
  ACTIVE: "bg-error/10 text-error",
  ACKNOWLEDGED: "bg-warning/15 text-warning",
  MATCHING: "bg-warning/15 text-warning",
  ASSISTANCE_FOUND: "bg-secondary/15 text-secondary",
  RESOLVED: "bg-success/15 text-success",
  CANCELLED: "bg-error/10 text-error",
};

const EVENT_LABEL: Record<string, string> = {
  created: "Emergency broadcast",
  acknowledged: "A responder acknowledged",
  responder_accepted: "A responder is on the way",
  responder_declined: "A responder declined",
  responder_not_now: "A responder said not now",
  responder_withdrew: "A responder withdrew",
  reopened: "Reopened for matching",
  assistance_found: "Assistance secured",
  resolved: "Emergency resolved",
  cancelled: "Emergency cancelled",
  shortlist_contacted: "Ranked donors contacted",
  shortlist_timed_out: "Contacted donors did not answer in time",
  shortlist_confirmation: "Donor confirmation re-check",
  donor_alerts_withheld: "Donor alerts withheld (no location)",
  pathway_handed_off: "Delivery plan handed to shipment",
  pathway_recovery_required: "Delivery plan NOT secured — recovery required",
  disruption_during_execution: "Route disrupted during delivery",
};

const OPEN_STATES: EmergencyStatus[] = ["ACTIVE", "ACKNOWLEDGED", "MATCHING", "ASSISTANCE_FOUND"];
const PRE_ASSIST_STATES: EmergencyStatus[] = ["ACTIVE", "ACKNOWLEDGED", "MATCHING"];

export default function EmergencyTrackDrawer({
  emergencyId,
  onClose,
  mode = "creator",
}: {
  emergencyId: string | null;
  onClose: () => void;
  mode?: "creator" | "responder";
}) {
  const qc = useQueryClient();
  const { user } = useAuth();
  const q = useQuery({
    queryKey: ["emergencies", emergencyId],
    queryFn: () => getEmergency(emergencyId!),
    enabled: !!emergencyId,
  });
  const candidates = useQuery({
    queryKey: ["emergencies", emergencyId, "match"],
    queryFn: () => getEmergencyCandidates(emergencyId!),
    enabled: !!emergencyId && mode === "creator",
  });
  // Only a HOSPITAL responder needs its own facility id, and only to call
  // the pathway-selection endpoint (which requires the caller to own the
  // named origin facility) -- see EmergencyTrackDrawer's "Select this
  // facility as the resource pathway" action below.
  const myHospital = useQuery({
    queryKey: ["hospitals", "me"],
    queryFn: getHospitalProfile,
    enabled: mode === "responder" && user?.role === "HOSPITAL",
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["emergencies"] });
    qc.invalidateQueries({ queryKey: ["hospital", "emergencies"] });
    qc.invalidateQueries({ queryKey: ["donor", "emergencies"] });
    qc.invalidateQueries({ queryKey: ["notifications"] });
  };
  const cancel = useMutation({ mutationFn: () => cancelEmergency(emergencyId!), onSuccess: invalidate });
  const resolve = useMutation({ mutationFn: () => resolveEmergency(emergencyId!), onSuccess: invalidate });
  const assist = useMutation({ mutationFn: () => emergencyAssistanceFound(emergencyId!), onSuccess: invalidate });
  const ack = useMutation({ mutationFn: () => acknowledgeEmergency(emergencyId!), onSuccess: invalidate });
  const respond = useMutation({
    mutationFn: (action: "ACCEPTED" | "DECLINED" | "NOT_NOW") =>
      respondToEmergency(emergencyId!, action),
    onSuccess: invalidate,
  });
  const selectPathway = useMutation({
    mutationFn: () => selectEmergencyPathway(emergencyId!, myHospital.data!.id),
    onSuccess: invalidate,
  });
  const busy =
    cancel.isPending || resolve.isPending || assist.isPending || ack.isPending || respond.isPending;

  const em = q.data;
  const isOpen = em ? OPEN_STATES.includes(em.status) : false;
  const accepted = em?.my_response?.status === "ACCEPTED";

  return (
    <Modal
      open={!!emergencyId}
      onClose={onClose}
      title={mode === "responder" ? "Emergency response" : "Emergency tracking"}
      width="max-w-xl"
    >
      {q.isLoading && (
        <div className="flex items-center gap-2 text-on-surface-variant">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading…
        </div>
      )}
      {q.isError && <p className="text-error text-body-sm">{toApiError(q.error).detail}</p>}
      {em && (
        <div className="flex flex-col gap-5">
          <div className="flex items-center justify-between">
            <div>
              <span className="bg-error/10 text-error px-3 py-1 rounded font-bold text-sm">
                {em.blood_type ? BLOOD_LABEL[em.blood_type] : "Any type"}
              </span>
              <span className="ml-2 text-on-surface-variant text-sm">{em.urgency.toLowerCase()}</span>
            </div>
            <span className={`px-3 py-1 rounded-full text-xs font-bold ${STATUS_STYLE[em.status]}`}>
              {em.status.replace("_", " ")}
            </span>
          </div>

          {/* Phase 31.1: a separate, independent fact from `status` -- never
              merges with or replaces the status badge above, and only
              appears when the backend has actually flagged it (never
              derived here from slack_seconds, and never shown just because
              a request failed). */}
          {em.deadline_exceeded_at && (
            <div
              role="alert"
              className="flex items-center gap-2 rounded-lg bg-error/10 text-error px-3 py-2 text-sm font-semibold"
            >
              <span aria-hidden="true" className="material-symbols-outlined text-[18px]">schedule</span>
              Past deadline — action needed
            </div>
          )}

          {em.location_text && (
            <p className="text-sm text-on-surface-variant">{em.location_text}</p>
          )}

          {(em.units_needed != null || em.slack_seconds != null) && (
            <div className="flex flex-wrap gap-3 text-xs text-on-surface-variant">
              {em.units_needed != null && <span>{em.units_needed} unit(s) needed</span>}
              {em.slack_seconds != null && (
                <span className={em.slack_seconds < 0 ? "text-error font-semibold" : ""}>
                  {formatSlack(em.slack_seconds)}
                </span>
              )}
            </div>
          )}

          {mode === "creator" && em.blood_type && (
            <DonorShortlistPanel
              kind="EMERGENCY"
              subjectId={em.id}
              canManage
              isAdmin={user?.role === "ADMIN"}
            />
          )}

          {em.filed_by_family && (
            <p className="flex items-center gap-1.5 text-xs font-semibold text-secondary bg-secondary/10 rounded-full px-3 py-1 w-fit">
              <span aria-hidden="true" className="material-symbols-outlined text-[14px]">group</span>
              Requested by family member{em.created_by_name ? ` · ${em.created_by_name}` : ""}
            </p>
          )}

          {em.possible_duplicate_of && (
            <p className="text-xs text-warning bg-warning/10 rounded-lg px-3 py-2">
              You had another open emergency for this patient when this one was created. Both
              stay active independently.
            </p>
          )}

          <div>
            <h4 className="font-label-md text-label-md mb-2">
              Responders ({em.accepted_count} on the way)
            </h4>
            {em.responders.length === 0 ? (
              <p className="text-sm text-on-surface-variant">No responders yet.</p>
            ) : (
              <ul className="space-y-1.5">
                {em.responders.map((r) => (
                  <li key={r.id} className="text-sm">
                    <div className="flex items-center justify-between">
                      <span>
                        {r.responder_name ?? `${r.responder_role[0]}${r.responder_role.slice(1).toLowerCase()}`}
                        <span className="text-on-surface-variant"> · {r.responder_role.toLowerCase()}</span>
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
                        {r.status === "ACCEPTED" ? "on the way" : r.status === "NOT_NOW" ? "not now" : "declined"}
                      </span>
                    </div>
                    {r.inventory_note && (
                      <p className="text-xs text-on-surface-variant mt-0.5">{r.inventory_note}</p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          {mode === "creator" && candidates.data && (
            <div>
              <h4 className="font-label-md text-label-md mb-2">
                Compatible responders nearby{" "}
                <span className="text-on-surface-variant font-normal">
                  ({candidates.data.candidates.length})
                </span>
              </h4>
              <ul className="space-y-1.5">
                {candidates.data.candidates.slice(0, 8).map((c) => (
                  <li key={c.responder_ref} className="flex items-center justify-between text-sm">
                    <span>
                      {c.responder_name ?? `${c.role === "HOSPITAL" ? "Hospital" : "Donor"} ${c.responder_ref}`}
                      {c.blood_type && (
                        <span className="text-on-surface-variant"> · {BLOOD_LABEL[c.blood_type]}</span>
                      )}
                      {c.city && <span className="text-on-surface-variant"> · {c.city}</span>}
                    </span>
                    {c.has_accepted && <span className="text-success font-semibold text-xs">on the way</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(em.current_pathway || em.pathway_history.length > 0) && (
            <div>
              <h4 className="font-label-md text-label-md mb-2">Resource pathway</h4>
              <ul className="space-y-2">
                {em.current_pathway && <PathwayRow p={em.current_pathway} label="Current" />}
                {em.pathway_history
                  .filter((p) => p.id !== em.current_pathway?.id)
                  .map((p) => (
                    <PathwayRow key={p.id} p={p} label="Earlier" />
                  ))}
              </ul>
            </div>
          )}

          <div>
            <h4 className="font-label-md text-label-md mb-2">Timeline</h4>
            <ol className="relative border-l border-border-subtle ml-1 space-y-3">
              {em.events.map((e) => (
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

          {mode === "creator" && isOpen && (
            <div className="flex flex-wrap justify-end gap-3 pt-1">
              {PRE_ASSIST_STATES.includes(em.status) && (
                <button className={ghostButtonClass} onClick={() => assist.mutate()} disabled={busy}>
                  Assistance found
                </button>
              )}
              <button className={ghostButtonClass} onClick={() => cancel.mutate()} disabled={busy}>
                Cancel
              </button>
              <button className={primaryButtonClass} onClick={() => resolve.mutate()} disabled={busy}>
                {resolve.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                Mark resolved
              </button>
            </div>
          )}

          {mode === "responder" && user?.role === "HOSPITAL" && isOpen && myHospital.data && (
            <div className="flex justify-end pt-1">
              <button
                className={ghostButtonClass}
                onClick={() => selectPathway.mutate()}
                disabled={selectPathway.isPending}
              >
                {selectPathway.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                {em.current_pathway ? "Re-check pathway from my facility" : "Route from my facility"}
              </button>
            </div>
          )}
          {selectPathway.isError && (
            <p className="text-error text-body-sm text-right">
              {toApiError(selectPathway.error).detail}
            </p>
          )}

          {mode === "responder" && isOpen && (
            <div className="flex flex-wrap justify-end gap-3 pt-1">
              {em.status === "ACTIVE" && (
                <button className={ghostButtonClass} onClick={() => ack.mutate()} disabled={busy}>
                  Acknowledge
                </button>
              )}
              {accepted ? (
                <button
                  className={ghostButtonClass}
                  onClick={() => respond.mutate("DECLINED")}
                  disabled={busy}
                >
                  Withdraw
                </button>
              ) : (
                <>
                  {em.my_response && (
                    <span className="self-center text-xs text-on-surface-variant mr-auto">
                      You responded: {em.my_response.status === "NOT_NOW" ? "Not now" : "Declined"}
                    </span>
                  )}
                  <button
                    className={ghostButtonClass}
                    onClick={() => respond.mutate("DECLINED")}
                    disabled={busy}
                  >
                    Decline
                  </button>
                  <button
                    className={ghostButtonClass}
                    onClick={() => respond.mutate("NOT_NOW")}
                    disabled={busy}
                    title="Can't help right now, but not a hard no"
                  >
                    Not now
                  </button>
                  <button
                    className={primaryButtonClass}
                    onClick={() => respond.mutate("ACCEPTED")}
                    disabled={busy}
                  >
                    {respond.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                    I can help now
                  </button>
                </>
              )}
            </div>
          )}
          {respond.isError && (
            <p className="text-error text-body-sm text-right">{toApiError(respond.error).detail}</p>
          )}
        </div>
      )}
    </Modal>
  );
}
