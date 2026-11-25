/**
 * Research Mode -- LIVE strategy comparison.
 *
 * Runs the same request through the real planner under each of the three
 * documented urgency strategies (CRITICAL_FASTEST_FEASIBLE,
 * URGENT_DEADLINE_THEN_TIME, ROUTINE_EFFICIENT) and tabulates what each
 * chooses. For donor-ranking strategies see the authenticated Strategy Lab.
 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import LivePageShell from "../components/live/LivePageShell";
import { FeasibilityBadge } from "../components/live/PlanView";
import ScenarioForm from "../components/live/ScenarioForm";
import { toApiError } from "../lib/api";
import { simulatePathway, URGENCIES, type PlanResult, type SimulateBody } from "../lib/gapApi";

export default function ResearchMode() {
  const [rows, setRows] = useState<PlanResult[]>([]);
  const run = useMutation({
    mutationFn: (b: SimulateBody) => Promise.all(URGENCIES.map((u) => simulatePathway({ ...b, urgency: u }))),
    onSuccess: setRows,
  });
  return (
    <LivePageShell
      title="Research Mode"
      roles={["HOSPITAL", "ADMIN"]}
      intro={
        <>
          The same request planned under each urgency strategy, side by side, by the live engine.
          Donor-ranking strategies are compared in the{" "}
          <Link to="/matching/strategy-lab" className="text-primary underline">Strategy Lab</Link>.
        </>
      }
    >
      <ScenarioForm busy={run.isPending} submitLabel="Compare strategies" onRun={(b) => run.mutate(b)} />
      {run.isError && <p role="alert" className="text-error">{toApiError(run.error).detail}</p>}
      {rows.length > 0 && (
        <div tabIndex={0} role="region" aria-label="Strategy comparison (scrollable)" className="overflow-x-auto rounded-xl border border-border-subtle focus:outline-none focus:ring-2 focus:ring-primary/40">
          <table className="min-w-full text-body-sm">
            <caption className="sr-only">Planner outcome per strategy</caption>
            <thead className="bg-surface-container-low text-left">
              <tr>
                <th scope="col" className="px-3 py-2">Strategy</th>
                <th scope="col" className="px-3 py-2">Ordering keys</th>
                <th scope="col" className="px-3 py-2">Feasibility</th>
                <th scope="col" className="px-3 py-2">Sources</th>
                <th scope="col" className="px-3 py-2">ETA</th>
                <th scope="col" className="px-3 py-2">Route km</th>
                <th scope="col" className="px-3 py-2">Backups</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.strategy} className="border-t border-border-subtle align-top">
                  <th scope="row" className="px-3 py-2 text-left">{r.strategy}</th>
                  <td className="px-3 py-2">{r.strategy_keys.join(" › ")}</td>
                  <td className="px-3 py-2"><FeasibilityBadge value={r.primary.feasibility} /></td>
                  <td className="px-3 py-2">{r.primary.legs.map((l) => l.facility_name ?? l.facility_id.slice(0, 8)).join(", ") || "—"}</td>
                  <td className="px-3 py-2">{r.primary.eta_minutes != null ? `${r.primary.eta_minutes} min` : "—"}</td>
                  <td className="px-3 py-2">{r.primary.legs.reduce((s, l) => s + (l.distance_km ?? 0), 0).toFixed(1)}</td>
                  <td className="px-3 py-2">{r.backups.length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </LivePageShell>
  );
}
