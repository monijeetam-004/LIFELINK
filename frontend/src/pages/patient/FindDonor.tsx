/**
 * Find Donor — Patient screen
 * Adapted from the Stitch export: lifelink_1.1_patient_find_donor_emergency_search/code.html
 *
 * The Stitch mock invented a browsable list of named donors ("Verified Donor #DN-9042"),
 * fake distances, fake "response time" and fake hospital blood-bank inventory numbers
 * ("Available: O- (8 units), A+ (14 units)"), plus a fabricated "Regional Blood Network
 * Health" reserve-percentage widget. None of that exists on the backend and — per the
 * donor-privacy rule — a browse/search context must never show donor identity, only an
 * anonymized map pin (blood type + coarse location). So this screen keeps the design's
 * grammar (sidebar, hero banner, blood-type filter, two-column map + actions layout) but
 * replaces every browsable "result" with the real, privacy-safe `/map/data` pins (via the
 * shared <DonorMap/>) and real counts computed from those pins. Discovery of an actual
 * hospital directory / keyword search isn't backed by any endpoint, so instead of faking
 * it we surface the hospitals that appear in the patient's own request history.
 */
import { useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Menu } from "lucide-react";

import { useQuery } from "@tanstack/react-query";

import DonorMap from "../../components/DonorMap";
import NotificationsBell from "../../components/NotificationsBell";
import BloodRequestModal from "../../components/BloodRequestModal";
import SosModal from "../../components/SosModal";
import RequestTrackDrawer from "../../components/RequestTrackDrawer";
import BloodCompatibilityChart from "../../components/BloodCompatibilityChart";
import MobileNavDrawer from "../../components/MobileNavDrawer";
import {
  BLOOD_LABEL,
  BLOOD_TYPES,
  getMapData,
  listBloodRequests,
  type BloodType,
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

export default function FindDonor() {
  const location = useLocation();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [bloodType, setBloodType] = useState<BloodType | "">("");
  const [showNew, setShowNew] = useState(false);
  const [showSos, setShowSos] = useState(false);
  const [trackId, setTrackId] = useState<string | null>(null);

  const map = useQuery({
    queryKey: ["map", "data", bloodType || "all"],
    queryFn: () => getMapData(bloodType || undefined),
    // Phase F: live updates arrive over the event stream; this interval only
    // applies while that stream is down (graceful degradation).
    refetchInterval: useLiveFallbackInterval(60_000),
  });

  const requests = useQuery({
    queryKey: ["blood-requests", "list"],
    queryFn: () => listBloodRequests(),
  });

  // Distinct hospitals from the patient's own request history — the only real,
  // privacy-safe "hospital directory" data the backend exposes to a patient.
  const hospitalHistory = useMemo(() => {
    const byKey = new Map<string, { hospital_name: string; city: string | null; count: number }>();
    for (const r of requests.data ?? []) {
      if (!r.hospital_name) continue;
      const key = `${r.hospital_name}__${r.city ?? ""}`;
      const existing = byKey.get(key);
      if (existing) existing.count += 1;
      else byKey.set(key, { hospital_name: r.hospital_name, city: r.city, count: 1 });
    }
    return Array.from(byKey.values()).sort((a, b) => b.count - a.count);
  }, [requests.data]);

  const countsByType = useMemo(() => {
    const counts = new Map<BloodType, number>();
    for (const d of map.data?.donors ?? []) counts.set(d.blood_type, (counts.get(d.blood_type) ?? 0) + 1);
    return counts;
  }, [map.data]);

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
                n.path === "/patient/find-donor"
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
            <h1 className="font-title-lg text-title-lg text-on-surface truncate">Find Donor</h1>
          </div>
          <div className="flex items-center gap-space-3">
            <button
              onClick={() => setShowNew(true)}
              className="hidden sm:inline-flex items-center gap-space-2 h-10 px-space-4 rounded-full bg-surface-container-low hover:bg-surface-container-high text-on-surface font-label-lg text-label-lg font-semibold transition-colors"
            >
              <span aria-hidden="true" className="material-symbols-outlined text-[18px]">add</span>
              <span>New Request</span>
            </button>
            <button
              onClick={() => setShowSos(true)}
              className="inline-flex items-center gap-space-2 h-10 px-space-5 rounded-full bg-primary-container hover:bg-primary text-on-primary font-label-lg text-label-lg font-semibold shadow-level-1 transition-all"
            >
              <span aria-hidden="true" className="material-symbols-outlined text-[18px]">e911_emergency</span>
              <span className="hidden sm:inline">Emergency SOS</span>
            </button>
            <NotificationsBell buttonClassName="relative w-10 h-10 rounded-full flex items-center justify-center text-on-surface-variant hover:bg-surface-container-low hover:text-on-surface transition-colors" />
            <Link to="/profile" aria-label="Your profile" className="w-8 h-8 rounded-full bg-primary flex items-center justify-center flex-shrink-0">
              <span aria-hidden="true" className="material-symbols-outlined text-on-primary text-[18px]">person</span>
            </Link>
          </div>
        </header>

        <main className="lg:pt-16 min-h-screen bg-background w-full px-space-4 lg:px-space-6 py-space-6">
          <div className="flex flex-col w-full gap-space-8 max-w-container-max mx-auto">
            <div className="flex flex-col gap-space-2">
              <div className="flex flex-wrap items-center gap-space-2">
                <span className="inline-flex items-center gap-1.5 px-space-2 py-0.5 rounded-full bg-secondary-fixed text-on-secondary-fixed-variant font-label-sm text-label-sm">
                  <span className="w-1.5 h-1.5 rounded-full bg-secondary" />
                  Anonymized donor locations only
                </span>
              </div>
              <h2 className="font-headline-lg text-headline-lg text-on-surface tracking-tight">
                Donor &amp; blood bank discovery
              </h2>
              <p className="font-body-md text-body-md text-on-surface-variant max-w-2xl">
                Donor identities are never shown here — only anonymized pins for available,
                location-sharing donors near you. To reach a specific donor, submit a blood
                request or Emergency SOS and compatible donors are notified automatically.
              </p>
            </div>

            {/* Blood type filter */}
            <section className="bg-surface-container-lowest rounded-xl p-space-6 shadow-level-1">
              <div className="flex items-center justify-between mb-space-3">
                <label className="font-label-lg text-label-lg text-on-surface" htmlFor="filter-blood-type">
                  Filter by blood group
                </label>
                {bloodType && (
                  <button
                    onClick={() => setBloodType("")}
                    className="font-label-sm text-label-sm text-secondary hover:underline"
                  >
                    Clear filter
                  </button>
                )}
              </div>
              <div className="grid grid-cols-4 sm:grid-cols-8 gap-space-2" id="filter-blood-type">
                {BLOOD_TYPES.map((bt) => (
                  <button
                    key={bt}
                    type="button"
                    onClick={() => setBloodType(bt)}
                    aria-pressed={bloodType === bt}
                    className={`py-space-2 rounded-lg font-title-md text-title-md text-center transition-all active:scale-95 ${
                      bloodType === bt
                        ? "bg-primary text-on-primary shadow-level-1 ring-2 ring-primary-container"
                        : "bg-surface-container-low text-on-surface hover:bg-surface-container-high"
                    }`}
                  >
                    {BLOOD_LABEL[bt]}
                  </button>
                ))}
              </div>
            </section>

            {/* Blood compatibility reference (capability: "view blood compatibility information") */}
            <details className="bg-surface-container-lowest rounded-xl shadow-level-1 p-space-6 group">
              <summary className="flex items-center justify-between cursor-pointer list-none font-label-lg text-label-lg text-on-surface">
                <span>
                  {bloodType
                    ? `Who can donate to ${BLOOD_LABEL[bloodType]}?`
                    : "Blood type compatibility reference"}
                </span>
                <span aria-hidden="true" className="material-symbols-outlined text-on-surface-variant transition-transform group-open:rotate-180">
                  expand_more
                </span>
              </summary>
              <div className="mt-space-4">
                <BloodCompatibilityChart recipientType={bloodType || undefined} />
              </div>
            </details>

            <div className="grid grid-cols-1 xl:grid-cols-12 gap-space-6 items-start">
              {/* Map */}
              <div className="xl:col-span-8 flex flex-col gap-space-4">
                <div className="bg-surface-container-lowest rounded-xl shadow-level-1 overflow-hidden h-[420px]">
                  <DonorMap bloodType={bloodType || undefined} />
                </div>

                <div className="bg-surface-container-lowest rounded-xl p-space-5 shadow-level-1">
                  <h3 className="font-title-md text-title-md text-on-surface mb-space-3">
                    Available donors by blood group
                  </h3>
                  {map.isLoading && (
                    <p className="text-sm text-on-surface-variant">Loading live donor pins…</p>
                  )}
                  {map.isError && <p className="text-sm text-error">Couldn't load donor availability.</p>}
                  {map.data && (map.data.donors.length === 0 ? (
                    <p className="text-sm text-on-surface-variant">
                      No donors are currently sharing their location for this filter.
                    </p>
                  ) : (
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-space-3">
                      {BLOOD_TYPES.filter((bt) => countsByType.has(bt)).map((bt) => (
                        <div key={bt} className="p-space-3 rounded-lg bg-surface-container-low flex items-center justify-between">
                          <span className="font-label-md text-label-md text-on-surface">{BLOOD_LABEL[bt]}</span>
                          <span className="font-title-md text-title-md text-secondary font-bold">
                            {countsByType.get(bt)}
                          </span>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              </div>

              {/* Actions rail */}
              <div className="xl:col-span-4 flex flex-col gap-space-6">
                <div className="bg-primary-container/10 rounded-xl p-space-5 shadow-level-1 flex flex-col gap-space-3">
                  <div className="flex items-center gap-space-2 text-primary">
                    <span aria-hidden="true" className="material-symbols-outlined text-[24px]">crisis_alert</span>
                    <span className="font-title-md text-title-md font-semibold">Need blood right now?</span>
                  </div>
                  <p className="font-body-sm text-body-sm text-on-surface-variant">
                    An Emergency SOS broadcasts to compatible, available donors and nearby
                    hospitals immediately.
                  </p>
                  <button
                    onClick={() => setShowSos(true)}
                    className="w-full h-11 bg-primary text-on-primary hover:bg-primary-container rounded-full font-label-lg text-label-lg font-semibold flex items-center justify-center gap-2 shadow-level-1 transition-all"
                  >
                    <span aria-hidden="true" className="material-symbols-outlined text-[18px]">broadcast_on_personal</span>
                    <span>Broadcast Emergency SOS</span>
                  </button>
                </div>

                <div className="bg-surface-container-lowest rounded-xl p-space-5 shadow-level-1 flex flex-col gap-space-3">
                  <div className="flex items-center gap-space-2 text-on-surface">
                    <span aria-hidden="true" className="material-symbols-outlined text-[24px] text-secondary">post_add</span>
                    <span className="font-title-md text-title-md font-semibold">Start a blood request</span>
                  </div>
                  <p className="font-body-sm text-body-sm text-on-surface-variant">
                    Submit units needed, urgency and hospital — compatible donors near your
                    request are notified automatically.
                  </p>
                  <button
                    onClick={() => setShowNew(true)}
                    className="w-full h-11 bg-secondary text-on-secondary hover:opacity-90 rounded-full font-label-lg text-label-lg font-semibold flex items-center justify-center gap-2 shadow-level-1 transition-all"
                  >
                    New Blood Request
                  </button>
                </div>

                <div className="bg-surface-container-lowest rounded-xl p-space-5 shadow-level-1 flex flex-col gap-space-3">
                  <span className="font-title-md text-title-md text-on-surface font-semibold">
                    Hospitals in your history
                  </span>
                  {requests.isLoading && (
                    <p className="text-sm text-on-surface-variant">Loading…</p>
                  )}
                  {requests.isError && (
                    <p className="text-sm text-error">Couldn't load your request history.</p>
                  )}
                  {requests.data && hospitalHistory.length === 0 && (
                    <p className="text-sm text-on-surface-variant">
                      Hospitals you've requested from will show up here. There isn't a live
                      hospital directory or keyword search on the backend yet.
                    </p>
                  )}
                  {hospitalHistory.length > 0 && (
                    <ul className="flex flex-col gap-space-2">
                      {hospitalHistory.slice(0, 6).map((h) => (
                        <li
                          key={`${h.hospital_name}-${h.city}`}
                          className="flex items-center justify-between gap-space-2 px-space-3 py-space-2 rounded-lg bg-surface-container-low"
                        >
                          <div className="min-w-0">
                            <p className="font-body-sm text-body-sm text-on-surface truncate">{h.hospital_name}</p>
                            {h.city && <p className="font-body-sm text-body-sm text-on-surface-variant truncate">{h.city}</p>}
                          </div>
                          <span className="font-label-sm text-label-sm text-on-surface-variant flex-shrink-0">
                            {h.count} request{h.count === 1 ? "" : "s"}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                  <Link to="/patient/history" className="text-secondary font-label-sm text-label-sm hover:underline self-start">
                    View full request history →
                  </Link>
                </div>
              </div>
            </div>
          </div>
        </main>
      </div>

      <BloodRequestModal open={showNew} onClose={() => setShowNew(false)} />
      <SosModal open={showSos} onClose={() => setShowSos(false)} />
      <RequestTrackDrawer requestId={trackId} onClose={() => setTrackId(null)} />
    </>
  );
}
