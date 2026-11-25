/**
 * Admin — Shipments & Chain of Custody (Phase D)
 *
 * Read-only oversight across every facility's shipments: GET /shipments
 * and GET /shipments/{id}/timeline, the same endpoints a HOSPITAL caller
 * uses, with the ADMIN role bypassing the origin/destination-only
 * visibility check (app/api/routes/shipments.py::_assert_is_party) --
 * nothing here is a separate admin-only dataset or a second API surface.
 * No action buttons: an admin can see active/delayed/failed/completed
 * shipments, their full chain-of-custody timeline, and route condition,
 * but dispatching/receiving/cancelling stays a facility action (Section 21's
 * authorization matrix, adapted to this app's HOSPITAL/ADMIN roles) --
 * mirrors how /admin/transport-routes lets admins disrupt/recover routes
 * but never lets them act as a hospital's own inventory.
 *
 * Phase E exception, deliberately: reconciling a FAILED shipment. The
 * backend authorizes the origin facility OR an admin for that one
 * decision (e.g. when the origin can't or won't), so the same attested
 * ReconcilePanel the hospital sees is shown here too.
 */
import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Menu } from "lucide-react";

import NotificationsBell from "../../components/NotificationsBell";
import { useAuth } from "../../lib/auth";
import { toApiError } from "../../lib/api";
import { ROLE_LABEL } from "../../lib/roles";
import {
  BLOOD_LABEL,
  componentLabel,
  getShipment,
  getShipmentTimeline,
  listShipments,
  type ShipmentStatus,
} from "../../lib/domain";
import { ReconcilePanel } from "../hospital/Shipments";

import { ADMIN_NAV, ACCOUNT_NAV } from "./adminNav";
import MobileNavDrawer from "../../components/MobileNavDrawer";

const STATUS_FILTERS: { label: string; value: ShipmentStatus | undefined }[] = [
  { label: "All", value: undefined },
  { label: "Created", value: "CREATED" },
  { label: "Dispatched", value: "DISPATCHED" },
  { label: "In transit", value: "IN_TRANSIT" },
  { label: "Arrived", value: "ARRIVED" },
  { label: "Received", value: "RECEIVED" },
  { label: "Cancelled", value: "CANCELLED" },
  { label: "Failed", value: "FAILED" },
];

const STATUS_STYLE: Record<ShipmentStatus, string> = {
  CREATED: "bg-surface-container-high text-on-surface",
  DISPATCHED: "bg-secondary-fixed text-on-secondary-fixed",
  IN_TRANSIT: "bg-warning/15 text-warning",
  ARRIVED: "bg-secondary text-on-secondary",
  RECEIVED: "bg-success/15 text-success",
  CANCELLED: "bg-error/10 text-error",
  FAILED: "bg-error text-on-primary",
};

const EVENT_LABEL: Record<string, string> = {
  created: "Created — units reserved at origin",
  dispatched: "Dispatched",
  in_transit: "In transit",
  delayed: "Delay reported",
  arrived: "Arrived at destination",
  received: "Received — destination inventory updated",
  cancelled: "Cancelled",
  failed: "Failed",
};

export default function AdminShipments() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [statusFilter, setStatusFilter] = useState<ShipmentStatus | undefined>(undefined);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["admin", "shipments", statusFilter],
    queryFn: () => listShipments(statusFilter ? { status: statusFilter } : undefined),
  });
  const detail = useQuery({
    queryKey: ["admin", "shipments", "detail", selectedId],
    queryFn: () => getShipment(selectedId as string),
    enabled: !!selectedId,
  });
  const timeline = useQuery({
    queryKey: ["admin", "shipments", "timeline", selectedId],
    queryFn: () => getShipmentTimeline(selectedId as string),
    enabled: !!selectedId,
  });

  const shipments = list.data ?? [];
  const activeCount = shipments.filter((s) => !["RECEIVED", "CANCELLED", "FAILED"].includes(s.status)).length;
  const delayedCount = shipments.filter((s) => s.delayed_at).length;
  const failedCount = shipments.filter((s) => s.status === "FAILED").length;

  async function handleLogout() {
    setLogoutBusy(true);
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <>
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
            {logoutBusy ? <Loader2 className="h-[18px] w-[18px] animate-spin" /> : <span aria-hidden="true" className="material-symbols-outlined text-[18px]">logout</span>}
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
                <span className="font-body-sm text-body-sm text-on-surface-variant truncate">{user?.email}</span>
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
            </button>
          </>
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
              <span className="font-title-lg text-title-lg text-on-surface truncate">Shipments</span>
              <span className="font-label-sm text-label-sm text-on-surface-variant hidden sm:block">
                Cross-facility chain of custody oversight
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
            <div>
              <h1 className="font-headline-lg text-headline-lg text-on-surface">
                Shipments &amp; chain of custody
              </h1>
              <p className="font-body-md text-body-md text-on-surface-variant">
                Every facility-to-facility shipment, its real lifecycle state, and its full custody
                timeline — read-only; dispatch/receive/cancel stays a facility action.
              </p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-space-4">
              <div className="bg-surface-container-lowest p-space-4 rounded-xl shadow-level-1">
                <p className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">Active</p>
                <p className="font-headline-md text-headline-md text-on-surface mt-1">{activeCount}</p>
              </div>
              <div className="bg-surface-container-lowest p-space-4 rounded-xl shadow-level-1">
                <p className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">Delayed (ever)</p>
                <p className="font-headline-md text-headline-md text-warning mt-1">{delayedCount}</p>
              </div>
              <div className="bg-surface-container-lowest p-space-4 rounded-xl shadow-level-1">
                <p className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">Failed</p>
                <p className="font-headline-md text-headline-md text-error mt-1">{failedCount}</p>
              </div>
            </div>

            <div className="flex items-center gap-2 overflow-x-auto">
              {STATUS_FILTERS.map((f) => (
                <button
                  key={f.label}
                  type="button"
                  aria-pressed={statusFilter === f.value}
                  onClick={() => setStatusFilter(f.value)}
                  className={`px-3 py-1.5 rounded-full font-label-sm text-label-sm font-semibold whitespace-nowrap transition-all ${
                    statusFilter === f.value
                      ? "bg-primary text-on-primary shadow-sm"
                      : "bg-surface-container text-on-surface-variant hover:bg-surface-container-high"
                  }`}
                >
                  {f.label}
                </button>
              ))}
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-12 gap-space-6 items-start">
              <section className="xl:col-span-6 bg-surface-container-lowest rounded-xl shadow-level-1 overflow-hidden">
                {list.isLoading && (
                  <div className="flex items-center gap-2 text-on-surface-variant py-10 justify-center">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading shipments…
                  </div>
                )}
                {list.isError && (
                  <p role="alert" className="text-error text-body-sm p-4">{toApiError(list.error).detail}</p>
                )}
                {list.data && shipments.length === 0 && (
                  <p className="text-body-sm text-on-surface-variant p-6 text-center">No shipments match this filter.</p>
                )}
                {shipments.length > 0 && (
                  <table className="w-full text-left">
                    <thead className="bg-surface-container-low">
                      <tr>
                        <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">Shipment</th>
                        <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">Route</th>
                        <th className="px-4 py-3 font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-subtle">
                      {shipments.map((s) => (
                        <tr
                          key={s.id}
                          onClick={() => setSelectedId(s.id)}
                          aria-current={selectedId === s.id}
                          className={`cursor-pointer ${selectedId === s.id ? "bg-primary/5" : "hover:bg-surface-container-low"}`}
                        >
                          <td className="px-4 py-3">
                            <p className="font-data-mono text-label-sm text-on-surface">{s.shipment_code}</p>
                            <p className="text-[11px] text-on-surface-variant">{s.items.length} unit(s)</p>
                          </td>
                          <td className="px-4 py-3 text-body-sm text-on-surface">
                            {s.origin_facility_name ?? "Unknown"} → {s.destination_facility_name ?? "Unknown"}
                          </td>
                          <td className="px-4 py-3">
                            <span className={`px-2 py-1 rounded-full text-[11px] font-bold ${STATUS_STYLE[s.status]}`}>
                              {s.status.replace("_", " ")}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>

              <section className="xl:col-span-6">
                {!selectedId && (
                  <div className="bg-surface-container-lowest rounded-xl shadow-level-1 p-10 text-center text-on-surface-variant">
                    Select a shipment to see its detail and chain-of-custody timeline.
                  </div>
                )}
                {selectedId && detail.isLoading && (
                  <div className="flex items-center gap-2 text-on-surface-variant py-10 justify-center">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading…
                  </div>
                )}
                {selectedId && detail.data && (
                  <div className="flex flex-col gap-space-4">
                    <div className="bg-surface-container-lowest rounded-xl shadow-level-1 p-space-5">
                      <div className="flex items-start justify-between gap-3 flex-wrap">
                        <div>
                          <p className="font-data-mono text-body-sm text-on-surface-variant">{detail.data.shipment_code}</p>
                          <h2 className="font-title-lg text-title-lg text-on-surface mt-0.5">
                            {detail.data.origin_facility_name ?? "Unknown"} → {detail.data.destination_facility_name ?? "Unknown"}
                          </h2>
                        </div>
                        <span className={`px-3 py-1 rounded-full text-xs font-bold ${STATUS_STYLE[detail.data.status]}`}>
                          {detail.data.status.replace("_", " ")}
                        </span>
                      </div>
                      {detail.data.route_status && (
                        <p className="text-body-sm mt-3">
                          Route condition:{" "}
                          <span className={detail.data.route_status === "DISRUPTED" ? "text-error font-semibold" : "text-success font-semibold"}>
                            {detail.data.route_status === "DISRUPTED" ? "Disrupted" : "Operational"}
                          </span>
                        </p>
                      )}
                      {detail.data.delay_reason && (
                        <p className="text-body-sm text-warning mt-1">Last delay: {detail.data.delay_reason}</p>
                      )}
                      {detail.data.cancel_reason && (
                        <p className="text-body-sm text-error mt-1">Cancelled: {detail.data.cancel_reason}</p>
                      )}
                      {detail.data.failure_reason && (
                        <p className="text-body-sm text-error mt-1">Failed: {detail.data.failure_reason}</p>
                      )}
                      {detail.data.reconciliation_status && (
                        <p className="text-body-sm mt-1">
                          Reconciliation:{" "}
                          <span className={detail.data.reconciliation_status === "REQUIRED" ? "text-warning font-semibold" : "font-semibold"}>
                            {detail.data.reconciliation_status.replace(/_/g, " ").toLowerCase()}
                          </span>
                          {detail.data.reconciliation_note && ` — ${detail.data.reconciliation_note}`}
                        </p>
                      )}
                      <ul className="flex flex-wrap gap-1.5 mt-3">
                        {detail.data.items.map((it) => (
                          <li key={it.id} className="px-2 py-1 rounded bg-surface-container font-data-mono text-[11px]">
                            {it.unit_code ?? it.blood_unit_id.slice(0, 8)} · {it.blood_type ? BLOOD_LABEL[it.blood_type] : "?"}{" "}
                            {componentLabel(it.component)}
                          </li>
                        ))}
                      </ul>
                    </div>
                    {detail.data.reconciliation_status === "REQUIRED" && (
                      <ReconcilePanel
                        shipment={detail.data}
                        onDone={() => qc.invalidateQueries({ queryKey: ["admin", "shipments"] })}
                      />
                    )}
                    <div className="bg-surface-container-lowest rounded-xl shadow-level-1 p-space-5">
                      <h3 className="font-title-md text-title-md text-on-surface mb-3">Chain-of-custody timeline</h3>
                      {timeline.isLoading && (
                        <div className="flex items-center gap-2 text-on-surface-variant py-4 justify-center">
                          <Loader2 className="h-4 w-4 animate-spin" /> Loading…
                        </div>
                      )}
                      {timeline.data && timeline.data.length === 0 && (
                        <p className="text-body-sm text-on-surface-variant">No events yet.</p>
                      )}
                      {timeline.data && timeline.data.length > 0 && (
                        <ol className="relative border-l border-border-subtle ml-1 space-y-3">
                          {timeline.data.map((e) => (
                            <li key={e.id} className="ml-4">
                              <span className="absolute -left-1.5 h-3 w-3 rounded-full bg-primary" />
                              <p className="text-sm text-on-surface">{EVENT_LABEL[e.event_type] ?? e.event_type}</p>
                              {e.note && <p className="text-xs text-on-surface-variant">{e.note}</p>}
                              <p className="text-[11px] text-on-surface-variant">{new Date(e.created_at).toLocaleString()}</p>
                            </li>
                          ))}
                        </ol>
                      )}
                    </div>
                  </div>
                )}
              </section>
            </div>
          </div>
        </main>
      </div>
    </>
  );
}
