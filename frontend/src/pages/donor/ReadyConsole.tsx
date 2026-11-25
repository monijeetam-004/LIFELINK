/**
 * Donor Ready Console — adapted from the Stitch export
 * lifelink_2.4_2.5_donor_ready_console_alert_watcher/code.html.
 *
 * What's REAL here (see backend/app/api/routes/donors.py, emergencies.py,
 * notifications.py, and DonorProfile.alert_min_urgency/alert_radius_km):
 *   - The three-way Available / Not now / Unavailable selector via
 *     setAvailability() (PUT /donors/me/availability), same as Settings.tsx.
 *   - Open compatible blood requests via listDonorOpenRequests(), with a real
 *     three-way Accept / Not now / Decline response via respondToRequest() —
 *     the same server rule DonorDashboard.tsx uses (eligible && available)
 *     gates the Accept button here too.
 *   - Active emergencies near this donor via listActiveEmergencies(), tracked with
 *     the existing EmergencyTrackDrawer (mode="responder") — reused, not reinvented.
 *   - A live notifications feed via listNotifications()/markNotificationRead().
 *   - Alert Watcher preferences: a minimum urgency threshold and an alert
 *     radius (km), both persisted on DonorProfile and applied server-side in
 *     notify_matching_donors() — an emergency SOS always reaches you
 *     regardless of the urgency threshold, and the radius only applies once
 *     you're sharing your approximate location.
 *
 * What's deliberately NOT here: a blood-group/component picker. A donor has
 * exactly one blood type on file and LifeLink only tracks whole-blood
 * donations (no components), so a selector for either would have nothing
 * real behind it — compatibility is already computed automatically.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import AvailabilitySelector from "../../components/AvailabilitySelector";
import DonorSectionHeader from "../../components/DonorSectionHeader";
import EmergencyTrackDrawer from "../../components/EmergencyTrackDrawer";
import { toApiError } from "../../lib/api";
import {
  BLOOD_LABEL,
  getDonorProfile,
  listActiveEmergencies,
  listDonorOpenRequests,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  respondToRequest,
  setAvailability,
  updateDonorProfile,
  type DonorAvailabilityState,
  type Urgency,
} from "../../lib/domain";
import { useLiveFallbackInterval } from "../../lib/realtime";
import { useInFlightIds } from "../../lib/inflight";

const URGENCY_OPTIONS: { value: Urgency; label: string }[] = [
  { value: "ROUTINE", label: "Every request" },
  { value: "URGENT", label: "Urgent & critical only" },
  { value: "CRITICAL", label: "Critical only" },
];

function timeAgo(iso: string): string {
  const s = Math.max(1, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function ReadyConsole() {
  const qc = useQueryClient();
  const [emTrackId, setEmTrackId] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");

  const profile = useQuery({ queryKey: ["donor", "me"], queryFn: getDonorProfile });
  const open = useQuery({ queryKey: ["donor", "requests"], queryFn: listDonorOpenRequests });
  const emergencies = useQuery({
    queryKey: ["donor", "emergencies"],
    queryFn: listActiveEmergencies,
    // Phase F: live updates arrive over the event stream; this interval only
    // applies while that stream is down (graceful degradation).
    refetchInterval: useLiveFallbackInterval(20_000),
  });
  const notifications = useQuery({
    queryKey: ["notifications", "list"],
    queryFn: () => listNotifications(false),
  });

  const availabilityMut = useMutation({
    mutationFn: (state: DonorAvailabilityState) => setAvailability(state),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["donor", "me"] });
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
  const alertPrefsMut = useMutation({
    mutationFn: (body: { alert_min_urgency?: Urgency; alert_radius_km?: number | null }) =>
      updateDonorProfile(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["donor", "me"] });
      setActionError("");
    },
    onError: (e) => setActionError(toApiError(e).detail),
  });
  const reading = useInFlightIds();
  const readOne = useMutation({
    mutationFn: (id: string) => markNotificationRead(id),
    onMutate: (id: string) => reading.start(id),
    onSettled: (_d, _e, id) => reading.done(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
      setActionError("");
    },
    onError: (e) => setActionError(toApiError(e).detail),
  });
  const readAll = useMutation({
    mutationFn: markAllNotificationsRead,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
      setActionError("");
    },
    onError: (e) => setActionError(toApiError(e).detail),
  });

  const isEligible = profile.data?.eligible ?? true;
  const isAvailable = profile.data?.is_available ?? false;
  const canRespond = isEligible && isAvailable;
  let respondBlockReason: string | null = null;
  if (!isAvailable) respondBlockReason = "Mark yourself available to respond";
  else if (!isEligible)
    respondBlockReason = `Not eligible until ${profile.data?.eligible_on ?? "your interval passes"}`;

  const unreadNotifications = (notifications.data ?? []).filter((n) => !n.read_at);

  return (
    <div className="min-h-screen bg-background">
      <DonorSectionHeader
        title="Ready Console"
        subtitle="Live dispatch: availability, open requests, and notifications"
        icon={<span aria-hidden="true" className="material-symbols-outlined">radiology</span>}
      />

      <main className="max-w-6xl mx-auto px-4 md:px-margin-desktop py-6 md:py-8 space-y-6 md:space-y-8">
        {actionError && (
          <p role="alert" className="text-sm text-error">{actionError}</p>
        )}
        {profile.isError && (
          <div role="alert" className="bg-error-container text-on-error-container rounded-xl p-4 text-sm flex items-center justify-between gap-3">
            <span>Couldn't load your donor profile: {toApiError(profile.error).detail}</span>
            <button type="button" className="font-label-sm text-label-sm underline flex-shrink-0" onClick={() => profile.refetch()}>
              Retry
            </button>
          </div>
        )}

        {/* Readiness banner */}
        <section
          className={`rounded-xl p-6 shadow-sm flex flex-col sm:flex-row sm:items-center justify-between gap-4 ${
            canRespond ? "bg-secondary/10 border border-secondary/30" : "bg-surface border border-border-subtle"
          }`}
        >
          <div className="flex items-start gap-3">
            <div
              className={`w-11 h-11 rounded-lg flex items-center justify-center flex-shrink-0 ${
                canRespond ? "bg-secondary text-on-secondary" : "bg-surface-container-high text-on-surface-variant"
              }`}
            >
              <span aria-hidden="true" className="material-symbols-outlined text-[24px]">
                {canRespond ? "sensors" : "lock_clock"}
              </span>
            </div>
            <div>
              <p className="font-title-md text-title-md text-on-surface font-bold">
                {canRespond
                  ? "You're ready — visible for dispatch"
                  : !isAvailable
                    ? "You're marked unavailable"
                    : "Recovery interval active"}
              </p>
              <p className="font-body-sm text-body-sm text-on-surface-variant mt-0.5 max-w-xl">
                {canRespond
                  ? "You can accept open requests and respond to emergencies below."
                  : respondBlockReason}
              </p>
            </div>
          </div>
          <AvailabilitySelector
            value={profile.data?.availability_state}
            onChange={(state) => availabilityMut.mutate(state)}
            disabled={!profile.data || availabilityMut.isPending}
          />
        </section>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 flex flex-col gap-6">
            {/* Active emergencies */}
            <section className="flex flex-col gap-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-error">
                  {/* Phase 27: Tailwind's default `animate-pulse` (opacity
                      1<->0.5) drops this `text-error` icon well below WCAG
                      AA contrast for part of every cycle (real axe-core
                      finding, ~3.76:1 even at a 0.7 floor). `animate-pulse-
                      subtle` (1<->0.8, see index.css) keeps it above 4.5:1
                      throughout while still visibly pulsing. */}
                  <span aria-hidden="true" className="material-symbols-outlined animate-pulse-subtle">e911_emergency</span>
                  <h2 className="font-headline-sm text-headline-sm text-on-surface">
                    Active Emergencies Near You
                  </h2>
                </div>
                <span className="font-label-sm text-label-sm text-on-surface-variant bg-surface-container-high px-2 py-1 rounded">
                  {emergencies.data?.length ?? 0} live
                </span>
              </div>
              {emergencies.isLoading && <p className="text-sm text-on-surface-variant">Loading…</p>}
              {emergencies.isError && (
                <div role="alert" className="bg-error-container text-on-error-container rounded-xl p-4 text-sm flex items-center justify-between gap-3">
                  <span>Couldn't check for active emergencies near you — you may be missing a live alert.</span>
                  <button type="button" className="font-label-sm text-label-sm underline flex-shrink-0" onClick={() => emergencies.refetch()}>
                    Retry
                  </button>
                </div>
              )}
              {emergencies.data && emergencies.data.length === 0 && (
                <div className="bg-surface rounded-xl border border-border-subtle p-6 text-center text-sm text-on-surface-variant">
                  No active emergencies in range right now.
                </div>
              )}
              {emergencies.data?.map((e) => {
                const accepted = e.my_response?.status === "ACCEPTED";
                return (
                  <button
                    key={e.id}
                    onClick={() => setEmTrackId(e.id)}
                    className="text-left bg-surface rounded-xl border-l-4 border-l-error border border-border-subtle shadow-sm p-5 hover:bg-surface-container-lowest transition-colors flex flex-col sm:flex-row sm:items-center justify-between gap-3"
                  >
                    <div className="flex items-start gap-3">
                      <div className="w-12 h-12 rounded-xl bg-error/10 text-error flex items-center justify-center flex-shrink-0 font-title-md text-title-md font-bold">
                        {e.blood_type ? BLOOD_LABEL[e.blood_type] : "Any"}
                      </div>
                      <div>
                        <h3 className="font-label-md text-label-md text-on-surface">
                          {e.location_text || "Location not specified"}
                        </h3>
                        <p className="font-body-sm text-body-sm text-on-surface-variant">
                          {e.urgency.toLowerCase()} · {e.accepted_count} responder(s) ·{" "}
                          {e.status.replace("_", " ").toLowerCase()}
                        </p>
                      </div>
                    </div>
                    <span
                      className={`px-4 py-2 rounded-lg font-label-md text-label-md flex-shrink-0 ${
                        accepted ? "bg-success/10 text-success" : "bg-primary text-on-primary"
                      }`}
                    >
                      {accepted ? "You're responding ✓" : "Respond"}
                    </span>
                  </button>
                );
              })}
            </section>

            {/* Open compatible requests */}
            <section className="flex flex-col gap-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-primary">
                  <span aria-hidden="true" className="material-symbols-outlined">assignment_turned_in</span>
                  <h2 className="font-headline-sm text-headline-sm text-on-surface">
                    Open Compatible Requests
                  </h2>
                </div>
                <span className="font-label-sm text-label-sm text-on-surface-variant bg-surface-container-high px-2 py-1 rounded">
                  {open.data?.length ?? 0} open
                </span>
              </div>

              <div className="p-4 rounded-lg bg-surface-container-high/60 flex flex-col gap-3">
                <div className="flex items-center gap-2 text-on-surface">
                  <span aria-hidden="true" className="material-symbols-outlined text-[18px]">tune</span>
                  <span className="font-label-md text-label-md">Alert Watcher</span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label
                      htmlFor="ready-console-alert-urgency"
                      className="block font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider mb-1"
                    >
                      Notify me for
                    </label>
                    <select
                      id="ready-console-alert-urgency"
                      className="w-full h-10 px-3 bg-surface rounded-lg border border-outline-variant font-body-md text-body-md text-on-surface"
                      value={profile.data?.alert_min_urgency ?? "ROUTINE"}
                      disabled={!profile.data || alertPrefsMut.isPending}
                      onChange={(e) =>
                        alertPrefsMut.mutate({ alert_min_urgency: e.target.value as Urgency })
                      }
                    >
                      {URGENCY_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label
                      htmlFor="ready-console-alert-radius"
                      className="block font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider mb-1"
                    >
                      Radius (km)
                    </label>
                    <input
                      id="ready-console-alert-radius"
                      type="number"
                      min={1}
                      max={500}
                      placeholder="No limit"
                      className="w-full h-10 px-3 bg-surface rounded-lg border border-outline-variant font-body-md text-body-md text-on-surface"
                      defaultValue={profile.data?.alert_radius_km ?? ""}
                      disabled={!profile.data || alertPrefsMut.isPending}
                      onBlur={(e) => {
                        const raw = e.target.value.trim();
                        alertPrefsMut.mutate({ alert_radius_km: raw === "" ? null : Number(raw) });
                      }}
                    />
                  </div>
                </div>
                <p className="font-label-sm text-label-sm text-on-surface-variant">
                  An active emergency SOS always reaches you regardless of these filters. Radius only
                  applies once you're sharing your approximate location (Settings).
                </p>
              </div>

              {open.isLoading && (
                <div className="bg-surface rounded-xl border border-border-subtle p-6 text-sm text-on-surface-variant">
                  Loading…
                </div>
              )}
              {open.isError && (
                <div role="alert" className="bg-surface rounded-xl border border-border-subtle p-6 text-sm text-error flex items-center justify-between gap-3">
                  <span>{toApiError(open.error).detail}</span>
                  <button type="button" className="font-label-sm text-label-sm underline flex-shrink-0" onClick={() => open.refetch()}>
                    Retry
                  </button>
                </div>
              )}
              {open.data && open.data.length === 0 && (
                <div className="bg-surface rounded-xl border border-border-subtle p-8 text-center text-sm text-on-surface-variant">
                  {profile.data?.blood_type
                    ? "No open requests match your blood group right now."
                    : "Set your blood group in your profile to see requests you can help with."}
                </div>
              )}
              {open.data?.map((r) => {
                const accepted = r.my_response?.status === "ACCEPTED";
                return (
                  <div
                    key={r.id}
                    className="bg-surface rounded-xl border border-border-subtle shadow-sm p-5 flex flex-col gap-4"
                  >
                    <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
                      <div className="flex items-start gap-3">
                        <div className="w-11 h-11 rounded-lg bg-primary/10 text-primary flex items-center justify-center flex-shrink-0 font-title-md text-title-md font-bold">
                          {BLOOD_LABEL[r.blood_type]}
                        </div>
                        <div>
                          <h3 className="font-label-md text-label-md text-on-surface">
                            {r.hospital_name || r.patient_name || "Patient request"}
                          </h3>
                          <p className="font-body-sm text-body-sm text-on-surface-variant">
                            Needs {r.units_needed} unit(s) {BLOOD_LABEL[r.blood_type]} · {r.urgency.toLowerCase()}
                          </p>
                          <p className="font-label-sm text-label-sm text-secondary mt-1 flex items-center gap-1">
                            <span aria-hidden="true" className="material-symbols-outlined text-[16px]">location_on</span>
                            {r.city || "Location not specified"}
                          </p>
                        </div>
                      </div>
                    </div>
                    <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 pt-1 border-t border-border-subtle">
                      {accepted ? (
                        <div className="flex items-center gap-2">
                          <span className="px-4 py-2 rounded-lg font-label-md text-label-md bg-success/10 text-success">
                            Accepted ✓
                          </span>
                          <button
                            onClick={() => respondMut.mutate({ id: r.id, action: "DECLINED" })}
                            disabled={respondMut.isPending}
                            className="px-4 py-2 rounded-lg font-label-md text-label-md text-on-surface-variant border border-border-subtle hover:bg-surface-variant transition-colors disabled:opacity-60"
                          >
                            Withdraw
                          </button>
                        </div>
                      ) : (
                        <div className="flex flex-col gap-1">
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
                              className="bg-primary text-on-primary px-5 py-2 rounded-lg font-label-md text-label-md hover:bg-primary-container transition-colors disabled:opacity-60"
                            >
                              Accept
                            </button>
                            <button
                              onClick={() => respondMut.mutate({ id: r.id, action: "NOT_NOW" })}
                              disabled={respondMut.isPending}
                              title="Can't help right now, but not a hard no"
                              className="bg-surface-container-high text-on-surface px-5 py-2 rounded-lg font-label-md text-label-md hover:bg-surface-variant transition-colors disabled:opacity-60"
                            >
                              Not now
                            </button>
                            <button
                              onClick={() => respondMut.mutate({ id: r.id, action: "DECLINED" })}
                              disabled={respondMut.isPending}
                              className="text-on-surface-variant px-4 py-2 rounded-lg font-label-md text-label-md hover:bg-surface-variant transition-colors disabled:opacity-60"
                            >
                              Decline
                            </button>
                          </div>
                          {respondBlockReason && (
                            <span className="font-label-sm text-label-sm text-error">{respondBlockReason}</span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </section>
          </div>

          {/* Notifications rail */}
          <section className="bg-surface rounded-xl border border-border-subtle shadow-sm p-5 flex flex-col gap-3 h-fit">
            <div className="flex items-center justify-between">
              <h2 className="font-headline-sm text-headline-sm text-on-surface">Notifications</h2>
              <button
                className="text-secondary text-xs font-label-md hover:underline disabled:opacity-40"
                disabled={unreadNotifications.length === 0 || readAll.isPending}
                onClick={() => readAll.mutate()}
              >
                Mark all read
              </button>
            </div>
            {notifications.isLoading && <p className="text-sm text-on-surface-variant">Loading…</p>}
            {notifications.isError && (
              <div role="alert" className="text-sm text-error flex items-center justify-between gap-3">
                <span>{toApiError(notifications.error).detail}</span>
                <button type="button" className="font-label-sm text-label-sm underline flex-shrink-0" onClick={() => notifications.refetch()}>
                  Retry
                </button>
              </div>
            )}
            {notifications.data && notifications.data.length === 0 && (
              <p className="text-sm text-on-surface-variant text-center py-6">You're all caught up.</p>
            )}
            <ul className="divide-y divide-border-subtle -mx-1">
              {/* Phase 27: opacity-60 (below) dropped a read row's
                  on-surface-variant text below WCAG AA contrast (real
                  axe-core finding, ~3.1:1); opacity-80 keeps the same
                  "read = dimmer" visual cue while staying above 4.5:1
                  (~5.1:1 measured). */}
              {notifications.data?.slice(0, 20).map((n) => (
                <li key={n.id} className={`px-1 py-3 flex gap-3 ${n.read_at ? "opacity-80" : ""}`}>
                  <span
                    className={`mt-1.5 h-2 w-2 rounded-full flex-shrink-0 ${
                      n.read_at ? "bg-transparent" : "bg-primary"
                    }`}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="font-label-md text-label-md text-on-surface truncate">{n.title}</p>
                    {n.body && (
                      <p className="text-xs text-on-surface-variant mt-0.5 line-clamp-2">{n.body}</p>
                    )}
                    <p className="text-[11px] text-on-surface-variant mt-1">{timeAgo(n.created_at)}</p>
                  </div>
                  {!n.read_at && (
                    <button
                      className="text-secondary text-[11px] font-label-md hover:underline self-start flex-shrink-0 disabled:opacity-40"
                      disabled={reading.has(n.id)}
                      onClick={() => !reading.has(n.id) && readOne.mutate(n.id)}
                    >
                      {reading.has(n.id) ? "Reading…" : "Read"}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </section>
        </div>
      </main>

      <EmergencyTrackDrawer emergencyId={emTrackId} onClose={() => setEmTrackId(null)} mode="responder" />
    </div>
  );
}
