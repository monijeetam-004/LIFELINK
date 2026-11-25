/**
 * Donor Experience Dashboard (desktop, also the responsive mobile layout at
 * this same route — see the md:hidden bottom nav below).
 * Layout/markup converted 1:1 from the Google Stitch export (donor_experience_dashboard).
 * Phase 2 wired the profile, availability, open compatible requests, responses,
 * donation history and notifications to the real backend. Phase 4 added the
 * real three-way Accept / Not now / Decline response (previously "Not now"
 * silently called DECLINED) and switched the availability quick-toggle to the
 * real DonorAvailabilityState-backed endpoint — the full Available / Not now /
 * Unavailable picker lives on the Ready Console and in Settings. Design
 * otherwise unchanged.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { Heart } from "lucide-react";

import NotificationsBell from "../components/NotificationsBell";
import EmergencyTrackDrawer from "../components/EmergencyTrackDrawer";
import { useAuth } from "../lib/auth";
import { toApiError } from "../lib/api";
import {
  BLOOD_LABEL,
  BLOOD_TYPES,
  getDonorProfile,
  issueCertificate,
  listActiveEmergencies,
  listCertificates,
  listDonations,
  listDonorOpenRequests,
  listDonorResponses,
  respondToRequest,
  setAvailability,
  updateDonorProfile,
  type BloodType,
  isOpenRequestStatus,
} from "../lib/domain";
import DonationVerificationBadge, { canRequestCertificate } from "../components/DonationVerificationBadge";
import { useLiveFallbackInterval } from "../lib/realtime";

const DONATION_INTERVAL_DAYS = 56;

function eligibility(lastDonation: string | null): { text: string; sub: string } {
  if (!lastDonation) return { text: "Eligible", sub: "No donation on record yet" };
  const next = new Date(lastDonation);
  next.setDate(next.getDate() + DONATION_INTERVAL_DAYS);
  const days = Math.ceil((next.getTime() - Date.now()) / 86_400_000);
  if (days <= 0) return { text: "Eligible", sub: `Last donated ${lastDonation}` };
  return { text: `${days}`, sub: `Eligible on ${next.toISOString().slice(0, 10)}` };
}

/** Real recovery progress toward the next-donation interval (0–100). */
function recoveryPercent(lastDonation: string | null, eligible: boolean): number {
  if (eligible || !lastDonation) return 100;
  const elapsed = (Date.now() - new Date(lastDonation).getTime()) / 86_400_000;
  return Math.max(0, Math.min(100, Math.round((elapsed / DONATION_INTERVAL_DAYS) * 100)));
}

const RING_CIRCUMFERENCE = 502.65;

function initials(name: string | undefined): string {
  const parts = (name ?? "").trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase() || "?";
}

export default function DonorDashboard() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const navigate = useNavigate();

  const [emTrackId, setEmTrackId] = useState<string | null>(null);
  // Phase 26: every mutation below now reports failures here instead of
  // failing silently -- previously a failed availability toggle, blood-type
  // save, request response, location share, or certificate issue just
  // re-enabled its button with zero feedback, leaving the user unable to
  // tell whether their action actually happened.
  const [actionError, setActionError] = useState("");

  const profile = useQuery({ queryKey: ["donor", "me"], queryFn: getDonorProfile });
  const open = useQuery({ queryKey: ["donor", "requests"], queryFn: listDonorOpenRequests });
  const donations = useQuery({ queryKey: ["donor", "donations"], queryFn: listDonations });
  const certificates = useQuery({ queryKey: ["certificates"], queryFn: listCertificates });
  const history = useQuery({ queryKey: ["donor", "responses"], queryFn: listDonorResponses });
  const emergencies = useQuery({
    queryKey: ["donor", "emergencies"],
    queryFn: listActiveEmergencies,
    // Phase F: live updates arrive over the event stream; this interval only
    // applies while that stream is down (graceful degradation).
    refetchInterval: useLiveFallbackInterval(20_000),
  });

  const availabilityMut = useMutation({
    // Quick toggle between the two most common states; the full three-way
    // Available / Not now / Unavailable picker lives on the Ready Console
    // and in Settings.
    mutationFn: (nextAvailable: boolean) =>
      setAvailability(nextAvailable ? "AVAILABLE" : "UNAVAILABLE"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["donor", "me"] });
      setActionError("");
    },
    onError: (e) => setActionError(toApiError(e).detail),
  });
  const bloodTypeMut = useMutation({
    mutationFn: (bt: BloodType) => updateDonorProfile({ blood_type: bt }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["donor", "me"] });
      qc.invalidateQueries({ queryKey: ["donor", "requests"] });
      setActionError("");
    },
    onError: (e) => setActionError(toApiError(e).detail),
  });
  const respondMut = useMutation({
    mutationFn: (v: { id: string; action: "ACCEPTED" | "DECLINED" | "NOT_NOW" }) =>
      respondToRequest(v.id, v.action),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["donor", "requests"] });
      qc.invalidateQueries({ queryKey: ["donor", "responses"] });
      qc.invalidateQueries({ queryKey: ["notifications"] });
      setActionError("");
    },
    onError: (e) => setActionError(toApiError(e).detail),
  });
  const locationMut = useMutation({
    mutationFn: (body: {
      share_location: boolean;
      latitude?: number | null;
      longitude?: number | null;
    }) => updateDonorProfile(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["donor", "me"] });
      setActionError("");
    },
    onError: (e) => setActionError(toApiError(e).detail),
  });
  const certMut = useMutation({
    mutationFn: (donationId: string) => issueCertificate(donationId),
    onSuccess: (cert) => {
      qc.invalidateQueries({ queryKey: ["certificates"] });
      qc.invalidateQueries({ queryKey: ["notifications"] });
      setActionError("");
      navigate(`/certificate/${cert.id}`);
    },
    onError: (e) => setActionError(toApiError(e).detail),
  });
  const certByDonation = new Map(
    (certificates.data ?? []).map((c) => [c.donation_id, c]),
  );

  function toggleLocation() {
    if (profile.data?.share_location) {
      locationMut.mutate({ share_location: false, latitude: null, longitude: null });
      return;
    }
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (pos) =>
        locationMut.mutate({
          share_location: true,
          // stored coarse: backend also rounds before publishing to any map
          latitude: Math.round(pos.coords.latitude * 100) / 100,
          longitude: Math.round(pos.coords.longitude * 100) / 100,
        }),
      () => locationMut.reset(),
      { enableHighAccuracy: false, timeout: 8000 },
    );
  }

  const elig = eligibility(profile.data?.last_donation_date ?? null);
  const unitsGiven = (donations.data ?? []).reduce((n, d) => n + d.units, 0);
  const firstName = user?.full_name?.split(" ")[0] ?? "there";

  const isEligible = profile.data?.eligible ?? true;
  const isAvailable = profile.data?.is_available ?? false;
  const canRespond = isEligible && isAvailable;
  let respondBlockReason: string | null = null;
  if (!isAvailable) respondBlockReason = "Mark yourself available to respond";
  else if (!isEligible)
    respondBlockReason = `Not eligible until ${profile.data?.eligible_on ?? "your interval passes"}`;
  const recoveryPct = recoveryPercent(
    profile.data?.last_donation_date ?? null,
    isEligible,
  );
  // Closed commitments (fulfilled / cancelled / withdrawn) for the history panel.
  const pastMatches = (history.data ?? []).filter(
    (m) => !isOpenRequestStatus(m.status),
  );

  return (
    <>
    <header className="fixed top-0 left-0 w-full z-50 flex justify-between items-center px-6 md:px-margin-desktop h-16 bg-surface border-b border-border-subtle shadow-sm">
    <div className="flex items-center gap-4">
    <span className="font-headline-md text-headline-md font-extrabold text-primary tracking-tight">LifeLink</span>
    <div className="hidden md:flex gap-6 ml-8">
    <Link className="text-primary font-bold border-b-2 border-primary py-4 transition-all" to="/donor/milestones">Impact</Link>
    <Link className="text-on-surface-variant hover:text-primary transition-colors py-4" to="/donor/eligibility">Eligibility</Link>
    <Link className="text-on-surface-variant hover:text-primary transition-colors py-4" to="/donor/ready-console">Ready</Link>
    </div>
    </div>
    <div className="flex items-center gap-4">
    <NotificationsBell buttonClassName="relative p-2 text-on-surface-variant hover:bg-surface-container rounded-full transition-colors" />
    {/* Phase L: the help control was a <button> with no action at all, and
        the "avatar" was the same stock photo (hot-linked from the design
        export) for every donor -- someone else's face shown as the signed-in
        donor. Now: a real link to the help page, and the donor's own
        initials. */}
    <Link
      to="/help"
      aria-label="Help and guidelines"
      className="p-2 text-on-surface-variant hover:bg-surface-container rounded-full transition-colors"
    >
    <span className="material-symbols-outlined" aria-hidden="true" data-icon="help_outline">help_outline</span>
    </Link>
    <div
      className="w-10 h-10 rounded-full border-2 border-primary-fixed bg-primary-container text-on-primary-container flex items-center justify-center font-bold"
      aria-hidden="true"
      data-testid="donor-initials"
    >
    {initials(user?.full_name)}
    </div>
    </div>
    </header>
    <main className="pt-24 pb-24 md:pb-8 px-4 md:px-margin-desktop max-w-[1440px] mx-auto">
    <div className="grid grid-cols-1 md:grid-cols-12 gap-gutter">

    <section className="col-span-12 mb-4">
    <h1 className="font-headline-xl text-headline-xl text-text-main">Welcome back, {firstName}.</h1>
    <p className="font-body-lg text-body-lg text-on-surface-variant mt-2">Your next donation could save up to 3 lives. Here is your current readiness status.</p>
    {actionError && (
      <p role="alert" className="mt-3 text-sm text-error">{actionError}</p>
    )}
    {profile.isError && (
      <div role="alert" className="mt-3 flex items-center gap-3 text-sm text-error">
        <span>Couldn't load your donor profile.</span>
        <button type="button" className="font-label-sm text-label-sm underline" onClick={() => profile.refetch()}>
          Retry
        </button>
      </div>
    )}
    <div className="mt-4 flex flex-wrap items-center gap-4">
      <button
        onClick={() => availabilityMut.mutate(!(profile.data?.is_available ?? true))}
        disabled={!profile.data || availabilityMut.isPending}
        className={`inline-flex items-center gap-2 px-4 py-2 rounded-full font-label-md text-label-md transition-colors ${
          profile.data?.is_available
            ? "bg-success/10 text-success border border-success/30"
            : "bg-surface-container-high text-on-surface-variant border border-border-subtle"
        }`}
      >
        <span className={`h-2 w-2 rounded-full ${profile.data?.is_available ? "bg-success" : "bg-on-surface-variant"}`} />
        {profile.data?.is_available ? "Available for donation" : "Not available"}
        {/* Phase 27: the smaller text-xs size already de-emphasizes this
            hint relative to the button's main label -- the added
            `opacity-70` was redundant and dropped it below WCAG AA
            contrast against both button states (measured ~3.2:1 / 3.7:1,
            real axe-core finding). Removed. */}
        <span className="text-xs">· tap to toggle</span>
      </button>
      <button
        onClick={toggleLocation}
        disabled={!profile.data || locationMut.isPending}
        className={`inline-flex items-center gap-2 px-4 py-2 rounded-full font-label-md text-label-md transition-colors ${
          profile.data?.share_location
            ? "bg-secondary/10 text-secondary border border-secondary/30"
            : "bg-surface-container-high text-on-surface-variant border border-border-subtle"
        }`}
      >
        <span aria-hidden="true" className="material-symbols-outlined text-[18px]">location_on</span>
        {profile.data?.share_location ? "Sharing approx. location" : "Share approx. location"}
      </button>
      {profile.data && !profile.data.blood_type && (
        <div className="inline-flex items-center gap-2 text-sm text-error">
          <span aria-hidden="true" className="material-symbols-outlined text-[18px]">error</span>
          Set your blood group:
          <select
            className="border border-error/40 rounded-lg px-2 py-1 bg-surface"
            defaultValue=""
            onChange={(e) => e.target.value && bloodTypeMut.mutate(e.target.value as BloodType)}
          >
            <option value="" disabled>Choose…</option>
            {BLOOD_TYPES.map((b) => <option key={b} value={b}>{BLOOD_LABEL[b]}</option>)}
          </select>
        </div>
      )}
    </div>
    </section>


    <div className="col-span-12 md:col-span-4 lg:col-span-3 bg-surface rounded-xl border border-border-subtle p-8 shadow-sm flex flex-col items-center justify-center relative overflow-hidden group">
    <div className="absolute inset-0 shimmer opacity-0 group-hover:opacity-10 transition-opacity"></div>
    <h3 className="font-label-md text-label-md text-on-surface-variant mb-6 self-start uppercase tracking-wider">Donation Readiness</h3>
    <div className="relative w-48 h-48 flex items-center justify-center">
    <svg className="w-full h-full">
    <circle className="text-surface-variant" cx="96" cy="96" fill="transparent" r="80" stroke="currentColor" strokeWidth="12"></circle>
    <circle className="text-primary progress-ring-circle" cx="96" cy="96" fill="transparent" r="80" stroke="currentColor" strokeDasharray={RING_CIRCUMFERENCE} strokeDashoffset={RING_CIRCUMFERENCE * (1 - recoveryPct / 100)} strokeLinecap="round" strokeWidth="12"></circle>
    </svg>
    <div className="absolute flex flex-col items-center">
    <span className="font-headline-xl text-headline-xl text-primary leading-none">{profile.data ? `${recoveryPct}%` : "—"}</span>
    <span className="font-label-sm text-label-sm text-on-surface-variant">{isEligible ? "Ready to donate" : "Recovering"}</span>
    </div>
    </div>
    <div className="mt-8 text-center">
    <p className="font-body-sm text-body-sm text-on-surface-variant">
    {isEligible
      ? "Your donation interval has passed — you can accept requests."
      : `Eligible again on ${profile.data?.eligible_on ?? "—"}.`}
    </p>
    </div>
    </div>

    <div className="col-span-12 md:col-span-4 lg:col-span-5 bg-surface rounded-xl border border-border-subtle p-8 shadow-sm flex flex-col justify-between">
    <div>
    <h3 className="font-label-md text-label-md text-on-surface-variant mb-4 uppercase tracking-wider">Next Eligibility</h3>
    <div className="flex items-end gap-2">
    <span className="font-headline-xl text-headline-xl text-text-main">{elig.text}</span>
    <span className="font-headline-md text-headline-md text-on-surface-variant mb-2">{elig.text === "Eligible" ? "now" : "days left"}</span>
    </div>
    <div className="mt-4 flex gap-2">
    {/* Phase L: was a fixed 80% bar for every donor; now the real recovery
        progress toward the next eligible date (same value as the ring). */}
    <div
      className="flex-1 h-2 bg-surface-variant rounded-full overflow-hidden"
      role="progressbar"
      aria-label="Recovery toward next eligible donation"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={recoveryPct}
    >
    <div className="h-full bg-secondary rounded-full" style={{ width: `${recoveryPct}%` }}></div>
    </div>
    </div>
    <p className="font-body-sm text-body-sm text-on-surface-variant mt-4">{elig.sub}</p>
    </div>
    <div className="mt-8 flex gap-3">
    {/* Phase L: both were <button>s with no action. Appointment requests
        and the eligibility criteria both live on the Eligibility Center
        (POST /donors/me/appointments). */}
    <Link to="/donor/eligibility" className="flex-1 text-center bg-primary text-on-primary py-3 px-4 rounded-xl font-label-md hover:bg-primary-container transition-colors shadow-sm">Pre-Schedule Now</Link>
    <Link to="/donor/eligibility" className="flex-1 text-center border border-border-subtle py-3 px-4 rounded-xl font-label-md hover:bg-surface-container-low transition-colors">Review Criteria</Link>
    </div>
    </div>

    <div className="col-span-12 md:col-span-4 lg:col-span-4 bg-secondary text-on-secondary rounded-xl p-8 shadow-md relative overflow-hidden">
    <div className="absolute -right-8 -bottom-8 opacity-10" aria-hidden="true">
    {/* Phase J: an SVG, not a 160px icon-font ligature. As text, this
        purely decorative glyph was the one color-contrast node Phase 27
        had to disclose as an exception; as a graphic it has no text to
        contrast-check and looks the same. */}
    <Heart data-icon="favorite" style={{width: '160px', height: '160px'}} fill="currentColor" strokeWidth={0} />
    </div>
    <h3 className="font-label-md text-label-md text-secondary-fixed mb-6 uppercase tracking-wider">Your Impact</h3>
    <div className="space-y-6">
    <div className="flex items-center gap-4">
    <div className="w-12 h-12 bg-white/10 rounded-lg flex items-center justify-center">
    <span aria-hidden="true" className="material-symbols-outlined" data-icon="person_add">person_add</span>
    </div>
    <div>
    <p className="font-headline-md text-headline-md leading-none">{donations.data?.length ?? "—"}</p>
    <p className="font-body-sm text-body-sm opacity-80">Donations Logged</p>
    </div>
    </div>
    <div className="flex items-center gap-4">
    <div className="w-12 h-12 bg-white/10 rounded-lg flex items-center justify-center">
    <span aria-hidden="true" className="material-symbols-outlined" data-icon="check_circle" style={{fontVariationSettings: '\'FILL\' 1'}}>check_circle</span>
    </div>
    <div>
    <p className="font-headline-md text-headline-md leading-none">{donations.data ? unitsGiven : "—"}</p>
    <p className="font-body-sm text-body-sm opacity-80">Units Given</p>
    </div>
    </div>
    <div className="pt-4 border-t border-white/20">
    <p className="font-body-md text-body-md">Blood group on file: <span className="font-bold">{profile.data?.blood_type ? BLOOD_LABEL[profile.data.blood_type] : "not set"}</span></p>
    </div>
    </div>
    </div>

    <div className="col-span-12 lg:col-span-7 bg-surface rounded-xl border border-border-subtle shadow-sm overflow-hidden">
    <div className="p-6 border-b border-border-subtle flex justify-between items-center">
    <div className="flex items-center gap-2 text-primary">
    {/* Phase 27: Tailwind's default `animate-pulse` cycles opacity 1<->0.5,
        which drops this icon's on-primary-colored text below WCAG AA
        contrast for roughly half of every animation cycle (a real
        axe-core finding, not a false positive -- it genuinely is that low
        partway through each pulse). `animate-pulse-soft` (this file's own
        existing custom animation, see index.css) cycles 1<->0.7 instead --
        still a clearly visible pulse for drawing attention to an open
        emergency, but its floor stays above 4.5:1 (measured ~5.1:1)
        throughout the whole cycle. */}
    <span aria-hidden="true" className="material-symbols-outlined animate-pulse-soft" data-icon="emergency">emergency</span>
    <h3 className="font-headline-md text-headline-md">Open Requests You Can Help</h3>
    </div>
    <span className="bg-error-container text-on-error-container px-3 py-1 rounded-full font-label-sm">{open.data?.length ?? 0} open</span>
    </div>
    <div className="divide-y divide-border-subtle">
    {open.isLoading && <div className="p-6 text-sm text-on-surface-variant">Loading…</div>}
    {open.isError && (
      <div role="alert" className="p-6 text-sm text-error flex items-center gap-3">
        <span>Couldn't load open requests.</span>
        <button type="button" className="font-label-sm text-label-sm underline" onClick={() => open.refetch()}>
          Retry
        </button>
      </div>
    )}
    {open.data && open.data.length === 0 && (
      <div className="p-8 text-sm text-on-surface-variant text-center">
        {profile.data?.blood_type
          ? "No open requests match your blood group right now."
          : "Set your blood group above to see requests you can help with."}
      </div>
    )}
    {open.data?.map((r) => {
      const accepted = r.my_response?.status === "ACCEPTED";
      return (
      <div key={r.id} className="p-6 hover:bg-surface-container-lowest transition-colors flex flex-col md:flex-row md:items-center justify-between gap-4">
      <div className="flex items-start gap-4">
      <div className="w-14 h-14 rounded-xl bg-primary/10 text-primary flex items-center justify-center flex-shrink-0 font-headline-md font-bold">
      {BLOOD_LABEL[r.blood_type]}
      </div>
      <div>
      <h4 className="font-label-md text-label-md text-text-main">{r.hospital_name || r.patient_name || "Patient request"}</h4>
      <p className="font-body-sm text-body-sm text-on-surface-variant">Needs {r.units_needed} unit(s) {BLOOD_LABEL[r.blood_type]} · {r.urgency.toLowerCase()}</p>
      <p className="font-label-sm text-label-sm text-secondary mt-1 flex items-center gap-1">
      <span aria-hidden="true" className="material-symbols-outlined text-[16px]" data-icon="location_on">location_on</span> {r.city || "Location not specified"}
      </p>
      </div>
      </div>
      {accepted ? (
        <div className="flex items-center gap-2">
        <span className="px-4 py-2 rounded-lg font-label-md bg-success/10 text-success">Accepted ✓</span>
        <button
          onClick={() => respondMut.mutate({ id: r.id, action: "DECLINED" })}
          disabled={respondMut.isPending}
          className="px-4 py-2 rounded-lg font-label-md text-on-surface-variant border border-border-subtle hover:bg-surface-variant transition-colors disabled:opacity-60">Withdraw</button>
        </div>
      ) : (
        <div className="flex flex-col items-end gap-1">
        {r.my_response && (
          <span className="font-label-sm text-label-sm text-on-surface-variant">
            You responded: {r.my_response.status === "NOT_NOW" ? "Not now" : "Declined"}
          </span>
        )}
        <div className="flex gap-2">
        <button
          onClick={() => respondMut.mutate({ id: r.id, action: "ACCEPTED" })}
          disabled={respondMut.isPending || !canRespond}
          title={respondBlockReason ?? undefined}
          className="bg-primary-fixed text-on-primary-fixed-variant px-6 py-2 rounded-lg font-label-md hover:bg-primary-fixed-dim transition-colors disabled:opacity-60">Respond Now</button>
        <button
          onClick={() => respondMut.mutate({ id: r.id, action: "NOT_NOW" })}
          disabled={respondMut.isPending}
          title="Can't help right now, but not a hard no"
          className="bg-surface-container-high text-on-surface px-6 py-2 rounded-lg font-label-md hover:bg-surface-variant transition-colors disabled:opacity-60">Not now</button>
        <button
          onClick={() => respondMut.mutate({ id: r.id, action: "DECLINED" })}
          disabled={respondMut.isPending}
          className="text-on-surface-variant px-4 py-2 rounded-lg font-label-md hover:bg-surface-variant transition-colors disabled:opacity-60">Decline</button>
        </div>
        {respondBlockReason && (
          <span className="font-label-sm text-label-sm text-error">{respondBlockReason}</span>
        )}
        </div>
      )}
      </div>
      );
    })}
    </div>
    </div>

    {emergencies.isError && (
    <div role="alert" className="col-span-12 bg-error-container text-on-error-container rounded-xl p-4 shadow-sm flex items-center justify-between gap-3">
    <span className="font-label-md text-label-md">Couldn't check for active emergencies near you — you may be missing a live alert.</span>
    <button type="button" className="font-label-sm text-label-sm underline flex-shrink-0" onClick={() => emergencies.refetch()}>
      Retry
    </button>
    </div>
    )}
    {emergencies.data && emergencies.data.length > 0 && (
    <div className="col-span-12 bg-surface rounded-xl border border-l-4 border-l-error border-border-subtle shadow-sm overflow-hidden">
    <div className="p-6 border-b border-border-subtle flex justify-between items-center">
    <div className="flex items-center gap-2 text-error">
    {/* Phase 27: same real axe-core finding as ReadyConsole.tsx's identical
        icon -- Tailwind's default animate-pulse (1<->0.5) drops this
        text-error icon below AA for part of every cycle; animate-pulse-subtle
        (1<->0.8, index.css) keeps it above 4.5:1 throughout. */}
    <span aria-hidden="true" className="material-symbols-outlined animate-pulse-subtle" style={{fontVariationSettings: "'FILL' 1"}}>e911_emergency</span>
    <h3 className="font-headline-md text-headline-md">Active Emergencies Near You</h3>
    </div>
    <span className="bg-error-container text-on-error-container px-3 py-1 rounded-full font-label-sm">{emergencies.data.length} live</span>
    </div>
    <div className="divide-y divide-border-subtle">
    {emergencies.data.map((e) => {
      const accepted = e.my_response?.status === "ACCEPTED";
      return (
      <button key={e.id} onClick={() => setEmTrackId(e.id)} className="w-full text-left p-6 hover:bg-surface-container-lowest transition-colors flex flex-col md:flex-row md:items-center justify-between gap-3">
      <div className="flex items-start gap-4">
      <div className="w-14 h-14 rounded-xl bg-error/10 text-error flex items-center justify-center flex-shrink-0 font-headline-md font-bold">
      {e.blood_type ? BLOOD_LABEL[e.blood_type] : "Any"}
      </div>
      <div>
      <h4 className="font-label-md text-label-md text-text-main">{e.location_text || "Location not specified"}</h4>
      <p className="font-body-sm text-body-sm text-on-surface-variant">{e.urgency.toLowerCase()} · {e.accepted_count} responder(s) · {e.status.replace("_", " ").toLowerCase()}</p>
      </div>
      </div>
      <span className={`px-4 py-2 rounded-lg font-label-md ${accepted ? "bg-success/10 text-success" : "bg-primary-fixed text-on-primary-fixed-variant"}`}>
      {accepted ? "You're responding ✓" : "Respond"}
      </span>
      </button>
      );
    })}
    </div>
    </div>
    )}

    <div className="col-span-12 lg:col-span-5 bg-surface rounded-xl border border-border-subtle p-6 shadow-sm">
    <div className="flex justify-between items-center mb-6">
    <h3 className="font-headline-md text-headline-md">Donation History</h3>
    <button
      onClick={() => navigate("/donor/milestones")}
      className="font-label-sm text-label-sm text-secondary hover:underline whitespace-nowrap"
    >
      Log a donation
    </button>
    </div>
    {donations.isLoading && <p className="text-sm text-on-surface-variant">Loading…</p>}
    {donations.isError && (
      <div role="alert" className="flex items-center gap-3 text-sm text-error">
        <span>Couldn't load donation history.</span>
        <button type="button" className="font-label-sm text-label-sm underline" onClick={() => donations.refetch()}>
          Retry
        </button>
      </div>
    )}
    <ul className="divide-y divide-border-subtle">
    {donations.data?.map((d) => {
      const cert = certByDonation.get(d.id);
      return (
      <li key={d.id} className="py-3 flex items-center justify-between gap-3">
        <div>
          <p className="font-label-md text-label-md text-text-main">{d.hospital_name || "Donation"}</p>
          <p className="font-body-sm text-body-sm text-on-surface-variant">{d.donated_on} · {d.units} unit(s)</p>
          <DonationVerificationBadge status={d.verification_status} />
        </div>
        {cert ? (
          <button
            onClick={() => navigate(`/certificate/${cert.id}`)}
            className="font-label-sm text-label-sm text-secondary hover:underline whitespace-nowrap flex items-center gap-1"
          >
            <span aria-hidden="true" className="material-symbols-outlined text-[16px]">workspace_premium</span>
            Certificate
          </button>
        ) : canRequestCertificate(d) ? (
          <button
            onClick={() => certMut.mutate(d.id)}
            disabled={certMut.isPending}
            className="font-label-sm text-label-sm text-on-surface-variant hover:text-primary whitespace-nowrap disabled:opacity-50 flex items-center gap-1"
          >
            <span aria-hidden="true" className="material-symbols-outlined text-[16px]">verified</span>
            Get certificate
          </button>
        ) : (
          <span className="font-label-sm text-label-sm text-on-surface-variant whitespace-nowrap">
            Certificate after verification
          </span>
        )}
      </li>
      );
    })}
    {pastMatches.map((m) => (
      <li key={m.id} className="py-3 flex items-center justify-between">
        <div>
          <p className="font-label-md text-label-md text-text-main">{m.hospital_name || m.patient_name || "Patient request"}</p>
          <p className="font-body-sm text-body-sm text-on-surface-variant">
            {BLOOD_LABEL[m.blood_type]} · you{" "}
            {m.my_response.status === "ACCEPTED"
              ? "accepted"
              : m.my_response.status === "NOT_NOW"
                ? "said not now"
                : "declined"}{" "}
            · request {m.status.toLowerCase()}
          </p>
        </div>
        <span aria-hidden="true" className="material-symbols-outlined text-on-surface-variant" data-icon="history">history</span>
      </li>
    ))}
    </ul>
    {donations.data?.length === 0 && pastMatches.length === 0 && !donations.isLoading && (
      <p className="text-sm text-on-surface-variant">Requests you respond to will be tracked here.</p>
    )}
    </div>
    </div>
    </main>

    <nav aria-label="Donor sections" className="fixed bottom-0 left-0 w-full z-50 flex justify-around items-center px-4 py-2 bg-surface border-t border-border-subtle shadow-lg md:hidden">
    <Link className="flex flex-col items-center justify-center bg-primary-container text-on-primary-container rounded-2xl px-4 py-1" to="/donor/milestones">
    <span className="material-symbols-outlined" aria-hidden="true" data-icon="favorite" style={{fontVariationSettings: '\'FILL\' 1'}}>favorite</span>
    <span className="font-label-sm text-label-sm">Impact</span>
    </Link>
    <Link className="flex flex-col items-center justify-center text-on-surface-variant" to="/donor/eligibility">
    <span className="material-symbols-outlined" aria-hidden="true" data-icon="check_circle">check_circle</span>
    <span className="font-label-sm text-label-sm">Eligibility</span>
    </Link>
    <Link className="flex flex-col items-center justify-center text-on-surface-variant" to="/donor/ready-console">
    <span className="material-symbols-outlined" aria-hidden="true" data-icon="verified">verified</span>
    <span className="font-label-sm text-label-sm">Ready</span>
    </Link>
    <Link className="flex flex-col items-center justify-center text-on-surface-variant" to="/profile">
    <span className="material-symbols-outlined" aria-hidden="true" data-icon="person">person</span>
    <span className="font-label-sm text-label-sm">Account</span>
    </Link>
    </nav>
    <EmergencyTrackDrawer emergencyId={emTrackId} onClose={() => setEmTrackId(null)} mode="responder" />
    </>
  );
}
