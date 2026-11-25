/**
 * Graph Decision Engine -- LIVE.
 *
 * Formerly a static Stitch mock-up ("conceptual illustration"). It now runs
 * the real pathway planner (POST /pathways/simulate -- the same code every
 * emergency uses) on this deployment's data and shows every facility it
 * evaluated, the chosen primary plan with its full route, and the stored
 * backups. Nothing is computed in the browser.
 */
import { useMutation } from "@tanstack/react-query";

import LivePageShell from "../components/live/LivePageShell";
import PlanView from "../components/live/PlanView";
import ScenarioForm from "../components/live/ScenarioForm";
import { toApiError } from "../lib/api";
import { simulatePathway } from "../lib/gapApi";

export default function GraphDecisionEngine() {
  const run = useMutation({ mutationFn: simulatePathway });
  return (
    <LivePageShell
      title="Graph Decision Engine"
      roles={["HOSPITAL", "ADMIN"]}
      intro={
        <>
          Runs the real pathway planner: compatible blood types for the chosen component, only
          usable and unreserved units that will still be in date on arrival, verified facilities,
          hop-bounded shortest paths over operational transport routes (Neo4j when configured,
          otherwise PostgreSQL), and the urgency's documented strategy. Nothing is stored or
          reserved.
        </>
      }
    >
      <ScenarioForm busy={run.isPending} onRun={(b) => run.mutate(b)} />
      {run.isError && <p role="alert" className="text-error">{toApiError(run.error).detail}</p>}
      {run.data && <PlanView result={run.data} />}
    </LivePageShell>
  );
}
