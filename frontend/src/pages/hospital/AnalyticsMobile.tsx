/**
 * Mobile-first hospital analytics.
 *
 * Adapted from the Stitch export
 * `lifelink_3.4_5.2_hospital_analytics_mobile_blueprint/code.html`, whose
 * KPIs and charts (national "lives saved", "Dijkstra execution ms",
 * regional shortage heatmaps, a fabricated transaction log) have no backend
 * source at all — `backend/app/api/routes/` has no analytics/stats/aggregate
 * endpoint of any kind (confirmed by grep). Every number on this screen is
 * computed client-side from the three real hospital lists the backend does
 * expose: `listHospitalRequests(true)`, `getInventory()`, and
 * `listHospitalEmergencies()`. `src/pages/Analytics.tsx` (the existing
 * `/analytics` route) is a 1:1 Stitch port that is *not* wired to real data
 * at all — this screen is a genuinely-wired, mobile-first alternative
 * scoped to what a single hospital's own data can actually show, not a
 * duplicate of it.
 */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Loader2 } from "lucide-react";

import { toApiError } from "../../lib/api";
import {
  BLOOD_LABEL,
  getInventory,
  listHospitalEmergencies,
  listHospitalRequests,
  type BloodRequestStatus,
  type HospitalResponseStatus,
  type Urgency,
} from "../../lib/domain";
import { STOCK_TIER_STYLE, stockTier } from "./inventoryStatus";
import NotificationsBell from "../../components/NotificationsBell";

const URGENCY_ORDER: Urgency[] = ["CRITICAL", "URGENT", "ROUTINE"];
const URGENCY_COLOR: Record<Urgency, string> = {
  CRITICAL: "bg-error",
  URGENT: "bg-warning",
  ROUTINE: "bg-secondary",
};
const STATUS_ORDER: BloodRequestStatus[] = ["PENDING", "MATCHING", "PARTIALLY_FULFILLED", "FULFILLED", "CANCELLED", "EXPIRED"];
const STATUS_COLOR: Record<BloodRequestStatus, string> = {
  PENDING: "bg-on-surface-variant",
  MATCHING: "bg-warning",
  PARTIALLY_FULFILLED: "bg-secondary",
  FULFILLED: "bg-success",
  CANCELLED: "bg-error",
  EXPIRED: "bg-error",
};
const HANDLING_ORDER: (HospitalResponseStatus | "NONE")[] = [
  "NONE", "ACKNOWLEDGED", "PREPARING", "DISPATCHED", "ALLOCATED", "FULFILLED", "DECLINED",
];
const HANDLING_LABEL: Record<HospitalResponseStatus | "NONE", string> = {
  NONE: "Not yet handled",
  ACKNOWLEDGED: "Acknowledged",
  PREPARING: "Preparing",
  DISPATCHED: "Dispatched",
  ALLOCATED: "Allocated from inventory",
  FULFILLED: "Fulfilled from inventory",
  DECLINED: "Declined",
};
const HANDLING_COLOR: Record<HospitalResponseStatus | "NONE", string> = {
  NONE: "bg-surface-container-high",
  ACKNOWLEDGED: "bg-secondary",
  PREPARING: "bg-warning",
  DISPATCHED: "bg-success",
  ALLOCATED: "bg-secondary",
  FULFILLED: "bg-success",
  DECLINED: "bg-error",
};

function BarRow({
  label,
  count,
  total,
  colorClass,
}: {
  label: string;
  count: number;
  total: number;
  colorClass: string;
}) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between text-label-sm font-label-sm">
        <span className="text-on-surface">{label}</span>
        <span className="text-on-surface-variant font-data-mono">
          {count} {total > 0 ? `(${pct}%)` : ""}
        </span>
      </div>
      <div className="h-2 w-full rounded-full bg-surface-container-high overflow-hidden">
        <div className={`h-full rounded-full ${colorClass}`} style={{ width: `${Math.max(count > 0 ? 3 : 0, pct)}%` }} />
      </div>
    </div>
  );
}

function KpiTile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-surface-container-lowest rounded-xl p-4 shadow-sm border border-border-subtle flex flex-col gap-1 min-w-0">
      <span className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">{label}</span>
      <span className="font-headline-md text-headline-md text-on-surface truncate">{value}</span>
      {sub && <span className="font-body-sm text-body-sm text-on-surface-variant">{sub}</span>}
    </div>
  );
}

export default function AnalyticsMobile() {
  const requests = useQuery({
    queryKey: ["hospital", "requests", "all"],
    queryFn: () => listHospitalRequests(true),
  });
  const inventory = useQuery({ queryKey: ["hospital", "inventory"], queryFn: getInventory });
  const emergencies = useQuery({ queryKey: ["hospital", "emergencies"], queryFn: listHospitalEmergencies });

  const isLoading = requests.isLoading || inventory.isLoading || emergencies.isLoading;
  const firstError = requests.error ?? inventory.error ?? emergencies.error;

  const stats = useMemo(() => {
    const reqs = requests.data ?? [];
    const inv = inventory.data ?? [];
    const emg = emergencies.data ?? [];

    const byUrgency: Record<Urgency, number> = { CRITICAL: 0, URGENT: 0, ROUTINE: 0 };
    const byStatus: Record<BloodRequestStatus, number> = {
      PENDING: 0, MATCHING: 0, PARTIALLY_FULFILLED: 0, FULFILLED: 0, CANCELLED: 0, EXPIRED: 0,
    };
    const byHandling: Record<HospitalResponseStatus | "NONE", number> = {
      NONE: 0, ACKNOWLEDGED: 0, PREPARING: 0, DISPATCHED: 0, ALLOCATED: 0, FULFILLED: 0, DECLINED: 0,
    };
    let distanceSum = 0;
    let distanceCount = 0;

    for (const r of reqs) {
      byUrgency[r.urgency]++;
      byStatus[r.status]++;
      byHandling[r.hospital_status ?? "NONE"]++;
      if (r.distance_km != null) {
        distanceSum += r.distance_km;
        distanceCount++;
      }
    }

    const open = byStatus.PENDING + byStatus.MATCHING;
    const handled = reqs.filter((r) => r.hospital_status != null).length;
    const responseRate = reqs.length > 0 ? Math.round((handled / reqs.length) * 100) : null;

    // Last 7 days, grouped from real created_at timestamps.
    const days: { label: string; count: number }[] = [];
    const today = new Date();
    for (let i = 6; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      const count = reqs.filter((r) => new Date(r.created_at).toDateString() === d.toDateString()).length;
      days.push({ label: d.toLocaleDateString(undefined, { weekday: "short" }), count });
    }
    const weekTotal = days.reduce((n, d) => n + d.count, 0);

    const totalUnits = inv.reduce((n, i) => n + i.units, 0);
    const tierCounts = { CRITICAL: 0, LOW: 0, OPTIMAL: 0 };
    for (const i of inv) tierCounts[stockTier(i.units)]++;

    const emergencyByStatus = new Map<string, number>();
    for (const e of emg) emergencyByStatus.set(e.status, (emergencyByStatus.get(e.status) ?? 0) + 1);

    return {
      total: reqs.length,
      open,
      byUrgency,
      byStatus,
      byHandling,
      responseRate,
      avgDistance: distanceCount > 0 ? Math.round((distanceSum / distanceCount) * 10) / 10 : null,
      distanceCount,
      days,
      weekTotal,
      totalUnits,
      tierCounts,
      inv,
      emergenciesActive: emg.length,
      emergencyByStatus,
    };
  }, [requests.data, inventory.data, emergencies.data]);

  return (
    <div className="min-h-screen bg-surface pb-20">
      <header className="sticky top-0 z-40 bg-surface/95 backdrop-blur border-b border-border-subtle">
        <div className="flex items-center justify-between gap-2 px-4 h-16">
          <div className="min-w-0">
            <p className="font-headline-sm text-headline-sm text-on-surface leading-tight">Analytics</p>
            <p className="font-body-sm text-body-sm text-on-surface-variant truncate">
              Computed live from your own requests, inventory & emergencies
            </p>
          </div>
          <NotificationsBell buttonClassName="p-2 rounded-full text-on-surface-variant hover:bg-surface-container-low relative shrink-0" />
        </div>
      </header>

      <main className="px-4 py-5 max-w-lg mx-auto flex flex-col gap-6">
        {isLoading && (
          <div className="flex items-center gap-2 text-on-surface-variant py-10 justify-center">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading analytics…
          </div>
        )}
        {firstError && (
          <div className="text-error text-body-sm flex items-center gap-3" role="alert">
            <span>{toApiError(firstError).detail}</span>
            <button
              type="button"
              className="underline flex-shrink-0"
              onClick={() => {
                requests.refetch();
                inventory.refetch();
                emergencies.refetch();
              }}
            >
              Retry
            </button>
          </div>
        )}

        {!isLoading && !firstError && (
          <>
            {/* KPI grid — 2 columns even at 320px */}
            <section className="grid grid-cols-2 gap-3">
              <KpiTile label="Incoming requests" value={String(stats.total)} sub={`${stats.open} still open`} />
              <KpiTile
                label="Response rate"
                value={stats.responseRate != null ? `${stats.responseRate}%` : "—"}
                sub="you've responded to"
              />
              <KpiTile label="Units in stock" value={stats.totalUnits.toLocaleString()} sub="across 8 ABO/Rh types" />
              <KpiTile label="Active emergencies" value={String(stats.emergenciesActive)} sub="visible to you" />
            </section>

            {/* Requests this week */}
            <section className="bg-surface-container-lowest rounded-xl p-4 shadow-sm border border-border-subtle">
              <div className="flex items-baseline justify-between mb-3">
                <h2 className="font-title-md text-title-md text-on-surface">Requests, last 7 days</h2>
                <span className="font-data-mono text-label-sm text-on-surface-variant">{stats.weekTotal} total</span>
              </div>
              <div className="flex items-end gap-2 h-24">
                {stats.days.map((d, i) => {
                  const max = Math.max(1, ...stats.days.map((x) => x.count));
                  const h = Math.max(4, Math.round((d.count / max) * 100));
                  return (
                    <div key={i} className="flex-1 flex flex-col items-center gap-1 h-full justify-end">
                      <span className="font-data-mono text-[10px] text-on-surface-variant">{d.count}</span>
                      <div className="w-full rounded-t bg-primary/70" style={{ height: `${h}%` }} />
                      <span className="font-label-sm text-[10px] text-on-surface-variant">{d.label}</span>
                    </div>
                  );
                })}
              </div>
              {stats.total === 0 && (
                <p className="text-body-sm text-on-surface-variant mt-2">No incoming requests recorded yet.</p>
              )}
            </section>

            {/* Urgency breakdown */}
            <section className="bg-surface-container-lowest rounded-xl p-4 shadow-sm border border-border-subtle flex flex-col gap-3">
              <h2 className="font-title-md text-title-md text-on-surface">By urgency</h2>
              {stats.total === 0 ? (
                <p className="text-body-sm text-on-surface-variant">No requests yet.</p>
              ) : (
                URGENCY_ORDER.map((u) => (
                  <BarRow key={u} label={u} count={stats.byUrgency[u]} total={stats.total} colorClass={URGENCY_COLOR[u]} />
                ))
              )}
            </section>

            {/* Status breakdown */}
            <section className="bg-surface-container-lowest rounded-xl p-4 shadow-sm border border-border-subtle flex flex-col gap-3">
              <h2 className="font-title-md text-title-md text-on-surface">By request status</h2>
              {stats.total === 0 ? (
                <p className="text-body-sm text-on-surface-variant">No requests yet.</p>
              ) : (
                STATUS_ORDER.filter((s) => stats.byStatus[s] > 0).map((s) => (
                  <BarRow key={s} label={s} count={stats.byStatus[s]} total={stats.total} colorClass={STATUS_COLOR[s]} />
                ))
              )}
            </section>

            {/* Handling status breakdown */}
            <section className="bg-surface-container-lowest rounded-xl p-4 shadow-sm border border-border-subtle flex flex-col gap-3">
              <h2 className="font-title-md text-title-md text-on-surface">Your handling status</h2>
              {stats.total === 0 ? (
                <p className="text-body-sm text-on-surface-variant">No requests yet.</p>
              ) : (
                HANDLING_ORDER.filter((h) => stats.byHandling[h] > 0).map((h) => (
                  <BarRow
                    key={h}
                    label={HANDLING_LABEL[h]}
                    count={stats.byHandling[h]}
                    total={stats.total}
                    colorClass={HANDLING_COLOR[h]}
                  />
                ))
              )}
            </section>

            {/* Distance */}
            <section className="bg-surface-container-lowest rounded-xl p-4 shadow-sm border border-border-subtle">
              <h2 className="font-title-md text-title-md text-on-surface mb-1">Average request distance</h2>
              <p className="font-headline-md text-headline-md text-on-surface">
                {stats.avgDistance != null ? `${stats.avgDistance} km` : "—"}
              </p>
              <p className="font-body-sm text-body-sm text-on-surface-variant mt-1">
                {stats.distanceCount > 0
                  ? `Computed from ${stats.distanceCount} request(s) with known coordinates`
                  : "No requests with known coordinates yet"}
              </p>
            </section>

            {/* Inventory tiers */}
            <section className="bg-surface-container-lowest rounded-xl p-4 shadow-sm border border-border-subtle flex flex-col gap-3">
              <div className="flex items-center justify-between">
                <h2 className="font-title-md text-title-md text-on-surface">Inventory health</h2>
                <Link to="/hospital" className="text-secondary font-label-sm text-label-sm hover:underline">
                  Manage
                </Link>
              </div>
              <div className="grid grid-cols-3 gap-2">
                {(["CRITICAL", "LOW", "OPTIMAL"] as const).map((tier) => (
                  <div key={tier} className={`rounded-lg p-2.5 text-center ${STOCK_TIER_STYLE[tier].badge}`}>
                    <p className="font-headline-sm text-headline-sm font-bold">{stats.tierCounts[tier]}</p>
                    <p className="font-label-sm text-label-sm">{STOCK_TIER_STYLE[tier].label}</p>
                  </div>
                ))}
              </div>
              <ul className="flex flex-col gap-2 mt-1">
                {stats.inv
                  .slice()
                  .sort((a, b) => a.units - b.units)
                  .map((i) => (
                    <li key={i.blood_type} className="flex items-center justify-between text-body-sm">
                      <span className="text-on-surface font-semibold">{BLOOD_LABEL[i.blood_type]}</span>
                      <span className="text-on-surface-variant font-data-mono">{i.units} units</span>
                    </li>
                  ))}
              </ul>
            </section>

            {/* Emergencies */}
            <section className="bg-surface-container-lowest rounded-xl p-4 shadow-sm border border-border-subtle flex flex-col gap-3">
              <h2 className="font-title-md text-title-md text-on-surface">Emergencies visible to you</h2>
              {stats.emergenciesActive === 0 ? (
                <p className="text-body-sm text-on-surface-variant">No active emergencies in your area.</p>
              ) : (
                Array.from(stats.emergencyByStatus.entries()).map(([status, count]) => (
                  <div key={status} className="flex items-center justify-between text-body-sm">
                    <span className="text-on-surface">{status.replace(/_/g, " ")}</span>
                    <span className="text-on-surface-variant font-data-mono">{count}</span>
                  </div>
                ))
              )}
            </section>
          </>
        )}
      </main>

      {/* Mobile bottom nav — every target is a real registered route */}
      <nav
        aria-label="Primary"
        className="fixed bottom-0 inset-x-0 z-40 bg-surface-container-lowest border-t border-border-subtle flex items-stretch justify-around h-16 max-w-lg mx-auto"
      >
        <Link to="/hospital" className="flex-1 flex flex-col items-center justify-center gap-0.5 text-on-surface-variant hover:text-primary">
          <span aria-hidden="true" className="material-symbols-outlined text-[22px]">dashboard</span>
          <span className="font-label-sm text-[10px]">Dashboard</span>
        </Link>
        <span className="flex-1 flex flex-col items-center justify-center gap-0.5 text-primary" aria-current="page">
          <span aria-hidden="true" className="material-symbols-outlined text-[22px]" style={{ fontVariationSettings: "'FILL' 1" }}>
            analytics
          </span>
          <span className="font-label-sm text-[10px] font-bold">Analytics</span>
        </span>
        <Link to="/notifications" className="flex-1 flex flex-col items-center justify-center gap-0.5 text-on-surface-variant hover:text-primary">
          <span aria-hidden="true" className="material-symbols-outlined text-[22px]">notifications</span>
          <span className="font-label-sm text-[10px]">Alerts</span>
        </Link>
        <Link to="/profile" className="flex-1 flex flex-col items-center justify-center gap-0.5 text-on-surface-variant hover:text-primary">
          <span aria-hidden="true" className="material-symbols-outlined text-[22px]">account_circle</span>
          <span className="font-label-sm text-[10px]">Profile</span>
        </Link>
      </nav>
    </div>
  );
}
