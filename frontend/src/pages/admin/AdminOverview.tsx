/**
 * Admin — Overview & System Audit Feed (Stitch 4.1 / 4.2)
 *
 * The Admin API now exists (getAdminUserStats, listAdminBloodRequests,
 * listAdminEmergencies, listAdminVerifications, getAdminSystemHealth,
 * listAdminAuditLog) so this screen is wired to real, live admin-scoped data.
 * Only two controls remain honest "Backend required" gaps: there is no CSV
 * export endpoint for the audit log, and no broadcast-alert / arbitrary
 * filter endpoint — those buttons stay disabled rather than faking success.
 */
import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Menu } from "lucide-react";

import NotificationsBell from "../../components/NotificationsBell";
import { useAuth } from "../../lib/auth";
import { toApiError } from "../../lib/api";
import { ROLE_LABEL } from "../../lib/roles";
import {
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  getAdminUserStats,
  listAdminBloodRequests,
  listAdminEmergencies,
  listAdminVerifications,
  getAdminSystemHealth,
  listAdminAuditLog,
  type AppNotification,
} from "../../lib/domain";

import { ADMIN_NAV, ACCOUNT_NAV } from "./adminNav";
import MobileNavDrawer from "../../components/MobileNavDrawer";
import { useLiveFallbackInterval } from "../../lib/realtime";
import { useInFlightIds } from "../../lib/inflight";

function timeAgo(iso: string): string {
  const s = Math.max(1, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function AdminOverview() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [notifError, setNotifError] = useState("");

  const notifications = useQuery({
    queryKey: ["notifications", "list"],
    queryFn: () => listNotifications(false),
  });

  const reading = useInFlightIds();
  const readOne = useMutation({
    mutationFn: (id: string) => markNotificationRead(id),
    onMutate: (id: string) => reading.start(id),
    onSettled: (_d, _e, id) => reading.done(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
      setNotifError("");
    },
    onError: (e) => setNotifError(toApiError(e).detail),
  });
  const readAll = useMutation({
    mutationFn: markAllNotificationsRead,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
      setNotifError("");
    },
    onError: (e) => setNotifError(toApiError(e).detail),
  });

  const userStats = useQuery({
    queryKey: ["admin", "users", "stats"],
    queryFn: getAdminUserStats,
  });
  const bloodRequests = useQuery({
    queryKey: ["admin", "blood-requests", "recent"],
    queryFn: () => listAdminBloodRequests(),
  });
  const activeEmergencies = useQuery({
    queryKey: ["admin", "emergencies", "ACTIVE"],
    queryFn: () => listAdminEmergencies("ACTIVE"),
  });
  const pendingVerifications = useQuery({
    queryKey: ["admin", "verifications", "PENDING"],
    queryFn: () => listAdminVerifications({ status: "PENDING" }),
  });
  const triageVerifications = useQuery({
    queryKey: ["admin", "verifications", "PENDING", "triage"],
    queryFn: () => listAdminVerifications({ status: "PENDING", limit: 5 }),
  });
  const health = useQuery({
    queryKey: ["admin", "system", "health"],
    queryFn: getAdminSystemHealth,
    // Phase F: live updates arrive over the event stream; this interval only
    // applies while that stream is down (graceful degradation).
    refetchInterval: useLiveFallbackInterval(30_000),
  });
  const auditLog = useQuery({
    queryKey: ["admin", "audit-log", "recent"],
    queryFn: () => listAdminAuditLog({ limit: 8 }),
  });

  async function handleLogout() {
    setLogoutBusy(true);
    await logout();
    navigate("/login", { replace: true });
  }

  const items: AppNotification[] = notifications.data ?? [];
  const unread = items.filter((n) => !n.read_at).length;

  return (
    <>
      {/* Sidebar */}
      <aside className="fixed left-0 top-0 h-full w-72 bg-surface-container-lowest z-40 flex flex-col justify-between shadow-level-1 hidden lg:flex">
        <div className="flex flex-col">
          <div className="h-16 px-space-6 flex items-center gap-space-3">
            <div className="w-9 h-9 rounded-lg bg-primary-container flex items-center justify-center shadow-level-1">
              <span aria-hidden="true" className="material-symbols-outlined text-on-primary text-[22px]">
                shield_with_heart
              </span>
            </div>
            <div className="flex flex-col">
              <span className="font-headline-sm text-headline-sm text-on-surface leading-none">
                LifeLink
              </span>
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
                className="flex items-center justify-between gap-space-3 px-space-3 py-space-3 rounded-lg text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface transition-all font-label-lg text-label-lg"
              >
                <span className="flex items-center gap-space-3">
                  <span aria-hidden="true" className="material-symbols-outlined text-[20px]">{n.icon}</span>
                  <span>{n.label}</span>
                </span>
                {n.path === "/notifications" && unread > 0 && (
                  <span className="bg-primary text-on-primary font-label-sm text-label-sm px-space-2 py-0.5 rounded-full font-semibold">
                    {unread}
                  </span>
                )}
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
              <span className="font-body-sm text-body-sm text-on-surface-variant truncate">
                {user?.email}
              </span>
            </div>
          </div>
          <button
            onClick={handleLogout}
            disabled={logoutBusy}
            className="w-full flex items-center gap-space-3 px-space-3 py-space-2 rounded-lg text-on-surface-variant hover:bg-surface-container-low hover:text-error transition-all disabled:opacity-60"
          >
            {logoutBusy ? (
              <Loader2 className="h-[18px] w-[18px] animate-spin" />
            ) : (
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
        {/* Header */}
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
            <span className="font-title-lg text-title-lg text-on-surface truncate">Overview</span>
            <span className="font-label-sm text-label-sm text-on-surface-variant hidden sm:block">
              Admin Portal · System Audit Feed
            </span>
          </div>
          </div>
          <div className="flex items-center gap-space-3">
            {/* Phase 19: shared admin notification surface -- the same
                component every other role already uses, so an admin sees
                the same live unread count/panel/mark-read behavior no
                matter which admin sub-page they're on, instead of only on
                Overview's own hand-rolled "Your Notifications" panel
                further down this page. */}
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
            {/* Command bar */}
            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-space-4 bg-surface-container-lowest p-space-6 rounded-xl shadow-level-1">
              <div className="flex items-center gap-space-4">
                <div className="w-12 h-12 rounded-xl bg-primary text-on-primary flex items-center justify-center shadow-level-1 shrink-0">
                  <span aria-hidden="true" className="material-symbols-outlined text-[26px]">emergency_home</span>
                </div>
                <div className="flex flex-col">
                  <span className="font-headline-md text-headline-md text-on-surface">
                    Operations Command Console
                  </span>
                  <p className="font-body-sm text-body-sm text-on-surface-variant">
                    Clinical operations overview for the signed-in administrator.
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-space-3">
                <button
                  type="button"
                  disabled
                  title="No admin API to filter by yet"
                  aria-disabled="true"
                  className="flex items-center gap-space-2 px-space-4 py-2 rounded-full bg-surface-container font-label-md text-label-md text-on-surface-variant cursor-not-allowed opacity-60"
                >
                  <span aria-hidden="true" className="material-symbols-outlined text-[18px]">tune</span>
                  <span>Filter</span>
                </button>
                <button
                  type="button"
                  disabled
                  title="No admin API to broadcast an alert yet"
                  aria-disabled="true"
                  className="flex items-center gap-space-2 px-space-5 py-2.5 rounded-full bg-primary-container text-on-primary font-label-lg text-label-lg cursor-not-allowed opacity-60"
                >
                  <span aria-hidden="true" className="material-symbols-outlined text-[18px]">campaign</span>
                  <span>Broadcast Alert</span>
                </button>
              </div>
            </div>

            {/* KPI row */}
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-space-4">
              <div className="flex flex-col gap-space-3 p-space-5 rounded-xl bg-surface-container-lowest shadow-level-1">
                <span className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">
                  Total User Ecosystem
                </span>
                <div className="w-10 h-10 rounded-lg bg-surface-container flex items-center justify-center text-primary">
                  <span aria-hidden="true" className="material-symbols-outlined text-[22px]">group</span>
                </div>
                {userStats.isLoading && (
                  <p className="font-body-sm text-body-sm text-on-surface-variant">Loading…</p>
                )}
                {userStats.isError && (
                  <p role="alert" className="font-body-sm text-body-sm text-error flex items-center gap-2">
                    <span>{toApiError(userStats.error).detail}</span>
                    <button type="button" className="underline flex-shrink-0" onClick={() => userStats.refetch()}>
                      Retry
                    </button>
                  </p>
                )}
                {userStats.data && (
                  <>
                    <span className="font-headline-lg text-headline-lg text-on-surface">
                      {userStats.data.total_users}
                    </span>
                    <p className="font-body-sm text-body-sm text-on-surface-variant">
                      {Object.entries(userStats.data.by_role)
                        .map(([role, count]) => `${ROLE_LABEL[role as keyof typeof ROLE_LABEL] ?? role}: ${count}`)
                        .join(" · ")}
                    </p>
                  </>
                )}
              </div>

              <div className="flex flex-col gap-space-3 p-space-5 rounded-xl bg-surface-container-lowest shadow-level-1">
                <span className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">
                  Recent Blood Requests
                </span>
                <div className="w-10 h-10 rounded-lg bg-surface-container flex items-center justify-center text-primary">
                  <span aria-hidden="true" className="material-symbols-outlined text-[22px]">emergency_home</span>
                </div>
                {bloodRequests.isLoading && (
                  <p className="font-body-sm text-body-sm text-on-surface-variant">Loading…</p>
                )}
                {bloodRequests.isError && (
                  <p role="alert" className="font-body-sm text-body-sm text-error flex items-center gap-2">
                    <span>{toApiError(bloodRequests.error).detail}</span>
                    <button type="button" className="underline flex-shrink-0" onClick={() => bloodRequests.refetch()}>
                      Retry
                    </button>
                  </p>
                )}
                {bloodRequests.data && (
                  <>
                    <span className="font-headline-lg text-headline-lg text-on-surface">
                      {bloodRequests.data.length}
                    </span>
                    <p className="font-body-sm text-body-sm text-on-surface-variant">
                      Most recent (up to 50) fetched from the platform.
                    </p>
                  </>
                )}
              </div>

              <div className="flex flex-col gap-space-3 p-space-5 rounded-xl bg-surface-container-lowest shadow-level-1">
                <span className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">
                  Active Emergencies
                </span>
                <div className="w-10 h-10 rounded-lg bg-surface-container flex items-center justify-center text-primary">
                  <span aria-hidden="true" className="material-symbols-outlined text-[22px]">sos</span>
                </div>
                {activeEmergencies.isLoading && (
                  <p className="font-body-sm text-body-sm text-on-surface-variant">Loading…</p>
                )}
                {activeEmergencies.isError && (
                  <p role="alert" className="font-body-sm text-body-sm text-error flex items-center gap-2">
                    <span>{toApiError(activeEmergencies.error).detail}</span>
                    <button type="button" className="underline flex-shrink-0" onClick={() => activeEmergencies.refetch()}>
                      Retry
                    </button>
                  </p>
                )}
                {activeEmergencies.data && (
                  <span className="font-headline-lg text-headline-lg text-on-surface">
                    {activeEmergencies.data.length}
                  </span>
                )}
              </div>

              <div className="flex flex-col gap-space-3 p-space-5 rounded-xl bg-surface-container-lowest shadow-level-1">
                <span className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">
                  Pending Verifications
                </span>
                <div className="w-10 h-10 rounded-lg bg-surface-container flex items-center justify-center text-primary">
                  <span aria-hidden="true" className="material-symbols-outlined text-[22px]">assignment_late</span>
                </div>
                {pendingVerifications.isLoading && (
                  <p className="font-body-sm text-body-sm text-on-surface-variant">Loading…</p>
                )}
                {pendingVerifications.isError && (
                  <p role="alert" className="font-body-sm text-body-sm text-error flex items-center gap-2">
                    <span>{toApiError(pendingVerifications.error).detail}</span>
                    <button type="button" className="underline flex-shrink-0" onClick={() => pendingVerifications.refetch()}>
                      Retry
                    </button>
                  </p>
                )}
                {pendingVerifications.data && (
                  <span className="font-headline-lg text-headline-lg text-on-surface">
                    {pendingVerifications.data.length}
                  </span>
                )}
              </div>
            </div>

            {/* Main split */}
            <div className="grid grid-cols-1 xl:grid-cols-12 gap-space-6 items-start">
              <div className="xl:col-span-8 flex flex-col gap-space-6">
                <div className="flex flex-col rounded-xl bg-surface-container-lowest shadow-level-1 p-space-6 gap-space-4">
                  <div className="flex items-center justify-between gap-space-3">
                    <div className="flex items-center gap-space-3">
                      <div className="w-8 h-8 rounded-lg bg-surface-container flex items-center justify-center text-primary-container">
                        <span aria-hidden="true" className="material-symbols-outlined text-[20px]">assignment_turned_in</span>
                      </div>
                      <div>
                        <h2 className="font-title-lg text-title-lg text-on-surface">
                          Verification Quick Action Triage
                        </h2>
                        <p className="font-body-sm text-body-sm text-on-surface-variant">
                          Facility, practitioner and donor credentials awaiting review.
                        </p>
                      </div>
                    </div>
                    <Link
                      to="/admin/verifications"
                      className="hidden sm:flex items-center gap-1 font-label-lg text-label-lg text-secondary hover:underline shrink-0"
                    >
                      Open queue
                      <span aria-hidden="true" className="material-symbols-outlined text-[18px]">arrow_forward</span>
                    </Link>
                  </div>
                  {triageVerifications.isLoading && (
                    <p className="font-body-sm text-body-sm text-on-surface-variant">Loading…</p>
                  )}
                  {triageVerifications.isError && (
                  <p role="alert" className="font-body-sm text-body-sm text-error flex items-center gap-2">
                    <span>{toApiError(triageVerifications.error).detail}</span>
                    <button type="button" className="underline flex-shrink-0" onClick={() => triageVerifications.refetch()}>
                      Retry
                    </button>
                  </p>
                )}
                  {triageVerifications.data && triageVerifications.data.length === 0 && (
                    <p className="font-body-sm text-body-sm text-on-surface-variant">
                      No pending verifications.
                    </p>
                  )}
                  {triageVerifications.data && triageVerifications.data.length > 0 && (
                    <div className="flex flex-col divide-y divide-outline-variant/60">
                      {triageVerifications.data.map((v) => (
                        <div key={v.id} className="flex items-center justify-between gap-space-3 py-space-3">
                          <div className="flex flex-col min-w-0">
                            <span className="font-label-md text-label-md text-on-surface font-semibold">
                              {v.kind === "DONOR_IDENTITY" ? "Donor identity" : "Hospital facility"}
                            </span>
                            <span className="font-data-mono text-label-sm text-on-surface-variant">
                              {timeAgo(v.created_at)}
                            </span>
                          </div>
                          <Link
                            to="/admin/verifications"
                            className="font-label-md text-label-md text-secondary hover:underline shrink-0"
                          >
                            Review
                          </Link>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div className="flex flex-col rounded-xl bg-surface-container-lowest shadow-level-1 p-space-6 gap-space-4">
                  <div className="flex items-center gap-space-3">
                    <div className="w-8 h-8 rounded-lg bg-secondary-fixed flex items-center justify-center text-secondary">
                      <span aria-hidden="true" className="material-symbols-outlined text-[20px]">hub</span>
                    </div>
                    <div>
                      <h2 className="font-title-lg text-title-lg text-on-surface">System Health</h2>
                      <p className="font-body-sm text-body-sm text-on-surface-variant">
                        Infrastructure telemetry for the platform.
                      </p>
                    </div>
                  </div>
                  {health.isLoading && (
                    <p className="font-body-sm text-body-sm text-on-surface-variant">Loading…</p>
                  )}
                  {health.isError && (
                    <p role="alert" className="font-body-sm text-body-sm text-error flex items-center gap-2">
                      <span>{toApiError(health.error).detail}</span>
                      <button type="button" className="underline flex-shrink-0" onClick={() => health.refetch()}>
                        Retry
                      </button>
                    </p>
                  )}
                  {health.data && (
                    <div className="flex flex-col gap-space-3">
                      <span
                        className={`font-label-md text-label-md font-semibold ${
                          health.data.status === "ok" ? "text-success" : "text-error"
                        }`}
                      >
                        Overall: {health.data.status}
                      </span>
                      {(
                        [
                          ["Database", health.data.db],
                          ["Redis", health.data.redis],
                          ["Storage", health.data.storage],
                        ] as const
                      ).map(([label, component]) => (
                        <div key={label} className="flex items-center gap-space-3">
                          <span
                            className={`h-2.5 w-2.5 rounded-full shrink-0 ${
                              component.ok ? "bg-success" : "bg-error"
                            }`}
                          />
                          <span className="font-label-md text-label-md text-on-surface">{label}</span>
                          {component.detail && (
                            <span className="font-body-sm text-body-sm text-on-surface-variant">
                              {component.detail}
                            </span>
                          )}
                        </div>
                      ))}
                      {/* Graph engine: `configured=false` is a normal "feature off" state,
                          not a fault -- shown neutral rather than red. */}
                      <div className="flex items-center gap-space-3">
                        <span
                          className={`h-2.5 w-2.5 rounded-full shrink-0 ${
                            health.data.graph.status === "ok"
                              ? "bg-success"
                              : health.data.graph.status === "disabled"
                              ? "bg-on-surface-variant"
                              : "bg-error"
                          }`}
                        />
                        <span className="font-label-md text-label-md text-on-surface">
                          Graph (Neo4j): {health.data.graph.status}
                        </span>
                      </div>
                      {/* SMS: `configured=false` (no MSG91 credentials) is this
                          deployment's normal, expected state -- shown neutral, same
                          treatment as the graph engine. This is a configuration/recent-
                          history readout, not a live delivery guarantee -- it never claims
                          SMS delivery is available (see LIFELINK_SMS_STATUS.md). Not shown
                          to normal users; admin-only technical status. */}
                      <div className="flex items-center gap-space-3">
                        <span
                          className={`h-2.5 w-2.5 rounded-full shrink-0 ${
                            health.data.sms.configured
                              ? health.data.sms.recent_failures === 0
                                ? "bg-success"
                                : "bg-error"
                              : "bg-on-surface-variant"
                          }`}
                        />
                        <span className="font-label-md text-label-md text-on-surface">
                          SMS ({health.data.sms.provider}): {health.data.sms.status}
                        </span>
                        {health.data.sms.configured && (
                          <span className="font-body-sm text-body-sm text-on-surface-variant">
                            {health.data.sms.recent_failures} recent failure(s)
                          </span>
                        )}
                      </div>
                      {/* Phase 30: RQ email-queue backlog visibility -- added
                          directly in response to Phase 28's 148,965-stale-job
                          finding. "healthy" is shown neutral/success; "warning"
                          amber; "critical" red -- this never claims the
                          backend's own overall status changed (it doesn't --
                          see AdminRqHealthOut's own docstring), it is purely
                          informational for an operator. */}
                      <div className="flex items-center gap-space-3">
                        <span
                          className={`h-2.5 w-2.5 rounded-full shrink-0 ${
                            health.data.rq.status === "healthy"
                              ? "bg-success"
                              : health.data.rq.status === "warning"
                              ? "bg-warning"
                              : "bg-error"
                          }`}
                        />
                        <span className="font-label-md text-label-md text-on-surface">
                          Email queue: {health.data.rq.queued} queued, {health.data.rq.failed}{" "}
                          failed, {health.data.rq.workers} worker(s)
                        </span>
                      </div>
                      <Link
                        to="/admin/transport-routes"
                        className="font-label-sm text-label-sm text-secondary hover:underline mt-1"
                      >
                        Manage transport routes &amp; resync graph →
                      </Link>
                      {/* Phase 30: safe, non-secret build identification --
                          see AdminVersionOut's own docstring. */}
                      <span className="font-data-mono text-label-sm text-on-surface-variant">
                        v{health.data.version.app_version} · {health.data.version.git_commit_sha}
                      </span>
                    </div>
                  )}
                </div>
              </div>

              <div className="xl:col-span-4 flex flex-col gap-space-6">
                {/* Real notifications feed */}
                <div className="flex flex-col rounded-xl bg-surface-container-lowest shadow-level-1 p-space-6 gap-space-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-space-3">
                      <div className="w-8 h-8 rounded-lg bg-primary-fixed flex items-center justify-center text-primary">
                        <span aria-hidden="true" className="material-symbols-outlined text-[20px]">notifications</span>
                      </div>
                      <div>
                        <h2 className="font-title-lg text-title-lg text-on-surface">Your Notifications</h2>
                        <span className="font-data-mono text-label-sm text-on-surface-variant">
                          Live — GET /notifications
                        </span>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => readAll.mutate()}
                      disabled={unread === 0 || readAll.isPending}
                      className="text-secondary text-label-sm font-label-sm hover:underline disabled:opacity-40 disabled:no-underline shrink-0"
                    >
                      Mark all read
                    </button>
                  </div>

                  {notifError && (
                    <p role="alert" className="font-body-sm text-body-sm text-error">{notifError}</p>
                  )}
                  {notifications.isLoading && (
                    <p className="font-body-sm text-body-sm text-on-surface-variant">Loading…</p>
                  )}
                  {notifications.isError && (
                    <p role="alert" className="font-body-sm text-body-sm text-error flex items-center gap-3">
                      <span>Couldn't load notifications.</span>
                      <button type="button" className="underline" onClick={() => notifications.refetch()}>
                        Retry
                      </button>
                    </p>
                  )}
                  {notifications.data && items.length === 0 && (
                    <p className="font-body-sm text-body-sm text-on-surface-variant">
                      You're all caught up — no notifications yet.
                    </p>
                  )}
                  <div className="flex flex-col divide-y divide-outline-variant/60 max-h-96 overflow-y-auto">
                    {items.slice(0, 8).map((n) => (
                      <div key={n.id} className="flex items-start gap-space-3 py-space-3">
                        <span
                          className={`mt-1.5 h-2 w-2 rounded-full shrink-0 ${
                            n.read_at ? "bg-transparent" : "bg-primary"
                          }`}
                        />
                        <div className="flex flex-col min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-space-2">
                            <span className="font-label-md text-label-md text-on-surface font-semibold truncate">
                              {n.title}
                            </span>
                            <span className="font-data-mono text-label-sm text-on-surface-variant shrink-0">
                              {timeAgo(n.created_at)}
                            </span>
                          </div>
                          {n.body && (
                            <p className="font-body-sm text-body-sm text-on-surface-variant mt-0.5 line-clamp-2">
                              {n.body}
                            </p>
                          )}
                        </div>
                        {!n.read_at && (
                          <button
                            type="button"
                            disabled={reading.has(n.id)}
                            onClick={() => !reading.has(n.id) && readOne.mutate(n.id)}
                            className="text-secondary text-label-sm font-label-sm hover:underline shrink-0 disabled:opacity-40"
                          >
                            {reading.has(n.id) ? "Reading…" : "Read"}
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>

                <div className="flex flex-col rounded-xl bg-surface-container-lowest shadow-level-1 p-space-6 gap-space-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-space-3">
                      <div className="w-8 h-8 rounded-lg bg-primary-fixed flex items-center justify-center text-primary">
                        <span aria-hidden="true" className="material-symbols-outlined text-[20px]">history_edu</span>
                      </div>
                      <div>
                        <h2 className="font-title-lg text-title-lg text-on-surface">System Audit Feed</h2>
                        <span className="font-data-mono text-label-sm text-on-surface-variant">
                          Platform-wide event stream
                        </span>
                      </div>
                    </div>
                  </div>
                  {auditLog.isLoading && (
                    <p className="font-body-sm text-body-sm text-on-surface-variant">Loading…</p>
                  )}
                  {auditLog.isError && (
                    <p role="alert" className="font-body-sm text-body-sm text-error flex items-center gap-2">
                      <span>{toApiError(auditLog.error).detail}</span>
                      <button type="button" className="underline flex-shrink-0" onClick={() => auditLog.refetch()}>
                        Retry
                      </button>
                    </p>
                  )}
                  {auditLog.data && auditLog.data.items.length === 0 && (
                    <p className="font-body-sm text-body-sm text-on-surface-variant">
                      No audit events recorded yet.
                    </p>
                  )}
                  {auditLog.data && auditLog.data.items.length > 0 && (
                    <div className="flex flex-col divide-y divide-outline-variant/60 max-h-96 overflow-y-auto">
                      {auditLog.data.items.map((entry) => (
                        <div key={entry.id} className="flex items-start justify-between gap-space-3 py-space-2">
                          <div className="flex flex-col min-w-0">
                            <span className="font-label-md text-label-md text-on-surface font-semibold truncate">
                              {entry.action}
                            </span>
                            <span className="font-data-mono text-label-sm text-on-surface-variant truncate">
                              {entry.actor_user_id ? entry.actor_user_id.slice(0, 8) : "system"}
                            </span>
                          </div>
                          <span className="font-data-mono text-label-sm text-on-surface-variant shrink-0">
                            {timeAgo(entry.created_at)}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                  <button
                    type="button"
                    disabled
                    title="No audit-log endpoint to export from yet"
                    aria-disabled="true"
                    className="w-full py-2 rounded-lg bg-surface-container font-label-md text-label-md text-on-surface-variant flex items-center justify-center gap-space-2 cursor-not-allowed opacity-60"
                  >
                    <span aria-hidden="true" className="material-symbols-outlined text-[16px]">download</span>
                    <span>Export audit log (CSV)</span>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </main>
      </div>
    </>
  );
}
