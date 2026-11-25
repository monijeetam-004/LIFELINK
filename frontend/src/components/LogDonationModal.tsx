import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import Modal, { fieldClass, ghostButtonClass, labelClass, primaryButtonClass } from "./Modal";
import { toApiError } from "../lib/api";
import {
  BLOOD_LABEL,
  type CreateDonationBody,
  getHospitalDirectory,
  listDonorResponses,
  logDonation,
} from "../lib/domain";
import { newIdempotencyKey } from "../lib/idempotency";

/**
 * Phase 26: `POST /donors/me/donations` existed on the backend since Phase
 * 5/6 but had no frontend entry point at all -- donors could only ever
 * *view* their donation history (MilestonesHistory.tsx's listDonations()),
 * never actually log one through the UI. This closes that real, evidenced
 * gap. Mirrors BloodRequestModal.tsx's structure and its
 * idempotency-key-per-attempt contract exactly (see lib/idempotency.ts and
 * app/api/routes/donors.py::log_donation's Phase 26 idempotency handling).
 *
 * UI double-submit protection here (disabled-while-pending button, an
 * early-return guard against a stray double form-submit event) is
 * supplementary only -- the backend's (donor_user_id, idempotency_key)
 * unique constraint is what's actually authoritative under a real race
 * (two tabs, a flaky network causing a genuine duplicate click before
 * React re-renders the disabled state). This UI guard alone would never
 * cover that case.
 */
export default function LogDonationModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const qc = useQueryClient();

  // Requests this donor has actually accepted -- optionally linked to the
  // donation being logged. Fetched only while the modal is open.
  const responses = useQuery({
    queryKey: ["donor", "responses"],
    queryFn: listDonorResponses,
    enabled: open,
  });
  const acceptedMatches = (responses.data ?? []).filter(
    (m) => m.my_response.status === "ACCEPTED",
  );

  const [donatedOn, setDonatedOn] = useState(() => new Date().toISOString().slice(0, 10));
  const [units, setUnits] = useState(1);
  const [hospitalName, setHospitalName] = useState("");
  // Phase E: an optional link to a real facility -- only that facility (or
  // an admin) can later verify the donation; a free-text name alone can
  // only ever be verified by an admin.
  const [facilityId, setFacilityId] = useState("");
  const directory = useQuery({
    queryKey: ["hospital", "directory"],
    queryFn: getHospitalDirectory,
    enabled: open,
  });
  const [note, setNote] = useState("");
  const [requestId, setRequestId] = useState("");
  const [error, setError] = useState("");
  // Same per-attempt contract as BloodRequestModal/SosModal: minted once per
  // genuinely new attempt, reused across retries of that same attempt so a
  // dropped-response retry replays instead of duplicating, and re-minted
  // only after a real 409 (the form content changed since this attempt's
  // key was issued).
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);

  useEffect(() => {
    if (open) {
      setDonatedOn(new Date().toISOString().slice(0, 10));
      setUnits(1);
      setHospitalName("");
      setFacilityId("");
      setNote("");
      setRequestId("");
      setError("");
      setIdempotencyKey(newIdempotencyKey());
    }
  }, [open]);

  const create = useMutation({
    mutationFn: (body: CreateDonationBody) => logDonation(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["donor", "donations"] });
      qc.invalidateQueries({ queryKey: ["donor", "responses"] });
      qc.invalidateQueries({ queryKey: ["certificates"] });
      onClose();
    },
    onError: (e) => {
      const apiError = toApiError(e);
      setError(apiError.detail);
      // Real idempotency-key conflict (409): the form changed since this
      // attempt's key was minted. Mint a fresh key so the next submit
      // isn't rejected again for the same reason -- every other error
      // (validation, network, auth, server) keeps the same key so a
      // genuine retry still dedupes correctly. Mirrors BloodRequestModal.
      if (apiError.status === 409) {
        setIdempotencyKey(newIdempotencyKey());
      }
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!donatedOn) {
      setError("Enter the donation date.");
      return;
    }
    // Double-click / rapid-resubmit guard -- supplementary to the backend's
    // authoritative idempotency key (see the file docstring above).
    if (create.isPending) return;
    create.mutate({
      donated_on: donatedOn,
      units,
      hospital_name: hospitalName || null,
      ...(facilityId ? { hospital_id: facilityId } : {}),
      note: note || null,
      blood_request_id: requestId || null,
      idempotency_key: idempotencyKey,
    });
  }

  return (
    <Modal open={open} onClose={onClose} title="Log a donation">
      <form onSubmit={submit} className="flex flex-col gap-4">
        {responses.isError && (
          <p role="alert" className="text-body-sm text-error">
            Couldn't load your accepted requests, so linking this donation to one isn't available right
            now — you can still log it as an off-platform donation below.{" "}
            <button type="button" className="underline" onClick={() => responses.refetch()}>
              Retry
            </button>
          </p>
        )}
        {acceptedMatches.length > 0 && (
          <div>
            <label className={labelClass} htmlFor="ld-request">
              Link to a request you accepted (optional)
            </label>
            <select
              id="ld-request"
              className={fieldClass}
              value={requestId}
              onChange={(e) => setRequestId(e.target.value)}
            >
              <option value="">Off-platform / not listed</option>
              {acceptedMatches.map((m) => (
                <option key={m.id} value={m.id}>
                  {BLOOD_LABEL[m.blood_type]} · {m.hospital_name || m.city || "Unspecified location"}
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className={labelClass} htmlFor="ld-date">
              Date
            </label>
            <input
              id="ld-date"
              type="date"
              className={fieldClass}
              value={donatedOn}
              max={new Date().toISOString().slice(0, 10)}
              onChange={(e) => setDonatedOn(e.target.value)}
              required
            />
          </div>
          <div>
            <label className={labelClass} htmlFor="ld-units">
              Units
            </label>
            <input
              id="ld-units"
              type="number"
              min={1}
              max={10}
              className={fieldClass}
              value={units}
              onChange={(e) => setUnits(Math.max(1, Number(e.target.value) || 1))}
            />
          </div>
        </div>
        {(directory.data?.length ?? 0) > 0 && (
          <div>
            <label className={labelClass} htmlFor="ld-facility">
              Facility on LifeLink (optional)
            </label>
            <select
              id="ld-facility"
              className={fieldClass}
              value={facilityId}
              onChange={(e) => setFacilityId(e.target.value)}
            >
              <option value="">Not listed / off-platform</option>
              {directory.data!.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.name ?? "Unnamed facility"}
                  {h.city ? ` · ${h.city}` : ""}
                </option>
              ))}
            </select>
            <p className="text-body-sm text-on-surface-variant mt-1">
              Your entry is recorded as self-reported. Linking the facility lets it verify the
              donation; verified donations can receive a certificate.
            </p>
          </div>
        )}
        <div>
          <label className={labelClass} htmlFor="ld-hospital">
            Hospital (optional)
          </label>
          <input
            id="ld-hospital"
            className={fieldClass}
            value={hospitalName}
            onChange={(e) => setHospitalName(e.target.value)}
          />
        </div>
        <div>
          <label className={labelClass} htmlFor="ld-note">
            Note (optional)
          </label>
          <textarea
            id="ld-note"
            className={fieldClass}
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </div>
        {error && (
          <p role="alert" className="text-error text-body-sm">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-3 pt-2">
          <button type="button" className={ghostButtonClass} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={primaryButtonClass} disabled={create.isPending}>
            {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Log donation
          </button>
        </div>
      </form>
    </Modal>
  );
}
