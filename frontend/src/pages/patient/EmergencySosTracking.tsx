/**
 * Emergency SOS & Live Tracking — Patient screen
 * Adapted from the Stitch export: lifelink_1.2_emergency_sos_live_tracking_flow/code.html
 *
 * The Stitch mock invented a named attending physician, a "#SOS-7749" ticket id, ETA
 * countdowns, cold-chain temperature/GPS telemetry, ambulance/courier tracking, a SHA-256
 * "audit hash" and LifePoints rewards — none of which the backend models. What the backend
 * *does* model, and what this screen is built around, is the real `EmergencyStatus` machine
 * (ACTIVE → ACKNOWLEDGED → MATCHING → ASSISTANCE_FOUND → RESOLVED, or CANCELLED) which maps
 * almost exactly onto the design's 5-step "dispatch lifecycle" stepper — so that stepper is
 * kept, driven by real status instead of a fake clock. Responder detail, timeline and the
 * cancel/resolve/assistance-found actions reuse <EmergencyTrackDrawer mode="creator"/>
 * rather than re-implementing that (already privacy-correct) logic.
 */
import { useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Menu } from "lucide-react";

import NotificationsBell from "../../components/NotificationsBell";
import SosModal from "../../components/SosModal";
import EmergencyTrackDrawer from "../../components/EmergencyTrackDrawer";
import MobileNavDrawer from "../../components/MobileNavDrawer";
import {
  BLOOD_LABEL,
  getEmergency,
  listEmergencies,
  type Emergency,
  type EmergencyStatus,
} from "../../lib/domain";
import { useLiveFallbackInterval } from "../../lib/realtime";

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

const OPEN_STATES: EmergencyStatus[] = ["ACTIVE", "ACKNOWLEDGED", "MATCHING", "ASSISTANCE_FOUND"];
const CLOSED_STATES: EmergencyStatus[] = ["RESOLVED", "CANCELLED"];

const STEPS: { status: EmergencyStatus; label: string; icon: string; blurb: string }[] = [
  { status: "ACTIVE", label: "Active", icon: "sensors", blurb: "SOS broadcast created and live." },
  { status: "ACKNOWLEDGED", label: "Acknowledged", icon: "check", blurb: "A responder has acknowledged." },
  { status: "MATCHING", label: "Matching", icon: "hub", blurb: "Scanning compatible responders." },
  { status: "ASSISTANCE_FOUND", label: "Assistance Found", icon: "volunteer_activism", blurb: "A responder has accepted." },
  { status: "RESOLVED", label: "Resolved", icon: "task_alt", blurb: "Emergency marked resolved." },
];

const STATUS_BADGE: Record<EmergencyStatus, string> = {
  ACTIVE: "bg-error-container text-on-error-container",
  ACKNOWLEDGED: "bg-secondary-fixed text-on-secondary-fixed-variant",
  MATCHING: "bg-secondary-fixed text-on-secondary-fixed-variant",
  ASSISTANCE_FOUND: "bg-primary-fixed text-on-primary-fixed-variant",
  RESOLVED: "bg-surface-container-high text-on-surface",
  CANCELLED: "bg-surface-container-high text-on-surface-variant",
};

function timeAgo(iso: string): string {
  const s = Math.max(1, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function SpotlightStepper({ status }: { status: EmergencyStatus }) {
  if (status === "CANCELLED") {
    return (
      <div className="p-space-4 rounded-lg bg-surface-container-low text-on-surface-variant font-body-sm text-body-sm flex items-center gap-space-2">
        <span aria-hidden="true" className="material-symbols-outlined text-[20px]">cancel</span>
        This emergency was cancelled.
      </div>
    );
  }
  const currentIdx = STEPS.findIndex((s) => s.status === status);
  return (
    <div className="grid grid-cols-1 sm:grid-cols-5 gap-space-3">
      {STEPS.map((s, i) => {
        const done = i < currentIdx;
        const current = i === currentIdx;
        return (
          <div
            key={s.status}
            className={`flex sm:flex-col items-center sm:items-start gap-space-3 p-space-4 rounded-lg transition-all ${
              current
                ? "bg-primary-fixed shadow-level-1"
                : done
                  ? "bg-surface-container-low"
                  : // Phase 27: this step is already distinguished as
                    // "not yet reached" by background shade, badge color
                    // and icon (below) -- the extra `opacity-60` here was
                    // redundant and dropped its own text/icon color below
                    // WCAG AA contrast (real axe-core finding). Removed.
                    "bg-surface-container"
            }`}
          >
            <div
              className={`w-9 h-9 rounded-full flex items-center justify-center shrink-0 font-label-md text-label-md font-semibold ${
                current
                  ? "bg-primary text-on-primary animate-pulse"
                  : done
                    ? "bg-secondary text-on-secondary"
                    : "bg-surface-container-highest text-on-surface-variant"
              }`}
            >
              <span aria-hidden="true" className="material-symbols-outlined text-[18px]">{done ? "check" : s.icon}</span>
            </div>
            <div className="min-w-0">
              <p
                className={`font-label-lg text-label-lg font-semibold ${
                  current ? "text-primary" : "text-on-surface"
                }`}
              >
                {s.label}
              </p>
              <p className="font-body-sm text-body-sm text-on-surface-variant">{s.blurb}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function SpotlightCard({ emergency, onTrack }: { emergency: Emergency; onTrack: (id: string) => void }) {
  const detail = useQuery({
    queryKey: ["emergencies", emergency.id],
    queryFn: () => getEmergency(emergency.id),
    // Phase F: live updates arrive over the event stream; this interval only
    // applies while that stream is down (graceful degradation).
    refetchInterval: useLiveFallbackInterval(15_000),
  });

  return (
    <section className="bg-surface-container-lowest rounded-xl shadow-level-2 overflow-hidden">
      <div className="h-1.5 w-full bg-gradient-to-r from-primary via-primary-container to-secondary" />
      <div className="p-space-6 lg:p-space-8 flex flex-col gap-space-6">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-space-4">
          <div className="flex flex-col gap-space-2">
            <div className="flex flex-wrap items-center gap-space-3">
              <span className={`inline-flex items-center gap-2 px-space-3 py-1 rounded-full font-label-md text-label-md font-semibold ${STATUS_BADGE[emergency.status]}`}>
                <span className="w-2 h-2 rounded-full bg-current animate-pulse" />
                {emergency.status.replace(/_/g, " ")}
              </span>
              <span className="font-body-sm text-body-sm text-on-surface-variant">
                Broadcast {timeAgo(emergency.created_at)}
              </span>
            </div>
            <h2 className="font-headline-md text-headline-md text-on-surface tracking-tight">
              {emergency.blood_type ? BLOOD_LABEL[emergency.blood_type] : "Any blood type"} needed
              {emergency.location_text ? ` · ${emergency.location_text}` : ""}
            </h2>
            {emergency.note && (
              <p className="font-body-md text-body-md text-on-surface-variant max-w-2xl">{emergency.note}</p>
            )}
          </div>
          <button
            onClick={() => onTrack(emergency.id)}
            className="inline-flex items-center gap-space-2 px-space-5 py-2.5 rounded-full bg-primary hover:bg-primary-container text-on-primary font-label-lg text-label-lg font-semibold transition-all shadow-level-1 self-start"
          >
            <span aria-hidden="true" className="material-symbols-outlined text-[18px]">radar</span>
            <span>View full tracking</span>
          </button>
        </div>

        <SpotlightStepper status={emergency.status} />

        <div className="flex flex-wrap items-center gap-space-4 pt-space-2 border-t border-surface-container-high">
          <div className="flex items-center gap-space-2 text-on-surface-variant">
            <span aria-hidden="true" className="material-symbols-outlined text-[18px] text-secondary">groups</span>
            <span className="font-label-md text-label-md">
              {detail.isLoading
                ? "Loading responders…"
                : detail.isError
                  ? "Responder count unavailable right now"
                  : `${detail.data?.accepted_count ?? 0} responder${(detail.data?.accepted_count ?? 0) === 1 ? "" : "s"} on the way`}
            </span>
          </div>
          <div className="flex items-center gap-space-2 text-on-surface-variant">
            <span aria-hidden="true" className="material-symbols-outlined text-[18px]">priority_high</span>
            <span className="font-label-md text-label-md">{emergency.urgency[0]}{emergency.urgency.slice(1).toLowerCase()} urgency</span>
          </div>
        </div>
      </div>
    </section>
  );
}

export default function EmergencySosTracking() {
  const location = useLocation();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [showSos, setShowSos] = useState(false);
  const [trackId, setTrackId] = useState<string | null>(null);
  const [tab, setTab] = useState<"all" | "active" | "closed">("all");

  const emergencies = useQuery({
    queryKey: ["emergencies", "list"],
    queryFn: listEmergencies,
    // Phase F: live updates arrive over the event stream; this interval only
    // applies while that stream is down (graceful degradation).
    refetchInterval: useLiveFallbackInterval(20_000),
  });

  const all = emergencies.data ?? [];
  const active = all.filter((e) => OPEN_STATES.includes(e.status));
  const closed = all.filter((e) => CLOSED_STATES.includes(e.status));
  const spotlight = active[0] ?? null;

  const visible = useMemo(() => {
    if (tab === "active") return active;
    if (tab === "closed") return closed;
    return all;
  }, [tab, all, active, closed]);

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
                n.path === "/patient/sos-tracking"
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
            <h1 className="font-title-lg text-title-lg text-on-surface truncate">Emergency SOS</h1>
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
            {/* Hero */}
            <section className="relative overflow-hidden rounded-xl bg-surface-container-lowest p-space-6 shadow-level-1">
              <div className="absolute -right-16 -top-16 w-64 h-64 bg-primary/10 rounded-full blur-3xl pointer-events-none" />
              <div className="relative z-10 flex flex-col lg:flex-row lg:items-center justify-between gap-space-6">
                <div className="flex flex-col gap-space-2 max-w-2xl">
                  <h2 className="font-headline-lg text-headline-lg text-on-surface tracking-tight">
                    Emergency SOS
                  </h2>
                  <p className="font-body-md text-body-md text-on-surface-variant">
                    Broadcasts your need immediately to compatible, available donors and
                    hospitals near you. Use it only for genuine emergencies — donor identities
                    stay anonymized until a responder accepts.
                  </p>
                </div>
                <button
                  onClick={() => setShowSos(true)}
                  className="inline-flex items-center gap-space-2 px-space-6 py-3 rounded-full bg-primary hover:bg-primary-container text-on-primary font-label-lg text-label-lg font-semibold shadow-level-2 transition-all active:scale-95 self-start"
                >
                  <span aria-hidden="true" className="material-symbols-outlined text-[20px]">e911_emergency</span>
                  <span>Broadcast new SOS</span>
                </button>
              </div>
            </section>

            {emergencies.isLoading && (
              <div className="bg-surface-container-lowest rounded-xl shadow-level-1 p-space-8 flex items-center justify-center gap-space-2 text-on-surface-variant">
                <Loader2 className="h-5 w-5 animate-spin" /> Loading your emergencies…
              </div>
            )}
            {emergencies.isError && (
              <div role="alert" className="bg-surface-container-lowest rounded-xl shadow-level-1 p-space-8 flex flex-col items-center gap-space-2 text-center text-error">
                <span>Couldn't load your emergencies.</span>
                <button type="button" className="underline font-label-md text-label-md" onClick={() => emergencies.refetch()}>
                  Retry
                </button>
              </div>
            )}

            {spotlight && <SpotlightCard emergency={spotlight} onTrack={setTrackId} />}

            {/* History */}
            <section className="bg-surface-container-lowest rounded-xl shadow-level-1 overflow-hidden">
              <div className="p-space-6 border-b border-surface-container flex flex-col lg:flex-row lg:items-center justify-between gap-space-4">
                <div>
                  <h3 className="font-headline-sm text-headline-sm text-on-surface">Your emergencies</h3>
                  <p className="font-body-md text-body-md text-on-surface-variant">
                    Every SOS you've broadcast, with its real-time status.
                  </p>
                </div>
                <div className="inline-flex p-1 bg-surface-container rounded-lg self-start">
                  {([
                    { key: "all", label: `All (${all.length})` },
                    { key: "active", label: `Active (${active.length})` },
                    { key: "closed", label: `Closed (${closed.length})` },
                  ] as const).map((t) => (
                    <button
                      key={t.key}
                      onClick={() => setTab(t.key)}
                      className={`px-space-4 py-space-2 rounded-md font-label-lg text-label-lg transition-all ${
                        tab === t.key
                          ? "bg-surface-container-lowest text-on-surface shadow-sm font-semibold"
                          : "text-on-surface-variant hover:text-on-surface"
                      }`}
                    >
                      {t.label}
                    </button>
                  ))}
                </div>
              </div>

              {emergencies.data && visible.length === 0 && (
                <div className="p-space-12 text-center flex flex-col items-center gap-space-4">
                  <div className="w-16 h-16 rounded-full bg-surface-container flex items-center justify-center text-on-surface-variant">
                    <span aria-hidden="true" className="material-symbols-outlined text-[32px]">emergency</span>
                  </div>
                  <h4 className="font-headline-sm text-headline-sm text-on-surface">
                    {tab === "all" ? "No emergencies yet" : `No ${tab} emergencies`}
                  </h4>
                  <p className="font-body-md text-body-md text-on-surface-variant max-w-md">
                    When you need blood urgently, broadcast an Emergency SOS and track its
                    progress here in real time.
                  </p>
                </div>
              )}

              {visible.length > 0 && (
                <ul className="divide-y divide-border-subtle">
                  {visible.map((e) => (
                    <li key={e.id} className="p-space-5 flex flex-col sm:flex-row sm:items-center justify-between gap-space-3">
                      <div className="flex items-start gap-space-3 min-w-0">
                        <span className="w-10 h-10 rounded-full bg-error/10 text-error flex items-center justify-center flex-shrink-0">
                          <span aria-hidden="true" className="material-symbols-outlined text-[20px]">emergency</span>
                        </span>
                        <div className="min-w-0">
                          <p className="font-label-lg text-label-lg text-on-surface">
                            {e.blood_type ? BLOOD_LABEL[e.blood_type] : "Any type"}
                            {e.location_text ? ` · ${e.location_text}` : ""}
                          </p>
                          <p className="font-body-sm text-body-sm text-on-surface-variant">
                            {timeAgo(e.created_at)} · {e.urgency.toLowerCase()} urgency
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-space-3 flex-shrink-0">
                        <span className={`px-space-3 py-0.5 rounded-full font-label-sm text-label-sm font-semibold ${STATUS_BADGE[e.status]}`}>
                          {e.status.replace(/_/g, " ")}
                        </span>
                        <button
                          onClick={() => setTrackId(e.id)}
                          className="text-secondary font-label-md text-label-md hover:underline"
                        >
                          Track
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </main>
      </div>

      <SosModal open={showSos} onClose={() => setShowSos(false)} />
      <EmergencyTrackDrawer emergencyId={trackId} onClose={() => setTrackId(null)} mode="creator" />
    </>
  );
}
