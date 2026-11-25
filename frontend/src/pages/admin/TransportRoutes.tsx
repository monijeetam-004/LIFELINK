/**
 * Admin — Transport Routes & Graph Administration (Phase 8)
 *
 * The real administration surface for the resource-pathway graph engine
 * (app/services/graph_service.py, app/api/routes/admin.py). Everything here
 * is a genuine mutation against the same TransportRoute rows that
 * GET /hospitals/me/resource-pathways and GET /emergencies/{id}/resource-
 * pathways traverse -- disrupting a route here really removes it from
 * pathway consideration for every hospital and emergency query, and the
 * "impact" shown after a disrupt/recover action is a real before/after
 * comparison run against the graph (or its honest haversine fallback), not
 * a canned number. "Resync graph" calls the one endpoint that must fail
 * loudly (503) rather than claim success when Neo4j is unreachable.
 */
import { useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Menu } from "lucide-react";

import NotificationsBell from "../../components/NotificationsBell";
import { useAuth } from "../../lib/auth";
import { toApiError } from "../../lib/api";
import { ROLE_LABEL } from "../../lib/roles";
import {
  BLOOD_LABEL,
  getAdminSystemHealth,
  getHospitalDirectory,
  listAdminTransportRoutes,
  createAdminTransportRoute,
  disruptAdminTransportRoute,
  recoverAdminTransportRoute,
  resyncGraph,
  type TransportRoute,
  type TransportRouteAction,
} from "../../lib/domain";

import { ADMIN_NAV, ACCOUNT_NAV } from "./adminNav";
import MobileNavDrawer from "../../components/MobileNavDrawer";

function ImpactPanel({ action }: { action: TransportRouteAction }) {
  const { impact, sample_blood_type, graph_synced } = action;
  return (
    <div className="mt-3 rounded-lg border border-border-subtle bg-surface-container-lowest p-4 text-body-sm">
      <p className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider mb-2">
        Measured impact {!graph_synced && "(graph sync failed -- Neo4j unreachable)"}
      </p>
      {impact == null || sample_blood_type == null ? (
        <p className="text-on-surface-variant">
          No recorded stock at either endpoint of this route could be used as a real probe, so no
          honest before/after comparison could be computed.
        </p>
      ) : (
        <div className="flex flex-col gap-1 text-on-surface">
          <span>
            Probed with <strong>{BLOOD_LABEL[sample_blood_type]}</strong> via{" "}
            {impact.source === "GRAPH" ? "live graph traversal" : "haversine fallback"}
          </span>
          <span>
            Best distance: {impact.before_best_distance_km?.toFixed(1) ?? "unreachable"} km →{" "}
            {impact.after_best_distance_km?.toFixed(1) ?? "unreachable"} km
            {impact.distance_increase_km != null && impact.distance_increase_km > 0 && (
              <span className="text-error"> (+{impact.distance_increase_km.toFixed(1)} km)</span>
            )}
          </span>
          <span>
            Candidates: {impact.before_candidate_count} → {impact.after_candidate_count}
          </span>
          {impact.became_unreachable && (
            <span className="text-error font-semibold">This blood type became unreachable from here.</span>
          )}
          {impact.became_reachable && (
            <span className="text-success font-semibold">This blood type became reachable again.</span>
          )}
        </div>
      )}
    </div>
  );
}

export default function TransportRoutes() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  const [originId, setOriginId] = useState("");
  const [destId, setDestId] = useState("");
  const [distanceOverride, setDistanceOverride] = useState("");
  const [transitOverride, setTransitOverride] = useState("");
  const [createError, setCreateError] = useState("");

  const [disruptingId, setDisruptingId] = useState<string | null>(null);
  const [reasonDraft, setReasonDraft] = useState("");
  const [lastAction, setLastAction] = useState<Record<string, TransportRouteAction>>({});
  const [actionError, setActionError] = useState("");
  const [resyncError, setResyncError] = useState("");

  const health = useQuery({ queryKey: ["admin", "system", "health"], queryFn: getAdminSystemHealth });
  const directory = useQuery({ queryKey: ["hospital-directory"], queryFn: getHospitalDirectory });
  const routes = useQuery({ queryKey: ["admin", "transport-routes"], queryFn: () => listAdminTransportRoutes() });

  const facilityById = useMemo(() => {
    const m = new Map<string, { name: string | null; city: string | null }>();
    for (const f of directory.data ?? []) m.set(f.id, { name: f.name, city: f.city });
    return m;
  }, [directory.data]);

  function facilityLabel(id: string) {
    const f = facilityById.get(id);
    if (!f) return id.slice(0, 8);
    return f.city ? `${f.name ?? "Unnamed"} (${f.city})` : f.name ?? "Unnamed";
  }

  function refreshRoutes() {
    qc.invalidateQueries({ queryKey: ["admin", "transport-routes"] });
  }

  const create = useMutation({
    mutationFn: () =>
      createAdminTransportRoute({
        origin_facility_id: originId,
        destination_facility_id: destId,
        distance_km: distanceOverride.trim() ? Number(distanceOverride) : undefined,
        transit_minutes: transitOverride.trim() ? Number(transitOverride) : undefined,
      }),
    onSuccess: () => {
      setCreateError("");
      setOriginId("");
      setDestId("");
      setDistanceOverride("");
      setTransitOverride("");
      refreshRoutes();
    },
    onError: (e) => setCreateError(toApiError(e).detail),
  });

  const disrupt = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) => disruptAdminTransportRoute(id, reason),
    onSuccess: (action) => {
      setActionError("");
      setDisruptingId(null);
      setReasonDraft("");
      setLastAction((m) => ({ ...m, [action.route.id]: action }));
      refreshRoutes();
    },
    onError: (e) => setActionError(toApiError(e).detail),
  });

  const recover = useMutation({
    mutationFn: (id: string) => recoverAdminTransportRoute(id),
    onSuccess: (action) => {
      setActionError("");
      setLastAction((m) => ({ ...m, [action.route.id]: action }));
      refreshRoutes();
    },
    onError: (e) => setActionError(toApiError(e).detail),
  });

  const resync = useMutation({
    mutationFn: resyncGraph,
    onSuccess: () => {
      setResyncError("");
      qc.invalidateQueries({ queryKey: ["admin", "system", "health"] });
    },
    onError: (e) => setResyncError(toApiError(e).detail),
  });

  async function handleLogout() {
    setLogoutBusy(true);
    await logout();
    navigate("/login", { replace: true });
  }

  const graphHealth = health.data?.graph;

  return (
    <>
      {/* Sidebar */}
      <aside className="fixed left-0 top-0 h-full w-72 bg-surface-container-lowest z-40 flex flex-col justify-between shadow-level-1 hidden lg:flex">
        <div className="flex flex-col">
          <div className="h-16 px-space-6 flex items-center gap-space-3">
            <div className="w-9 h-9 rounded-lg bg-primary-container flex items-center justify-center shadow-level-1">
              <span aria-hidden="true" className="material-symbols-outlined text-on-primary text-[22px]">shield_with_heart</span>
            </div>
            <div className="flex flex-col">
              <span className="font-headline-sm text-headline-sm text-on-surface leading-none">LifeLink</span>
              <span className="font-label-sm text-label-sm text-secondary tracking-wider font-semibold uppercase mt-space-1">
                Admin Portal
              </span>
            </div>
          </div>
          <nav className="flex-1 px-space-3 py-space-4 space-y-1">
            <div className="px-space-3 py-space-1">
              <span className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">
                Control Center
              </span>
            </div>
            {ADMIN_NAV.map((n) => (
              <Link
                key={n.path}
                to={n.path}
                aria-current={location.pathname === n.path ? "page" : undefined}
                className={`flex items-center gap-space-3 px-space-3 py-space-3 rounded-lg transition-all font-label-lg text-label-lg ${
                  location.pathname === n.path
                    ? "bg-primary-container text-on-primary font-bold shadow-level-1"
                    : "text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"
                }`}
              >
                <span aria-hidden="true" className="material-symbols-outlined text-[20px]">{n.icon}</span>
                <span>{n.label}</span>
              </Link>
            ))}
            <div className="pt-space-3 px-space-3 py-space-1">
              <span className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">
                Account
              </span>
            </div>
            {ACCOUNT_NAV.map((n) => (
              <Link
                key={n.path}
                to={n.path}
                className="flex items-center gap-space-3 px-space-3 py-space-3 rounded-lg text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface transition-all font-label-lg text-label-lg"
              >
                <span aria-hidden="true" className="material-symbols-outlined text-[20px]">{n.icon}</span>
                <span>{n.label}</span>
              </Link>
            ))}
          </nav>
        </div>
        <div className="p-space-4 space-y-space-2">
          <div className="flex items-center gap-space-3 p-space-3 rounded-lg bg-surface-container-low">
            <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center shrink-0">
              <span aria-hidden="true" className="material-symbols-outlined text-on-primary text-[18px]">verified_user</span>
            </div>
            <div className="flex flex-col min-w-0 flex-1">
              <span className="font-label-md text-label-md text-on-surface font-semibold truncate">
                {user?.full_name ?? "Admin"}
              </span>
              <span className="font-body-sm text-body-sm text-on-surface-variant truncate">{user?.email}</span>
            </div>
          </div>
          <button
            onClick={handleLogout}
            disabled={logoutBusy}
            className="w-full flex items-center gap-space-3 px-space-3 py-space-2 rounded-lg text-on-surface-variant hover:bg-surface-container-low hover:text-error transition-all disabled:opacity-60"
          >
            {logoutBusy ? <Loader2 className="h-[18px] w-[18px] animate-spin" /> : (
              <span aria-hidden="true" className="material-symbols-outlined text-[18px]">logout</span>
            )}
            <span className="font-label-md text-label-md">Log out</span>
          </button>
        </div>
      </aside>

      <MobileNavDrawer
        open={mobileNavOpen}
        onClose={() => setMobileNavOpen(false)}
        activePath={location.pathname}
        title="LifeLink"
        subtitle="Admin Portal"
        sections={[
          { heading: "Control Center", items: ADMIN_NAV },
          { heading: "Account", items: ACCOUNT_NAV },
        ]}
        footer={
          <>
            <div className="flex items-center gap-space-3 p-space-3 rounded-lg bg-surface-container-low">
              <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center shrink-0">
                <span aria-hidden="true" className="material-symbols-outlined text-on-primary text-[18px]">verified_user</span>
              </div>
              <div className="flex flex-col min-w-0 flex-1">
                <span className="font-label-md text-label-md text-on-surface font-semibold truncate">
                  {user?.full_name ?? "Admin"}
                </span>
                <span className="font-body-sm text-body-sm text-on-surface-variant truncate">
                  {user?.email}
                </span>
              </div>
            </div>
            <button
              type="button"
              onClick={handleLogout}
              disabled={logoutBusy}
              className="w-full flex items-center gap-space-3 px-space-3 py-space-2 rounded-lg text-on-surface-variant hover:bg-surface-container-low hover:text-error transition-all disabled:opacity-60"
            >
              <span aria-hidden="true" className="material-symbols-outlined text-[18px]">logout</span>
              <span className="font-label-md text-label-md">Log out</span>
            </button></>
        }
      />

      <div className="lg:pl-72">
        <header className="sticky top-0 lg:fixed lg:top-0 lg:left-72 lg:right-0 h-16 bg-surface/90 backdrop-blur-xl shadow-level-1 z-30 flex items-center justify-between px-space-4 lg:px-space-8">
          <div className="flex items-center gap-space-3 min-w-0">
            <button
              type="button"
              onClick={() => setMobileNavOpen(true)}
              aria-label="Open navigation menu"
              className="lg:hidden p-2 -ml-2 rounded-full text-on-surface-variant hover:bg-surface-container-low shrink-0"
            >
              <Menu className="h-5 w-5" />
            </button>
            <div className="flex flex-col min-w-0">
            <span className="font-title-lg text-title-lg text-on-surface truncate">Transport Routes</span>
            <span className="font-label-sm text-label-sm text-on-surface-variant hidden sm:block">
              Graph engine administration
            </span>
          </div>
          </div>
          <div className="flex items-center gap-space-3">
            <NotificationsBell buttonClassName="relative p-2 rounded-full hover:bg-surface-container-low text-on-surface-variant" />
            <div className="flex items-center gap-space-2 pl-space-2">
              <div className="hidden md:flex flex-col items-end">
                <span className="font-label-lg text-label-lg text-on-surface font-semibold truncate max-w-[160px]">
                  {user?.full_name}
                </span>
                <span className="font-body-sm text-body-sm text-on-surface-variant">
                  {user ? ROLE_LABEL[user.role] : ""}
                </span>
              </div>
              <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center shrink-0">
                <span aria-hidden="true" className="material-symbols-outlined text-on-primary text-[18px]">person</span>
              </div>
            </div>
          </div>
        </header>

        <main className="relative lg:pt-16 bg-background w-full min-h-screen px-space-4 sm:px-space-6 lg:px-space-8 py-space-6">
          <div className="flex flex-col w-full gap-space-6 pb-space-12">
            <div className="flex flex-col md:flex-row md:items-end justify-between gap-space-4">
              <div>
                <h1 className="font-headline-lg text-headline-lg text-on-surface">Transport Routes &amp; Graph</h1>
                <p className="font-body-md text-body-md text-on-surface-variant">
                  Real facility-to-facility transport links. Disrupting a route removes it from every
                  resource-pathway query network-wide, for real.
                </p>
              </div>
            </div>

            {/* Graph engine health */}
            <div className="rounded-xl bg-surface-container-lowest shadow-level-1 p-space-6 flex flex-col gap-space-3">
              <div className="flex items-center justify-between gap-space-4 flex-wrap">
                <div className="flex items-center gap-space-3">
                  <span
                    className={`h-2.5 w-2.5 rounded-full shrink-0 ${
                      graphHealth?.status === "ok"
                        ? "bg-success"
                        : graphHealth?.status === "disabled"
                        ? "bg-on-surface-variant"
                        : "bg-error"
                    }`}
                  />
                  <span className="font-label-md text-label-md text-on-surface">
                    Neo4j: {health.isLoading ? "checking…" : graphHealth?.status ?? "unknown"}
                  </span>
                  {graphHealth?.reason && (
                    <span className="font-body-sm text-body-sm text-on-surface-variant">{graphHealth.reason}</span>
                  )}
                </div>
                <button
                  type="button"
                  className="h-9 px-4 rounded-lg bg-secondary text-on-secondary font-label-md text-label-md flex items-center gap-2 hover:opacity-90 transition-all disabled:opacity-50"
                  disabled={resync.isPending}
                  onClick={() => resync.mutate()}
                >
                  {resync.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                  Resync graph from database
                </button>
              </div>
              {resyncError && <p className="text-error text-body-sm">{resyncError}</p>}
              {resync.data && (
                <p className="font-body-sm text-body-sm text-success">
                  Rebuilt: {resync.data.facilities} facilities, {resync.data.cities} cities,{" "}
                  {resync.data.stock_edges} stock edges, {resync.data.routes} routes,{" "}
                  {resync.data.donor_aggregate_edges} donor-aggregate edges.
                </p>
              )}
            </div>

            {/* Create route */}
            <div className="rounded-xl bg-surface-container-lowest shadow-level-1 p-space-6 flex flex-col gap-space-3">
              <h2 className="font-title-lg text-title-lg text-on-surface">Add transport route</h2>
              <div className="flex flex-col md:flex-row gap-space-3 md:items-end flex-wrap">
                <label className="flex flex-col gap-1 min-w-[220px]">
                  <span className="font-label-sm text-label-sm text-on-surface-variant">Origin facility</span>
                  <select
                    className="h-10 px-3 rounded-lg bg-surface-container-low text-on-surface font-body-sm text-body-sm outline-none border border-border-subtle focus:ring-2 focus:ring-primary/40 focus:border-primary"
                    value={originId}
                    onChange={(e) => setOriginId(e.target.value)}
                  >
                    <option value="">Select…</option>
                    {(directory.data ?? []).map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.name ?? f.id.slice(0, 8)} {f.city ? `(${f.city})` : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 min-w-[220px]">
                  <span className="font-label-sm text-label-sm text-on-surface-variant">Destination facility</span>
                  <select
                    className="h-10 px-3 rounded-lg bg-surface-container-low text-on-surface font-body-sm text-body-sm outline-none border border-border-subtle focus:ring-2 focus:ring-primary/40 focus:border-primary"
                    value={destId}
                    onChange={(e) => setDestId(e.target.value)}
                  >
                    <option value="">Select…</option>
                    {(directory.data ?? []).map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.name ?? f.id.slice(0, 8)} {f.city ? `(${f.city})` : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 w-32">
                  <span className="font-label-sm text-label-sm text-on-surface-variant">Distance km (optional)</span>
                  <input
                    className="h-10 px-3 rounded-lg bg-surface-container-low text-on-surface font-body-sm text-body-sm outline-none border border-border-subtle focus:ring-2 focus:ring-primary/40 focus:border-primary"
                    value={distanceOverride}
                    onChange={(e) => setDistanceOverride(e.target.value)}
                    placeholder="auto"
                    inputMode="decimal"
                  />
                </label>
                <label className="flex flex-col gap-1 w-32">
                  <span className="font-label-sm text-label-sm text-on-surface-variant">Transit min (optional)</span>
                  <input
                    className="h-10 px-3 rounded-lg bg-surface-container-low text-on-surface font-body-sm text-body-sm outline-none border border-border-subtle focus:ring-2 focus:ring-primary/40 focus:border-primary"
                    value={transitOverride}
                    onChange={(e) => setTransitOverride(e.target.value)}
                    placeholder="auto"
                    inputMode="numeric"
                  />
                </label>
                <button
                  type="button"
                  className="h-10 px-5 rounded-lg bg-primary hover:bg-primary-container text-on-primary font-label-md text-label-md transition-all disabled:opacity-50 flex items-center gap-2"
                  disabled={!originId || !destId || originId === destId || create.isPending}
                  onClick={() => {
                    setCreateError("");
                    create.mutate();
                  }}
                >
                  {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                  Add route
                </button>
              </div>
              {createError && <p className="text-error text-body-sm">{createError}</p>}
              <p className="font-body-sm text-body-sm text-on-surface-variant">
                When distance/transit are left blank, they are computed for real from each facility's
                recorded coordinates -- never invented.
              </p>
            </div>

            {/* Routes table */}
            <div className="rounded-xl bg-surface-container-lowest shadow-level-1 overflow-hidden">
              <div className="p-space-6 pb-0">
                <h2 className="font-title-lg text-title-lg text-on-surface">Routes ({routes.data?.length ?? 0})</h2>
              </div>
              {actionError && <p className="text-error text-body-sm px-space-6 pt-space-3">{actionError}</p>}
              {routes.isLoading && <p className="px-space-6 py-space-6 text-on-surface-variant">Loading…</p>}
              {routes.isError && (
                <p className="px-space-6 py-space-6 text-error">{toApiError(routes.error).detail}</p>
              )}
              <div className="overflow-x-auto">
                <table className="w-full text-left mt-space-4">
                  <thead className="bg-surface-container-low">
                    <tr>
                      <th className="px-space-6 py-space-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                        Route
                      </th>
                      <th className="px-space-6 py-space-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                        Distance
                      </th>
                      <th className="px-space-6 py-space-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                        Status
                      </th>
                      <th className="px-space-6 py-space-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
                        Actions
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-subtle">
                    {(routes.data ?? []).map((r: TransportRoute) => (
                      <tr key={r.id} className="align-top">
                        <td className="px-space-6 py-space-4 font-body-sm text-body-sm text-on-surface">
                          {facilityLabel(r.origin_facility_id)} ↔ {facilityLabel(r.destination_facility_id)}
                        </td>
                        <td className="px-space-6 py-space-4 font-body-sm text-body-sm text-on-surface">
                          {r.distance_km.toFixed(1)} km / {r.transit_minutes} min
                        </td>
                        <td className="px-space-6 py-space-4">
                          <span
                            className={`px-2 py-1 rounded-full text-label-sm font-semibold ${
                              r.status === "OPERATIONAL" ? "bg-success/15 text-success" : "bg-error/15 text-error"
                            }`}
                          >
                            {r.status}
                          </span>
                          {r.disrupted_reason && (
                            <p className="font-body-sm text-body-sm text-on-surface-variant mt-1">
                              {r.disrupted_reason}
                            </p>
                          )}
                        </td>
                        <td className="px-space-6 py-space-4 min-w-[220px]">
                          {r.status === "OPERATIONAL" ? (
                            disruptingId === r.id ? (
                              <div className="flex flex-col gap-2">
                                <input
                                  className="h-9 px-3 rounded-lg bg-surface-container-low text-on-surface font-body-sm text-body-sm outline-none border border-border-subtle focus:ring-2 focus:ring-primary/40 focus:border-primary"
                                  placeholder="Reason (min 3 chars)"
                                  value={reasonDraft}
                                  onChange={(e) => setReasonDraft(e.target.value)}
                                />
                                <div className="flex gap-2">
                                  <button
                                    type="button"
                                    className="h-8 px-3 rounded-lg bg-error text-on-error font-label-sm text-label-sm disabled:opacity-50 flex items-center gap-1"
                                    disabled={reasonDraft.trim().length < 3 || disrupt.isPending}
                                    onClick={() => disrupt.mutate({ id: r.id, reason: reasonDraft.trim() })}
                                  >
                                    {disrupt.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
                                    Confirm disrupt
                                  </button>
                                  <button
                                    type="button"
                                    className="h-8 px-3 rounded-lg bg-surface-container text-on-surface-variant font-label-sm text-label-sm"
                                    onClick={() => {
                                      setDisruptingId(null);
                                      setReasonDraft("");
                                    }}
                                  >
                                    Cancel
                                  </button>
                                </div>
                              </div>
                            ) : (
                              <button
                                type="button"
                                className="h-8 px-3 rounded-lg bg-error/10 text-error font-label-sm text-label-sm hover:bg-error/20 transition-all"
                                onClick={() => {
                                  setDisruptingId(r.id);
                                  setReasonDraft("");
                                  setActionError("");
                                }}
                              >
                                Disrupt
                              </button>
                            )
                          ) : (
                            <button
                              type="button"
                              className="h-8 px-3 rounded-lg bg-success/10 text-success font-label-sm text-label-sm hover:bg-success/20 transition-all disabled:opacity-50 flex items-center gap-1"
                              disabled={recover.isPending}
                              onClick={() => recover.mutate(r.id)}
                            >
                              {recover.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
                              Recover
                            </button>
                          )}
                          {lastAction[r.id] && <ImpactPanel action={lastAction[r.id]} />}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!routes.isLoading && (routes.data ?? []).length === 0 && (
                <p className="px-space-6 py-space-8 text-center text-on-surface-variant">
                  No transport routes yet. Add one above.
                </p>
              )}
            </div>
          </div>
        </main>
      </div>
    </>
  );
}
