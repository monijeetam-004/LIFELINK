/**
 * Full detail/management view for one incoming blood request.
 *
 * Adapted from the Stitch export
 * `lifelink_3.11_hospital_request_management_full_detail_flow/code.html`.
 * Request fields, the event timeline and the handling-status actions are
 * all real (`GET /blood-requests/{id}`, `POST /hospitals/me/requests/{id}/respond`,
 * `GET /hospitals/me/requests`). Per `backend/app/api/routes/blood_requests.py`,
 * `BloodRequestDetail.responses` (donor identity) is only populated for the
 * request's own patient/family/requester — a HOSPITAL caller always gets an
 * empty list there, so this screen never renders it. Instead, donor
 * visibility for hospitals comes exclusively from the anonymized
 * `GET /blood-requests/{id}/match-candidates` endpoint (`donor_name` is
 * always `null` for a hospital caller — see `matching.py`), rendered with
 * the same `<DonorCard>` used on the Donor Directory screen.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { Loader2 } from "lucide-react";

import HospitalSubNav from "./HospitalSubNav";
import { DonorCard } from "./DonorDirectoryAndLogistics";
import { toApiError } from "../../lib/api";
import {
  allocateToRequest,
  BLOOD_LABEL,
  fulfillFromHospital,
  getBloodRequest,
  getInventory,
  getMatchCandidates,
  hospitalRespond,
  listHospitalRequests,
  type BloodRequestStatus,
  type HospitalResponseStatus,
  OPEN_REQUEST_STATUSES,
} from "../../lib/domain";
import FulfillmentProgress, { remainingUnits } from "../../components/FulfillmentProgress";
import DonorShortlistPanel from "../../components/DonorShortlistPanel";
import { useAuth } from "../../lib/auth";

const STATUS_STYLE: Record<BloodRequestStatus, string> = {
  PENDING: "bg-surface-container-high text-on-surface",
  MATCHING: "bg-warning/15 text-warning",
  PARTIALLY_FULFILLED: "bg-secondary/15 text-secondary",
  FULFILLED: "bg-success/15 text-success",
  CANCELLED: "bg-error/10 text-error",
  EXPIRED: "bg-error/10 text-error",
};

const HOSPITAL_STATUS_STYLE: Record<HospitalResponseStatus, string> = {
  ACKNOWLEDGED: "bg-secondary-fixed text-on-secondary-fixed",
  PREPARING: "bg-warning/15 text-warning",
  DISPATCHED: "bg-success/15 text-success",
  DECLINED: "bg-error/10 text-error",
  ALLOCATED: "bg-secondary text-on-secondary",
  FULFILLED: "bg-success text-on-primary",
};

const EVENT_LABEL: Record<string, string> = {
  created: "Request created",
  donor_accepted: "A donor accepted",
  donor_declined: "A donor declined",
  donor_withdrew: "A donor withdrew",
  reopened: "Reopened for matching",
  cancelled: "Request cancelled",
  fulfilled: "Request fulfilled",
  hospital_acknowledged: "Hospital acknowledged",
  hospital_preparing: "Hospital preparing units",
  hospital_dispatched: "Hospital dispatched units",
  hospital_declined: "Hospital declined",
  hospital_allocated: "Hospital allocated units from inventory",
  hospital_fulfilled: "Hospital fulfilled from inventory",
};

const HOSPITAL_ACTIONS: { status: HospitalResponseStatus; icon: string }[] = [
  { status: "ACKNOWLEDGED", icon: "task_alt" },
  { status: "PREPARING", icon: "science" },
  { status: "DISPATCHED", icon: "local_shipping" },
  { status: "DECLINED", icon: "block" },
];

const OPEN_STATUSES = OPEN_REQUEST_STATUSES;

export default function RequestManagementDetail() {
  const { requestId } = useParams<{ requestId: string }>();
  const { user } = useAuth();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [allocateUnits, setAllocateUnits] = useState(1);
  const [allocateError, setAllocateError] = useState("");

  const req = useQuery({
    queryKey: ["blood-requests", requestId],
    queryFn: () => getBloodRequest(requestId!),
    enabled: !!requestId,
  });
  const candidates = useQuery({
    queryKey: ["blood-requests", requestId, "match"],
    queryFn: () => getMatchCandidates(requestId!),
    enabled: !!requestId,
  });
  const hospitalList = useQuery({
    queryKey: ["hospital", "requests", "all"],
    queryFn: () => listHospitalRequests(true),
    enabled: !!requestId,
  });
  const inventory = useQuery({ queryKey: ["hospital", "inventory"], queryFn: getInventory });

  const respond = useMutation({
    mutationFn: (s: HospitalResponseStatus) => hospitalRespond(requestId!, s, note.trim() || undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["blood-requests", requestId] });
      qc.invalidateQueries({ queryKey: ["hospital"] });
      setNote("");
    },
  });

  const allocate = useMutation({
    mutationFn: () =>
      allocateToRequest(requestId!, {
        blood_type: req.data!.blood_type, units: allocateUnits, note: note.trim() || undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["blood-requests", requestId] });
      qc.invalidateQueries({ queryKey: ["hospital"] });
      setNote("");
      setAllocateError("");
    },
    onError: (e) => setAllocateError(toApiError(e).detail),
  });

  const fulfill = useMutation({
    mutationFn: () => fulfillFromHospital(requestId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["blood-requests", requestId] });
      qc.invalidateQueries({ queryKey: ["hospital"] });
      // Phase 21 fix: a prior failed `allocate` attempt could have left a
      // message here; without clearing it a *successful* fulfil would keep
      // showing a stale error underneath the now-updated status.
      setAllocateError("");
    },
    onError: (e) => setAllocateError(toApiError(e).detail),
  });

  const availableForRequest = req.data
    ? inventory.data?.find((i) => i.blood_type === req.data!.blood_type)?.available_units ?? 0
    : 0;
  const requestIsOpen = req.data ? OPEN_STATUSES.includes(req.data.status) : false;

  const mine = useMemo(
    () => hospitalList.data?.find((r) => r.id === requestId),
    [hospitalList.data, requestId],
  );

  if (!requestId) {
    return (
      <div className="min-h-screen bg-surface">
        <HospitalSubNav title="Request not found" />
        <main className="px-4 sm:px-6 py-10 text-center text-on-surface-variant">
          No request id was given.{" "}
          <Link to="/hospital" className="text-secondary hover:underline">
            Back to Hospital Operations
          </Link>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-surface">
      <HospitalSubNav
        title={req.data ? `${BLOOD_LABEL[req.data.blood_type]} · ${req.data.units_needed} unit(s)` : "Request detail"}
        subtitle={`#${requestId.slice(0, 8).toUpperCase()}`}
      />

      <main className="px-4 sm:px-6 lg:px-10 py-6 max-w-container-max mx-auto flex flex-col gap-6">
        {req.isLoading && (
          <div className="flex items-center gap-2 text-on-surface-variant py-10 justify-center">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading request…
          </div>
        )}
        {req.isError && (
          <div className="rounded-xl border border-border-subtle bg-surface-container-lowest p-8 text-center">
            <p className="text-error text-body-sm mb-2">{toApiError(req.error).detail}</p>
            <Link to="/hospital" className="text-secondary hover:underline text-body-sm">
              Back to Hospital Operations
            </Link>
          </div>
        )}

        {/* Phase 21 fix: `inventory`/`hospitalList` used to be fetched but
            their `isError` was never read -- a failure silently made
            "0 unit(s) available" and "distance: Not available" look like
            real, current data instead of a stale/failed fetch. */}
        {req.data && (inventory.isError || hospitalList.isError) && (
          <div className="rounded-lg border border-error/30 bg-error/5 p-3 text-body-sm text-error" role="alert">
            {inventory.isError && (
              <p>Couldn't load your current inventory — the available-units figure below may be out of date. {toApiError(inventory.error).detail}</p>
            )}
            {hospitalList.isError && (
              <p>Couldn't load your handling status for this request. {toApiError(hospitalList.error).detail}</p>
            )}
          </div>
        )}

        {req.data && (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
            <section className="lg:col-span-7 flex flex-col gap-6">
              {/* Summary */}
              <div className="bg-surface-container-lowest rounded-xl p-5 shadow-sm border border-border-subtle">
                <div className="flex items-start justify-between gap-3 flex-wrap">
                  <div>
                    <span className="bg-primary/10 text-primary px-3 py-1 rounded font-bold text-sm">
                      {BLOOD_LABEL[req.data.blood_type]}
                    </span>
                    <span className="ml-2 text-on-surface-variant text-sm">
                      {req.data.units_needed} unit(s) · {req.data.urgency.toLowerCase()}
                    </span>
                  </div>
                  <span className={`px-3 py-1 rounded-full text-xs font-bold ${STATUS_STYLE[req.data.status]}`}>
                    {req.data.status}
                  </span>
                </div>
                <div className="mt-4">
                  <FulfillmentProgress fulfilled={req.data.fulfilled_units} needed={req.data.units_needed} />
                </div>
                <dl className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-3 text-body-sm">
                  <div>
                    <dt className="text-on-surface-variant font-label-sm text-label-sm uppercase tracking-wide">
                      Hospital / city
                    </dt>
                    <dd className="text-on-surface mt-0.5">
                      {[req.data.hospital_name, req.data.city].filter(Boolean).join(" · ") || "Not given"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-on-surface-variant font-label-sm text-label-sm uppercase tracking-wide">
                      Needed by
                    </dt>
                    <dd className="text-on-surface mt-0.5">
                      {req.data.needed_by ? new Date(req.data.needed_by).toLocaleString() : "Not specified"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-on-surface-variant font-label-sm text-label-sm uppercase tracking-wide">
                      Donors accepted
                    </dt>
                    <dd className="text-on-surface mt-0.5">{req.data.accepted_count}</dd>
                  </div>
                  <div>
                    <dt className="text-on-surface-variant font-label-sm text-label-sm uppercase tracking-wide">
                      Distance from your hospital
                    </dt>
                    <dd className="text-on-surface mt-0.5">
                      {mine?.distance_km != null ? `${mine.distance_km} km` : "Not available"}
                    </dd>
                  </div>
                  {req.data.note && (
                    <div className="sm:col-span-2">
                      <dt className="text-on-surface-variant font-label-sm text-label-sm uppercase tracking-wide">
                        Note from requester
                      </dt>
                      <dd className="text-on-surface mt-0.5">{req.data.note}</dd>
                    </div>
                  )}
                </dl>
              </div>

              {/* Handling status */}
              <div className="bg-surface-container-lowest rounded-xl p-5 shadow-sm border border-border-subtle">
                <div className="flex items-center justify-between mb-3">
                  <h2 className="font-title-lg text-title-lg text-on-surface">Your handling status</h2>
                  {mine?.hospital_status && (
                    <span className={`px-3 py-1 rounded-full text-xs font-bold ${HOSPITAL_STATUS_STYLE[mine.hospital_status]}`}>
                      {mine.hospital_status}
                    </span>
                  )}
                </div>
                <label className="block mb-3">
                  <span className="block text-label-sm font-label-sm uppercase tracking-wider text-on-surface-variant mb-1.5">
                    Note to the requester (optional)
                  </span>
                  <textarea
                    className="w-full px-3.5 py-2.5 rounded-lg border border-outline-variant bg-surface text-on-surface text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/40 min-h-[72px]"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="e.g. Two units cross-matched, courier dispatched at 14:20"
                  />
                </label>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  {HOSPITAL_ACTIONS.map((a) => (
                    <button
                      key={a.status}
                      type="button"
                      disabled={respond.isPending}
                      onClick={() => respond.mutate(a.status)}
                      className="py-2.5 rounded-lg text-label-sm font-label-md border border-border-subtle text-on-surface-variant hover:bg-surface-container-low disabled:opacity-50 flex flex-col items-center gap-1"
                    >
                      {respond.isPending ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <span aria-hidden="true" className="material-symbols-outlined text-[18px]">{a.icon}</span>
                      )}
                      {a.status[0] + a.status.slice(1).toLowerCase()}
                    </button>
                  ))}
                </div>
                {respond.isError && (
                  <p className="text-error text-body-sm mt-2" role="alert">
                    {toApiError(respond.error).detail}
                  </p>
                )}
              </div>

              {/* Allocate from inventory / Fulfil — real inventory deduction
                  and a real BloodRequestStatus transition, not a label. */}
              {req.data && (
                <div className="bg-surface-container-lowest rounded-xl p-5 shadow-sm border border-border-subtle">
                  <h2 className="font-title-lg text-title-lg text-on-surface mb-1">
                    Allocate from your inventory
                  </h2>
                  <p className="font-body-sm text-body-sm text-on-surface-variant mb-3">
                    {availableForRequest} unit(s) of {BLOOD_LABEL[req.data.blood_type]} available to
                    promise right now
                    {requestIsOpen && (
                      <> · this request still needs {remainingUnits(req.data.fulfilled_units, req.data.units_needed)}</>
                    )}
                    .
                  </p>
                  {!requestIsOpen ? (
                    <p className="text-body-sm text-on-surface-variant">
                      This request is {req.data.status.toLowerCase()} — no further allocation is possible.
                    </p>
                  ) : (
                    <>
                      <div className="flex items-center gap-2 mb-3">
                        <input
                          type="number"
                          min={1}
                          max={Math.max(1, remainingUnits(req.data.fulfilled_units, req.data.units_needed))}
                          aria-label="Units to allocate"
                          className="w-24 px-3 py-2 rounded-lg border border-outline-variant bg-surface text-on-surface text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
                          value={allocateUnits}
                          onChange={(e) =>
                            setAllocateUnits(Math.max(1, Math.round(Number(e.target.value)) || 1))
                          }
                        />
                        <button
                          type="button"
                          disabled={allocate.isPending}
                          onClick={() => allocate.mutate()}
                          className="h-10 px-4 rounded-lg bg-secondary hover:bg-secondary-container text-on-secondary font-label-md text-label-md flex items-center gap-2 disabled:opacity-50"
                        >
                          {allocate.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                          Allocate units
                        </button>
                      </div>
                      <button
                        type="button"
                        disabled={fulfill.isPending}
                        onClick={() => fulfill.mutate()}
                        className="h-10 px-4 rounded-lg bg-success hover:opacity-90 text-on-primary font-label-md text-label-md flex items-center gap-2 disabled:opacity-50"
                      >
                        {fulfill.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                        {req.data.fulfilled_units > 0
                          ? `Mark remaining ${remainingUnits(req.data.fulfilled_units, req.data.units_needed)} unit(s) fulfilled`
                          : "Mark fulfilled from inventory"}
                      </button>
                      {allocateError && (
                        <p role="alert" className="text-error text-body-sm mt-2">{allocateError}</p>
                      )}
                    </>
                  )}
                </div>
              )}

              {/* Timeline */}
              <div className="bg-surface-container-lowest rounded-xl p-5 shadow-sm border border-border-subtle">
                <h2 className="font-title-lg text-title-lg text-on-surface mb-3">Timeline</h2>
                {req.data.events.length === 0 ? (
                  <p className="text-body-sm text-on-surface-variant">No events yet.</p>
                ) : (
                  <ol className="relative border-l border-border-subtle ml-1 space-y-3">
                    {req.data.events.map((e) => (
                      <li key={e.id} className="ml-4">
                        <span className="absolute -left-1.5 h-3 w-3 rounded-full bg-primary" />
                        <p className="text-sm text-on-surface">{EVENT_LABEL[e.event_type] ?? e.event_type}</p>
                        {e.note && <p className="text-xs text-on-surface-variant">{e.note}</p>}
                        <p className="text-[11px] text-on-surface-variant">
                          {new Date(e.created_at).toLocaleString()}
                        </p>
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            </section>

            {/* Anonymized compatible donors */}
            <section className="lg:col-span-5 flex flex-col gap-4">
              <div className="bg-surface-container-lowest rounded-xl p-4 shadow-sm border border-border-subtle">
                <DonorShortlistPanel
                  kind="BLOOD_REQUEST"
                  subjectId={requestId}
                  canManage={!!user && req.data.requester_user_id === user.id}
                />
              </div>
              <div className="bg-surface-container-lowest rounded-xl p-4 shadow-sm border border-border-subtle">
                <h2 className="font-title-lg text-title-lg text-on-surface">Compatible donors</h2>
                <p className="font-body-sm text-body-sm text-on-surface-variant mt-1">
                  Ranking view only — these donors have not been contacted or confirmed.
                </p>
                <p className="font-body-sm text-body-sm text-on-surface-variant mt-1">
                  Anonymized — donor names, phone numbers and photos are never sent to hospitals.
                </p>
                {candidates.data && (
                  <p className="font-label-sm text-label-sm text-on-surface-variant mt-2">
                    {candidates.data.candidates.length} donor(s) · types{" "}
                    {candidates.data.compatible_donor_types.map((b) => BLOOD_LABEL[b]).join(", ")}
                  </p>
                )}
              </div>

              {candidates.isLoading && (
                <div className="flex items-center gap-2 text-on-surface-variant py-6 justify-center">
                  <Loader2 className="h-4 w-4 animate-spin" /> Scanning…
                </div>
              )}
              {candidates.isError && (
                <p className="text-error text-body-sm" role="alert">
                  {toApiError(candidates.error).detail}
                </p>
              )}
              {candidates.data && candidates.data.candidates.length === 0 && (
                <div className="rounded-xl border border-border-subtle bg-surface-container-lowest p-6 text-center text-on-surface-variant text-body-sm">
                  No available compatible donors right now.
                </div>
              )}
              <ul className="flex flex-col gap-3">
                {(candidates.data?.candidates ?? []).map((c) => (
                  <DonorCard key={c.donor_ref} c={c} />
                ))}
              </ul>
            </section>
          </div>
        )}
      </main>
    </div>
  );
}
