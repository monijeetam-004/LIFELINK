/**
 * Family / Attendant Care — Dashboard
 * Adapted from the Stitch export: lifelink_1b.1_family_attendant_care_dashboard/code.html
 *
 * The Stitch mock used invented clinical telemetry (attending physician, cold-chain
 * temperature, bed numbers, hemoglobin readings, a toll-free hotline) that the LifeLink
 * backend does not model or expose. Layout, color system, spacing and component grammar
 * (sidebar, hero banner, filter tabs, card structure, stat widgets, activity feed) are
 * preserved from the design; every data point shown is wired to a real endpoint
 * (/family/links, /blood-requests, /emergencies, /notifications). Fields the backend
 * doesn't have are omitted rather than invented.
 */
import { useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Menu } from "lucide-react";

import NotificationsBell from "../../components/NotificationsBell";
import BloodRequestModal from "../../components/BloodRequestModal";
import SosModal from "../../components/SosModal";
import RequestTrackDrawer from "../../components/RequestTrackDrawer";
import EmergencyTrackDrawer from "../../components/EmergencyTrackDrawer";
import MobileNavDrawer from "../../components/MobileNavDrawer";
import { fieldClass, primaryButtonClass } from "../../components/Modal";
import { useAuth } from "../../lib/auth";
import { toApiError } from "../../lib/api";
import {
  BLOOD_LABEL,
  listBloodRequests,
  listEmergencies,
  listFamilyLinks,
  listNotifications,
  requestFamilyLink,
  type BloodRequest,
  type Emergency,
  OPEN_REQUEST_STATUSES,
} from "../../lib/domain";
import { useLiveFallbackInterval } from "../../lib/realtime";

const EMERGENCY_OPEN = ["ACTIVE", "ACKNOWLEDGED", "MATCHING", "ASSISTANCE_FOUND"];
const REQUEST_OPEN = OPEN_REQUEST_STATUSES;

const NAV = [
  { path: "/family", icon: "grid_view", label: "Dashboard" },
  { path: "/family/patients", icon: "groups", label: "Linked Patients" },
  { path: "/notifications", icon: "notifications", label: "Notifications" },
  { path: "/help", icon: "menu_book", label: "Guidelines & Support" },
  { path: "/settings", icon: "tune", label: "Settings" },
  { path: "/profile", icon: "account_circle", label: "Profile" },
];

function timeAgo(iso: string): string {
  const s = Math.max(1, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function FamilyDashboard() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const qc = useQueryClient();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [filter, setFilter] = useState<"all" | "active" | "standby">("all");
  const [showLink, setShowLink] = useState(false);
  const [linkEmail, setLinkEmail] = useState("");
  const [linkRelationship, setLinkRelationship] = useState("");
  const [linkError, setLinkError] = useState("");
  const [showNew, setShowNew] = useState<string | null>(null); // patient email for new request
  const [showSos, setShowSos] = useState<string | null>(null);
  const [trackId, setTrackId] = useState<string | null>(null);
  const [emTrackId, setEmTrackId] = useState<string | null>(null);

  const links = useQuery({ queryKey: ["family", "links"], queryFn: listFamilyLinks });
  const requests = useQuery({ queryKey: ["blood-requests", "list"], queryFn: () => listBloodRequests() });
  const emergencies = useQuery({
    queryKey: ["emergencies", "list"],
    queryFn: listEmergencies,
    // Phase F: live updates arrive over the event stream; this interval only
    // applies while that stream is down (graceful degradation).
    refetchInterval: useLiveFallbackInterval(20_000),
  });
  const notifications = useQuery({
    queryKey: ["notifications", "list"],
    queryFn: () => listNotifications(false),
  });

  const linkMutation = useMutation({
    mutationFn: () => requestFamilyLink(linkEmail, linkRelationship),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["family", "links"] });
      setShowLink(false);
      setLinkEmail("");
      setLinkRelationship("");
      setLinkError("");
    },
    onError: (e) => setLinkError(toApiError(e).detail),
  });

  const acceptedLinks = (links.data ?? []).filter((l) => l.status === "ACCEPTED");

  // Every request/emergency for each linked patient — filed by this family member OR by the
  // patient directly from their own account. The backend scopes /blood-requests and
  // /emergencies to "things I filed, plus things any patient I have an ACCEPTED link to
  // filed," so nothing the patient does themselves is hidden here.
  const byPatient = useMemo(() => {
    const map = new Map<string, { requests: BloodRequest[]; emergencies: Emergency[] }>();
    for (const l of acceptedLinks) map.set(l.patient_user_id, { requests: [], emergencies: [] });
    for (const r of requests.data ?? []) {
      const bucket = map.get(r.patient_user_id);
      if (bucket) bucket.requests.push(r);
    }
    for (const e of emergencies.data ?? []) {
      if (!e.patient_user_id) continue;
      const bucket = map.get(e.patient_user_id);
      if (bucket) bucket.emergencies.push(e);
    }
    return map;
  }, [acceptedLinks, requests.data, emergencies.data]);

  function isActive(patientId: string) {
    const bucket = byPatient.get(patientId);
    if (!bucket) return false;
    return bucket.emergencies.some((e) => EMERGENCY_OPEN.includes(e.status));
  }

  const visibleLinks = acceptedLinks.filter((l) => {
    if (filter === "all") return true;
    if (filter === "active") return isActive(l.patient_user_id);
    return !isActive(l.patient_user_id);
  });
  const activeCount = acceptedLinks.filter((l) => isActive(l.patient_user_id)).length;
  const pendingCount = (links.data ?? []).filter((l) => l.status === "PENDING").length;

  const totalOpenRequests = [...byPatient.values()].reduce(
    (n, b) => n + b.requests.filter((r) => REQUEST_OPEN.includes(r.status)).length,
    0,
  );

  return (
    <>
      <aside className="fixed left-0 top-0 h-full w-72 bg-surface-container-lowest z-40 flex flex-col justify-between shadow-[0_1px_8px_rgba(0,0,0,0.04)] hidden lg:flex">
        <div className="flex flex-col">
          <div className="h-20 px-space-6 flex items-center gap-space-3">
            <div className="w-10 h-10 rounded-lg bg-primary-fixed flex items-center justify-center text-primary-container">
              <span aria-hidden="true" className="material-symbols-outlined text-[24px]">cardiology</span>
            </div>
            <div className="flex flex-col">
              <span className="font-title-md text-title-md text-primary tracking-tight">LifeLink</span>
              <span className="font-label-sm text-label-sm text-on-surface-variant font-medium">Family Portal</span>
            </div>
          </div>
          <div className="px-space-6 pt-space-6 pb-space-2">
            <span className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant font-semibold">
              Operational Care
            </span>
          </div>
          <nav className="flex flex-col gap-space-1 px-space-4">
            {NAV.map((n) => (
              <Link
                key={n.path}
                to={n.path}
                className={`flex items-center justify-between px-space-4 py-space-3 transition-all rounded-lg ${
                  n.path === "/family"
                    ? "bg-primary-container text-on-primary font-title-md shadow-level-1"
                    : "text-on-surface-variant hover:bg-surface-container-low hover:text-on-surface"
                }`}
              >
                <div className="flex items-center gap-space-3">
                  <span aria-hidden="true" className="material-symbols-outlined text-[20px]">{n.icon}</span>
                  <span className="font-label-lg text-label-lg">{n.label}</span>
                </div>
                {n.path === "/family/patients" && pendingCount > 0 && (
                  <span className="bg-secondary-fixed text-on-secondary-fixed font-label-sm text-label-sm px-space-2 py-0.5 rounded-full font-semibold">
                    {pendingCount}
                  </span>
                )}
              </Link>
            ))}
          </nav>
        </div>
        <div className="flex flex-col gap-space-4 p-space-4">
          <div className="p-space-3 rounded-xl bg-surface-container-low shadow-[0_1px_3px_0_rgba(25,28,30,0.02)]">
            <div className="flex items-center gap-space-2 text-secondary mb-space-1">
              <span aria-hidden="true" className="material-symbols-outlined text-[18px]">verified_user</span>
              <span className="font-label-sm text-label-sm font-semibold">Signed in as Family</span>
            </div>
            <p className="font-body-sm text-body-sm text-on-surface-variant leading-tight truncate">
              {user?.email}
            </p>
          </div>
          <button
            onClick={() => logout().then(() => navigate("/login", { replace: true }))}
            className="flex items-center gap-space-3 px-space-4 py-space-3 rounded-lg text-on-surface-variant hover:bg-surface-container-low hover:text-error transition-all"
          >
            <span aria-hidden="true" className="material-symbols-outlined text-[20px]">logout</span>
            <span className="font-label-lg text-label-lg">Logout</span>
          </button>
        </div>
      </aside>

      <MobileNavDrawer
        open={mobileNavOpen}
        onClose={() => setMobileNavOpen(false)}
        activePath={location.pathname}
        title="LifeLink"
        subtitle="Family Portal"
        sections={[{ heading: "Operational Care", items: NAV }]}
        footer={
          <>
            <div className="p-space-3 rounded-xl bg-surface-container-low">
              <div className="flex items-center gap-space-2 text-secondary mb-space-1">
                <span aria-hidden="true" className="material-symbols-outlined text-[18px]">verified_user</span>
                <span className="font-label-sm text-label-sm font-semibold">Signed in as Family</span>
              </div>
              <p className="font-body-sm text-body-sm text-on-surface-variant leading-tight truncate">
                {user?.email}
              </p>
            </div>
            <button
              type="button"
              onClick={() => logout().then(() => navigate("/login", { replace: true }))}
              className="w-full flex items-center gap-space-3 px-space-4 py-space-3 rounded-lg text-on-surface-variant hover:bg-surface-container-low hover:text-error transition-all mt-space-2"
            >
              <span aria-hidden="true" className="material-symbols-outlined text-[20px]">logout</span>
              <span className="font-label-lg text-label-lg">Logout</span>
            </button>
          </>
        }
      />

      <div className="lg:pl-72">
        <header className="sticky top-0 lg:fixed lg:top-0 lg:left-72 lg:right-0 h-20 bg-surface/85 backdrop-blur-xl z-30 shadow-[0_1px_8px_rgba(0,0,0,0.04)] px-space-4 lg:px-space-8">
          <div className="h-20 w-full flex items-center justify-between gap-space-4">
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
                <span className="font-title-lg text-title-lg text-on-surface tracking-tight leading-snug truncate">
                  Family / Attendant Care
                </span>
                <span className="font-label-sm text-label-sm text-on-surface-variant hidden sm:block">
                  Hospital &amp; Medical Logistics Portal
                </span>
              </div>
            </div>
            <div className="flex items-center gap-space-3">
              <button
                onClick={() => setShowLink(true)}
                className="hidden sm:inline-flex items-center gap-space-2 px-space-5 py-2.5 rounded-full bg-primary-container text-on-primary font-label-lg text-label-lg hover:bg-primary transition-colors shadow-[0_2px_8px_-2px_rgba(183,19,26,0.25)]"
              >
                <span aria-hidden="true" className="material-symbols-outlined text-[18px]">person_add</span>
                <span>Link a Patient</span>
              </button>
              <NotificationsBell buttonClassName="relative p-2.5 rounded-full bg-surface-container-lowest text-on-surface-variant hover:bg-surface-container-low hover:text-on-surface transition-colors shadow-[0_1px_4px_rgba(0,0,0,0.04)]" />
              <div className="flex items-center gap-space-3 pl-space-2">
                <div className="hidden md:flex flex-col items-end">
                  <span className="font-label-lg text-label-lg text-on-surface font-semibold truncate max-w-[160px]">
                    {user?.full_name}
                  </span>
                  <span className="font-body-sm text-body-sm text-on-surface-variant">Family Attendant</span>
                </div>
                <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center flex-shrink-0">
                  <span aria-hidden="true" className="material-symbols-outlined text-on-primary text-[18px]">person</span>
                </div>
              </div>
            </div>
          </div>
        </header>

        <main className="w-full lg:pt-20 bg-background min-h-screen px-space-4 sm:px-space-6 lg:px-space-8 py-space-6">
          <div className="flex flex-col w-full gap-space-8">
            {/* Hero */}
            <div className="relative overflow-hidden rounded-xl bg-surface-container-lowest shadow-sm p-space-6 lg:p-space-8">
              <div className="absolute -right-20 -top-20 w-80 h-80 rounded-full bg-primary-fixed/30 blur-3xl pointer-events-none" />
              <div className="relative z-10 flex flex-col lg:flex-row lg:items-center lg:justify-between gap-space-6">
                <div className="flex flex-col max-w-2xl">
                  <div className="flex flex-wrap items-center gap-space-3 mb-space-2">
                    <span className="inline-flex items-center gap-1.5 px-space-3 py-1 rounded-full bg-secondary-fixed text-on-secondary-fixed font-label-sm text-label-sm font-semibold">
                      <span className="w-2 h-2 rounded-full bg-secondary" />
                      Connected to {acceptedLinks.length} linked patient{acceptedLinks.length === 1 ? "" : "s"}
                    </span>
                    {pendingCount > 0 && (
                      <span className="inline-flex items-center gap-1.5 px-space-3 py-1 rounded-full bg-surface-container text-on-surface-variant font-label-sm text-label-sm font-semibold">
                        {pendingCount} link request{pendingCount === 1 ? "" : "s"} pending
                      </span>
                    )}
                  </div>
                  <h1 className="font-headline-lg text-headline-lg text-on-surface tracking-tight">
                    Family Care &amp; Patient Guardian Console
                  </h1>
                  <p className="font-body-lg text-body-lg text-on-surface-variant mt-space-1">
                    Monitor blood requests and emergencies you manage for your linked loved ones.
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-space-3">
                  <button
                    onClick={() => setShowLink(true)}
                    className="inline-flex items-center gap-space-2 px-space-6 py-3 rounded-full bg-primary-container text-on-primary hover:bg-primary transition-all font-label-lg text-label-lg shadow-md active:scale-95"
                  >
                    <span aria-hidden="true" className="material-symbols-outlined text-[20px]">favorite</span>
                    <span>Link a Patient</span>
                  </button>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-space-2 mt-space-6 pt-space-6">
                {(["all", "active", "standby"] as const).map((f) => (
                  <button
                    key={f}
                    onClick={() => setFilter(f)}
                    className={`px-space-4 py-2 rounded-full font-label-lg text-label-lg transition-all flex items-center gap-space-2 ${
                      filter === f
                        ? "bg-surface-container-high text-on-surface font-semibold"
                        : "text-on-surface-variant hover:bg-surface-container"
                    }`}
                  >
                    {f === "active" && <span className="w-2 h-2 rounded-full bg-error animate-pulse" />}
                    {f === "standby" && <span className="w-2 h-2 rounded-full bg-secondary" />}
                    {f === "all" && `All Linked Patients (${acceptedLinks.length})`}
                    {f === "active" && `Active Emergency (${activeCount})`}
                    {f === "standby" && `Standby (${acceptedLinks.length - activeCount})`}
                  </button>
                ))}
              </div>
            </div>

            {/* Patient cards */}
            <div className="flex flex-col gap-space-6">
              <div className="flex items-center justify-between">
                <span className="font-title-lg text-title-lg text-on-surface">Your Linked Patients</span>
              </div>

              {links.isLoading && (
                <div className="rounded-xl bg-surface-container-lowest shadow-sm p-space-8 text-center text-on-surface-variant">
                  Loading your linked patients…
                </div>
              )}
              {links.isError && (
                <div role="alert" className="rounded-xl bg-surface-container-lowest shadow-sm p-space-8 text-center text-error">
                  <p>Couldn't load your linked patients. {toApiError(links.error).detail}</p>
                  <button
                    type="button"
                    onClick={() => links.refetch()}
                    className="mt-2 text-secondary font-label-md hover:underline"
                  >
                    Retry
                  </button>
                </div>
              )}
              {links.data && (requests.isError || emergencies.isError) && (
                <div role="alert" className="rounded-xl border border-error/30 bg-error/5 p-4 text-body-sm text-error">
                  {requests.isError && <p>Couldn't load requests — the figures below may be incomplete. {toApiError(requests.error).detail}</p>}
                  {emergencies.isError && <p>Couldn't load emergencies — the figures below may be incomplete. {toApiError(emergencies.error).detail}</p>}
                </div>
              )}

              {links.data && acceptedLinks.length === 0 && (
                <div className="rounded-xl bg-surface-container-lowest shadow-sm p-space-12 text-center flex flex-col items-center gap-space-6">
                  <div className="w-20 h-20 rounded-full bg-primary-fixed flex items-center justify-center text-primary-container shadow-inner">
                    <span aria-hidden="true" className="material-symbols-outlined text-[40px]">group_add</span>
                  </div>
                  <div className="flex flex-col max-w-lg gap-space-2">
                    <span className="font-headline-md text-headline-md text-on-surface">
                      No linked patients yet
                    </span>
                    <p className="font-body-lg text-body-lg text-on-surface-variant">
                      Enter your loved one's LifeLink email to send a link request. Once they accept it from
                      their account, you'll be able to file and track requests on their behalf here.
                    </p>
                  </div>
                  <button onClick={() => setShowLink(true)} className={primaryButtonClass}>
                    Send link request
                  </button>
                  {pendingCount > 0 && (
                    <p className="text-sm text-on-surface-variant">
                      You have {pendingCount} pending link request{pendingCount === 1 ? "" : "s"} awaiting the
                      patient's acceptance.
                    </p>
                  )}
                </div>
              )}

              {visibleLinks.map((l) => {
                const bucket = byPatient.get(l.patient_user_id) ?? { requests: [], emergencies: [] };
                const activeEm = bucket.emergencies.find((e) => EMERGENCY_OPEN.includes(e.status));
                const openRequests = bucket.requests.filter((r) => REQUEST_OPEN.includes(r.status));
                return (
                  <div
                    key={l.id}
                    className="relative rounded-xl bg-surface-container-lowest shadow-md overflow-hidden"
                  >
                    <div className={`h-1.5 w-full ${activeEm ? "bg-primary-container" : "bg-secondary"}`} />
                    <div className="p-space-6 lg:p-space-8 flex flex-col gap-space-6">
                      <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-space-4">
                        <div className="flex items-start gap-space-4">
                          <div className="w-14 h-14 rounded-xl bg-surface-container-high flex items-center justify-center text-on-surface-variant flex-shrink-0">
                            <span aria-hidden="true" className="material-symbols-outlined text-[28px]">person</span>
                          </div>
                          <div className="flex flex-col min-w-0">
                            <div className="flex flex-wrap items-center gap-space-2">
                              <span className="font-headline-md text-headline-md text-on-surface truncate">
                                {l.patient?.full_name ?? "Linked patient"}
                              </span>
                              <span className="px-space-3 py-0.5 rounded-full bg-surface-container text-on-surface font-label-sm text-label-sm font-semibold">
                                {l.relationship}
                              </span>
                            </div>
                            <span className="font-body-sm text-body-sm text-on-surface-variant truncate">
                              {l.patient?.email}
                            </span>
                          </div>
                        </div>
                        {activeEm ? (
                          <div className="flex items-center gap-space-3 self-start px-space-4 py-space-2 rounded-xl bg-error-container/60 shadow-sm">
                            <div className="relative flex items-center justify-center">
                              <span className="w-3 h-3 rounded-full bg-error" />
                              <span className="w-3 h-3 rounded-full bg-error animate-ping absolute" />
                            </div>
                            <div className="flex flex-col">
                              <span className="font-label-lg text-label-lg text-on-error-container font-bold">
                                Active emergency — {activeEm.status.replace(/_/g, " ").toLowerCase()}
                              </span>
                              <span className="font-label-sm text-label-sm text-error font-medium">
                                {activeEm.blood_type ? BLOOD_LABEL[activeEm.blood_type] : "Any type"}
                                {activeEm.location_text ? ` · ${activeEm.location_text}` : ""}
                              </span>
                            </div>
                          </div>
                        ) : (
                          <div className="flex items-center gap-space-3 self-start px-space-4 py-space-2 rounded-xl bg-surface-container shadow-xs">
                            <span className="w-3 h-3 rounded-full bg-secondary" />
                            <span className="font-label-lg text-label-lg text-on-surface font-semibold">
                              No active emergencies
                            </span>
                          </div>
                        )}
                      </div>

                      <div className="p-space-5 rounded-xl bg-surface-container-low flex flex-col gap-space-3">
                        <div className="flex items-center justify-between text-on-surface">
                          <span className="font-label-md text-label-md uppercase tracking-wider text-on-surface-variant font-semibold">
                            Requests
                          </span>
                          <span className="font-label-sm text-label-sm text-primary font-bold">
                            {openRequests.length} open
                          </span>
                        </div>
                        {bucket.requests.length === 0 ? (
                          <p className="font-body-sm text-body-sm text-on-surface-variant">
                            No requests filed yet.
                          </p>
                        ) : (
                          <div className="flex flex-col gap-space-2">
                            {bucket.requests.slice(0, 3).map((r) => (
                              <button
                                key={r.id}
                                onClick={() => setTrackId(r.id)}
                                className="flex items-center justify-between text-left px-space-3 py-space-2 rounded-lg bg-surface-container-lowest hover:bg-surface-container transition-colors"
                              >
                                <span className="font-body-sm text-body-sm text-on-surface">
                                  {BLOOD_LABEL[r.blood_type]} · {r.status[0] + r.status.slice(1).toLowerCase()}
                                  {r.filed_by_family && (
                                    <span className="ml-2 text-on-surface-variant">
                                      · Requested by family member
                                    </span>
                                  )}
                                </span>
                                <span className="text-secondary font-label-sm text-label-sm">Track</span>
                              </button>
                            ))}
                          </div>
                        )}
                      </div>

                      <div className="flex flex-wrap items-center justify-between gap-space-4 pt-space-2">
                        <Link
                          to={`/family/patients/${l.patient_user_id}`}
                          className="text-secondary font-label-lg text-label-lg hover:underline"
                        >
                          View patient details →
                        </Link>
                        <div className="flex flex-wrap items-center gap-space-3">
                          <button
                            onClick={() => l.patient?.email && setShowSos(l.patient.email)}
                            className="px-space-4 py-2.5 rounded-lg bg-error/10 text-error hover:bg-error/20 transition-colors font-label-lg text-label-lg flex items-center gap-space-2"
                          >
                            <span aria-hidden="true" className="material-symbols-outlined text-[18px]">emergency</span>
                            <span>Emergency SOS</span>
                          </button>
                          <button
                            onClick={() => l.patient?.email && setShowNew(l.patient.email)}
                            className="px-space-6 py-2.5 rounded-full bg-primary-container text-on-primary hover:bg-primary transition-all font-label-lg text-label-lg shadow-md flex items-center gap-space-2"
                          >
                            <span aria-hidden="true" className="material-symbols-outlined text-[18px]">add</span>
                            <span>New request</span>
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Stats + activity */}
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-space-6">
              <div className="lg:col-span-7 flex flex-col gap-space-6">
                <span className="font-title-lg text-title-lg text-on-surface">Operational Care Summary</span>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-space-4">
                  <div className="p-space-5 rounded-xl bg-surface-container-lowest shadow-sm flex flex-col gap-space-3">
                    <span className="font-label-sm text-label-sm uppercase font-bold tracking-wider text-on-surface-variant">
                      Active Emergencies
                    </span>
                    <span className="font-display-lg text-display-lg font-bold text-error tracking-tight">
                      {activeCount}
                    </span>
                    <span className="font-body-sm text-body-sm text-on-surface-variant">Across linked patients</span>
                  </div>
                  <div className="p-space-5 rounded-xl bg-surface-container-lowest shadow-sm flex flex-col gap-space-3">
                    <span className="font-label-sm text-label-sm uppercase font-bold tracking-wider text-on-surface-variant">
                      Open Requests
                    </span>
                    <span className="font-display-lg text-display-lg font-bold text-on-surface tracking-tight">
                      {totalOpenRequests}
                    </span>
                    <span className="font-body-sm text-body-sm text-on-surface-variant">Filed for your patients</span>
                  </div>
                  <div className="p-space-5 rounded-xl bg-primary text-on-primary shadow-md flex flex-col gap-space-3">
                    <span className="font-label-sm text-label-sm uppercase font-bold tracking-wider text-on-primary-container">
                      Care Circle Links
                    </span>
                    <span className="font-headline-sm text-headline-sm font-bold text-on-primary tracking-tight">
                      {acceptedLinks.length} active
                    </span>
                    <span className="font-body-sm text-body-sm text-on-primary/80">
                      {pendingCount} pending acceptance
                    </span>
                  </div>
                </div>
              </div>
              <div className="lg:col-span-5 flex flex-col gap-space-4">
                <span className="font-title-lg text-title-lg text-on-surface">Recent Activity</span>
                <div className="p-space-6 rounded-xl bg-surface-container-lowest shadow-sm flex flex-col gap-space-5">
                  {notifications.isLoading && (
                    <p className="text-sm text-on-surface-variant">Loading…</p>
                  )}
                  {notifications.isError && (
                    <p role="alert" className="text-sm text-error">
                      Couldn't load recent activity. {toApiError(notifications.error).detail}
                    </p>
                  )}
                  {notifications.data && notifications.data.length === 0 && (
                    <p className="text-sm text-on-surface-variant">No recent activity.</p>
                  )}
                  {notifications.data?.slice(0, 6).map((n) => (
                    <div key={n.id} className="flex items-start gap-space-4">
                      <div className="w-9 h-9 rounded-full bg-surface-container text-on-surface flex items-center justify-center shrink-0 shadow-xs">
                        <span aria-hidden="true" className="material-symbols-outlined text-[18px]">notifications</span>
                      </div>
                      <div className="flex flex-col min-w-0">
                        <div className="flex items-center justify-between gap-space-2">
                          <span className="font-label-md text-label-md font-bold text-on-surface">{n.title}</span>
                          <span className="font-body-sm text-body-sm text-on-surface-variant shrink-0">
                            {timeAgo(n.created_at)}
                          </span>
                        </div>
                        {n.body && (
                          <p className="font-body-md text-body-md text-on-surface-variant mt-1">{n.body}</p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </main>
      </div>

      {/* Link a patient modal */}
      {showLink && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/40"
          onMouseDown={() => setShowLink(false)}
        >
          <div
            className="w-full max-w-md bg-surface rounded-2xl shadow-2xl border border-border-subtle p-6"
            onMouseDown={(e) => e.stopPropagation()}
          >
            <h3 className="font-headline-md text-headline-md text-on-surface mb-4">Link a patient</h3>
            <div className="flex flex-col gap-4">
              <div>
                <label className="block text-label-sm font-label-sm uppercase tracking-wider text-on-surface-variant mb-1.5">
                  Patient's LifeLink email
                </label>
                <input
                  type="email"
                  className={fieldClass}
                  value={linkEmail}
                  onChange={(e) => setLinkEmail(e.target.value)}
                  placeholder="patient@example.com"
                />
              </div>
              <div>
                <label className="block text-label-sm font-label-sm uppercase tracking-wider text-on-surface-variant mb-1.5">
                  Your relationship to them
                </label>
                <input
                  className={fieldClass}
                  value={linkRelationship}
                  onChange={(e) => setLinkRelationship(e.target.value)}
                  placeholder="Daughter, spouse, caregiver…"
                />
              </div>
              {linkError && <p className="text-error text-body-sm">{linkError}</p>}
              <p className="text-xs text-on-surface-variant">
                The patient must accept this request from their own account before you can act on their
                behalf.
              </p>
              <div className="flex justify-end gap-3 pt-2">
                <button
                  className="border border-border-subtle text-on-surface font-label-md text-label-md px-5 py-2.5 rounded-lg hover:bg-surface-container-low transition-colors"
                  onClick={() => setShowLink(false)}
                >
                  Cancel
                </button>
                <button
                  className={primaryButtonClass}
                  disabled={!linkEmail || !linkRelationship || linkMutation.isPending}
                  onClick={() => linkMutation.mutate()}
                >
                  {linkMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                  Send link request
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <BloodRequestModal
        open={!!showNew}
        onClose={() => setShowNew(null)}
        defaultPatientEmail={showNew ?? undefined}
      />
      <SosModal open={!!showSos} onClose={() => setShowSos(null)} defaultPatientEmail={showSos ?? undefined} />
      <RequestTrackDrawer requestId={trackId} onClose={() => setTrackId(null)} />
      <EmergencyTrackDrawer emergencyId={emTrackId} onClose={() => setEmTrackId(null)} mode="creator" />
    </>
  );
}
