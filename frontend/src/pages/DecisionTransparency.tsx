/**
 * Decision Transparency -- LIVE.
 *
 * For a real emergency: every stored pathway (active, backups, superseded,
 * invalidated), the strategy and assumptions used, the plain-language
 * explanation, each leg's full route with each route's status when planned
 * and now, and why a plan was invalidated or replaced. Hospitals and
 * administrators can also (re)plan, reserve, release and hand the reserved
 * plan off to shipments (audit C21: the units are never released in
 * between) from here. The securing state is shown explicitly (audit C15).
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import EmergencyPicker from "../components/live/EmergencyPicker";
import LivePageShell from "../components/live/LivePageShell";
import { FeasibilityBadge } from "../components/live/PlanView";
import { toApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  handOffEmergencyPathway,
  listEmergencyPathways,
  planEmergencyPathway,
  releaseEmergencyPathway,
  reserveEmergencyPathway,
  type StoredPathway,
} from "../lib/gapApi";

const STATUS_STYLE: Record<StoredPathway["status"], string> = {
  ACTIVE: "border-green-400",
  BACKUP: "border-blue-300",
  SUPERSEDED: "border-gray-300 opacity-80",
  INVALIDATED: "border-red-300 opacity-80",
};

function PathwayCard({ p }: { p: StoredPathway }) {
  return (
    <article className={`rounded-xl border-2 ${STATUS_STYLE[p.status]} bg-surface-container-lowest p-4 flex flex-col gap-2`}>
      <header className="flex flex-wrap gap-2 items-center justify-between">
        <h3 className="font-headline-sm text-headline-sm">
          v{p.version} · round {p.plan_round} · {p.status}{p.status === "BACKUP" ? ` #${p.backup_rank}` : ""}
        </h3>
        {p.feasibility && <FeasibilityBadge value={p.feasibility} />}
      </header>
      <p className="text-body-sm text-on-surface-variant">
        {p.strategy} · engine {p.pathway_source} · {p.units_planned ?? 0}/{p.units_requested ?? "?"} units ·
        ETA {p.eta_minutes ?? "—"} min · slack {p.slack_minutes ?? "—"} min · reserved {p.reserved_units}
      </p>
      {p.status === "ACTIVE" && p.reservation_status && (
        <p
          data-testid="reservation-status"
          className={`text-body-sm font-semibold ${
            p.reservation_status === "RECOVERY_REQUIRED"
              ? "text-red-800"
              : p.reservation_status === "UNRESERVED"
                ? "text-on-surface-variant"
                : "text-green-800"
          }`}
        >
          {p.reservation_status === "RESERVED" && "Secured: units reserved for this emergency."}
          {p.reservation_status === "HANDED_OFF" && "In delivery: reservation handed to shipment(s), never released."}
          {p.reservation_status === "UNRESERVED" && "Not secured yet: no units reserved."}
          {p.reservation_status === "RECOVERY_REQUIRED" &&
            `NOT secured — units could not be reserved (${p.recovery_required_reason ?? "see timeline"}). Coordinator action needed.`}
        </p>
      )}
      {p.shipments && p.shipments.length > 0 && (
        <ul className="text-body-sm">
          {p.shipments.map((sh) => (
            <li key={sh.id}>
              Shipment <strong>{sh.shipment_code}</strong> · {sh.units} unit(s) · {sh.status}
            </li>
          ))}
        </ul>
      )}
      {p.invalidated_reason && <p className="text-body-sm text-red-800">Invalidated: {p.invalidated_reason}</p>}
      {p.superseded_reason && p.status === "SUPERSEDED" && (
        <p className="text-body-sm text-on-surface-variant">Superseded: {p.superseded_reason}</p>
      )}
      {p.legs.map((leg) => (
        <div key={leg.leg_index} className="rounded-lg bg-surface-container-low p-3 text-body-sm">
          <p className="font-label-md">
            Leg {leg.leg_index + 1}: {leg.source_facility_name ?? leg.source_facility_id.slice(0, 8)} — {leg.units} unit(s),{" "}
            {leg.route_basis}, {leg.distance_km.toFixed(1)} km
            {leg.straight_line_km != null && ` (straight line ${leg.straight_line_km.toFixed(1)} km)`}, ETA {leg.eta_minutes} min
          </p>
          {leg.steps.length > 0 && (
            <ol className="list-decimal pl-5 mt-1">
              {leg.steps.map((s) => (
                <li key={s.step_index}>
                  route {s.route_id.slice(0, 8)}: {s.distance_km.toFixed(1)} km / {s.transit_minutes} min — at planning{" "}
                  {s.route_status_at_plan}, now <strong>{s.route_status_now ?? "deleted"}</strong>
                </li>
              ))}
            </ol>
          )}
        </div>
      ))}
      {p.explanation && p.explanation.length > 0 && (
        <details>
          <summary className="cursor-pointer text-body-sm">Why this plan</summary>
          <ul className="list-disc pl-5 text-body-sm">{p.explanation.map((l, i) => <li key={i}>{l}</li>)}</ul>
        </details>
      )}
    </article>
  );
}

export default function DecisionTransparency() {
  const { user } = useAuth();
  const [eid, setEid] = useState("");
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["pathways", eid], queryFn: () => listEmergencyPathways(eid), enabled: !!eid });
  const after = { onSuccess: () => qc.invalidateQueries({ queryKey: ["pathways", eid] }) };
  const plan = useMutation({ mutationFn: () => planEmergencyPathway(eid), ...after });
  const reserve = useMutation({ mutationFn: () => reserveEmergencyPathway(eid), ...after });
  const release = useMutation({ mutationFn: () => releaseEmergencyPathway(eid, "released from transparency view"), ...after });
  const handoff = useMutation({ mutationFn: () => handOffEmergencyPathway(eid), ...after });
  const canAct = user?.role === "HOSPITAL" || user?.role === "ADMIN";
  const err = plan.error ?? reserve.error ?? release.error ?? handoff.error ?? q.error;
  const rows = q.data ?? [];
  const live = rows.filter((p) => p.status === "ACTIVE" || p.status === "BACKUP");
  const history = rows.filter((p) => p.status !== "ACTIVE" && p.status !== "BACKUP").reverse();
  return (
    <LivePageShell
      title="Decision Transparency"
      roles={["PATIENT", "FAMILY", "HOSPITAL", "ADMIN"]}
      intro="Every pathway decision stored for a real emergency, with the reasons, the assumptions, the complete route and what happened to it."
    >
      <EmergencyPicker value={eid} onChange={setEid} />
      {canAct && eid && (
        <div className="flex flex-wrap gap-3">
          <button className="bg-primary text-on-primary px-4 py-2 rounded-lg" onClick={() => plan.mutate()} disabled={plan.isPending}>
            {rows.length ? "Re-plan" : "Plan delivery"}
          </button>
          <button className="border border-primary text-primary px-4 py-2 rounded-lg" onClick={() => reserve.mutate()} disabled={reserve.isPending || !live.length}>
            Reserve active plan
          </button>
          <button className="border border-border-subtle px-4 py-2 rounded-lg" onClick={() => release.mutate()} disabled={release.isPending || !live.length}>
            Release reservation
          </button>
          <button
            className="border border-primary text-primary px-4 py-2 rounded-lg"
            onClick={() => handoff.mutate()}
            disabled={handoff.isPending || !live.some((p) => p.status === "ACTIVE" && p.reservation_status === "RESERVED")}
            title="Turn the reserved plan into shipment(s) without releasing the units"
          >
            Hand off to shipment
          </button>
        </div>
      )}
      {err && <p role="alert" className="text-error">{toApiError(err).detail}</p>}
      {eid && q.isSuccess && rows.length === 0 && <p className="text-body-md">No pathway has been planned for this emergency yet.</p>}
      {live.length > 0 && (
        <section aria-labelledby="live-h" className="flex flex-col gap-3">
          <h2 id="live-h" className="font-headline-md text-headline-md">Current plan and backups</h2>
          {live.map((p) => <PathwayCard key={p.id} p={p} />)}
        </section>
      )}
      {history.length > 0 && (
        <section aria-labelledby="hist-h" className="flex flex-col gap-3">
          <h2 id="hist-h" className="font-headline-md text-headline-md">History (never deleted)</h2>
          {history.map((p) => <PathwayCard key={p.id} p={p} />)}
        </section>
      )}
    </LivePageShell>
  );
}
