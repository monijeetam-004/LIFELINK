import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2 } from "lucide-react";

import Modal, { fieldClass, ghostButtonClass, labelClass } from "./Modal";
import { toApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  BLOOD_LABEL,
  BLOOD_TYPES,
  createEmergency,
  getHospitalDirectory,
  type BloodType,
  type CreateEmergencyBody,
} from "../lib/domain";
import { newIdempotencyKey } from "../lib/idempotency";

export default function SosModal({
  open,
  onClose,
  /** Pre-filled when opened "for" a specific linked patient (e.g. from the Family portal). */
  defaultPatientEmail,
}: {
  open: boolean;
  onClose: () => void;
  defaultPatientEmail?: string;
}) {
  const qc = useQueryClient();
  const { user } = useAuth();
  const [bloodType, setBloodType] = useState<BloodType | "">("");
  const [location, setLocation] = useState("");
  const [city, setCity] = useState("");
  const [note, setNote] = useState("");
  const [patientEmail, setPatientEmail] = useState(defaultPatientEmail ?? "");
  const [unitsNeeded, setUnitsNeeded] = useState("");
  const [deadline, setDeadline] = useState("");
  const [targetFacilityId, setTargetFacilityId] = useState("");
  const [error, setError] = useState("");
  const [duplicateNotice, setDuplicateNotice] = useState<string | null>(null);
  // Generated once per SOS attempt, kept stable across retries of the
  // *same* attempt (e.g. a double-click or a network retry both carry it)
  // so the backend recognizes a replay instead of creating a duplicate --
  // reset only when the modal is (re)opened for a fresh attempt.
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);

  const directory = useQuery({
    queryKey: ["hospitals", "directory"],
    queryFn: getHospitalDirectory,
    enabled: open,
  });
  const verifiedFacilities = (directory.data ?? []).filter((h) => h.verified && h.name);

  useEffect(() => {
    if (open) {
      setPatientEmail(defaultPatientEmail ?? "");
      setIdempotencyKey(newIdempotencyKey());
      setDuplicateNotice(null);
    }
  }, [open, defaultPatientEmail]);

  const create = useMutation({
    mutationFn: (body: CreateEmergencyBody) => createEmergency(body),
    onSuccess: (em) => {
      qc.invalidateQueries({ queryKey: ["emergencies"] });
      qc.invalidateQueries({ queryKey: ["notifications"] });
      setError("");
      // Non-blocking: the emergency was created regardless of the flag --
      // see EmergencyOut.possible_duplicate_of. When set, pause on a
      // confirmation screen instead of auto-closing so the notice is
      // actually seen; otherwise close immediately as before.
      if (em.possible_duplicate_of) {
        setDuplicateNotice(
          "Broadcast. Note: you already have another open emergency for this patient -- " +
            "both stay active, so check your emergency list if this was a mistake.",
        );
      } else {
        resetAndClose();
      }
    },
    onError: (e) => setError(toApiError(e).detail),
  });

  function resetAndClose() {
    onClose();
    setBloodType("");
    setLocation("");
    setCity("");
    setNote("");
    setPatientEmail("");
    setUnitsNeeded("");
    setDeadline("");
    setTargetFacilityId("");
    setError("");
    setDuplicateNotice(null);
  }

  if (duplicateNotice) {
    return (
      <Modal open={open} onClose={resetAndClose} title="Emergency SOS">
        <div className="flex flex-col gap-4">
          <div className="flex items-start gap-3 rounded-lg bg-secondary/10 text-secondary p-3">
            <AlertTriangle className="h-5 w-5 flex-shrink-0 mt-0.5" />
            <p className="text-sm">{duplicateNotice}</p>
          </div>
          <div className="flex justify-end pt-1">
            <button className={ghostButtonClass} onClick={resetAndClose}>
              Close
            </button>
          </div>
        </div>
      </Modal>
    );
  }

  return (
    <Modal open={open} onClose={resetAndClose} title="Emergency SOS">
      <div className="flex flex-col gap-4">
        <div className="flex items-start gap-3 rounded-lg bg-error/10 text-error p-3">
          <AlertTriangle className="h-5 w-5 flex-shrink-0 mt-0.5" />
          <p className="text-sm">
            This broadcasts an active emergency to available compatible donors right now. Use it
            only for genuine emergencies.
          </p>
        </div>
        {user?.role === "FAMILY" && (
          <div>
            <label className={labelClass} htmlFor="sos-patient-email">
              Patient email
            </label>
            <input
              id="sos-patient-email"
              type="email"
              className={fieldClass}
              value={patientEmail}
              onChange={(e) => setPatientEmail(e.target.value)}
              placeholder="patient@example.com"
              required
            />
            <p className="text-xs text-on-surface-variant mt-1">
              Must be a patient you have an accepted family link with.
            </p>
          </div>
        )}
        <div>
          <label className={labelClass} htmlFor="sos-blood-group">
            Blood group needed (optional)
          </label>
          <select
            id="sos-blood-group"
            className={fieldClass}
            value={bloodType}
            onChange={(e) => setBloodType(e.target.value as BloodType)}
          >
            <option value="">Unknown / any</option>
            {BLOOD_TYPES.map((b) => (
              <option key={b} value={b}>
                {BLOOD_LABEL[b]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className={labelClass} htmlFor="sos-location">
            Location
          </label>
          <input
            id="sos-location"
            className={fieldClass}
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            placeholder="Hospital / area"
          />
        </div>
        <div>
          <label className={labelClass} htmlFor="sos-city">
            City
          </label>
          <input
            id="sos-city"
            className={fieldClass}
            value={city}
            onChange={(e) => setCity(e.target.value)}
            placeholder="e.g. Mumbai"
            aria-describedby="sos-scope-hint"
          />
          <p id="sos-scope-hint" className="text-xs text-on-surface-variant mt-1">
            Donors are alerted only within the distance limit of a known location, or in the same
            city. Without a city, a recognised location or a target facility, no donor can be
            alerted.
          </p>
        </div>
        <div>
          <label className={labelClass} htmlFor="sos-note">
            What's happening? (optional)
          </label>
          <textarea
            id="sos-note"
            className={fieldClass}
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={labelClass} htmlFor="sos-units">
              Units needed (optional)
            </label>
            <input
              id="sos-units"
              type="number"
              min={1}
              max={50}
              className={fieldClass}
              value={unitsNeeded}
              onChange={(e) => setUnitsNeeded(e.target.value)}
              placeholder="1"
            />
          </div>
          <div>
            <label className={labelClass} htmlFor="sos-deadline">
              Deadline (optional)
            </label>
            <input
              id="sos-deadline"
              type="datetime-local"
              className={fieldClass}
              value={deadline}
              onChange={(e) => setDeadline(e.target.value)}
            />
          </div>
        </div>
        <div>
          <label className={labelClass} htmlFor="sos-target-facility">
            Target facility (optional)
          </label>
          <select
            id="sos-target-facility"
            className={fieldClass}
            value={targetFacilityId}
            onChange={(e) => setTargetFacilityId(e.target.value)}
          >
            <option value="">No specific facility -- broadcast only</option>
            {verifiedFacilities.map((h) => (
              <option key={h.id} value={h.id}>
                {h.name} {h.city ? `· ${h.city}` : ""}
              </option>
            ))}
          </select>
          <p className="text-xs text-on-surface-variant mt-1">
            Optional -- only verified facilities can be named. Naming one also gives the SOS a
            location, so nearby compatible donors can be alerted.
          </p>
        </div>
        {error && <p className="text-error text-body-sm">{error}</p>}
        <div className="flex justify-end gap-3 pt-1">
          <button className={ghostButtonClass} onClick={resetAndClose}>
            Cancel
          </button>
          <button
            className="bg-error hover:opacity-90 text-on-error font-label-md text-label-md px-5 py-2.5 rounded-lg transition-all active:scale-[0.99] disabled:opacity-60 flex items-center gap-2"
            disabled={create.isPending || (user?.role === "FAMILY" && !patientEmail)}
            onClick={() =>
              create.mutate({
                blood_type: bloodType || null,
                location_text: location || null,
                city: city || null,
                note: note || null,
                patient_email: user?.role === "FAMILY" ? patientEmail : null,
                units_needed: unitsNeeded ? Number(unitsNeeded) : null,
                deadline: deadline ? new Date(deadline).toISOString() : null,
                target_facility_id: targetFacilityId || null,
                idempotency_key: idempotencyKey,
              })
            }
          >
            {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Broadcast emergency
          </button>
        </div>
      </div>
    </Modal>
  );
}
