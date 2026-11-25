/**
 * Mathematical Model -- the formulas the live code actually uses, with the
 * parameter values configured on THIS deployment (GET /pathways/config).
 * No example numbers are invented here.
 */
import { useQuery } from "@tanstack/react-query";

import LivePageShell from "../components/live/LivePageShell";
import { toApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { getPathwayConfig } from "../lib/gapApi";

function Formula({ children }: { children: React.ReactNode }) {
  return (
    <pre
      tabIndex={0}
      aria-label="Formula (scrollable)"
      className="font-data-mono text-body-sm bg-surface-container-low rounded-lg p-3 overflow-x-auto focus:outline-none focus:ring-2 focus:ring-primary/40"
    >
      {children}
    </pre>
  );
}

export default function MathematicalModel() {
  const { user } = useAuth();
  // QA round: GET /pathways/config needs a session. The shell below only
  // renders the content for a signed-in user, but the query ran for anonymous
  // visitors too, producing a 401 and a console error on a public page.
  const cfg = useQuery({ queryKey: ["pathway-config"], queryFn: getPathwayConfig, enabled: !!user });
  const c = cfg.data;
  return (
    <LivePageShell
      title="Mathematical Model"
      roles={["PATIENT", "FAMILY", "DONOR", "HOSPITAL", "ADMIN"]}
      intro="The model behind pathway planning and forecasting, exactly as implemented, with this deployment's configured values. These are planning assumptions, not measured or clinically validated quantities."
    >
      {cfg.isError && <p role="alert">{toApiError(cfg.error).detail}</p>}
      {c && (
        <div className="flex flex-col gap-6">
          <section aria-labelledby="m-graph">
            <h2 id="m-graph" className="font-headline-md text-headline-md">1. Network and paths</h2>
            <p className="text-body-md">Facilities are nodes; operational transport routes are undirected edges with distance d(e) and transit time t(e). For each candidate source s the engine finds the cheapest path to the destination with at most H = {c.max_hops} edges (hop-bounded dynamic programming; Neo4j variable-length match when configured):</p>
            <Formula>{"P*(s) = argmin over paths P from s with |P| ≤ H of  cost(P),\ncost(P) = Σ t(e)  (CRITICAL, URGENT)     or     Σ d(e)  (ROUTINE)"}</Formula>
            <p className="text-body-md">If either facility has no recorded route at all, distance is a labelled straight-line estimate (haversine) at {c.straight_line_speed_kmph} km/h; if both are on the network and no operational path exists, the source is unreachable.</p>
          </section>
          <section aria-labelledby="m-time">
            <h2 id="m-time" className="font-headline-md text-headline-md">2. Time, deadline and feasibility</h2>
            <Formula>{`ETA(s)   = handling + Σ t(e)        handling = ${c.handling_minutes} min\narrival  = now + ETA(s)\nslack    = deadline − arrival\nusable(s)= #{units u at s : component(u) = requested, type(u) ∈ compatible(patient),\n             state(u) = AVAILABLE (or RESERVED for this emergency),\n             expiry(u) > arrival, thaw_expiry(u) > arrival}`}</Formula>
            <Formula>{`IMPOSSIBLE     if deadline ≤ now or no units can be planned\nPARTIAL        if planned units < requested\nDEADLINE_RISK  if slack < ${c.deadline_risk_minutes} min, or any leg is a straight-line estimate\nVIABLE         otherwise`}</Formula>
          </section>
          <section aria-labelledby="m-strat">
            <h2 id="m-strat" className="font-headline-md text-headline-md">3. Strategies (lexicographic ordering)</h2>
            <ul className="list-disc pl-6 text-body-md">
              {Object.entries(c.strategies).map(([u, s]) => (
                <li key={u}><strong>{u}</strong> → {s.name} (path objective: {s.objective}): {s.keys.join(" › ")}. {s.description}</li>
              ))}
            </ul>
            <p className="text-body-md mt-2">Up to {c.max_backups} backup plans are stored, route-disjoint from the primary first. On failure a backup is promoted only after re-validation (routes operational, facility verified, stock usable at arrival, deadline, compatibility).</p>
          </section>
          <section aria-labelledby="m-donor">
            <h2 id="m-donor" className="font-headline-md text-headline-md">4. Donor distance and alerts</h2>
            <Formula>{`include donor  ⇔  distance unknown  or  distance ≤ ${c.max_donor_distance_km} km  (or audited admin override)\nalert hospital ⇔  verified ∧ distance ≤ ${c.hospital_alert_radius_km} km ∧ holds a compatible usable unit`}</Formula>
          </section>
          <section aria-labelledby="m-fc">
            <h2 id="m-fc" className="font-headline-md text-headline-md">5. Demand forecast (simple exponential smoothing)</h2>
            <Formula>{"ℓ_t = α·y_t + (1−α)·ℓ_{t−1},  α chosen on the history (grid 0.05…0.95)\nŷ_{T+h} = ℓ_T,   80% interval ≈ ŷ ± 1.2816·σ·√(1 + (h−1)·α²)\nbacktest: MAE on held-out days vs naive and seasonal-naive baselines"}</Formula>
          </section>
          <p className="text-body-sm text-on-surface-variant">Data on this deployment is {c.data_is_synthetic ? "SYNTHETIC" : "declared real"}.</p>
        </div>
      )}
    </LivePageShell>
  );
}
