/**
 * Hospital Emergency Queue & Inventory Dashboard
 * Layout/markup originally converted 1:1 from the Google Stitch export
 * (hospital_dashboard). Phase 3 wired KPIs, blood inventory, the
 * request-management board, the live donor map and notifications to the
 * real backend. Phase 5 adds real Alerts / Recent Activity / Transport
 * summary panels (all from real endpoints — nothing here is invented) and
 * replaces the original bespoke, fixed-width (`w-64`/`ml-64`) sidebar with
 * the shared `<AppShell>` (`src/components/shell/`), which is the only way
 * this screen — including the EMERGENCY SOS button and the inventory
 * quick-action — is actually usable on a phone: the original layout had no
 * responsive behavior at all below desktop width.
 */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  BadgeCheck,
  BarChart3,
  Boxes,
  GitBranch,
  LayoutDashboard,
  Route,
  Users,
  ClipboardCheck,
} from "lucide-react";

import AppShell from "../components/shell/AppShell";
import type { ShellNavItem } from "../components/shell/navTypes";
import BloodRequestModal from "../components/BloodRequestModal";
import RequestTrackDrawer from "../components/RequestTrackDrawer";
import EmergencyTrackDrawer from "../components/EmergencyTrackDrawer";
import SosModal from "../components/SosModal";
import DonorMap from "../components/DonorMap";
import HospitalInventoryModal from "../components/HospitalInventoryModal";
import { toApiError } from "../lib/api";
import {
  BLOOD_LABEL,
  getHospitalActivity,
  getHospitalRoutes,
  getMapData,
  listHospitalEmergencies,
  listHospitalRequests,
  getInventory,
  type HospitalRequestItem,
  isOpenRequestStatus,
} from "../lib/domain";
import { useLiveFallbackInterval } from "../lib/realtime";

const NAV_ITEMS: ShellNavItem[] = [
  { label: "Dashboard", to: "/hospital", icon: LayoutDashboard, end: true },
  { label: "Inventory", to: "/hospital/inventory", icon: Boxes },
  { label: "Resource Pathways", to: "/hospital/resource-pathways", icon: Route },
  { label: "Donors & Logistics", to: "/hospital/donors", icon: Users },
  { label: "Analytics", to: "/hospital/analytics-mobile", icon: BarChart3 },
  { label: "Facility Verification", to: "/hospital/verification", icon: BadgeCheck },
  { label: "Donation Verification", to: "/hospital/donation-reviews", icon: ClipboardCheck },
  { label: "Simulation", to: "/simulation", icon: GitBranch },
];

const CRITICAL_BELOW = 10;

function stockLabel(units: number): { text: string; cls: string } {
  if (units < CRITICAL_BELOW) return { text: "Critical", cls: "text-warning font-bold uppercase tracking-wider" };
  if (units < 30) return { text: "Low", cls: "text-on-surface-variant" };
  return { text: "Optimal", cls: "text-on-surface-variant" };
}

const ACTIVITY_ICON: Record<string, string> = { inventory: "inventory_2", request: "fact_check" };

export default function HospitalDashboard() {
  const [showNew, setShowNew] = useState(false);
  const [showSos, setShowSos] = useState(false);
  const [showInventory, setShowInventory] = useState(false);
  const [trackId, setTrackId] = useState<string | null>(null);
  const [emTrackId, setEmTrackId] = useState<string | null>(null);

  const inventory = useQuery({ queryKey: ["hospital", "inventory"], queryFn: getInventory });
  const requests = useQuery({
    queryKey: ["hospital", "requests"],
    queryFn: () => listHospitalRequests(true),
    // Phase F: live updates arrive over the event stream; this interval only
    // applies while that stream is down (graceful degradation).
    refetchInterval: useLiveFallbackInterval(30_000),
  });
  const emergencies = useQuery({
    queryKey: ["hospital", "emergencies"],
    queryFn: listHospitalEmergencies,
    // Phase F: live updates arrive over the event stream; this interval only
    // applies while that stream is down (graceful degradation).
    refetchInterval: useLiveFallbackInterval(30_000),
  });
  const map = useQuery({ queryKey: ["map", "data", "all"], queryFn: () => getMapData() });
  const routes = useQuery({ queryKey: ["hospital", "routes"], queryFn: getHospitalRoutes });
  const activity = useQuery({
    queryKey: ["hospital", "activity"],
    queryFn: () => getHospitalActivity(8),
  });

  const totalUnits = (inventory.data ?? []).reduce((n, i) => n + i.units, 0);
  const criticalTypes = (inventory.data ?? []).filter((i) => i.units < CRITICAL_BELOW);
  const disruptedRoutes = (routes.data ?? []).filter((r) => r.status === "DISRUPTED");

  const columns = useMemo(() => {
    const all = requests.data ?? [];
    const open = all.filter((r) => isOpenRequestStatus(r.status));
    const pending = open.filter((r) => !r.hospital_status || r.hospital_status === "DECLINED");
    const searching = open.filter(
      (r) => r.hospital_status === "ACKNOWLEDGED" || r.hospital_status === "PREPARING",
    );
    const matched = open.filter(
      (r) => r.hospital_status === "DISPATCHED" || r.hospital_status === "ALLOCATED",
    );
    const verified = all.filter((r) => r.status === "FULFILLED");
    return { pending, searching, matched, verified };
  }, [requests.data]);

  const criticalUnhandled = useMemo(
    () => columns.pending.filter((r) => r.urgency === "CRITICAL"),
    [columns.pending],
  );

  const card = (r: HospitalRequestItem) => (
    <div
      key={r.id}
      onClick={() => setTrackId(r.id)}
      className="bg-surface p-4 rounded-xl border border-border-subtle shadow-sm hover:shadow-md transition-shadow cursor-pointer"
    >
      <div className="flex justify-between mb-2">
        <span className="text-[10px] font-bold bg-primary-fixed text-on-primary-fixed-variant px-2 py-0.5 rounded uppercase">
          {r.urgency}
        </span>
        <span className="text-[10px] font-medium text-on-surface-variant">
          #{r.id.slice(0, 6).toUpperCase()}
        </span>
      </div>
      <h5 className="font-bold text-body-sm mb-1">
        {BLOOD_LABEL[r.blood_type]} ·{" "}
        {r.fulfilled_units > 0 ? `${r.fulfilled_units}/${r.units_needed}` : r.units_needed} unit(s)
      </h5>
      <p className="text-[12px] text-on-surface-variant mb-3">
        {r.patient_name ?? "Patient"}{r.hospital_name ? ` · ${r.hospital_name}` : ""}
      </p>
      <div className="flex items-center justify-between text-[11px] text-on-surface-variant">
        <span className="flex items-center gap-1">
          <span aria-hidden="true" className="material-symbols-outlined text-[14px]">volunteer_activism</span>
          {r.accepted_count} accepted
        </span>
        {r.distance_km != null && (
          <span className="flex items-center gap-1">
            <span aria-hidden="true" className="material-symbols-outlined text-[14px]">location_on</span>
            {r.distance_km} km
          </span>
        )}
      </div>
    </div>
  );

  return (
    <>
      <AppShell
        title="Hospital Command Center"
        roleLabel="Hospital"
        navItems={NAV_ITEMS}
        actions={
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowNew(true)}
              className="hidden sm:inline-flex h-9 px-3 rounded-lg bg-secondary text-on-secondary font-label-md text-label-sm items-center hover:bg-secondary-container transition-all active:scale-95"
            >
              New Request
            </button>
            <button
              onClick={() => setShowSos(true)}
              className="h-9 px-3 rounded-lg bg-primary text-on-primary font-bold text-label-sm hover:opacity-90 active:scale-95 transition-all flex items-center gap-1.5"
            >
              <span aria-hidden="true"
                className="material-symbols-outlined text-[16px]"
                style={{ fontVariationSettings: "'FILL' 1" }}
              >
                emergency
              </span>
              <span className="hidden sm:inline">EMERGENCY SOS</span>
              <span className="sm:hidden">SOS</span>
            </button>
          </div>
        }
      >
        <div className="flex flex-col gap-gutter max-w-container-max mx-auto">
          <p className="font-body-md text-body-md text-on-surface-variant">
            Real-time vital analytics for your facility
          </p>

          <div className="grid grid-cols-2 lg:grid-cols-4 gap-gutter">
            <div className="bg-surface p-6 rounded-xl border border-border-subtle shadow-sm">
              <div className="p-2 bg-error-container rounded-lg w-fit mb-4">
                <span aria-hidden="true" className="material-symbols-outlined text-primary" style={{ fontVariationSettings: "'FILL' 1" }}>medical_services</span>
              </div>
              <p className="font-label-md text-label-md text-on-surface-variant">Active Emergencies</p>
              <h3 className="font-headline-lg text-headline-lg text-text-main mt-1">{emergencies.data?.length ?? "—"}</h3>
            </div>
            <div className="bg-surface p-6 rounded-xl border border-border-subtle shadow-sm">
              <div className="flex items-start justify-between mb-4">
                <div className="p-2 bg-secondary-fixed rounded-lg w-fit">
                  <span aria-hidden="true" className="material-symbols-outlined text-secondary" style={{ fontVariationSettings: "'FILL' 1" }}>bloodtype</span>
                </div>
                <button onClick={() => setShowInventory(true)} className="text-secondary font-bold text-label-sm hover:underline">Manage</button>
              </div>
              <p className="font-label-md text-label-md text-on-surface-variant">Blood Units in Stock</p>
              <h3 className="font-headline-lg text-headline-lg text-text-main mt-1">{inventory.data ? totalUnits.toLocaleString() : "—"}</h3>
            </div>
            <div className="bg-surface p-6 rounded-xl border border-border-subtle shadow-sm">
              <div className="p-2 bg-tertiary-fixed rounded-lg w-fit mb-4">
                <span aria-hidden="true" className="material-symbols-outlined text-tertiary" style={{ fontVariationSettings: "'FILL' 1" }}>fact_check</span>
              </div>
              <p className="font-label-md text-label-md text-on-surface-variant">Unhandled Requests</p>
              <h3 className="font-headline-lg text-headline-lg text-text-main mt-1">{requests.data ? columns.pending.length : "—"}</h3>
              <div className="mt-2 text-[12px] text-on-surface-variant">{requests.data?.length ?? 0} incoming total</div>
            </div>
            <div className="bg-surface p-6 rounded-xl border border-border-subtle shadow-sm">
              <div className="p-2 bg-surface-container-highest rounded-lg w-fit mb-4">
                <span aria-hidden="true" className="material-symbols-outlined text-on-background" style={{ fontVariationSettings: "'FILL' 1" }}>groups</span>
              </div>
              <p className="font-label-md text-label-md text-on-surface-variant">Donors on Map</p>
              <h3 className="font-headline-lg text-headline-lg text-text-main mt-1">{map.data ? map.data.donors.length : "—"}</h3>
            </div>
          </div>

          {/* Alerts — real thresholds on real data, nothing simulated. */}
          <section className="bg-surface rounded-xl border border-border-subtle shadow-sm p-5">
            <h3 className="font-title-lg text-title-lg text-on-surface mb-3 flex items-center gap-2">
              <span aria-hidden="true" className="material-symbols-outlined text-warning">warning</span>
              Alerts
            </h3>
            {criticalTypes.length === 0 && criticalUnhandled.length === 0 && disruptedRoutes.length === 0 ? (
              <p className="text-body-sm text-on-surface-variant">No active alerts right now.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {criticalTypes.map((i) => (
                  <li key={i.blood_type} className="flex items-center justify-between text-body-sm bg-error-container/20 rounded-lg px-3 py-2">
                    <span className="text-on-surface">Critical stock: {BLOOD_LABEL[i.blood_type]} ({i.units} unit(s))</span>
                    <button onClick={() => setShowInventory(true)} className="text-secondary text-label-sm font-semibold hover:underline shrink-0">Manage</button>
                  </li>
                ))}
                {criticalUnhandled.map((r) => (
                  <li key={r.id} className="flex items-center justify-between text-body-sm bg-error-container/20 rounded-lg px-3 py-2">
                    <span className="text-on-surface">
                      Unhandled critical request — {BLOOD_LABEL[r.blood_type]}, {r.units_needed} unit(s)
                    </span>
                    <button onClick={() => setTrackId(r.id)} className="text-secondary text-label-sm font-semibold hover:underline shrink-0">View</button>
                  </li>
                ))}
                {disruptedRoutes.map((r) => (
                  <li key={r.id} className="flex items-center justify-between text-body-sm bg-warning/10 rounded-lg px-3 py-2">
                    <span className="text-on-surface">
                      Transport route disrupted — {r.partner_facility_name ?? "Unnamed facility"}
                    </span>
                    <Link to="/hospital/resource-pathways" className="text-secondary text-label-sm font-semibold hover:underline shrink-0">Details</Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section>
            <div className="flex items-center justify-between mb-6">
              <h3 className="font-headline-md text-headline-md text-text-main">Blood Inventory</h3>
              <button onClick={() => setShowInventory(true)} className="text-secondary font-label-md text-label-md flex items-center gap-1 hover:underline">
                View Full Inventory <span aria-hidden="true" className="material-symbols-outlined text-[18px]">chevron_right</span>
              </button>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-4">
              {inventory.isLoading && <p className="text-sm text-on-surface-variant col-span-full">Loading inventory…</p>}
              {/* Phase 19: a failed fetch previously fell straight through
                  to the empty-data render below (an empty grid, identical
                  to "zero units of everything") -- real finding from the
                  hospital/offline E2E audit; see HospitalInventoryModal for
                  the same fix applied there. */}
              {inventory.isError && (
                <div className="col-span-full text-center py-4">
                  <p className="text-error text-sm mb-2">Couldn't load inventory.</p>
                  <button
                    type="button"
                    className="text-secondary text-sm font-label-md hover:underline"
                    onClick={() => inventory.refetch()}
                  >
                    Retry
                  </button>
                </div>
              )}
              {!inventory.isError && (inventory.data ?? []).map((i) => {
                const s = stockLabel(i.units);
                const h = Math.min(95, Math.round((i.units / 200) * 100));
                return (
                  <div key={i.blood_type} className={`bg-surface p-4 rounded-xl border border-border-subtle border-l-4 ${i.units < 10 ? "border-l-warning" : "border-l-primary"} shadow-sm`}>
                    <p className={`font-bold text-xl ${i.units < 10 ? "text-warning" : "text-primary"}`}>{BLOOD_LABEL[i.blood_type]}</p>
                    <div className="mt-2 text-2xl font-bold">{i.units}</div>
                    <div className={`mt-1 text-[12px] ${s.cls}`}>{s.text}</div>
                    <div className="mt-3 relative h-20 w-full bg-surface-container-low rounded overflow-hidden">
                      <div className={`absolute bottom-0 w-full ${i.units < 10 ? "bg-warning" : "bg-primary"} opacity-20`} style={{ height: `${Math.max(h, 4)}%` }}></div>
                    </div>
                  </div>
                );
              })}
            </div>
          </section>

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-gutter">
            <section className="lg:col-span-8 space-y-4">
              <h3 className="font-headline-md text-headline-md text-text-main">Request Management</h3>
              <div
                className="flex gap-4 overflow-x-auto pb-4 kanban-board"
                tabIndex={0}
                role="region"
                aria-label="Request management board, scrollable horizontally"
              >
                {([
                  ["PENDING", "bg-slate-400", columns.pending],
                  ["SEARCHING", "bg-secondary animate-pulse", columns.searching],
                  ["MATCHED", "bg-tertiary", columns.matched],
                  ["VERIFIED", "bg-success", columns.verified],
                ] as const).map(([label, dot, items]) => (
                  <div key={label} className="min-w-[280px] flex-1 space-y-3">
                    <div className="flex items-center justify-between px-2">
                      <h4 className="font-label-md text-label-md text-on-surface-variant flex items-center gap-2">
                        <span className={`w-2 h-2 rounded-full ${dot}`}></span> {label} ({items.length})
                      </h4>
                    </div>
                    <div className="space-y-3 kanban-column max-h-[600px] overflow-y-auto pr-1" tabIndex={0} role="region" aria-label={`${label} requests`}>
                      {requests.isLoading && <p className="text-[12px] text-on-surface-variant px-2">Loading…</p>}
                      {requests.isError && (
                        <div className="px-2">
                          <p className="text-error text-[12px] mb-1">Couldn't load requests.</p>
                          <button
                            type="button"
                            className="text-secondary text-[12px] font-label-md hover:underline"
                            onClick={() => requests.refetch()}
                          >
                            Retry
                          </button>
                        </div>
                      )}
                      {!requests.isLoading && !requests.isError && items.length === 0 && (
                        <p className="text-[12px] text-on-surface-variant px-2">Nothing here.</p>
                      )}
                      {!requests.isError && items.map(card)}
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <div className="lg:col-span-4 space-y-gutter">
              <div className="bg-surface rounded-xl border border-border-subtle shadow-sm overflow-hidden flex flex-col h-[320px]">
                <div className="px-5 py-4 flex items-center justify-between border-b border-border-subtle">
                  <h4 className="font-label-md text-label-md text-on-surface">Active Donors (Live)</h4>
                  <span className="text-[11px] font-bold text-success flex items-center gap-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-success"></span>
                    {map.isError ? "Couldn't load" : (map.data ? map.data.donors.length : "—")} Online
                  </span>
                </div>
                <div className="flex-1 relative bg-surface-container-low">
                  <DonorMap />
                </div>
              </div>

              <div className="bg-surface rounded-xl border border-border-subtle shadow-sm overflow-hidden flex flex-col">
                <div className="px-5 py-4 border-b border-border-subtle">
                  <h4 className="font-label-md text-label-md text-on-surface">Incoming Emergencies</h4>
                </div>
                <div className="divide-y divide-border-subtle">
                  {emergencies.isError && (
                    <div role="alert" className="p-4 text-[12px] text-error flex items-center justify-between gap-3">
                      <span>Couldn't load incoming emergencies.</span>
                      <button type="button" className="underline flex-shrink-0" onClick={() => emergencies.refetch()}>
                        Retry
                      </button>
                    </div>
                  )}
                  {emergencies.data && emergencies.data.length === 0 && (
                    <div className="p-4 text-[12px] text-on-surface-variant">No active emergencies in your area.</div>
                  )}
                  {(emergencies.data ?? []).map((e) => (
                    <button key={e.id} onClick={() => setEmTrackId(e.id)} className="w-full text-left p-4 hover:bg-surface-container-lowest transition-colors flex items-center gap-3">
                      <div className="w-10 h-10 rounded-full bg-error-container text-primary flex items-center justify-center">
                        <span aria-hidden="true" className="material-symbols-outlined text-[20px]">e911_emergency</span>
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="font-bold text-body-sm truncate">{e.blood_type ? BLOOD_LABEL[e.blood_type] : "Any type"} · {e.status.replace("_", " ")}</p>
                        <p className="text-[12px] text-on-surface-variant truncate">{e.location_text || "Location not given"}</p>
                      </div>
                    </button>
                  ))}
                </div>
              </div>

              {/* Transport / resources — real TransportRoute rows touching
                  this facility (GET /hospitals/me/routes). */}
              <div className="bg-surface rounded-xl border border-border-subtle shadow-sm overflow-hidden flex flex-col">
                <div className="px-5 py-4 border-b border-border-subtle flex items-center justify-between">
                  <h4 className="font-label-md text-label-md text-on-surface">Transport & Resources</h4>
                  <Link to="/hospital/resource-pathways" className="text-secondary text-label-sm font-semibold hover:underline">
                    Find stock
                  </Link>
                </div>
                <div className="p-4 flex flex-col gap-2">
                  {routes.isLoading && <p className="text-[12px] text-on-surface-variant">Loading…</p>}
                  {routes.isError && (
                    <p role="alert" className="text-[12px] text-error flex items-center justify-between gap-3">
                      <span>{toApiError(routes.error).detail}</span>
                      <button type="button" className="underline flex-shrink-0" onClick={() => routes.refetch()}>
                        Retry
                      </button>
                    </p>
                  )}
                  {routes.data && routes.data.length === 0 && (
                    <p className="text-[12px] text-on-surface-variant">No transport links recorded to your facility yet.</p>
                  )}
                  {routes.data && routes.data.length > 0 && (
                    <p className="text-[12px] text-on-surface-variant">
                      {routes.data.length - disruptedRoutes.length} operational · {disruptedRoutes.length} disrupted
                      {" "}of {routes.data.length} connected route(s)
                    </p>
                  )}
                </div>
              </div>

              {/* Recent activity — merged real feed (GET /hospitals/me/activity). */}
              <div className="bg-surface rounded-xl border border-border-subtle shadow-sm overflow-hidden flex flex-col">
                <div className="px-5 py-4 border-b border-border-subtle">
                  <h4 className="font-label-md text-label-md text-on-surface">Recent Activity</h4>
                </div>
                <div className="divide-y divide-border-subtle">
                  {activity.isLoading && <div className="p-4 text-[12px] text-on-surface-variant">Loading…</div>}
                  {activity.isError && (
                    <div role="alert" className="p-4 text-[12px] text-error flex items-center justify-between gap-3">
                      <span>Couldn't load recent activity.</span>
                      <button type="button" className="underline flex-shrink-0" onClick={() => activity.refetch()}>
                        Retry
                      </button>
                    </div>
                  )}
                  {activity.data && activity.data.length === 0 && (
                    <div className="p-4 text-[12px] text-on-surface-variant">Nothing recorded yet.</div>
                  )}
                  {(activity.data ?? []).map((a, idx) => (
                    <div key={idx} className="p-3 flex items-start gap-3">
                      <span aria-hidden="true" className="material-symbols-outlined text-[18px] text-on-surface-variant mt-0.5">
                        {ACTIVITY_ICON[a.kind] ?? "history"}
                      </span>
                      <div className="min-w-0">
                        <p className="text-[12px] text-on-surface truncate">{a.summary}</p>
                        <p className="text-[11px] text-on-surface-variant">{new Date(a.created_at).toLocaleString()}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>

          <div className="pt-4 pb-8 border-t border-border-subtle flex flex-col md:flex-row justify-between items-center gap-4 text-on-surface-variant">
            <div className="flex items-center gap-2">
              <span aria-hidden="true" className="material-symbols-outlined text-[18px]">verified</span>
              <span className="font-body-sm text-body-sm">HIPAA Compliant Session</span>
            </div>
            <div className="font-body-sm text-body-sm">LifeLink Hospital Terminal</div>
          </div>
        </div>
      </AppShell>

      <BloodRequestModal open={showNew} onClose={() => setShowNew(false)} />
      <SosModal open={showSos} onClose={() => setShowSos(false)} />
      <HospitalInventoryModal open={showInventory} onClose={() => setShowInventory(false)} />
      <RequestTrackDrawer requestId={trackId} onClose={() => setTrackId(null)} mode="hospital" />
      <EmergencyTrackDrawer emergencyId={emTrackId} onClose={() => setEmTrackId(null)} mode="responder" />
    </>
  );
}
