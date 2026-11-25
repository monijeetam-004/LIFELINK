/**
 * Hospital — Resource Pathways (real graph-engine screen, Phase 8)
 *
 * This is a genuinely new screen, not a redesign of an existing Stitch
 * export -- it exists because GraphDecisionEngine.tsx (the Stitch
 * "Graph Decision Engine" page) is a public, unauthenticated explanatory
 * mock kept as-is from the export (see App.tsx), and the real
 * resource-pathway engine needs an authenticated caller (a facility on
 * whose behalf to search). So instead of bolting live data onto a public
 * marketing page, the real engine is wired in here: a normal hospital
 * screen calling the real `GET /hospitals/me/resource-pathways` endpoint.
 *
 * Every number here is real:
 *  - candidates come from the shared pathway planner: compatible types for
 *    the chosen component, usable unreserved units still in date on
 *    arrival, verified facilities, and real transport routes (Neo4j when
 *    configured, otherwise a PostgreSQL path search) -- `pathway_source`. Both cases are shown to the user, never
 *    hidden behind a fake "optimal path" claim.
 *  - `disrupted_routes_excluded` and `degraded_reason` surface graph
 *    degradation instead of silently substituting fake success.
 *  - hop counts / transit minutes are omitted (not zero-filled) when the
 *    fallback engine has no graph-hop concept for a candidate.
 *
 * Phase 5 adds a "Logistics" section below the search: the real
 * TransportRoute rows already connected to this facility
 * (GET /hospitals/me/routes) — the same relational data an admin manages,
 * shown read-only so a hospital can see what actually links to it and
 * whether any of those links are currently disrupted, independent of
 * running a new resource search.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Loader2 } from "lucide-react";

import HospitalSubNav from "./HospitalSubNav";
import { toApiError } from "../../lib/api";
import {
  BLOOD_LABEL,
  BLOOD_TYPES,
  getHospitalResourcePathways,
  getHospitalRoutes,
  type BloodType,
} from "../../lib/domain";
import { COMPONENTS, COMPONENT_LABEL, type Component } from "../../lib/gapApi";

const SOURCE_LABEL: Record<string, string> = {
  GRAPH: "Live Neo4j graph traversal",
  RELATIONAL_ROUTES: "Recorded transport routes (PostgreSQL path search)",
  HAVERSINE_FALLBACK: "Straight-line distance fallback (legacy)",
};

const BASIS_LABEL: Record<string, string> = {
  LOCAL: "Own stock",
  ROUTED: "Recorded route",
  STRAIGHT_LINE_ESTIMATE: "Straight-line estimate",
};

export default function ResourcePathways() {
  const [bloodType, setBloodType] = useState<BloodType>("O_NEG");
  const [component, setComponent] = useState<Component>("WHOLE_BLOOD");
  const [units, setUnits] = useState(1);
  const [queryKey, setQueryKey] = useState(0);

  const q = useQuery({
    queryKey: ["hospital", "resource-pathways", bloodType, component, units, queryKey],
    queryFn: () => getHospitalResourcePathways(bloodType, 8, component, units),
  });
  const routes = useQuery({ queryKey: ["hospital", "routes"], queryFn: getHospitalRoutes });

  const data = q.data;
  const isGraph = data?.pathway_source === "GRAPH";

  return (
    <div className="min-h-screen bg-surface">
      <HospitalSubNav
        title="Resource Pathways"
        subtitle="Real graph-engine resource discovery for your facility"
      />

      <main className="px-4 sm:px-6 lg:px-10 py-6 max-w-container-max mx-auto flex flex-col gap-6">
        <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-4">
          <div>
            <h1 className="font-headline-lg text-headline-lg text-on-surface">
              Find blood product from the network
            </h1>
            <p className="font-body-md text-body-md text-on-surface-variant mt-1 max-w-2xl">
              Queries <code className="font-data-mono text-label-sm">GET /hospitals/me/resource-pathways</code>{" "}
              for real -- ranking other facilities that currently hold stock of the selected blood
              type by actual transport distance, either via a live Neo4j graph traversal or an
              honestly-labelled fallback when Neo4j is unreachable.
            </p>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            <label className="flex items-center gap-2">
              <span className="font-label-sm text-label-sm text-on-surface-variant">Patient type</span>
              <select
                className="h-10 px-3 rounded-lg bg-surface-container-low text-on-surface font-label-md text-label-md outline-none border border-border-subtle focus:ring-2 focus:ring-primary/40 focus:border-primary"
                value={bloodType}
                onChange={(e) => setBloodType(e.target.value as BloodType)}
              >
                {BLOOD_TYPES.map((bt) => (
                  <option key={bt} value={bt}>
                    {BLOOD_LABEL[bt]}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-2">
              <span className="font-label-sm text-label-sm text-on-surface-variant">Component</span>
              <select
                className="h-10 px-3 rounded-lg bg-surface-container-low border border-border-subtle"
                value={component}
                onChange={(e) => setComponent(e.target.value as Component)}
              >
                {COMPONENTS.map((c) => <option key={c} value={c}>{COMPONENT_LABEL[c]}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-2">
              <span className="font-label-sm text-label-sm text-on-surface-variant">Units</span>
              <input
                type="number"
                min={1}
                max={50}
                className="h-10 w-20 px-3 rounded-lg bg-surface-container-low border border-border-subtle"
                value={units}
                onChange={(e) => setUnits(Math.max(1, Number(e.target.value)))}
              />
            </label>
            <button
              type="button"
              className="h-10 px-5 rounded-lg bg-primary hover:bg-primary-container text-on-primary font-label-md text-label-md transition-all disabled:opacity-50 flex items-center gap-2"
              disabled={q.isFetching}
              onClick={() => setQueryKey((n) => n + 1)}
            >
              {q.isFetching && <Loader2 className="h-4 w-4 animate-spin" />}
              Re-calculate
            </button>
          </div>
        </div>

        {q.isError && (
          <p role="alert" className="text-error text-body-sm bg-error-container/40 rounded-lg px-4 py-2">
            {toApiError(q.error).detail}
          </p>
        )}

        {q.isLoading && (
          <div className="h-40 rounded-xl border border-border-subtle bg-surface-container-lowest animate-pulse" />
        )}

        {data && (
          <>
            {/* Honest engine-status banner -- never hidden, never faked */}
            <div
              className={`flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-6 rounded-xl border p-4 ${
                isGraph
                  ? "bg-success/10 border-success/40"
                  : "bg-warning/10 border-warning/40"
              }`}
            >
              <span
                className={`inline-flex items-center gap-2 px-3 py-1 rounded-full text-label-sm font-semibold shrink-0 ${
                  isGraph ? "bg-success text-on-primary" : "bg-warning text-on-primary"
                }`}
              >
                <span aria-hidden="true" className="material-symbols-outlined text-[16px]">
                  {isGraph ? "hub" : "warning"}
                </span>
                {SOURCE_LABEL[data.pathway_source] ?? data.pathway_source}
              </span>
              <span className="font-body-sm text-body-sm text-on-surface-variant">
                {isGraph
                  ? "Neo4j is reachable -- these results came from a real weighted graph traversal."
                  : "Neo4j is unreachable right now, so this ranking was computed from real facility coordinates using straight-line (haversine) distance instead of the graph. Distances are still real; hop counts and transit-minute estimates from the graph are not available in this mode."}
              </span>
              {data.degraded_reason && (
                <span className="font-body-sm text-body-sm text-error shrink-0">
                  {data.degraded_reason}
                </span>
              )}
              {data.disrupted_routes_excluded > 0 && (
                <span className="font-body-sm text-body-sm text-on-surface-variant shrink-0">
                  {data.disrupted_routes_excluded} disrupted route
                  {data.disrupted_routes_excluded === 1 ? "" : "s"} excluded from consideration
                </span>
              )}
            </div>

            {data.candidates.length === 0 ? (
              <p className="font-body-md text-body-md text-on-surface-variant bg-surface-container-lowest border border-border-subtle rounded-xl p-6 text-center">
                No facility in the network currently has recorded stock of {BLOOD_LABEL[data.blood_type]}
                {data.graph_available ? "" : " (or the fallback distance engine could not reach any candidate)"}.
              </p>
            ) : (
              <div tabIndex={0} role="region" aria-label="Results table (scrollable)" className="overflow-x-auto rounded-xl border border-border-subtle shadow-sm focus:outline-none focus:ring-2 focus:ring-primary/40">
                <table className="w-full text-left">
                  <thead className="bg-surface-container-low">
                    <tr>
                      <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                        Facility
                      </th>
                      <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                        Units
                      </th>
                      <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                        Distance
                      </th>
                      <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                        Est. transit
                      </th>
                      <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                        Hops
                      </th>
                      <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                        Basis / ETA
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-subtle bg-surface">
                    {data.candidates.map((c, i) => (
                      <tr key={c.facility_id} className={i === 0 ? "bg-primary-container/10" : undefined}>
                        <td className="px-4 py-3">
                          <p className="font-label-md text-label-md text-on-surface">
                            {c.facility_name ?? "Unnamed facility"}
                            {i === 0 && (
                              <span className="ml-2 text-label-sm text-primary font-semibold">Best match</span>
                            )}
                          </p>
                          {c.city && (
                            <p className="font-body-sm text-body-sm text-on-surface-variant">{c.city}</p>
                          )}
                        </td>
                        <td className="px-4 py-3 font-body-md text-body-md text-on-surface">{c.units}</td>
                        <td className="px-4 py-3 font-body-md text-body-md text-on-surface">
                          {c.distance_km.toFixed(1)} km
                        </td>
                        <td className="px-4 py-3 font-body-md text-body-md text-on-surface">
                          {c.transit_minutes != null ? `${c.transit_minutes} min` : "—"}
                        </td>
                        <td className="px-4 py-3 font-body-md text-body-md text-on-surface">
                          {c.hops ?? "—"}
                        </td>
                        <td className="px-4 py-3 font-body-sm text-body-sm text-on-surface">
                          {c.route_basis ? BASIS_LABEL[c.route_basis] : "—"}
                          {c.eta_minutes != null && ` · ETA ${c.eta_minutes} min`}
                          {c.units_by_type && Object.keys(c.units_by_type).length > 0 && (
                            <span className="block text-on-surface-variant">
                              {Object.entries(c.units_by_type).map(([t, n]) => `${n} ${t}`).join(", ")}
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}

        {/* Logistics: real transport links already connected to this
            facility, read-only (creating/disrupting a route stays an
            admin/demo-control action, not something a hospital does). */}
        <section className="flex flex-col gap-4">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div>
              <h2 className="font-headline-md text-headline-md text-on-surface">Your transport links</h2>
              <p className="font-body-md text-body-md text-on-surface-variant mt-1 max-w-2xl">
                Queries <code className="font-data-mono text-label-sm">GET /hospitals/me/routes</code> —
                the same relational transport-route data behind the search above, filtered to links that
                touch your facility directly.
              </p>
            </div>
            <Link
              to="/hospital/shipments"
              className="shrink-0 px-4 py-2 rounded-lg bg-primary text-on-primary font-label-md text-label-md flex items-center gap-2 hover:opacity-90"
            >
              <span aria-hidden="true" className="material-symbols-outlined text-[18px]">local_shipping</span>
              Shipments & chain of custody
            </Link>
          </div>

          {routes.isLoading && (
            <div className="h-24 rounded-xl border border-border-subtle bg-surface-container-lowest animate-pulse" />
          )}
          {routes.isError && (
            <p role="alert" className="text-error text-body-sm">{toApiError(routes.error).detail}</p>
          )}
          {routes.data && routes.data.length === 0 && (
            <p className="font-body-md text-body-md text-on-surface-variant bg-surface-container-lowest border border-border-subtle rounded-xl p-6 text-center">
              No transport route connects to your facility yet.
            </p>
          )}
          {routes.data && routes.data.length > 0 && (
            <div tabIndex={0} role="region" aria-label="Results table (scrollable)" className="overflow-x-auto rounded-xl border border-border-subtle shadow-sm focus:outline-none focus:ring-2 focus:ring-primary/40">
              <table className="w-full text-left">
                <thead className="bg-surface-container-low">
                  <tr>
                    <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">Connected facility</th>
                    <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">Distance</th>
                    <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">Est. transit</th>
                    <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle bg-surface">
                  {routes.data.map((r) => (
                    <tr key={r.id}>
                      <td className="px-4 py-3">
                        <p className="font-label-md text-label-md text-on-surface">
                          {r.partner_facility_name ?? "Unnamed facility"}
                        </p>
                        {r.partner_facility_city && (
                          <p className="font-body-sm text-body-sm text-on-surface-variant">{r.partner_facility_city}</p>
                        )}
                      </td>
                      <td className="px-4 py-3 font-body-md text-body-md text-on-surface">{r.distance_km.toFixed(1)} km</td>
                      <td className="px-4 py-3 font-body-md text-body-md text-on-surface">{r.transit_minutes} min</td>
                      <td className="px-4 py-3">
                        <span
                          className={`px-2 py-1 rounded-full text-label-sm font-semibold ${
                            r.status === "OPERATIONAL" ? "bg-success/15 text-success" : "bg-error/10 text-error"
                          }`}
                        >
                          {r.status === "OPERATIONAL" ? "Operational" : "Disrupted"}
                        </span>
                        {r.disrupted_reason && (
                          <p className="font-body-sm text-body-sm text-on-surface-variant mt-1">{r.disrupted_reason}</p>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
