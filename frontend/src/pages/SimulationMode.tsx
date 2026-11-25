/**
 * Simulation Mode -- LIVE failure simulation.
 *
 * Plans the same request twice through the real planner: once on the
 * network as it is, once with the transport routes you tick treated as
 * failed. Shows which plan the system would switch to. Read-only: no route
 * is actually disrupted and nothing is reserved.
 */
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import LivePageShell from "../components/live/LivePageShell";
import PlanView, { FeasibilityBadge } from "../components/live/PlanView";
import ScenarioForm from "../components/live/ScenarioForm";
import { toApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { getHospitalRoutes, listAdminTransportRoutes } from "../lib/domain";
import { simulatePathway, type PlanResult, type SimulateBody } from "../lib/gapApi";

export default function SimulationMode() {
  const { user } = useAuth();
  const isAdmin = user?.role === "ADMIN";
  const adminRoutes = useQuery({ queryKey: ["admin", "routes"], queryFn: () => listAdminTransportRoutes(), enabled: isAdmin });
  const myRoutes = useQuery({ queryKey: ["hospital", "routes"], queryFn: getHospitalRoutes, enabled: !!user && !isAdmin });
  const [failed, setFailed] = useState<string[]>([]);
  const [pair, setPair] = useState<{ before: PlanResult; after: PlanResult } | null>(null);
  const run = useMutation({
    mutationFn: async (body: SimulateBody) => {
      const before = await simulatePathway(body);
      const after = await simulatePathway({ ...body, failed_route_ids: failed });
      return { before, after };
    },
    onSuccess: setPair,
  });
  const routes = isAdmin
    ? (adminRoutes.data ?? []).map((r) => ({ id: r.id, label: `${r.id.slice(0, 8)} · ${r.distance_km.toFixed(1)} km · ${r.status}` }))
    : (myRoutes.data ?? []).map((r) => ({ id: r.id, label: `to ${r.partner_facility_name ?? r.partner_facility_id.slice(0, 8)} · ${r.distance_km.toFixed(1)} km · ${r.status}` }));

  return (
    <LivePageShell
      title="Simulation Mode"
      roles={["HOSPITAL", "ADMIN"]}
      intro="Tick transport routes to fail, then compare the real planner's answer before and after. This is a dry run: no route is disrupted and nothing is reserved."
    >
      <ScenarioForm
        busy={run.isPending}
        submitLabel="Compare before / after"
        onRun={(b) => run.mutate(b)}
        extra={() => (
          <fieldset className="sm:col-span-2 lg:col-span-3 border border-border-subtle rounded-lg p-3">
            <legend className="px-1 text-body-sm">Routes to fail in the simulation</legend>
            {routes.length === 0 && <p className="text-body-sm text-on-surface-variant">No transport routes recorded.</p>}
            <div className="grid sm:grid-cols-2 gap-1">
              {routes.map((r) => (
                <label key={r.id} className="flex items-center gap-2 text-body-sm">
                  <input
                    type="checkbox"
                    checked={failed.includes(r.id)}
                    onChange={(e) => setFailed((f) => (e.target.checked ? [...f, r.id] : f.filter((x) => x !== r.id)))}
                  />
                  {r.label}
                </label>
              ))}
            </div>
          </fieldset>
        )}
      />
      {run.isError && <p role="alert" className="text-error">{toApiError(run.error).detail}</p>}
      {pair && (
        <div className="grid lg:grid-cols-2 gap-6">
          <div>
            <h2 className="font-headline-md text-headline-md mb-2">
              Network as it is <FeasibilityBadge value={pair.before.primary.feasibility} />
            </h2>
            <PlanView result={pair.before} />
          </div>
          <div>
            <h2 className="font-headline-md text-headline-md mb-2">
              With {failed.length} route(s) failed <FeasibilityBadge value={pair.after.primary.feasibility} />
            </h2>
            <PlanView result={pair.after} />
          </div>
        </div>
      )}
    </LivePageShell>
  );
}
