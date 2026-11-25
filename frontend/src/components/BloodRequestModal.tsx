import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import Modal, { fieldClass, ghostButtonClass, labelClass, primaryButtonClass } from "./Modal";
import { toApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  BLOOD_LABEL,
  BLOOD_TYPES,
  createBloodRequest,
  getPatientProfile,
  type BloodType,
  type CreateBloodRequestBody,
  type Urgency,
} from "../lib/domain";
import { newIdempotencyKey } from "../lib/idempotency";

const URGENCIES: Urgency[] = ["ROUTINE", "URGENT", "CRITICAL"];

export default function BloodRequestModal({
  open,
  onClose,
  defaultPatientEmail,
}: {
  open: boolean;
  onClose: () => void;
  /** Pre-filled when opened "for" a specific linked patient (e.g. from the Family portal). */
  defaultPatientEmail?: string;
}) {
  const qc = useQueryClient();
  const { user } = useAuth();

  const profile = useQuery({
    queryKey: ["patient", "me"],
    queryFn: getPatientProfile,
    enabled: open && user?.role === "PATIENT",
  });

  const [bloodType, setBloodType] = useState<BloodType | "">("");
  const [units, setUnits] = useState(1);
  const [urgency, setUrgency] = useState<Urgency>("ROUTINE");
  const [hospital, setHospital] = useState("");
  const [city, setCity] = useState("");
  const [note, setNote] = useState("");
  const [neededBy, setNeededBy] = useState("");
  const [patientEmail, setPatientEmail] = useState("");
  const [error, setError] = useState("");
  // Phase 19: generated once per submission attempt and kept stable across
  // retries of that *same* attempt (a double-click, a network retry, the
  // user pressing "Submit" again after a timeout with the form unchanged)
  // so the backend recognizes a replay instead of creating a duplicate
  // request -- mirrors the proven SosModal pattern. Only reset when the
  // modal is (re)opened for a genuinely new attempt, or after a real 409
  // idempotency-conflict from the backend (which only happens if the form
  // content changed since the key was minted -- see the mutation's
  // onError below).
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);

  // Default the blood group from the patient's own profile once loaded.
  useEffect(() => {
    if (open && bloodType === "" && profile.data?.blood_type) {
      setBloodType(profile.data.blood_type);
    }
  }, [open, bloodType, profile.data?.blood_type]);

  useEffect(() => {
    if (open) {
      setPatientEmail(defaultPatientEmail ?? "");
      setIdempotencyKey(newIdempotencyKey());
    }
  }, [open, defaultPatientEmail]);

  const create = useMutation({
    mutationFn: (body: CreateBloodRequestBody) => createBloodRequest(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["blood-requests"] });
      onClose();
      reset();
    },
    onError: (e) => {
      const apiError = toApiError(e);
      setError(apiError.detail);
      // A real idempotency-key conflict (409) only happens if the form's
      // content changed since this attempt's key was minted -- e.g. the
      // user edited a field, the first submit failed for an unrelated
      // reason, and they resubmitted with the same key but different
      // values. Mint a fresh key so the next submit isn't rejected again
      // for the same reason; every other error (validation, network,
      // auth, server) keeps the same key so a genuine retry still
      // dedupes correctly.
      if (apiError.status === 409) {
        setIdempotencyKey(newIdempotencyKey());
      }
    },
  });

  function reset() {
    setBloodType("");
    setUnits(1);
    setUrgency("ROUTINE");
    setHospital("");
    setCity("");
    setNote("");
    setNeededBy("");
    setPatientEmail("");
    setError("");
    setIdempotencyKey(newIdempotencyKey());
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!bloodType) {
      setError("Choose a blood group.");
      return;
    }
    // Double-click / rapid-resubmit guard: the mutation is already
    // in flight, so this submit is a duplicate UI event, not a new
    // attempt -- the disabled submit button already covers the common
    // case, but a form's native Enter-key submit can still race it.
    if (create.isPending) return;
    create.mutate({
      blood_type: bloodType,
      units_needed: units,
      urgency,
      hospital_name: hospital || null,
      city: city || null,
      note: note || null,
      needed_by: neededBy || null,
      patient_email: user?.role === "FAMILY" && patientEmail ? patientEmail : null,
      idempotency_key: idempotencyKey,
    });
  }

  return (
    <Modal open={open} onClose={onClose} title="New blood request">
      <form onSubmit={submit} className="flex flex-col gap-4">
        {user?.role === "FAMILY" && (
          <div>
            <label className={labelClass} htmlFor="br-patient-email">
              Patient email (leave blank for yourself)
            </label>
            <input
              id="br-patient-email"
              type="email"
              className={fieldClass}
              value={patientEmail}
              onChange={(e) => setPatientEmail(e.target.value)}
              placeholder="patient@example.com"
            />
          </div>
        )}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={labelClass} htmlFor="br-blood-group">
              Blood group
            </label>
            <select
              id="br-blood-group"
              className={fieldClass}
              value={bloodType}
              onChange={(e) => setBloodType(e.target.value as BloodType)}
              required
            >
              <option value="">Select…</option>
              {BLOOD_TYPES.map((b) => (
                <option key={b} value={b}>
                  {BLOOD_LABEL[b]}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className={labelClass} htmlFor="br-units">
              Units needed
            </label>
            <input
              id="br-units"
              type="number"
              min={1}
              max={20}
              className={fieldClass}
              value={units}
              onChange={(e) => setUnits(Math.max(1, Number(e.target.value) || 1))}
            />
          </div>
        </div>
        <div>
          <span className={labelClass}>Urgency</span>
          <div className="grid grid-cols-3 gap-2">
            {URGENCIES.map((u) => (
              <button
                type="button"
                key={u}
                onClick={() => setUrgency(u)}
                className={`py-2 rounded-lg text-label-sm font-label-md border transition-colors ${
                  urgency === u
                    ? "border-primary bg-primary/5 text-primary"
                    : "border-border-subtle text-on-surface-variant hover:bg-surface-container-low"
                }`}
              >
                {u[0] + u.slice(1).toLowerCase()}
              </button>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={labelClass} htmlFor="br-hospital">
              Hospital (optional)
            </label>
            <input
              id="br-hospital"
              className={fieldClass}
              value={hospital}
              onChange={(e) => setHospital(e.target.value)}
            />
          </div>
          <div>
            <label className={labelClass} htmlFor="br-city">
              City (optional)
            </label>
            <input
              id="br-city"
              className={fieldClass}
              value={city}
              onChange={(e) => setCity(e.target.value)}
              aria-describedby="br-city-hint"
            />
            <p id="br-city-hint" className="text-xs text-on-surface-variant mt-1">
              Donors are alerted only in this city or near a hospital&apos;s location. Without
              one, no donor is alerted.
            </p>
          </div>
        </div>
        <div>
          <label className={labelClass} htmlFor="br-needed-by">
            Needed by (optional)
          </label>
          <input
            id="br-needed-by"
            type="date"
            className={fieldClass}
            value={neededBy}
            onChange={(e) => setNeededBy(e.target.value)}
          />
        </div>
        <div>
          <label className={labelClass} htmlFor="br-note">
            Note (optional)
          </label>
          <textarea
            id="br-note"
            className={fieldClass}
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </div>
        {error && <p className="text-error text-body-sm">{error}</p>}
        <div className="flex justify-end gap-3 pt-2">
          <button type="button" className={ghostButtonClass} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={primaryButtonClass} disabled={create.isPending}>
            {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Submit request
          </button>
        </div>
      </form>
    </Modal>
  );
}
