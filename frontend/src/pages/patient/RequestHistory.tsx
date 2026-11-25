/**
 * Request History, Tracking & Preferred Hospitals — Patient screen
 * Adapted from the Stitch export:
 * lifelink_1.5_request_history_tracking_preferred_hospitals/code.html
 *
 * The Stitch mock invented ticket ids ("#RQ-8821"), ETA countdowns, cold-chain pod
 * temperatures, GPS courier routes, hospital "stock readiness" numbers, and — in its own
 * "Preferred Hospitals" section — a bookmarking feature the design itself labels
 * "[BACKEND REQUIRED]" because there is no persistence for it (confirmed against
 * backend/app/api/routes/hospitals.py and patients.py: neither exposes any preferred /
 * saved-hospital endpoint). So this screen keeps the two-section layout — request history
 * + preferred hospitals — but the history section is wired entirely to the real
 * `listBloodRequests` / `<RequestTrackDrawer/>`, and the preferred-hospitals section is an
 * honest, clearly-labelled "backend required" empty state instead of a bookmark button
 * that would silently do nothing.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Loader2, Menu } from "lucide-react";

import NotificationsBell from "../../components/NotificationsBell";
import RequestTrackDrawer from "../../components/RequestTrackDrawer";
import BloodRequestModal from "../../components/BloodRequestModal";
import MobileNavDrawer from "../../components/MobileNavDrawer";

/** QA fix: "PARTIALLY_FULFILLED" rendered as "Partially_fulfilled". */
function statusText(status: string): string {
  const t = status.toLowerCase().replace(/_/g, " ");
  return t[0].toUpperCase() + t.slice(1);
}
import {
  BLOOD_LABEL,
  listBloodRequests,
  type BloodRequest,
  type BloodRequestStatus,
  OPEN_REQUEST_STATUSES,
} from "../../lib/domain";

const NAV = [
  { path: "/patient", icon: "dashboard", label: "Dashboard" },
  { path: "/patient/find-donor", icon: "person_search", label: "Find Donor" },
  { path: "/patient/new-request", icon: "post_add", label: "New Blood Request" },
  { path: "/patient/sos-tracking", icon: "emergency", label: "Emergency SOS" },
  { path: "/patient/history", icon: "vital_signs", label: "Request History" },
  { path: "/decision-transparency", icon: "rule", label: "Decision Transparency" },
  { path: "/help", icon: "health_and_safety", label: "Guidelines & Support" },
  { path: "/settings", icon: "manage_accounts", label: "Settings" },
];

const TABS = [
  { key: "active", label: "Active", statuses: [...OPEN_REQUEST_STATUSES] as BloodRequestStatus[] },
  { key: "fulfilled", label: "Fulfilled", statuses: ["FULFILLED"] as BloodRequestStatus[] },
  { key: "closed", label: "Cancelled / Expired", statuses: ["CANCELLED", "EXPIRED"] as BloodRequestStatus[] },
] as const;

const STATUS_BADGE: Record<BloodRequestStatus, string> = {
  PENDING: "bg-surface-container-high text-on-surface",
  MATCHING: "bg-secondary-fixed text-on-secondary-fixed-variant",
  PARTIALLY_FULFILLED: "bg-secondary-fixed text-on-secondary-fixed-variant",
  FULFILLED: "bg-secondary-fixed text-on-secondary-fixed-variant",
  CANCELLED: "bg-surface-container text-on-surface-variant",
  EXPIRED: "bg-surface-container text-on-surface-variant",
};

// listBloodRequests (GET /blood-requests) has no server-side pagination — a
// single patient's own history is small. Paginating client-side over the
// already-fetched, real data still gives a real "page 2 of 3" experience
// (never invented rows) without waiting on a backend change.
const PAGE_SIZE = 10;

const URGENCY_BADGE: Record<string, string> = {
  ROUTINE: "bg-surface-container text-on-surface-variant",
  URGENT: "bg-secondary-fixed text-on-secondary-fixed-variant",
  CRITICAL: "bg-error-container text-on-error-container",
};

function fmt(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function RequestRow({ r, onTrack }: { r: BloodRequest; onTrack: (id: string) => void }) {
  return (
    <tr className="hover:bg-surface-container-low/50 transition-colors">
      <td className="py-space-4 px-space-6">
        <div className="font-data-mono text-data-mono font-semibold text-on-surface">
          {r.id.slice(0, 8).toUpperCase()}
        </div>
        <div className="font-body-sm text-body-sm text-on-surface-variant">
          {r.hospital_name || "No hospital specified"}
        </div>
      </td>
      <td className="py-space-4 px-space-4">
        <div className="inline-flex items-center gap-space-2 px-space-2.5 py-1 rounded bg-primary-fixed text-primary font-semibold font-label-md text-label-md">
          <span aria-hidden="true" className="material-symbols-outlined text-[16px]">water_drop</span>
          <span>{BLOOD_LABEL[r.blood_type]}</span>
        </div>
        <div className="font-body-sm text-body-sm text-on-surface-variant mt-1">
          {r.fulfilled_units > 0
            ? `${r.fulfilled_units}/${r.units_needed} units fulfilled`
            : `${r.units_needed} unit${r.units_needed === 1 ? "" : "s"}`}
        </div>
      </td>
      <td className="py-space-4 px-space-4">
        <span className={`inline-flex items-center gap-1.5 px-space-3 py-0.5 rounded-full font-label-sm text-label-sm font-semibold ${URGENCY_BADGE[r.urgency]}`}>
          {r.urgency[0] + r.urgency.slice(1).toLowerCase()}
        </span>
      </td>
      <td className="py-space-4 px-space-4">
        <span className={`inline-flex items-center gap-1 px-space-3 py-0.5 rounded-full font-label-sm text-label-sm font-semibold ${STATUS_BADGE[r.status]}`}>
          {statusText(r.status)}
        </span>
        <div className="font-body-sm text-body-sm text-on-surface-variant mt-1">
          {r.accepted_count} donor{r.accepted_count === 1 ? "" : "s"} accepted
        </div>
      </td>
      <td className="py-space-4 px-space-4">
        <div className="font-label-md text-label-md text-on-surface">{fmt(r.created_at)}</div>
        {r.needed_by && (
          <div className="font-body-sm text-body-sm text-on-surface-variant">Needed: {fmt(r.needed_by)}</div>
        )}
      </td>
      <td className="py-space-4 px-space-6 text-right">
        <button
          onClick={() => onTrack(r.id)}
          className="h-8 px-space-3 rounded-full bg-primary-container text-on-primary font-label-sm text-label-sm font-semibold hover:bg-primary transition-colors inline-flex items-center gap-1"
        >
          <span aria-hidden="true" className="material-symbols-outlined text-[16px]">radar</span>
          <span>Track</span>
        </button>
      </td>
    </tr>
  );
}

function RequestCard({ r, onTrack }: { r: BloodRequest; onTrack: (id: string) => void }) {
  return (
    <div className="p-space-4 rounded-xl bg-surface-container-low flex flex-col gap-space-2">
      <div className="flex items-center justify-between">
        <span className="inline-flex items-center gap-1.5 px-space-2 py-0.5 rounded bg-primary-fixed text-primary font-semibold font-label-md text-label-md">
          {BLOOD_LABEL[r.blood_type]}
        </span>
        <span className={`px-space-2 py-0.5 rounded-full font-label-sm text-label-sm font-semibold ${STATUS_BADGE[r.status]}`}>
          {statusText(r.status)}
        </span>
      </div>
      <p className="font-body-sm text-body-sm text-on-surface-variant">
        {r.hospital_name || "No hospital specified"} ·{" "}
        {r.fulfilled_units > 0
          ? `${r.fulfilled_units}/${r.units_needed} units fulfilled`
          : `${r.units_needed} unit${r.units_needed === 1 ? "" : "s"}`}
      </p>
      <p className="font-body-sm text-body-sm text-on-surface-variant">
        Created {fmt(r.created_at)} · {r.accepted_count} accepted
      </p>
      <button
        onClick={() => onTrack(r.id)}
        className="mt-space-1 self-start h-8 px-space-3 rounded-full bg-primary-container text-on-primary font-label-sm text-label-sm font-semibold hover:bg-primary transition-colors"
      >
        Track
      </button>
    </div>
  );
}

export default function RequestHistory() {
  const location = useLocation();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("active");
  const [trackId, setTrackId] = useState<string | null>(null);
  const [showNew, setShowNew] = useState(false);

  const requests = useQuery({
    queryKey: ["blood-requests", "list"],
    queryFn: () => listBloodRequests(),
  });

  const all = requests.data ?? [];
  const counts = useMemo(() => {
    const map = new Map<(typeof TABS)[number]["key"], number>();
    for (const t of TABS) map.set(t.key, all.filter((r) => t.statuses.includes(r.status)).length);
    return map;
  }, [all]);

  const activeTab = TABS.find((t) => t.key === tab)!;
  const filtered = all.filter((r) => activeTab.statuses.includes(r.status));

  const [page, setPage] = useState(1);
  useEffect(() => {
    setPage(1);
  }, [tab]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const visible = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  // Phase E: units actually supplied, from the server's own per-request
  // count -- previously this summed `units_needed` of FULFILLED requests
  // only, which ignored partial fulfilment entirely.
  const fulfilledUnits = all.reduce((n, r) => n + r.fulfilled_units, 0);

  // Distinct hospitals this patient has requested from — real data, but with no backend
  // persistence for "preferred"/bookmarked hospitals (see file header note).
  const historyHospitals = useMemo(() => {
    const byKey = new Map<string, { hospital_name: string; city: string | null; count: number }>();
    for (const r of all) {
      if (!r.hospital_name) continue;
      const key = `${r.hospital_name}__${r.city ?? ""}`;
      const existing = byKey.get(key);
      if (existing) existing.count += 1;
      else byKey.set(key, { hospital_name: r.hospital_name, city: r.city, count: 1 });
    }
    return Array.from(byKey.values()).sort((a, b) => b.count - a.count);
  }, [all]);

  return (
    <>
      <aside className="hidden lg:flex flex-col h-screen w-64 fixed left-0 top-0 bg-surface-container-lowest border-r border-border-subtle z-50 overflow-y-auto">
        <div className="flex items-center gap-3 px-space-6 py-space-6">
          <div className="w-9 h-9 rounded-lg bg-primary-container flex items-center justify-center">
            <span aria-hidden="true" className="material-symbols-outlined text-on-primary text-[22px]">ecg_heart</span>
          </div>
          <div className="flex flex-col">
            <span className="font-title-md text-title-md text-on-surface leading-none">LifeLink</span>
            <span className="font-label-sm text-label-sm text-primary uppercase tracking-wider mt-1">
              Clinical Pulse
            </span>
          </div>
        </div>
        <nav className="flex flex-col gap-space-1 px-space-3">
          {NAV.map((n) => (
            <Link
              key={n.path}
              to={n.path}
              className={`flex items-center gap-space-3 px-space-3 py-space-2 rounded-lg transition-all font-body-md text-body-md ${
                n.path === "/patient/history"
                  ? "bg-primary-container text-on-primary font-semibold shadow-level-1"
                  : "text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"
              }`}
            >
              <span aria-hidden="true" className="material-symbols-outlined text-[20px]">{n.icon}</span>
              <span>{n.label}</span>
            </Link>
          ))}
        </nav>
      </aside>

      <MobileNavDrawer
        open={mobileNavOpen}
        onClose={() => setMobileNavOpen(false)}
        activePath={location.pathname}
        title="LifeLink"
        subtitle="Clinical Pulse"
        sections={[{ items: NAV }]}
      />

      <div className="lg:pl-64">
        <header className="sticky top-0 lg:fixed lg:top-0 lg:left-64 lg:right-0 h-16 bg-surface-container-lowest/90 backdrop-blur-xl shadow-level-1 z-40 px-space-4 lg:px-space-6 flex items-center justify-between gap-space-4">
          <div className="flex items-center gap-space-3 min-w-0">
            <button
              type="button"
              onClick={() => setMobileNavOpen(true)}
              aria-label="Open navigation menu"
              className="lg:hidden p-2 -ml-2 rounded-full text-on-surface-variant hover:bg-surface-container-low shrink-0"
            >
              <Menu className="h-5 w-5" />
            </button>
            <h1 className="font-title-lg text-title-lg text-on-surface truncate">Request History</h1>
          </div>
          <div className="flex items-center gap-space-3">
            <NotificationsBell buttonClassName="relative w-10 h-10 rounded-full flex items-center justify-center text-on-surface-variant hover:bg-surface-container-low hover:text-on-surface transition-colors" />
            <Link to="/profile" aria-label="Your profile" className="w-8 h-8 rounded-full bg-primary flex items-center justify-center flex-shrink-0">
              <span aria-hidden="true" className="material-symbols-outlined text-on-primary text-[18px]">person</span>
            </Link>
          </div>
        </header>

        <main className="lg:pt-16 min-h-screen bg-background w-full px-space-4 lg:px-space-6 py-space-6">
          <div className="flex flex-col w-full gap-space-8 max-w-container-max mx-auto">
            {/* Header + quick metrics */}
            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-space-4">
              <div>
                <h2 className="font-headline-lg text-headline-lg text-on-surface tracking-tight">
                  Transfusion tracking &amp; history
                </h2>
                <p className="font-body-md text-body-md text-on-surface-variant">
                  Every blood request you've filed, with live status.
                </p>
              </div>
              <div className="flex items-center gap-space-3 overflow-x-auto pb-space-1">
                <div className="flex items-center gap-space-3 bg-surface-container-lowest px-space-4 py-space-2 rounded-lg shadow-level-1">
                  <div className="w-9 h-9 rounded-full bg-primary-fixed flex items-center justify-center text-primary">
                    <span aria-hidden="true" className="material-symbols-outlined text-[20px]">bloodtype</span>
                  </div>
                  <div>
                    <div className="font-label-sm text-label-sm text-on-surface-variant">Active</div>
                    <div className="font-headline-sm text-headline-sm text-on-surface leading-tight">
                      {counts.get("active") ?? 0}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-space-3 bg-surface-container-lowest px-space-4 py-space-2 rounded-lg shadow-level-1">
                  <div className="w-9 h-9 rounded-full bg-secondary-fixed flex items-center justify-center text-secondary">
                    <span aria-hidden="true" className="material-symbols-outlined text-[20px]">verified</span>
                  </div>
                  <div>
                    <div className="font-label-sm text-label-sm text-on-surface-variant">Fulfilled units</div>
                    <div className="font-headline-sm text-headline-sm text-on-surface leading-tight">{fulfilledUnits}</div>
                  </div>
                </div>
                <button
                  onClick={() => setShowNew(true)}
                  className="h-14 px-space-5 rounded-full bg-primary-container hover:bg-primary text-on-primary font-label-lg text-label-lg font-semibold shadow-level-1 transition-all flex items-center gap-space-2 flex-shrink-0"
                >
                  <span aria-hidden="true" className="material-symbols-outlined text-[18px]">add_circle</span>
                  <span>New Request</span>
                </button>
              </div>
            </div>

            {/* History table */}
            <section className="bg-surface-container-lowest rounded-xl shadow-level-1 overflow-hidden">
              <div className="p-space-6 border-b border-surface-container">
                <div className="inline-flex p-1 bg-surface-container rounded-lg">
                  {TABS.map((t) => (
                    <button
                      key={t.key}
                      onClick={() => setTab(t.key)}
                      className={`px-space-4 py-space-2 rounded-md font-label-lg text-label-lg transition-all ${
                        tab === t.key
                          ? "bg-surface-container-lowest text-on-surface shadow-sm font-semibold"
                          : "text-on-surface-variant hover:text-on-surface"
                      }`}
                    >
                      {t.label} ({counts.get(t.key) ?? 0})
                    </button>
                  ))}
                </div>
              </div>

              {requests.isLoading && (
                <div className="p-space-12 flex items-center justify-center gap-space-2 text-on-surface-variant">
                  <Loader2 className="h-5 w-5 animate-spin" /> Loading your requests…
                </div>
              )}
              {requests.isError && (
                <div className="p-space-12 text-center text-error">Couldn't load your request history.</div>
              )}
              {requests.data && visible.length === 0 && (
                <div className="p-space-12 text-center flex flex-col items-center gap-space-4">
                  <div className="w-16 h-16 rounded-full bg-surface-container flex items-center justify-center text-on-surface-variant">
                    <span aria-hidden="true" className="material-symbols-outlined text-[32px]">inbox</span>
                  </div>
                  <h3 className="font-headline-sm text-headline-sm text-on-surface">
                    No {activeTab.label.toLowerCase()} requests
                  </h3>
                  <p className="font-body-md text-body-md text-on-surface-variant max-w-md">
                    Requests in this state will appear here once you have some.
                  </p>
                </div>
              )}

              {visible.length > 0 && (
                <>
                  <div className="hidden md:block overflow-x-auto">
                    <table className="w-full text-left border-collapse">
                      <thead>
                        <tr className="bg-surface-container-low text-on-surface-variant font-label-md text-label-md uppercase tracking-wider">
                          <th className="py-space-3 px-space-6 font-semibold">Request</th>
                          <th className="py-space-3 px-space-4 font-semibold">Blood &amp; units</th>
                          <th className="py-space-3 px-space-4 font-semibold">Urgency</th>
                          <th className="py-space-3 px-space-4 font-semibold">Status</th>
                          <th className="py-space-3 px-space-4 font-semibold">Created / Needed</th>
                          <th className="py-space-3 px-space-6 text-right font-semibold">Action</th>
                        </tr>
                      </thead>
                      <tbody className="font-body-md text-body-md text-on-surface">
                        {visible.map((r) => (
                          <RequestRow key={r.id} r={r} onTrack={setTrackId} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="md:hidden flex flex-col gap-space-3 p-space-4">
                    {visible.map((r) => (
                      <RequestCard key={r.id} r={r} onTrack={setTrackId} />
                    ))}
                  </div>

                  {pageCount > 1 && (
                    <div className="flex items-center justify-between gap-space-3 px-space-6 py-space-4 border-t border-surface-container">
                      <p className="font-body-sm text-body-sm text-on-surface-variant">
                        Page {currentPage} of {pageCount} · {filtered.length} request{filtered.length === 1 ? "" : "s"}
                      </p>
                      <div className="flex items-center gap-space-2">
                        <button
                          type="button"
                          onClick={() => setPage((p) => Math.max(1, p - 1))}
                          disabled={currentPage === 1}
                          aria-label="Previous page"
                          className="h-9 w-9 rounded-full flex items-center justify-center bg-surface-container-low hover:bg-surface-container-high text-on-surface disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                        >
                          <ChevronLeft className="h-4 w-4" />
                        </button>
                        <button
                          type="button"
                          onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                          disabled={currentPage === pageCount}
                          aria-label="Next page"
                          className="h-9 w-9 rounded-full flex items-center justify-center bg-surface-container-low hover:bg-surface-container-high text-on-surface disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                        >
                          <ChevronRight className="h-4 w-4" />
                        </button>
                      </div>
                    </div>
                  )}
                </>
              )}
            </section>

            {/* Preferred hospitals */}
            <section className="flex flex-col gap-space-5">
              <div className="flex flex-col lg:flex-row lg:items-end justify-between gap-space-4">
                <div>
                  <div className="flex items-center gap-space-2 text-primary font-label-sm text-label-sm uppercase tracking-wider font-semibold">
                    <span aria-hidden="true" className="material-symbols-outlined text-[18px]">local_hospital</span>
                    <span>Hospitals</span>
                  </div>
                  <h2 className="font-headline-md text-headline-md text-on-surface tracking-tight">
                    Hospitals from your history
                  </h2>
                  <p className="font-body-md text-body-md text-on-surface-variant">
                    Hospitals you've named on past requests.
                  </p>
                </div>
                <span className="inline-flex items-center gap-1.5 font-label-sm text-label-sm bg-surface-container-high px-space-3 py-1 rounded-full text-on-surface-variant self-start">
                  <span aria-hidden="true" className="material-symbols-outlined text-[14px]">info</span>
                  Bookmarking a preferred hospital isn't available yet — backend required
                </span>
              </div>

              <div className="bg-surface-container-low p-space-4 rounded-xl flex items-start gap-space-3 text-body-sm font-body-sm text-on-surface-variant">
                <span aria-hidden="true" className="material-symbols-outlined text-primary text-[20px] shrink-0 mt-0.5">policy</span>
                <div className="leading-relaxed">
                  <strong className="font-semibold text-on-surface">Backend required:</strong> there is
                  no endpoint yet to save a preferred hospital — the list below is read-only,
                  built from the hospital names on your own past requests, not a bookmark
                  feature. Saving a preference here would silently do nothing today, so
                  the star/bookmark control the earlier design showed has been left out.
                </div>
              </div>

              {requests.data && historyHospitals.length === 0 && (
                <div className="bg-surface-container-lowest rounded-xl shadow-level-1 p-space-8 text-center text-on-surface-variant">
                  You haven't named a hospital on a request yet — hospitals you use will
                  appear here.
                </div>
              )}

              {historyHospitals.length > 0 && (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-space-4">
                  {historyHospitals.map((h) => (
                    <div
                      key={`${h.hospital_name}-${h.city}`}
                      className="bg-surface-container-lowest rounded-xl shadow-level-1 p-space-5 flex flex-col gap-space-2"
                    >
                      <div className="flex items-start justify-between gap-space-2">
                        <div className="min-w-0">
                          <h3 className="font-title-lg text-title-lg text-on-surface font-semibold truncate">
                            {h.hospital_name}
                          </h3>
                          {h.city && (
                            <p className="font-body-sm text-body-sm text-on-surface-variant truncate">{h.city}</p>
                          )}
                        </div>
                        <span
                          aria-disabled="true"
                          title="Bookmarking hospitals isn't available yet — backend required"
                          className="w-9 h-9 rounded-full bg-surface-container flex items-center justify-center text-on-surface-variant flex-shrink-0 opacity-60"
                        >
                          <span aria-hidden="true" className="material-symbols-outlined text-[18px]">bookmark_border</span>
                        </span>
                      </div>
                      <p className="font-body-sm text-body-sm text-on-surface-variant">
                        {h.count} request{h.count === 1 ? "" : "s"} filed here
                      </p>
                      <button
                        onClick={() => setShowNew(true)}
                        className="mt-space-1 self-start h-9 px-space-4 rounded-full bg-primary hover:bg-primary-container text-on-primary font-label-md text-label-md font-semibold transition-colors"
                      >
                        New request
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>
        </main>
      </div>

      <RequestTrackDrawer requestId={trackId} onClose={() => setTrackId(null)} />
      <BloodRequestModal open={showNew} onClose={() => setShowNew(false)} />
    </>
  );
}
