/**
 * New Blood Request — Guided Flow (full page)
 * Adapted from the Stitch export: lifelink_1.3_new_blood_request_guided_flow/code.html
 *
 * The Stitch mock included fabricated "Donor Match Probability 98.4%" and "14 Vehicles
 * On Standby / Cold-Chain Network" widgets, a hospital dropdown of invented named
 * facilities, and a "Save Draft" action explicitly marked [BACKEND REQUIRED] in the
 * design itself — there is no draft-saving endpoint, so that action is omitted rather
 * than faked. Everything else here is a client-side wizard around the one real endpoint,
 * `POST /blood-requests` (createBloodRequest): blood type, units, urgency, hospital name,
 * city, needed-by, and note are the only fields the backend accepts — no fake
 * "matching in progress" step is inserted between submit and the real result.
 */
import { useState } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { Loader2, Menu } from "lucide-react";

import RequestTrackDrawer from "../../components/RequestTrackDrawer";
import BloodCompatibilityChart from "../../components/BloodCompatibilityChart";
import MobileNavDrawer from "../../components/MobileNavDrawer";
import { toApiError } from "../../lib/api";
import {
  BLOOD_LABEL,
  BLOOD_TYPES,
  createBloodRequest,
  type BloodRequest,
  type BloodType,
  type CreateBloodRequestBody,
  type Urgency,
} from "../../lib/domain";
import { newIdempotencyKey } from "../../lib/idempotency";

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

const URGENCIES: { value: Urgency; label: string; blurb: string; icon: string }[] = [
  { value: "ROUTINE", label: "Routine", blurb: "Standard elective or scheduled transfusion.", icon: "schedule" },
  { value: "URGENT", label: "Urgent", blurb: "Priority admission, needed soon.", icon: "bolt" },
  { value: "CRITICAL", label: "Critical", blurb: "Immediate, life-threatening need.", icon: "e911_emergency" },
];

const STEP_LABELS = ["Blood & Urgency", "Facility & Timing", "Review & Submit"];

function Stepper({ step }: { step: number }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-space-3">
      {STEP_LABELS.map((label, i) => (
        <div
          key={label}
          className={`flex items-center gap-space-3 p-space-3 rounded-lg transition-all ${
            // Phase 27: step badge color already distinguishes
            // done/current/upcoming -- the extra `opacity-70` here was
            // redundant and dropped this card's own label text below WCAG
            // AA contrast (real axe-core finding). Removed.
            i === step ? "bg-surface-container-low" : "bg-surface-container-lowest"
          }`}
        >
          <div
            className={`w-8 h-8 rounded-full flex items-center justify-center font-label-lg text-label-lg font-bold shrink-0 ${
              i < step
                ? "bg-secondary text-on-secondary"
                : i === step
                  ? "bg-primary-container text-on-primary"
                  : "bg-surface-container-high text-on-surface-variant"
            }`}
          >
            {i < step ? <span aria-hidden="true" className="material-symbols-outlined text-[18px]">check</span> : i + 1}
          </div>
          <div className="min-w-0">
            <span className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider block">
              Step {i + 1}
            </span>
            <span className="font-title-md text-title-md text-on-surface truncate block">{label}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function NewRequestGuidedFlow() {
  const location = useLocation();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [bloodType, setBloodType] = useState<BloodType | "">("");
  const [units, setUnits] = useState(1);
  const [urgency, setUrgency] = useState<Urgency>("ROUTINE");
  const [hospital, setHospital] = useState("");
  const [city, setCity] = useState("");
  const [neededBy, setNeededBy] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [created, setCreated] = useState<BloodRequest | null>(null);
  const [trackId, setTrackId] = useState<string | null>(null);
  // Phase 21: mirrors BloodRequestModal's idempotency-key contract (see
  // lib/idempotency.ts) -- this guided flow calls the exact same
  // `POST /blood-requests` endpoint but, before this fix, sent no
  // idempotency_key at all, so a submit that failed for a transient reason
  // (timeout, network blip) and was retried by the user could create a
  // genuine duplicate blood request. Minted once per attempt, reused across
  // retries of that same attempt, and only rotated on a real 409
  // idempotency-key conflict (i.e. the form changed since the key was
  // minted) -- never on an ordinary retry.
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);

  const create = useMutation({
    mutationFn: (body: CreateBloodRequestBody) => createBloodRequest(body),
    onSuccess: (r) => {
      setCreated(r);
      setError("");
    },
    onError: (e) => {
      const apiError = toApiError(e);
      setError(apiError.detail);
      if (apiError.status === 409) {
        setIdempotencyKey(newIdempotencyKey());
      }
    },
  });

  function canProceedFromStep1() {
    return bloodType !== "" && units >= 1;
  }

  function submit() {
    if (!bloodType) {
      setError("Choose a blood group.");
      return;
    }
    // Double-click / rapid-resubmit guard -- see BloodRequestModal's
    // identical comment. The disabled attribute on the submit button covers
    // the common case, but this guards the same re-entrancy window a native
    // form's Enter-key submit would otherwise be able to race.
    if (create.isPending) return;
    create.mutate({
      blood_type: bloodType,
      units_needed: units,
      urgency,
      hospital_name: hospital || null,
      city: city || null,
      note: note || null,
      needed_by: neededBy || null,
      idempotency_key: idempotencyKey,
    });
  }

  function resetAll() {
    setStep(0);
    setBloodType("");
    setUnits(1);
    setUrgency("ROUTINE");
    setHospital("");
    setCity("");
    setNeededBy("");
    setNote("");
    setError("");
    setCreated(null);
    setIdempotencyKey(newIdempotencyKey());
  }

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
                n.path === "/patient/new-request"
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
            <h1 className="font-title-lg text-title-lg text-on-surface truncate">New Blood Request</h1>
          </div>
          <Link to="/patient" className="font-label-md text-label-md text-secondary hover:underline">
            Back to Dashboard
          </Link>
        </header>

        <main className="lg:pt-16 min-h-screen bg-background w-full px-space-4 lg:px-space-6 py-space-6">
          <div className="flex flex-col w-full gap-space-8 max-w-container-max mx-auto">
            {created ? (
              <section className="bg-surface-container-lowest rounded-xl shadow-level-2 p-space-8 flex flex-col items-center text-center gap-space-5 max-w-xl mx-auto w-full">
                <div className="w-16 h-16 rounded-full bg-secondary-fixed flex items-center justify-center text-secondary">
                  <span aria-hidden="true" className="material-symbols-outlined text-[32px]">task_alt</span>
                </div>
                <div className="flex flex-col gap-space-2">
                  <h2 className="font-headline-md text-headline-md text-on-surface">Request submitted</h2>
                  <p className="font-body-md text-body-md text-on-surface-variant">
                    {BLOOD_LABEL[created.blood_type]} · {created.units_needed} unit(s) ·{" "}
                    {created.urgency.toLowerCase()} urgency. Compatible donors near this request
                    are being notified now.
                  </p>
                </div>
                <div className="flex flex-col sm:flex-row items-center gap-space-3 w-full">
                  <button
                    onClick={() => setTrackId(created.id)}
                    className="w-full sm:flex-1 h-12 rounded-full bg-primary hover:bg-primary-container text-on-primary font-label-lg text-label-lg font-semibold shadow-level-1 transition-all"
                  >
                    Track this request
                  </button>
                  <button
                    onClick={resetAll}
                    className="w-full sm:w-auto h-12 px-space-6 rounded-full bg-surface-container-low hover:bg-surface-container-high text-on-surface font-label-lg text-label-lg font-semibold transition-colors"
                  >
                    Submit another
                  </button>
                </div>
                <Link to="/patient" className="text-secondary font-label-md text-label-md hover:underline">
                  Go to Dashboard
                </Link>
              </section>
            ) : (
              <>
                <div className="w-full bg-surface-container-lowest rounded-xl shadow-level-1 px-space-6 py-space-5">
                  <div className="flex items-center gap-space-2 text-primary mb-space-2">
                    <span aria-hidden="true" className="material-symbols-outlined text-[20px]">add_circle</span>
                    <span className="font-label-md text-label-md uppercase tracking-wider font-semibold">
                      Guided request
                    </span>
                  </div>
                  <h2 className="font-headline-md text-headline-md text-on-surface mb-space-5">
                    Initiate a blood request
                  </h2>
                  <Stepper step={step} />
                </div>

                <div className="grid grid-cols-1 xl:grid-cols-12 gap-space-8 items-start">
                  <div className="xl:col-span-8 flex flex-col gap-space-6">
                    {step === 0 && (
                      <>
                        <div className="bg-surface-container-lowest rounded-xl shadow-level-1 p-space-6 flex flex-col gap-space-5">
                          <div className="flex items-center gap-space-2">
                            <span aria-hidden="true" className="material-symbols-outlined text-primary text-[22px]">bloodtype</span>
                            <h3 className="font-headline-sm text-headline-sm text-on-surface">Blood group</h3>
                          </div>
                          <div className="grid grid-cols-2 sm:grid-cols-4 gap-space-3">
                            {BLOOD_TYPES.map((bt) => (
                              <button
                                key={bt}
                                type="button"
                                onClick={() => setBloodType(bt)}
                                aria-pressed={bloodType === bt}
                                className={`flex flex-col items-start p-space-4 rounded-xl text-left transition-all ${
                                  bloodType === bt
                                    ? "bg-primary-container text-on-primary shadow-level-1"
                                    : "bg-surface-container-low hover:bg-surface-container-high text-on-surface"
                                }`}
                              >
                                <span className="font-headline-md text-headline-md font-bold leading-none mb-space-2">
                                  {BLOOD_LABEL[bt]}
                                </span>
                              </button>
                            ))}
                          </div>

                          {bloodType && (
                            <div className="bg-surface-container-low rounded-xl p-space-4">
                              <BloodCompatibilityChart recipientType={bloodType} />
                            </div>
                          )}

                          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-space-4 bg-surface-container-low p-space-4 rounded-xl">
                            <div>
                              <span className="font-title-md text-title-md text-on-surface font-semibold block">
                                Units needed
                              </span>
                            </div>
                            <div className="flex items-center gap-space-3 bg-surface-container-lowest p-1 rounded-xl shadow-sm">
                              <button
                                type="button"
                                aria-label="Decrease units"
                                onClick={() => setUnits((u) => Math.max(1, u - 1))}
                                className="w-10 h-10 rounded-lg flex items-center justify-center bg-surface-container-low hover:bg-surface-container-high text-on-surface"
                              >
                                <span aria-hidden="true" className="material-symbols-outlined text-[20px]">remove</span>
                              </button>
                              <span className="font-headline-sm text-headline-sm text-on-surface font-bold min-w-[48px] text-center">
                                {units}
                              </span>
                              <button
                                type="button"
                                aria-label="Increase units"
                                onClick={() => setUnits((u) => Math.min(20, u + 1))}
                                className="w-10 h-10 rounded-lg flex items-center justify-center bg-surface-container-low hover:bg-surface-container-high text-on-surface"
                              >
                                <span aria-hidden="true" className="material-symbols-outlined text-[20px]">add</span>
                              </button>
                            </div>
                          </div>
                        </div>

                        <div className="bg-surface-container-lowest rounded-xl shadow-level-1 p-space-6 flex flex-col gap-space-5">
                          <div className="flex items-center gap-space-2">
                            <span aria-hidden="true" className="material-symbols-outlined text-primary text-[22px]">alarm</span>
                            <h3 className="font-headline-sm text-headline-sm text-on-surface">Urgency</h3>
                          </div>
                          <div className="grid grid-cols-1 md:grid-cols-3 gap-space-4">
                            {URGENCIES.map((u) => (
                              <label
                                key={u.value}
                                className={`relative flex flex-col justify-between p-space-4 rounded-xl cursor-pointer transition-all ${
                                  urgency === u.value ? "bg-primary-fixed" : "bg-surface-container-low hover:bg-surface-container-high"
                                }`}
                              >
                                <input
                                  type="radio"
                                  name="urgency"
                                  className="sr-only"
                                  checked={urgency === u.value}
                                  onChange={() => setUrgency(u.value)}
                                />
                                <span className="font-title-md text-title-md text-on-surface font-semibold block mb-space-1">
                                  {u.label}
                                </span>
                                <span className="font-body-sm text-body-sm text-on-surface-variant leading-relaxed block">
                                  {u.blurb}
                                </span>
                                <span className="mt-space-3 flex items-center gap-1 text-on-surface-variant font-label-sm text-label-sm">
                                  <span aria-hidden="true" className="material-symbols-outlined text-[16px]">{u.icon}</span>
                                </span>
                              </label>
                            ))}
                          </div>
                        </div>
                      </>
                    )}

                    {step === 1 && (
                      <div className="bg-surface-container-lowest rounded-xl shadow-level-1 p-space-6 flex flex-col gap-space-5">
                        <div className="flex items-center gap-space-2">
                          <span aria-hidden="true" className="material-symbols-outlined text-primary text-[22px]">local_hospital</span>
                          <h3 className="font-headline-sm text-headline-sm text-on-surface">Facility &amp; timing</h3>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-space-5">
                          <div className="flex flex-col gap-space-2">
                            <label className="font-label-md text-label-md text-on-surface font-semibold" htmlFor="hospital-name">
                              Hospital / clinic (optional)
                            </label>
                            <input
                              id="hospital-name"
                              className="w-full h-11 px-space-3 rounded-lg bg-surface-container-low text-body-md text-on-surface focus:outline-none focus:bg-surface-container-lowest focus:ring-2 focus:ring-primary/40"
                              value={hospital}
                              onChange={(e) => setHospital(e.target.value)}
                              placeholder="e.g. Metro General Hospital"
                            />
                          </div>
                          <div className="flex flex-col gap-space-2">
                            <label className="font-label-md text-label-md text-on-surface font-semibold" htmlFor="city">
                              City (optional)
                            </label>
                            <input
                              id="city"
                              className="w-full h-11 px-space-3 rounded-lg bg-surface-container-low text-body-md text-on-surface focus:outline-none focus:bg-surface-container-lowest focus:ring-2 focus:ring-primary/40"
                              value={city}
                              onChange={(e) => setCity(e.target.value)}
                              placeholder="e.g. Springfield"
                            />
                          </div>
                        </div>
                        <div className="flex flex-col gap-space-2">
                          <label className="font-label-md text-label-md text-on-surface font-semibold" htmlFor="needed-by">
                            Needed by (optional)
                          </label>
                          <input
                            id="needed-by"
                            type="date"
                            className="w-full sm:w-64 h-11 px-space-3 rounded-lg bg-surface-container-low text-body-md text-on-surface focus:outline-none focus:bg-surface-container-lowest focus:ring-2 focus:ring-primary/40"
                            value={neededBy}
                            onChange={(e) => setNeededBy(e.target.value)}
                          />
                        </div>
                        <div className="flex flex-col gap-space-2">
                          <label className="font-label-md text-label-md text-on-surface font-semibold" htmlFor="clinical-notes">
                            Note (optional)
                          </label>
                          <textarea
                            id="clinical-notes"
                            rows={3}
                            className="w-full p-space-3 rounded-lg bg-surface-container-low text-body-md text-on-surface focus:outline-none focus:bg-surface-container-lowest focus:ring-2 focus:ring-primary/40"
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                            placeholder="Anything donors or the hospital should know…"
                          />
                        </div>
                      </div>
                    )}

                    {step === 2 && (
                      <div className="bg-surface-container-lowest rounded-xl shadow-level-1 p-space-6 flex flex-col gap-space-5">
                        <div className="flex items-center gap-space-2">
                          <span aria-hidden="true" className="material-symbols-outlined text-primary text-[22px]">fact_check</span>
                          <h3 className="font-headline-sm text-headline-sm text-on-surface">Review &amp; submit</h3>
                        </div>
                        <dl className="flex flex-col gap-space-3">
                          {[
                            ["Blood group", bloodType ? BLOOD_LABEL[bloodType] : "—"],
                            ["Units", String(units)],
                            ["Urgency", urgency[0] + urgency.slice(1).toLowerCase()],
                            ["Hospital", hospital || "Not specified"],
                            ["City", city || "Not specified"],
                            ["Needed by", neededBy || "Not specified"],
                            ["Note", note || "—"],
                          ].map(([k, v]) => (
                            <div key={k} className="flex items-center justify-between gap-space-4 py-space-2 border-b border-surface-container-high last:border-0">
                              <dt className="font-body-md text-body-md text-on-surface-variant">{k}</dt>
                              <dd className="font-label-md text-label-md text-on-surface font-semibold text-right truncate max-w-[60%]">
                                {v}
                              </dd>
                            </div>
                          ))}
                        </dl>
                        {error && <p className="text-error text-body-sm">{error}</p>}
                      </div>
                    )}

                    <div className="flex items-center justify-between gap-space-3">
                      <button
                        type="button"
                        onClick={() => setStep((s) => Math.max(0, s - 1))}
                        disabled={step === 0}
                        className="h-11 px-space-5 rounded-full bg-surface-container-low hover:bg-surface-container-high text-on-surface font-label-lg text-label-lg font-semibold disabled:opacity-40 transition-colors"
                      >
                        Back
                      </button>
                      {step < 2 ? (
                        <button
                          type="button"
                          onClick={() => {
                            if (step === 0 && !canProceedFromStep1()) {
                              setError("Choose a blood group to continue.");
                              return;
                            }
                            setError("");
                            setStep((s) => Math.min(2, s + 1));
                          }}
                          className="h-11 px-space-6 rounded-full bg-primary-container hover:bg-primary text-on-primary font-label-lg text-label-lg font-bold shadow-level-1 transition-all flex items-center gap-space-2"
                        >
                          <span>Continue</span>
                          <span aria-hidden="true" className="material-symbols-outlined text-[18px]">arrow_forward</span>
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={submit}
                          disabled={create.isPending}
                          className="h-11 px-space-6 rounded-full bg-primary hover:bg-primary-container text-on-primary font-label-lg text-label-lg font-bold shadow-level-1 transition-all flex items-center gap-space-2 disabled:opacity-60"
                        >
                          {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                          <span>Submit request</span>
                        </button>
                      )}
                    </div>
                    {step === 0 && error && <p className="text-error text-body-sm">{error}</p>}
                  </div>

                  {/* Right rail: live summary */}
                  <div className="xl:col-span-4 flex flex-col gap-space-6 xl:sticky xl:top-20">
                    <div className="bg-surface-container-lowest rounded-xl shadow-level-1 p-space-6 flex flex-col gap-space-4">
                      <div className="flex items-center gap-space-2 border-b border-surface-container-high pb-space-3">
                        <span aria-hidden="true" className="material-symbols-outlined text-primary text-[20px]">assignment</span>
                        <span className="font-title-md text-title-md text-on-surface font-semibold">
                          Request summary
                        </span>
                      </div>
                      <div className="flex flex-col gap-space-3">
                        <div className="flex items-center justify-between">
                          <span className="font-body-md text-body-md text-on-surface-variant">Blood group:</span>
                          <span className="font-title-md text-title-md text-primary font-bold">
                            {bloodType ? BLOOD_LABEL[bloodType] : "—"}
                          </span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="font-body-md text-body-md text-on-surface-variant">Units:</span>
                          <span className="font-label-lg text-label-lg text-on-surface font-semibold">{units}</span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="font-body-md text-body-md text-on-surface-variant">Urgency:</span>
                          <span className="font-label-md text-label-md text-on-surface font-semibold">
                            {urgency[0] + urgency.slice(1).toLowerCase()}
                          </span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="font-body-md text-body-md text-on-surface-variant">Hospital:</span>
                          <span className="font-body-md text-body-md text-on-surface font-semibold truncate max-w-[55%] text-right">
                            {hospital || "Not specified"}
                          </span>
                        </div>
                      </div>
                      <div className="p-space-4 rounded-xl bg-surface-container-low flex flex-col gap-space-2">
                        <div className="flex items-center gap-space-2 text-on-surface font-semibold font-label-md text-label-md">
                          <span aria-hidden="true" className="material-symbols-outlined text-secondary text-[18px]">shield</span>
                          <span>Privacy-safe by default</span>
                        </div>
                        <p className="font-body-sm text-body-sm text-on-surface-variant leading-relaxed">
                          Your request only shows a compatibility summary to donors — never
                          your name or contact details until a donor accepts and you can
                          coordinate through your account.
                        </p>
                      </div>
                    </div>
                  </div>
                </div>
              </>
            )}
          </div>
        </main>
      </div>

      <RequestTrackDrawer requestId={trackId} onClose={() => { setTrackId(null); navigate("/patient"); }} />
    </>
  );
}
