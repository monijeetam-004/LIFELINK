/**
 * Patient Overview Dashboard
 * Layout/markup converted 1:1 from the Google Stitch export (patient_dashboard).
 * Phase 2 wires the request timeline, "New request", SOS and notifications to the
 * real backend; the visual design and page structure are unchanged.
 */
import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Menu } from "lucide-react";

import NotificationsBell from "../components/NotificationsBell";
import BloodRequestModal from "../components/BloodRequestModal";
import RequestTrackDrawer from "../components/RequestTrackDrawer";
import EmergencyTrackDrawer from "../components/EmergencyTrackDrawer";
import SosModal from "../components/SosModal";
import MobileNavDrawer from "../components/MobileNavDrawer";
import { toApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  acceptFamilyLink,
  BLOOD_LABEL,
  listBloodRequests,
  listEmergencies,
  listFamilyLinks,
  rejectFamilyLink,
  revokeFamilyLink,
  type BloodRequestStatus,
  isOpenRequestStatus,
} from "../lib/domain";
import { useLiveFallbackInterval } from "../lib/realtime";

const EMERGENCY_OPEN = ["ACTIVE", "ACKNOWLEDGED", "MATCHING", "ASSISTANCE_FOUND"];

const NAV = [
  { path: "/patient", icon: "dashboard", label: "Patient Overview" },
  { path: "/patient/find-donor", icon: "person_search", label: "Find Donor" },
  { path: "/patient/sos-tracking", icon: "emergency", label: "Emergency SOS & Tracking" },
  { path: "/patient/new-request", icon: "add_circle", label: "New Request (Guided)" },
  { path: "/patient/history", icon: "history", label: "Request History" },
];

const STATUS_DOT: Record<BloodRequestStatus, string> = {
  PENDING: "bg-warning",
  MATCHING: "bg-secondary",
  PARTIALLY_FULFILLED: "bg-secondary",
  FULFILLED: "bg-success",
  CANCELLED: "bg-error",
  EXPIRED: "bg-error",
};

export default function PatientDashboard() {
  const { user } = useAuth();
  const location = useLocation();
  const qc = useQueryClient();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [showSos, setShowSos] = useState(false);
  const [trackId, setTrackId] = useState<string | null>(null);
  const [emTrackId, setEmTrackId] = useState<string | null>(null);

  const requests = useQuery({
    queryKey: ["blood-requests", "list"],
    queryFn: () => listBloodRequests(),
  });
  const emergencies = useQuery({
    queryKey: ["emergencies", "list"],
    queryFn: listEmergencies,
    // Phase F: live updates arrive over the event stream; this interval only
    // applies while that stream is down (graceful degradation).
    refetchInterval: useLiveFallbackInterval(20_000),
  });
  const activeEmergencies = (emergencies.data ?? []).filter((e) =>
    EMERGENCY_OPEN.includes(e.status),
  );

  // Family members can request to manage this patient's requests — those requests
  // need the patient's explicit acceptance. Only PATIENT accounts have any to act on
  // (FAMILY accounts land on their own /family portal instead).
  const familyLinks = useQuery({
    queryKey: ["family", "links"],
    queryFn: listFamilyLinks,
    enabled: user?.role === "PATIENT",
  });
  const pendingLinks = (familyLinks.data ?? []).filter((l) => l.status === "PENDING");
  const acceptedLinks = (familyLinks.data ?? []).filter((l) => l.status === "ACCEPTED");
  const [linkBusyId, setLinkBusyId] = useState<string | null>(null);
  // Which pending/accepted link (if any) currently has its optional-reason
  // prompt open for decline/revoke, and the reason text typed so far.
  const [reasonPromptId, setReasonPromptId] = useState<string | null>(null);
  const [linkReason, setLinkReason] = useState("");
  // Phase 21: these three mutations had no onError -- a failed accept/
  // decline/revoke silently did nothing beyond re-enabling the button
  // (onSettled), with no indication anything went wrong.
  const [linkActionError, setLinkActionError] = useState("");
  const acceptLink = useMutation({
    mutationFn: (id: string) => acceptFamilyLink(id),
    onMutate: (id) => {
      setLinkBusyId(id);
      setLinkActionError("");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["family", "links"] }),
    onError: (e) => setLinkActionError(toApiError(e).detail),
    onSettled: () => setLinkBusyId(null),
  });
  const revokeLink = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) => revokeFamilyLink(id, reason),
    onMutate: ({ id }) => {
      setLinkBusyId(id);
      setLinkActionError("");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["family", "links"] }),
    onError: (e) => setLinkActionError(toApiError(e).detail),
    onSettled: () => setLinkBusyId(null),
  });
  // Declining a pending request is its own action (REJECTED), distinct from
  // revoking access that was already granted.
  const rejectLink = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) => rejectFamilyLink(id, reason),
    onMutate: ({ id }) => {
      setLinkBusyId(id);
      setLinkActionError("");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["family", "links"] }),
    onError: (e) => setLinkActionError(toApiError(e).detail),
    onSettled: () => setLinkBusyId(null),
  });

  return (
    <>
    <aside className="hidden md:flex flex-col h-screen w-64 fixed left-0 bg-surface-container-low border-r border-border-subtle p-4 space-y-2 z-50">
    <div className="flex items-center gap-3 px-2 py-6 mb-4">
    <img alt="LifeLink Logo" className="w-10 h-10 object-contain" src="/brand/lifelink-logo.svg"/>
    <div>
    <h1 className="font-headline-md text-headline-md text-primary leading-tight">LifeLink</h1>
    <p className="text-xs text-on-surface-variant font-label-md">Precision Support</p>
    </div>
    </div>
    <nav className="flex-1 space-y-1">
    <Link className="flex items-center gap-3 bg-primary text-on-primary rounded-lg px-4 py-3 cursor-pointer transition-transform duration-200 active:scale-95" to="/patient">
    <span aria-hidden="true" className="material-symbols-outlined" style={{fontVariationSettings: '\'FILL\' 1'}}>dashboard</span>
    <span className="font-label-md text-label-md">Patient Overview</span>
    </Link>
    <Link className="flex items-center gap-3 text-on-surface-variant px-4 py-3 hover:bg-surface-container-high rounded-lg cursor-pointer hover:translate-x-1 transition-all duration-200" to="/patient/find-donor">
    <span aria-hidden="true" className="material-symbols-outlined">person_search</span>
    <span className="font-label-md text-label-md">Find Donor</span>
    </Link>
    <Link className="flex items-center gap-3 text-on-surface-variant px-4 py-3 hover:bg-surface-container-high rounded-lg cursor-pointer hover:translate-x-1 transition-all duration-200" to="/patient/sos-tracking">
    <span aria-hidden="true" className="material-symbols-outlined">emergency</span>
    <span className="font-label-md text-label-md">Emergency SOS &amp; Tracking</span>
    </Link>
    <Link className="flex items-center gap-3 text-on-surface-variant px-4 py-3 hover:bg-surface-container-high rounded-lg cursor-pointer hover:translate-x-1 transition-all duration-200" to="/patient/new-request">
    <span aria-hidden="true" className="material-symbols-outlined">add_circle</span>
    <span className="font-label-md text-label-md">New Request (Guided)</span>
    </Link>
    <Link className="flex items-center gap-3 text-on-surface-variant px-4 py-3 hover:bg-surface-container-high rounded-lg cursor-pointer hover:translate-x-1 transition-all duration-200" to="/patient/history">
    <span aria-hidden="true" className="material-symbols-outlined">history</span>
    <span className="font-label-md text-label-md">Request History</span>
    </Link>
    </nav>
    <div className="pt-4 border-t border-border-subtle space-y-1">
    <button
      onClick={() => setShowNew(true)}
      className="w-full flex items-center justify-center gap-2 bg-secondary text-on-secondary py-3 rounded-lg font-label-md hover:opacity-90 transition-all active:scale-95">
    <span aria-hidden="true" className="material-symbols-outlined text-sm">add</span>
                    New Request
                </button>
    <Link className="flex items-center gap-3 text-on-surface-variant px-4 py-2 hover:bg-surface-container-high rounded-lg text-sm transition-all" to="/research-mode">
    <span aria-hidden="true" className="material-symbols-outlined">library_books</span>
                    Research Database
                </Link>
    <div className="flex items-center gap-3 px-4 py-2 text-sm text-on-surface-variant">
    <span className="w-2 h-2 rounded-full bg-success"></span>
                    System Status
                </div>
    </div>
    </aside>

    <MobileNavDrawer
      open={mobileNavOpen}
      onClose={() => setMobileNavOpen(false)}
      activePath={location.pathname}
      title="LifeLink"
      subtitle="Precision Support"
      sections={[
        { items: NAV },
        { heading: "More", items: [{ path: "/research-mode", icon: "library_books", label: "Research Database" }] },
      ]}
    />

    <main className="flex-1 md:ml-64 min-h-screen flex flex-col">

    <header className="sticky top-0 z-40 w-full h-16 bg-surface/80 backdrop-blur-md border-b border-border-subtle flex items-center justify-between px-margin-mobile md:px-margin-desktop">
    <div className="flex items-center gap-6">
    <div className="md:hidden flex items-center gap-2">
    <button
      type="button"
      onClick={() => setMobileNavOpen(true)}
      aria-label="Open navigation menu"
      className="p-2 -ml-2 rounded-full text-on-surface-variant hover:bg-surface-container-high"
    >
      <Menu className="h-5 w-5" />
    </button>
    <img alt="LifeLink Logo" className="w-8 h-8" src="/brand/lifelink-logo.svg"/>
    </div>
    <div className="hidden md:flex items-center gap-4 text-on-surface-variant font-body-sm">
    <Link className="hover:text-primary transition-colors" to="/help">Guidelines</Link>
    <Link className="hover:text-primary transition-colors" to="/patient/history">Protocols</Link>
    <Link className="hover:text-primary transition-colors" to="/decision-transparency">Analytics</Link>
    </div>
    </div>
    <div className="flex items-center gap-3">
    <button
      onClick={() => setShowSos(true)}
      className="hidden lg:flex items-center gap-2 bg-primary px-4 py-2 rounded-full text-white font-label-md hover:bg-primary-container transition-all active:scale-95">
    <span aria-hidden="true" className="material-symbols-outlined" style={{fontVariationSettings: '\'FILL\' 1'}}>emergency</span>
                        Emergency SOS
                    </button>
    <div className="flex items-center gap-1">
    <NotificationsBell buttonClassName="relative p-2 text-on-surface-variant hover:bg-surface-container rounded-full transition-all" />
    <Link to="/settings" aria-label="Settings" className="p-2 text-on-surface-variant hover:bg-surface-container rounded-full transition-all">
    <span aria-hidden="true" className="material-symbols-outlined">settings</span>
    </Link>
    </div>
    <Link
      to="/profile"
      aria-label="My profile"
      className="w-8 h-8 rounded-full border border-border-subtle bg-primary/10 text-primary flex items-center justify-center font-label-md text-label-md flex-shrink-0"
    >
      {(user?.full_name?.[0] ?? "?").toUpperCase()}
    </Link>
    </div>
    </header>

    <div className="flex-1 p-margin-mobile md:p-margin-desktop space-y-gutter max-w-container-max mx-auto w-full">

    <section className="relative overflow-hidden rounded-xl bg-gradient-to-r from-primary to-primary-container text-on-primary p-8 md:p-12 shadow-md">
    <div className="relative z-10 max-w-2xl">
    <span className="inline-block px-3 py-1 bg-white/20 backdrop-blur-sm rounded-full text-xs font-bold uppercase tracking-wider mb-4">Urgent Action Required</span>
    <h2 className="font-headline-xl text-headline-xl mb-4">Need Blood Urgently?</h2>
    <p className="font-body-lg text-body-lg opacity-90 mb-8 max-w-lg">Our matching engine ranks verified compatible donors by distance and availability in real-time. No delays, just data-driven care.</p>
    <div className="flex flex-wrap gap-4">
    <button
      onClick={() => setShowNew(true)}
      className="bg-white text-primary px-8 py-3 rounded-xl font-headline-md shadow-lg hover:shadow-xl transition-all hover:-translate-y-1 active:translate-y-0">
                                New Blood Request
                            </button>
    <button
      onClick={() => setShowSos(true)}
      className="border border-white/30 bg-white/10 backdrop-blur-md px-8 py-3 rounded-xl font-headline-md hover:bg-white/20 transition-all">
                                Emergency SOS
                            </button>
    </div>
    </div>

    <div className="absolute right-0 top-0 h-full w-1/3 opacity-20 pointer-events-none hidden lg:block">
    <span aria-hidden="true" className="material-symbols-outlined text-[300px] absolute -right-20 -top-20" style={{fontVariationSettings: '\'wght\' 100'}}>bloodtype</span>
    </div>
    </section>

    <section className="grid grid-cols-1 md:grid-cols-3 gap-6">
    <div className="glass-panel p-6 rounded-xl card-hover flex items-start gap-4">
    <div className="w-12 h-12 rounded-lg bg-secondary/10 text-secondary flex items-center justify-center">
    <span aria-hidden="true" className="material-symbols-outlined" style={{fontVariationSettings: '\'FILL\' 1'}}>person_search</span>
    </div>
    <div>
    <h3 className="font-headline-md text-lg">Find Donor</h3>
    <p className="text-on-surface-variant text-sm mt-1">Search the verified nationwide registry.</p>
    </div>
    </div>
    <div className="glass-panel p-6 rounded-xl card-hover flex items-start gap-4 border-l-4 border-l-error">
    <div className="w-12 h-12 rounded-lg bg-error/10 text-error flex items-center justify-center">
    <span aria-hidden="true" className="material-symbols-outlined" style={{fontVariationSettings: '\'FILL\' 1'}}>e911_emergency</span>
    </div>
    <div>
    <h3 className="font-headline-md text-lg">Emergency SOS</h3>
    <p className="text-on-surface-variant text-sm mt-1">Instant broadcast to nearest hospitals.</p>
    </div>
    </div>
    <div className="glass-panel p-6 rounded-xl card-hover flex items-start gap-4">
    <div className="w-12 h-12 rounded-lg bg-tertiary/10 text-tertiary flex items-center justify-center">
    <span aria-hidden="true" className="material-symbols-outlined" style={{fontVariationSettings: '\'FILL\' 1'}}>local_hospital</span>
    </div>
    <div>
    <h3 className="font-headline-md text-lg">Nearby Hospitals</h3>
    <p className="text-on-surface-variant text-sm mt-1">Check current blood stock & ICU beds.</p>
    </div>
    </div>
    </section>

    <section className="grid grid-cols-1 lg:grid-cols-12 gap-gutter">

    <div className="lg:col-span-4 space-y-6">
    <div className="bg-surface p-6 rounded-xl shadow-soft border border-border-subtle">
    <div className="flex items-center justify-between mb-4">
    <h4 className="font-label-md text-on-surface-variant uppercase tracking-widest text-xs">My Open Requests</h4>
    <span className="flex h-2 w-2 rounded-full bg-success animate-pulse-soft"></span>
    </div>
    <div className="flex items-baseline gap-2">
    <span className="font-headline-xl text-5xl tabular-nums tracking-tighter">
      {requests.data
        ? requests.data.filter((r) => isOpenRequestStatus(r.status)).length
        : "—"}
    </span>
    <span className="text-on-surface-variant font-label-md text-sm">active</span>
    </div>
    <div className="mt-6 h-1 w-full bg-surface-container rounded-full overflow-hidden">
    <div className="h-full bg-success w-3/4"></div>
    </div>
    </div>
    <div className="bg-surface p-6 rounded-xl shadow-soft border border-border-subtle">
    <div className="flex items-center justify-between mb-4">
    <h4 className="font-label-md text-on-surface-variant uppercase tracking-widest text-xs">Donors Responded</h4>
    <span aria-hidden="true" className="material-symbols-outlined text-tertiary">volunteer_activism</span>
    </div>
    <div className="flex items-baseline gap-2">
    <span className="font-headline-xl text-5xl tabular-nums tracking-tighter">
      {requests.data
        ? requests.data.reduce((n, r) => n + r.accepted_count, 0)
        : "—"}
    </span>
    <span className="text-on-surface-variant font-label-md text-sm">total accepts</span>
    </div>
    <p className="mt-4 text-sm text-on-surface-variant">Across all of your requests</p>
    </div>
    </div>

    <div className="lg:col-span-8">
    <div className="bg-white rounded-xl shadow-soft border border-border-subtle h-full flex flex-col">
    <div className="p-6 border-b border-border-subtle flex items-center justify-between">
    <div>
    <h3 className="font-headline-md text-xl">Decision Transparency Panel</h3>
    <p className="text-sm text-on-surface-variant">How LifeLink ranks donor matches</p>
    </div>
    <Link to="/decision-transparency" className="bg-surface-container-high px-3 py-1 rounded-full text-xs font-mono font-bold hover:bg-surface-container-highest transition-colors">
      FULL METHODOLOGY →
    </Link>
    </div>
    <div className="p-8 flex-1 grid md:grid-cols-3 gap-8">
    <div className="space-y-4">
    <div className="flex items-center gap-2 text-primary">
    <span aria-hidden="true" className="material-symbols-outlined">rule</span>
    <h5 className="font-label-md">1. Compatible</h5>
    </div>
    <p className="text-sm text-on-surface-variant leading-relaxed">Only donors whose blood type is a valid match under standard donor/recipient compatibility rules are considered.</p>
    </div>
    <div className="space-y-4">
    <div className="flex items-center gap-2 text-secondary">
    <span aria-hidden="true" className="material-symbols-outlined">distance</span>
    <h5 className="font-label-md">2. Nearby</h5>
    </div>
    <p className="text-sm text-on-surface-variant leading-relaxed">Candidates are ranked by real distance — via the graph-based transport/pathway engine where available, falling back to straight-line distance otherwise.</p>
    </div>
    <div className="space-y-4">
    <div className="flex items-center gap-2 text-success">
    <span aria-hidden="true" className="material-symbols-outlined">verified_user</span>
    <h5 className="font-label-md">3. Available</h5>
    </div>
    <p className="text-sm text-on-surface-variant leading-relaxed">Only donors who have marked themselves available, and who are past the 56-day minimum interval since their last recorded donation, are queued.</p>
    </div>
    </div>
    </div>
    </div>
    </section>

    {(pendingLinks.length > 0 || acceptedLinks.length > 0) && (
    <section className="space-y-3">
    <h3 className="font-headline-md text-xl px-2">Family Access</h3>
    {linkActionError && (
      <p role="alert" className="text-error text-sm px-2">{linkActionError}</p>
    )}
    <div className="bg-white rounded-xl shadow-soft border border-border-subtle divide-y divide-border-subtle overflow-hidden">
    {pendingLinks.map((l) => (
      <div key={l.id} className="px-6 py-4">
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0">
            <p className="font-label-md text-label-md">{l.family?.full_name} wants to manage your requests</p>
            <p className="text-sm text-on-surface-variant">as your {l.relationship} · {l.family?.email}</p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {reasonPromptId !== l.id && (
              <>
                <button
                  onClick={() => { setReasonPromptId(l.id); setLinkReason(""); }}
                  disabled={linkBusyId === l.id}
                  className="px-3 py-1.5 rounded-lg text-sm border border-border-subtle hover:bg-surface-container-low"
                >
                  Decline
                </button>
                <button
                  onClick={() => acceptLink.mutate(l.id)}
                  disabled={linkBusyId === l.id}
                  className="px-3 py-1.5 rounded-lg text-sm bg-primary text-on-primary hover:opacity-90 flex items-center gap-2"
                >
                  {linkBusyId === l.id && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                  Accept
                </button>
              </>
            )}
          </div>
        </div>
        {reasonPromptId === l.id && (
          <div className="mt-3 flex flex-col sm:flex-row gap-2">
            <input
              className="flex-1 rounded-lg border border-border-subtle px-3 py-1.5 text-sm"
              placeholder="Optional reason (visible to them)"
              value={linkReason}
              onChange={(e) => setLinkReason(e.target.value)}
              maxLength={500}
            />
            <div className="flex gap-2">
              <button
                onClick={() => {
                  rejectLink.mutate({ id: l.id, reason: linkReason.trim() || undefined });
                  setReasonPromptId(null);
                }}
                disabled={linkBusyId === l.id}
                className="px-3 py-1.5 rounded-lg text-sm bg-error text-on-primary hover:opacity-90"
              >
                Confirm decline
              </button>
              <button
                onClick={() => setReasonPromptId(null)}
                className="px-3 py-1.5 rounded-lg text-sm border border-border-subtle hover:bg-surface-container-low"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    ))}
    {acceptedLinks.map((l) => (
      <div key={l.id} className="px-6 py-4">
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0">
            <p className="font-label-md text-label-md">{l.family?.full_name} can manage your requests</p>
            <p className="text-sm text-on-surface-variant">as your {l.relationship} · {l.family?.email}</p>
          </div>
          {reasonPromptId !== l.id && (
            <button
              onClick={() => { setReasonPromptId(l.id); setLinkReason(""); }}
              disabled={linkBusyId === l.id}
              className="px-3 py-1.5 rounded-lg text-sm border border-border-subtle hover:bg-surface-container-low flex-shrink-0"
            >
              {linkBusyId === l.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Revoke access"}
            </button>
          )}
        </div>
        {reasonPromptId === l.id && (
          <div className="mt-3 flex flex-col sm:flex-row gap-2">
            <input
              className="flex-1 rounded-lg border border-border-subtle px-3 py-1.5 text-sm"
              placeholder="Optional reason (visible to them)"
              value={linkReason}
              onChange={(e) => setLinkReason(e.target.value)}
              maxLength={500}
            />
            <div className="flex gap-2">
              <button
                onClick={() => {
                  revokeLink.mutate({ id: l.id, reason: linkReason.trim() || undefined });
                  setReasonPromptId(null);
                }}
                disabled={linkBusyId === l.id}
                className="px-3 py-1.5 rounded-lg text-sm bg-error text-on-primary hover:opacity-90"
              >
                Confirm revoke
              </button>
              <button
                onClick={() => setReasonPromptId(null)}
                className="px-3 py-1.5 rounded-lg text-sm border border-border-subtle hover:bg-surface-container-low"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    ))}
    </div>
    </section>
    )}

    {activeEmergencies.length > 0 && (
    <section className="space-y-3">
    <h3 className="font-headline-md text-xl px-2">Active Emergency SOS</h3>
    <div className="bg-white rounded-xl shadow-soft border border-l-4 border-l-error border-border-subtle divide-y divide-border-subtle overflow-hidden">
    {activeEmergencies.map((e) => (
      <button
        key={e.id}
        onClick={() => setEmTrackId(e.id)}
        className="w-full flex items-center gap-4 px-6 py-4 text-left hover:bg-surface-container-lowest transition-colors"
      >
        <span className="w-10 h-10 rounded-full bg-error/10 text-error flex items-center justify-center flex-shrink-0">
          <span aria-hidden="true" className="material-symbols-outlined text-[20px]" style={{fontVariationSettings: "'FILL' 1"}}>e911_emergency</span>
        </span>
        <div className="flex-1 min-w-0">
          <p className="font-label-md text-label-md">
            {e.blood_type ? BLOOD_LABEL[e.blood_type] : "Any type"} · {e.location_text || "Location not given"}
          </p>
          <p className="text-sm text-on-surface-variant">Status: {e.status.replace("_", " ").toLowerCase()}</p>
        </div>
        <span className="text-secondary font-label-md text-sm">Track</span>
      </button>
    ))}
    </div>
    </section>
    )}

    <section className="space-y-4">
    <h3 className="font-headline-md text-xl px-2">Recent Requests Timeline</h3>
    <div className="bg-white rounded-xl shadow-soft border border-border-subtle overflow-hidden">
    <table className="w-full text-left border-collapse">
    <thead className="bg-surface-container-low text-on-surface-variant font-label-md text-xs uppercase tracking-wider">
    <tr>
    <th className="px-6 py-4">Request ID</th>
    <th className="px-6 py-4">Blood Group</th>
    <th className="px-6 py-4">Status</th>
    <th className="px-6 py-4">Donors</th>
    <th className="px-6 py-4">Action</th>
    </tr>
    </thead>
    <tbody className="divide-y divide-border-subtle">
    {requests.isLoading && (
      <tr><td colSpan={5} className="px-6 py-8 text-center text-on-surface-variant text-sm">Loading your requests…</td></tr>
    )}
    {requests.isError && (
      <tr role="alert"><td colSpan={5} className="px-6 py-8 text-center text-error text-sm">
        Couldn't load requests. {toApiError(requests.error).detail}{" "}
        <button onClick={() => requests.refetch()} className="text-secondary font-label-md hover:underline">Retry</button>
      </td></tr>
    )}
    {requests.data && requests.data.length === 0 && (
      <tr><td colSpan={5} className="px-6 py-10 text-center text-on-surface-variant text-sm">
        No requests yet. Use <button onClick={() => setShowNew(true)} className="text-secondary font-label-md hover:underline">New Blood Request</button> to create one.
      </td></tr>
    )}
    {requests.data?.map((r) => (
      <tr key={r.id} className="hover:bg-surface-container-lowest transition-colors">
        <td className="px-6 py-4 font-mono text-sm">{r.id.slice(0, 8).toUpperCase()}</td>
        <td className="px-6 py-4">
          <span className="bg-primary/10 text-primary px-3 py-1 rounded font-bold text-sm">{BLOOD_LABEL[r.blood_type]}</span>
        </td>
        <td className="px-6 py-4">
          <span className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${STATUS_DOT[r.status]}`}></span>
            {r.status[0] + r.status.slice(1).toLowerCase()}
          </span>
        </td>
        <td className="px-6 py-4 text-sm text-on-surface-variant">{r.accepted_count} accepted</td>
        <td className="px-6 py-4">
          <button onClick={() => setTrackId(r.id)} className="text-secondary font-label-md hover:underline">Track</button>
        </td>
      </tr>
    ))}
    </tbody>
    </table>
    </div>
    </section>
    </div>

    <footer className="mt-auto w-full border-t border-border-subtle bg-surface-container-lowest py-8 px-margin-mobile md:px-margin-desktop">
    <div className="max-w-container-max mx-auto flex flex-col md:flex-row justify-between items-center gap-4">
    <div className="flex items-center gap-4">
    <img alt="LifeLink Logo" className="w-8 h-8" src="/brand/lifelink-logo.svg"/>
    <p className="font-body-sm text-body-sm text-secondary">© 2024 LifeLink Healthcare. Precision support for critical care.</p>
    </div>
    <div className="flex gap-6">
    <Link className="text-on-surface-variant hover:text-primary transition-all text-sm" to="/research-mode">Research</Link>
    <Link className="text-on-surface-variant hover:text-primary transition-all text-sm" to="/help">Support</Link>
    <Link className="text-on-surface-variant hover:text-primary transition-all text-sm" to="/legal">Privacy</Link>
    <Link className="text-on-surface-variant hover:text-primary transition-all text-sm" to="/legal">Terms</Link>
    </div>
    </div>
    </footer>
    </main>

    <BloodRequestModal open={showNew} onClose={() => setShowNew(false)} />
    <SosModal open={showSos} onClose={() => setShowSos(false)} />
    <RequestTrackDrawer requestId={trackId} onClose={() => setTrackId(null)} />
    <EmergencyTrackDrawer emergencyId={emTrackId} onClose={() => setEmTrackId(null)} mode="creator" />
    </>
  );
}
