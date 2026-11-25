/**
 * Donor Eligibility Center — adapted from the Stitch export
 * lifelink_2.1_2.3_donor_eligibility_pre_schedule_center/code.html.
 *
 * What's REAL here (see backend/app/api/routes/donors.py + core/eligibility.py):
 *   - DonorProfile.eligible / eligible_on, server-computed from last_donation_date
 *     with a fixed 56-day whole-blood interval (core/eligibility.py). The recovery
 *     ring/countdown math below is the same shape as DonorDashboard.tsx's
 *     eligibility()/recoveryPercent() helpers, kept in sync intentionally.
 *   - Donation history via listDonations().
 *   - Profile fields on file: blood_type, weight_kg, medical_conditions.
 *   - Pre-scheduling is now real: the facility list comes from
 *     getHospitalDirectory(), and "Request an appointment" calls
 *     requestAppointment() (POST /donors/me/appointments). Existing/past
 *     appointments come from listMyAppointments() and can be cancelled while
 *     REQUESTED or CONFIRMED.
 *
 * What's FABRICATED in the original design and deliberately NOT reproduced:
 *   hemoglobin/ferritin telemetry, a named "medical director", and per-item
 *   clinical pass/fail chips (age, wellness, travel history) the backend never
 *   checks.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import DonorSectionHeader from "../../components/DonorSectionHeader";
import { toApiError } from "../../lib/api";
import {
  BLOOD_LABEL,
  cancelAppointment,
  getDonorProfile,
  getHospitalDirectory,
  listDonations,
  listMyAppointments,
  requestAppointment,
  type AppointmentStatus,
} from "../../lib/domain";

const DONATION_INTERVAL_DAYS = 56;
const RING_CIRCUMFERENCE = 502.65; // 2 * pi * r=80, matches DonorDashboard.tsx

/** Same shape as DonorDashboard.tsx's eligibility() helper — kept identical on purpose. */
function eligibility(lastDonation: string | null): { text: string; sub: string } {
  if (!lastDonation) return { text: "Eligible", sub: "No donation on record yet" };
  const next = new Date(lastDonation);
  next.setDate(next.getDate() + DONATION_INTERVAL_DAYS);
  const days = Math.ceil((next.getTime() - Date.now()) / 86_400_000);
  if (days <= 0) return { text: "Eligible", sub: `Last donated ${lastDonation}` };
  return { text: `${days} day${days === 1 ? "" : "s"}`, sub: `Eligible on ${next.toISOString().slice(0, 10)}` };
}

/** Same shape as DonorDashboard.tsx's recoveryPercent() helper. */
function recoveryPercent(lastDonation: string | null, eligible: boolean): number {
  if (eligible || !lastDonation) return 100;
  const elapsed = (Date.now() - new Date(lastDonation).getTime()) / 86_400_000;
  return Math.max(0, Math.min(100, Math.round((elapsed / DONATION_INTERVAL_DAYS) * 100)));
}

function daysElapsedSince(lastDonation: string | null): number {
  if (!lastDonation) return DONATION_INTERVAL_DAYS;
  const elapsed = (Date.now() - new Date(lastDonation).getTime()) / 86_400_000;
  return Math.max(0, Math.min(DONATION_INTERVAL_DAYS, Math.round(elapsed)));
}

const APPT_STATUS_STYLE: Record<AppointmentStatus, string> = {
  REQUESTED: "bg-warning/15 text-warning",
  CONFIRMED: "bg-success/15 text-success",
  CANCELLED: "bg-surface-container-high text-on-surface-variant",
  COMPLETED: "bg-primary/10 text-primary",
};

export default function EligibilityCenter() {
  const qc = useQueryClient();
  const [selectedHospitalId, setSelectedHospitalId] = useState("");
  const [selectedDate, setSelectedDate] = useState("");
  const [confirmMsg, setConfirmMsg] = useState("");

  const profile = useQuery({ queryKey: ["donor", "me"], queryFn: getDonorProfile });
  const donations = useQuery({ queryKey: ["donor", "donations"], queryFn: listDonations });
  const hospitals = useQuery({ queryKey: ["hospitals", "directory"], queryFn: getHospitalDirectory });
  const appointments = useQuery({ queryKey: ["donor", "appointments"], queryFn: listMyAppointments });

  const requestAppt = useMutation({
    mutationFn: () =>
      requestAppointment({
        hospital_id: selectedHospitalId,
        preferred_date: selectedDate,
      }),
    onSuccess: () => {
      setConfirmMsg("Appointment requested. You'll be notified once the facility confirms it.");
      setSelectedHospitalId("");
      setSelectedDate("");
      qc.invalidateQueries({ queryKey: ["donor", "appointments"] });
    },
  });

  const cancelAppt = useMutation({
    mutationFn: (id: string) => cancelAppointment(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["donor", "appointments"] }),
  });

  const canRequestAppt = !!selectedHospitalId && !!selectedDate && !requestAppt.isPending;

  const isEligible = profile.data?.eligible ?? true;
  const elig = eligibility(profile.data?.last_donation_date ?? null);
  const recoveryPct = recoveryPercent(profile.data?.last_donation_date ?? null, isEligible);
  const daysElapsed = daysElapsedSince(profile.data?.last_donation_date ?? null);

  return (
    <div className="min-h-screen bg-background">
      <DonorSectionHeader
        title="Eligibility Center"
        subtitle="Your donation interval, recovery window, and history"
        icon={<span aria-hidden="true" className="material-symbols-outlined">verified_user</span>}
      />

      <main className="max-w-6xl mx-auto px-4 md:px-margin-desktop py-6 md:py-8 space-y-6 md:space-y-8">
        {profile.isError && (
          <div className="bg-error-container text-on-error-container rounded-xl p-4 text-sm">
            Couldn't load your eligibility status: {toApiError(profile.error).detail}
          </div>
        )}

        {/* Hero: recovery ring + status */}
        <section className="bg-surface rounded-xl border border-border-subtle shadow-sm p-6 md:p-8">
          <div className="flex flex-col lg:flex-row lg:items-center gap-8">
            <div className="flex flex-col sm:flex-row items-center gap-6 flex-1">
              <div className="relative w-40 h-40 flex items-center justify-center flex-shrink-0">
                <svg className="w-full h-full transform -rotate-90" viewBox="0 0 192 192">
                  <circle
                    className="text-surface-variant"
                    cx="96"
                    cy="96"
                    fill="transparent"
                    r="80"
                    stroke="currentColor"
                    strokeWidth="12"
                  />
                  <circle
                    className="text-primary transition-all duration-700"
                    cx="96"
                    cy="96"
                    fill="transparent"
                    r="80"
                    stroke="currentColor"
                    strokeDasharray={RING_CIRCUMFERENCE}
                    strokeDashoffset={RING_CIRCUMFERENCE * (1 - recoveryPct / 100)}
                    strokeLinecap="round"
                    strokeWidth="12"
                  />
                </svg>
                <div className="absolute flex flex-col items-center">
                  <span className="font-headline-lg text-headline-lg text-primary leading-none">
                    {profile.data ? `${recoveryPct}%` : "—"}
                  </span>
                  <span className="font-label-sm text-label-sm text-on-surface-variant text-center mt-1">
                    {isEligible ? "Ready to donate" : "Recovering"}
                  </span>
                </div>
              </div>
              <div className="space-y-2 text-center sm:text-left">
                <span className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider">
                  Current status
                </span>
                <div className="font-headline-lg text-headline-lg text-on-surface">
                  {profile.isLoading ? "Loading…" : elig.text}
                </div>
                <p className="font-body-sm text-body-sm text-on-surface-variant">{elig.sub}</p>
                <p className="font-body-sm text-body-sm text-on-surface-variant">
                  {profile.data?.last_donation_date
                    ? `Last donation on record: ${profile.data.last_donation_date}`
                    : "No donation on record yet."}
                </p>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3 w-full lg:w-auto lg:min-w-[220px]">
              <div className="bg-surface-container-low rounded-lg p-3">
                <div className="font-label-sm text-label-sm text-on-surface-variant uppercase">Blood group</div>
                <div className="font-title-md text-title-md text-on-surface font-bold">
                  {profile.data?.blood_type ? BLOOD_LABEL[profile.data.blood_type] : "Not set"}
                </div>
              </div>
              <div className="bg-surface-container-low rounded-lg p-3">
                <div className="font-label-sm text-label-sm text-on-surface-variant uppercase">Weight on file</div>
                <div className="font-title-md text-title-md text-on-surface font-bold">
                  {profile.data?.weight_kg != null ? `${profile.data.weight_kg} kg` : "Not set"}
                </div>
              </div>
              <div className="bg-surface-container-low rounded-lg p-3 col-span-2">
                <div className="font-label-sm text-label-sm text-on-surface-variant uppercase">
                  Interval rule (server-enforced)
                </div>
                <div className="font-body-sm text-body-sm text-on-surface">
                  {DONATION_INTERVAL_DAYS} days between whole-blood donations
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Recovery interval timeline */}
        <section className="bg-surface rounded-xl border border-border-subtle shadow-sm p-6 space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <h2 className="font-headline-sm text-headline-sm text-on-surface">
              {DONATION_INTERVAL_DAYS}-Day Donation Interval
            </h2>
            <span className="font-label-sm text-label-sm text-on-surface-variant">
              Day {daysElapsed} of {DONATION_INTERVAL_DAYS}
            </span>
          </div>
          <p className="font-body-sm text-body-sm text-on-surface-variant">
            LifeLink enforces a fixed {DONATION_INTERVAL_DAYS}-day minimum interval between whole-blood
            donations, computed from your last recorded donation date. This is the same rule the server
            uses to accept or block your responses to requests.
          </p>
          <div className="w-full h-3 bg-surface-variant rounded-full overflow-hidden">
            <div
              className="h-full bg-primary rounded-full transition-all duration-700"
              style={{ width: `${(daysElapsed / DONATION_INTERVAL_DAYS) * 100}%` }}
            />
          </div>
          <div className="flex justify-between font-label-sm text-label-sm text-on-surface-variant">
            <span>Day 0 {profile.data?.last_donation_date ? `(${profile.data.last_donation_date})` : ""}</span>
            <span>Day {DONATION_INTERVAL_DAYS} {profile.data?.eligible_on ? `(${profile.data.eligible_on})` : ""}</span>
          </div>
          <div className="p-3 rounded-lg bg-surface-container-low text-on-surface-variant text-body-sm">
            {profile.data?.medical_conditions ? (
              <>
                <span className="font-semibold text-on-surface">Medical conditions on file: </span>
                {profile.data.medical_conditions}
              </>
            ) : (
              "No medical conditions on file."
            )}
          </div>
          <div className="p-3 rounded-lg bg-surface-container-high text-on-surface-variant text-body-sm flex items-start gap-2">
            <span aria-hidden="true" className="material-symbols-outlined text-[18px] text-on-surface-variant mt-0.5">info</span>
            <span>
              General donor guidance (minimum age, general wellness, recent travel) is informational only
              — LifeLink does not currently verify these automatically. The {DONATION_INTERVAL_DAYS}-day
              interval above is the only eligibility rule the server enforces.
            </span>
          </div>
        </section>

        {/* Pre-schedule a donation appointment */}
        <section className="bg-surface rounded-xl border border-border-subtle shadow-sm p-6 space-y-4">
          <h2 className="font-headline-sm text-headline-sm text-on-surface">Pre-Schedule a Donation</h2>
          <p className="font-body-sm text-body-sm text-on-surface-variant">
            Pick a registered facility and a preferred date. The facility will confirm your appointment;
            you'll see its status below.
          </p>

          {hospitals.isError && (
            <p className="text-sm text-error">{toApiError(hospitals.error).detail}</p>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label htmlFor="facility-select" className="block text-label-sm font-label-sm uppercase tracking-wider text-on-surface-variant mb-1.5">
                Collection facility
              </label>
              <select
                id="facility-select"
                value={selectedHospitalId}
                onChange={(e) => setSelectedHospitalId(e.target.value)}
                disabled={hospitals.isLoading || (hospitals.data?.length ?? 0) === 0}
                className="w-full h-11 px-4 bg-surface rounded-lg border border-outline-variant font-body-md text-body-md text-on-surface disabled:bg-surface-container-low disabled:text-on-surface-variant disabled:cursor-not-allowed"
              >
                <option value="">
                  {hospitals.isLoading
                    ? "Loading facilities…"
                    : (hospitals.data?.length ?? 0) === 0
                    ? "No facilities registered yet"
                    : "Select a facility"}
                </option>
                {hospitals.data?.map((h) => (
                  <option key={h.id} value={h.id}>
                    {h.name ?? "Unnamed facility"}
                    {h.city ? ` — ${h.city}` : ""}
                    {!h.verified ? " (Unverified)" : ""}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="preferred-date" className="block text-label-sm font-label-sm uppercase tracking-wider text-on-surface-variant mb-1.5">
                Preferred date
              </label>
              <input
                id="preferred-date"
                type="date"
                value={selectedDate}
                onChange={(e) => setSelectedDate(e.target.value)}
                min={profile.data?.eligible_on ?? undefined}
                className="w-full h-11 px-4 bg-surface rounded-lg border border-outline-variant font-body-md text-body-md text-on-surface"
              />
            </div>
          </div>
          <div className="pt-2">
            <button
              type="button"
              disabled={!canRequestAppt}
              onClick={() => {
                setConfirmMsg("");
                requestAppt.mutate();
              }}
              className="w-full sm:w-auto bg-primary hover:bg-primary-container text-on-primary font-label-md text-label-md px-6 py-3 rounded-xl transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
            >
              {requestAppt.isPending ? (
                <Loader2 className="h-5 w-5 animate-spin" />
              ) : (
                <span aria-hidden="true" className="material-symbols-outlined text-[20px]">event_available</span>
              )}
              Request an appointment
            </button>
            {requestAppt.isError && (
              <p className="mt-3 text-body-sm text-error bg-error-container rounded-lg p-3">
                {toApiError(requestAppt.error).detail}
              </p>
            )}
            {confirmMsg && (
              <p className="mt-3 text-body-sm text-success bg-success/10 rounded-lg p-3">{confirmMsg}</p>
            )}
          </div>

          {/* Existing appointments */}
          <div className="pt-2 border-t border-border-subtle">
            <h3 className="font-title-md text-title-md text-on-surface font-semibold mt-4 mb-2">
              Your appointments
            </h3>
            {appointments.isLoading && <p className="text-sm text-on-surface-variant">Loading…</p>}
            {appointments.isError && (
              <p className="text-sm text-error">{toApiError(appointments.error).detail}</p>
            )}
            {appointments.data && appointments.data.length === 0 && (
              <p className="text-sm text-on-surface-variant">No appointments requested yet.</p>
            )}
            {cancelAppt.isError && (
              <p role="alert" className="text-sm text-error">
                Couldn't cancel that appointment: {toApiError(cancelAppt.error).detail}
              </p>
            )}
            {appointments.data && appointments.data.length > 0 && (
              <ul className="divide-y divide-border-subtle">
                {appointments.data.map((a) => (
                  <li key={a.id} className="py-3 flex items-center justify-between gap-3">
                    <div>
                      <p className="font-label-md text-label-md text-on-surface">
                        {a.hospital_name || "—"}
                      </p>
                      <p className="font-body-sm text-body-sm text-on-surface-variant">
                        {a.preferred_date}
                        {a.note ? ` · ${a.note}` : ""}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <span className={`px-2.5 py-0.5 rounded-full text-xs font-bold ${APPT_STATUS_STYLE[a.status]}`}>
                        {a.status}
                      </span>
                      {(a.status === "REQUESTED" || a.status === "CONFIRMED") && (
                        <button
                          type="button"
                          onClick={() => cancelAppt.mutate(a.id)}
                          disabled={cancelAppt.isPending}
                          className="text-error text-xs font-label-sm hover:underline disabled:opacity-50"
                        >
                          Cancel
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>

        {/* Donation history */}
        <section className="bg-surface rounded-xl border border-border-subtle shadow-sm p-6 space-y-4">
          <h2 className="font-headline-sm text-headline-sm text-on-surface">Donation History</h2>
          {donations.isLoading && <p className="text-sm text-on-surface-variant">Loading…</p>}
          {donations.isError && (
            <p className="text-sm text-error">{toApiError(donations.error).detail}</p>
          )}
          {donations.data && donations.data.length === 0 && (
            <p className="text-sm text-on-surface-variant">
              No donations on record yet. Once you complete a donation, it will show up here.
            </p>
          )}
          {donations.data && donations.data.length > 0 && (
            <ul className="divide-y divide-border-subtle">
              {donations.data.map((d) => (
                <li key={d.id} className="py-3 flex items-center justify-between gap-3">
                  <div>
                    <p className="font-label-md text-label-md text-on-surface">
                      {d.hospital_name || "Donation"}
                    </p>
                    <p className="font-body-sm text-body-sm text-on-surface-variant">
                      {d.donated_on} · {d.units} unit(s)
                      {d.note ? ` · ${d.note}` : ""}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </div>
  );
}
