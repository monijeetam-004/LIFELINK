/**
 * Hospital — per-unit lifecycle.
 *
 * Every physical unit on this facility's books with its real state and
 * effective expiry (shelf life, or the post-thaw window for thawed plasma),
 * and the four per-unit actions the backend audits: thaw (plasma only),
 * quarantine, release from quarantine, discard. Counts shown elsewhere
 * (inventory) only ever include AVAILABLE + RESERVED units.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import HospitalSubNav from "./HospitalSubNav";
import { toApiError } from "../../lib/api";
import {
  COMPONENTS,
  COMPONENT_LABEL,
  discardUnit,
  listMyUnits,
  quarantineUnit,
  releaseQuarantine,
  thawUnit,
  type BloodUnitRow,
} from "../../lib/gapApi";

const STATES = ["AVAILABLE", "RESERVED", "QUARANTINED", "EXPIRED", "DISCARDED", "ALLOCATED"];

function fmt(ts: string | null) {
  return ts ? new Date(ts).toLocaleString() : "—";
}

export default function UnitLifecycle() {
  const qc = useQueryClient();
  const [state, setState] = useState("");
  const [component, setComponent] = useState("");
  const q = useQuery({
    queryKey: ["hospital", "units", state, component],
    queryFn: () => listMyUnits({ ...(state ? { state } : {}), ...(component ? { component } : {}) }),
  });
  const act = useMutation({
    mutationFn: async ({ unit, action }: { unit: BloodUnitRow; action: string }) => {
      if (action === "thaw") return thawUnit(unit.id);
      const reason = window.prompt(`Reason to ${action.replace("-", " ")} unit ${unit.unit_code}`);
      if (!reason || reason.trim().length < 3) throw new Error("A reason of at least 3 characters is required");
      if (action === "quarantine") return quarantineUnit(unit.id, reason);
      if (action === "release") return releaseQuarantine(unit.id, reason);
      return discardUnit(unit.id, reason);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hospital"] }),
  });
  const field = "border border-border-subtle rounded-lg px-3 py-2 bg-surface-container-lowest";
  const err = act.error instanceof Error && !("isAxiosError" in act.error) ? act.error.message
    : act.error ? toApiError(act.error).detail : null;
  return (
    <div className="min-h-screen bg-surface">
      <HospitalSubNav title="Blood units" subtitle="Per-unit state, expiry, thaw and quarantine" />
      <main className="px-4 sm:px-6 lg:px-10 py-6 max-w-container-max mx-auto flex flex-col gap-4">
        <div className="flex flex-wrap gap-4">
          <label className="flex flex-col gap-1 text-body-sm">State
            <select className={field} value={state} onChange={(e) => setState(e.target.value)}>
              <option value="">All</option>
              {STATES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-body-sm">Component
            <select className={field} value={component} onChange={(e) => setComponent(e.target.value)}>
              <option value="">All</option>
              {COMPONENTS.map((c) => <option key={c} value={c}>{COMPONENT_LABEL[c]}</option>)}
            </select>
          </label>
        </div>
        {err && <p role="alert" className="text-error">{err}</p>}
        {q.isError && <p role="alert" className="text-error">{toApiError(q.error).detail}</p>}
        <div tabIndex={0} role="region" aria-label="Blood units (scrollable)" className="overflow-x-auto rounded-xl border border-border-subtle focus:outline-none focus:ring-2 focus:ring-primary/40">
          <table className="min-w-full text-body-sm">
            <caption className="sr-only">Blood units at this facility</caption>
            <thead className="bg-surface-container-low text-left">
              <tr>
                <th scope="col" className="px-3 py-2">Unit</th>
                <th scope="col" className="px-3 py-2">Type</th>
                <th scope="col" className="px-3 py-2">State</th>
                <th scope="col" className="px-3 py-2">Effective expiry</th>
                <th scope="col" className="px-3 py-2">Thawed</th>
                <th scope="col" className="px-3 py-2">Note</th>
                <th scope="col" className="px-3 py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {(q.data ?? []).map((u) => {
                const counted = u.state === "AVAILABLE" || u.state === "RESERVED";
                return (
                  <tr key={u.id} className="border-t border-border-subtle align-top">
                    <td className="px-3 py-2 font-data-mono">{u.unit_code}</td>
                    <td className="px-3 py-2">{u.blood_type.replace("_", " ")} · {COMPONENT_LABEL[u.component]}</td>
                    <td className="px-3 py-2">{u.state}</td>
                    <td className="px-3 py-2">{fmt(u.effective_expires_at)}</td>
                    <td className="px-3 py-2">{u.thawed_at ? fmt(u.thawed_at) : u.component === "PLASMA" ? "frozen" : "n/a"}</td>
                    <td className="px-3 py-2">{u.status_reason ?? ""}</td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap gap-2">
                        {u.component === "PLASMA" && !u.thawed_at && counted && (
                          <button className="underline" onClick={() => act.mutate({ unit: u, action: "thaw" })}>Thaw</button>
                        )}
                        {counted && (
                          <button className="underline" onClick={() => act.mutate({ unit: u, action: "quarantine" })}>Quarantine</button>
                        )}
                        {u.state === "QUARANTINED" && (
                          <button className="underline" onClick={() => act.mutate({ unit: u, action: "release" })}>Release</button>
                        )}
                        {(counted || u.state === "QUARANTINED") && (
                          <button className="underline text-error" onClick={() => act.mutate({ unit: u, action: "discard" })}>Discard</button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
              {q.isSuccess && q.data.length === 0 && (
                <tr><td colSpan={7} className="px-3 py-4 text-on-surface-variant">No units match.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </main>
    </div>
  );
}
