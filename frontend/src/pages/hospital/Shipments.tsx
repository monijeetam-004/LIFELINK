/**
 * Phase D -- shipment / transport / chain-of-custody lifecycle, from the
 * hospital side: create a shipment to another facility, carry it through
 * its real backend-enforced lifecycle (dispatch -> in-transit -> arrive ->
 * receive, or cancel/fail), and see its full timeline.
 *
 * Every state shown here is real backend truth from `GET /shipments` /
 * `GET /shipments/{id}` (app/api/routes/shipments.py) -- `allowed_transitions`
 * decides which action buttons render, but the backend re-validates every
 * transition independently regardless of what this screen shows (Section 4
 * -- never trust frontend button visibility as the real guard). Route
 * condition (OPERATIONAL/DISRUPTED) is shown as a separate, explicitly
 * distinct field from the shipment's own `status` (Section 16) -- a
 * disrupted route never implies a cancelled or failed shipment.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Loader2 } from "lucide-react";

import HospitalSubNav from "./HospitalSubNav";
import { toApiError } from "../../lib/api";
import { addTelemetry, getTelemetry, receiveShipmentWithOutcomes } from "../../lib/gapApi";
import {
  BLOOD_COMPONENTS,
  BLOOD_LABEL,
  BLOOD_TYPES,
  COMPONENT_LABEL,
  cancelShipment,
  componentLabel,
  createShipment,
  dispatchShipment,
  failShipment,
  getHospitalDirectory,
  getHospitalProfile,
  getShipment,
  getShipmentTimeline,
  listShipments,
  markShipmentArrived,
  markShipmentInTransit,
  receiveShipment,
  reconcileShipment,
  reportShipmentDelay,
  type BloodComponent,
  type BloodType,
  type Shipment,
  type ShipmentReconcileInput,
  type ShipmentReconciliationStatus,
  type ShipmentStatus,
} from "../../lib/domain";

type Direction = "outgoing" | "incoming" | "all";

const STATUS_STYLE: Record<ShipmentStatus, string> = {
  CREATED: "bg-surface-container-high text-on-surface",
  DISPATCHED: "bg-secondary-fixed text-on-secondary-fixed",
  IN_TRANSIT: "bg-warning/15 text-warning",
  ARRIVED: "bg-secondary text-on-secondary",
  RECEIVED: "bg-success/15 text-success",
  CANCELLED: "bg-error/10 text-error",
  FAILED: "bg-error text-on-primary",
};

const EVENT_LABEL: Record<string, string> = {
  created: "Shipment created — units reserved at origin",
  dispatched: "Dispatched — units physically left the origin",
  in_transit: "In transit",
  delayed: "Delay reported",
  arrived: "Arrived at destination",
  received: "Received — destination inventory updated",
  cancelled: "Cancelled",
  failed: "Failed",
  reconciled_returned_to_origin: "Reconciled — units returned to origin stock (cold chain attested)",
  reconciled_redirected: "Reconciled — units recorded as redirected elsewhere",
  reconciled_quarantined: "Reconciled — units quarantined / written off",
};

const RECONCILIATION_LABEL: Record<ShipmentReconciliationStatus, string> = {
  REQUIRED: "Reconciliation required",
  RETURNED_TO_ORIGIN: "Returned to origin",
  REDIRECTED: "Redirected",
  QUARANTINED: "Quarantined",
};

export default function Shipments() {
  const [params, setParams] = useSearchParams();
  const selectedId = params.get("shipment");

  return (
    <div className="min-h-screen bg-surface">
      <HospitalSubNav
        title="Shipments & chain of custody"
        subtitle="Transport blood between facilities with real inventory ownership transfer"
        backTo="/hospital/resource-pathways"
        backLabel="Back to Resource Pathways"
      />
      <main className="px-4 sm:px-6 lg:px-10 py-6 max-w-container-max mx-auto flex flex-col gap-6">
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
          <section className="xl:col-span-5 flex flex-col gap-5">
            <CreateShipmentForm onCreated={(s) => setParams({ shipment: s.id })} />
            <ShipmentList
              selectedId={selectedId}
              onSelect={(id) => setParams({ shipment: id })}
            />
          </section>
          <section className="xl:col-span-7">
            {selectedId ? (
              <ShipmentDetail shipmentId={selectedId} />
            ) : (
              <div className="rounded-xl border border-border-subtle bg-surface-container-lowest p-10 text-center text-on-surface-variant">
                Select a shipment on the left to see its detail, timeline and available actions.
              </div>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}

// --------------------------------------------------------------------------- #
// Create
// --------------------------------------------------------------------------- #

function CreateShipmentForm({ onCreated }: { onCreated: (s: Shipment) => void }) {
  const [open, setOpen] = useState(false);
  const [destinationId, setDestinationId] = useState("");
  const [bloodType, setBloodType] = useState<BloodType>("O_NEG");
  const [component, setComponent] = useState<BloodComponent>("WHOLE_BLOOD");
  const [units, setUnits] = useState(1);
  const [error, setError] = useState("");

  const me = useQuery({ queryKey: ["hospital", "me"], queryFn: getHospitalProfile });
  const directory = useQuery({ queryKey: ["hospital", "directory"], queryFn: getHospitalDirectory });
  const partners = useMemo(
    () => (directory.data ?? []).filter((h) => h.id !== me.data?.id && h.verified),
    [directory.data, me.data],
  );

  const create = useMutation({
    mutationFn: () =>
      createShipment({
        origin_facility_id: me.data!.id,
        destination_facility_id: destinationId,
        items: [{ blood_type: bloodType, units, component }],
      }),
    onSuccess: (s) => {
      setOpen(false);
      setError("");
      setDestinationId("");
      setUnits(1);
      onCreated(s);
    },
    onError: (e) => setError(toApiError(e).detail),
  });

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="w-full h-11 rounded-lg bg-primary hover:opacity-90 text-on-primary font-label-md text-label-md flex items-center justify-center gap-2"
      >
        <span aria-hidden="true" className="material-symbols-outlined text-[18px]">add_box</span>
        New shipment
      </button>
    );
  }

  return (
    <div className="bg-surface-container-lowest rounded-xl p-5 shadow-sm border border-border-subtle flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="font-title-lg text-title-lg text-on-surface">New shipment from your facility</h2>
        <button type="button" onClick={() => setOpen(false)} aria-label="Close" className="text-on-surface-variant">
          <span aria-hidden="true" className="material-symbols-outlined">close</span>
        </button>
      </div>

      <label className="block">
        <span className="block text-label-sm font-label-sm uppercase tracking-wider text-on-surface-variant mb-1.5">
          Destination facility
        </span>
        <select
          className="w-full px-3 py-2 rounded-lg border border-outline-variant bg-surface text-on-surface text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
          value={destinationId}
          onChange={(e) => setDestinationId(e.target.value)}
        >
          <option value="">Choose a verified facility…</option>
          {partners.map((h) => (
            <option key={h.id} value={h.id}>
              {h.name ?? "Unnamed facility"} {h.city ? `· ${h.city}` : ""}
            </option>
          ))}
        </select>
        {directory.data && partners.length === 0 && (
          <p className="text-body-sm text-on-surface-variant mt-1">
            No other verified facility is available as a destination yet.
          </p>
        )}
      </label>

      <div className="flex flex-wrap items-end gap-3">
        <label className="flex-1 min-w-[7rem]">
          <span className="block text-label-sm font-label-sm uppercase tracking-wider text-on-surface-variant mb-1.5">
            Blood type
          </span>
          <select
            className="w-full px-3 py-2 rounded-lg border border-outline-variant bg-surface text-on-surface text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
            value={bloodType}
            onChange={(e) => setBloodType(e.target.value as BloodType)}
          >
            {BLOOD_TYPES.map((bt) => (
              <option key={bt} value={bt}>{BLOOD_LABEL[bt]}</option>
            ))}
          </select>
        </label>
        <label className="flex-1">
          <span className="block text-label-sm font-label-sm uppercase tracking-wider text-on-surface-variant mb-1.5">
            Component
          </span>
          <select
            aria-label="Component"
            className="w-full px-3 py-2 rounded-lg border border-outline-variant bg-surface text-on-surface text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
            value={component}
            onChange={(e) => setComponent(e.target.value as BloodComponent)}
          >
            {BLOOD_COMPONENTS.map((c) => (
              <option key={c} value={c}>{COMPONENT_LABEL[c]}</option>
            ))}
          </select>
        </label>
        <label className="w-28">
          <span className="block text-label-sm font-label-sm uppercase tracking-wider text-on-surface-variant mb-1.5">
            Units
          </span>
          <input
            type="number"
            min={1}
            className="w-full px-3 py-2 rounded-lg border border-outline-variant bg-surface text-on-surface text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
            value={units}
            onChange={(e) => setUnits(Math.max(1, Math.round(Number(e.target.value)) || 1))}
          />
        </label>
      </div>

      <button
        type="button"
        disabled={!destinationId || create.isPending}
        onClick={() => create.mutate()}
        className="h-11 rounded-lg bg-primary hover:opacity-90 text-on-primary font-label-md text-label-md flex items-center justify-center gap-2 disabled:opacity-50"
      >
        {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
        Reserve units & create shipment
      </button>
      {error && <p role="alert" className="text-error text-body-sm">{error}</p>}
    </div>
  );
}

// --------------------------------------------------------------------------- #
// List
// --------------------------------------------------------------------------- #

function ShipmentList({
  selectedId,
  onSelect,
}: {
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const [direction, setDirection] = useState<Direction>("all");
  const q = useQuery({
    queryKey: ["shipments", direction],
    queryFn: () => listShipments(direction === "all" ? undefined : { direction }),
  });

  return (
    <div className="bg-surface-container-lowest rounded-xl p-4 shadow-sm border border-border-subtle flex flex-col gap-3">
      <div className="flex items-center gap-2">
        {(["all", "outgoing", "incoming"] as Direction[]).map((d) => (
          <button
            key={d}
            type="button"
            aria-pressed={direction === d}
            onClick={() => setDirection(d)}
            className={`px-3 py-1.5 rounded-full font-label-sm text-label-sm font-semibold whitespace-nowrap transition-all ${
              direction === d
                ? "bg-primary text-on-primary shadow-sm"
                : "bg-surface-container text-on-surface-variant hover:bg-surface-container-high"
            }`}
          >
            {d === "all" ? "All" : d === "outgoing" ? "Outgoing" : "Incoming"}
          </button>
        ))}
      </div>

      {q.isLoading && (
        <div className="flex items-center gap-2 text-on-surface-variant py-8 justify-center">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading shipments…
        </div>
      )}
      {q.isError && (
        <div className="text-error text-body-sm flex items-center gap-3" role="alert">
          <span>{toApiError(q.error).detail}</span>
          <button type="button" className="underline flex-shrink-0" onClick={() => q.refetch()}>
            Retry
          </button>
        </div>
      )}
      {q.data && q.data.length === 0 && (
        <div className="rounded-xl border border-border-subtle bg-surface p-6 text-center text-on-surface-variant text-body-sm">
          No shipments {direction === "all" ? "" : direction} yet.
        </div>
      )}
      <ul className="flex flex-col gap-2 max-h-[560px] overflow-y-auto">
        {(q.data ?? []).map((s) => (
          <li key={s.id}>
            <button
              type="button"
              onClick={() => onSelect(s.id)}
              aria-current={selectedId === s.id}
              className={`w-full text-left p-3 rounded-lg border transition-colors ${
                selectedId === s.id
                  ? "border-primary bg-primary/5"
                  : "border-border-subtle bg-surface hover:bg-surface-container-low"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-data-mono text-label-sm text-on-surface-variant">{s.shipment_code}</span>
                <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${STATUS_STYLE[s.status]}`}>
                  {s.status.replace("_", " ")}
                </span>
              </div>
              <p className="font-body-sm text-body-sm text-on-surface mt-1">
                {s.origin_facility_name ?? "Unknown"} → {s.destination_facility_name ?? "Unknown"}
              </p>
              <p className="text-[11px] text-on-surface-variant mt-0.5">
                {s.items.length} unit(s) · {new Date(s.created_at).toLocaleString()}
              </p>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

// --------------------------------------------------------------------------- #
// Detail + actions
// --------------------------------------------------------------------------- #

function ShipmentDetail({ shipmentId }: { shipmentId: string }) {
  const qc = useQueryClient();
  const [reason, setReason] = useState("");
  const [actionError, setActionError] = useState("");

  const me = useQuery({ queryKey: ["hospital", "me"], queryFn: getHospitalProfile });
  const s = useQuery({ queryKey: ["shipments", shipmentId], queryFn: () => getShipment(shipmentId) });
  const timeline = useQuery({
    queryKey: ["shipments", shipmentId, "timeline"],
    queryFn: () => getShipmentTimeline(shipmentId),
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["shipments"] });
    setActionError("");
  };

  const onError = (e: unknown) => setActionError(toApiError(e).detail);

  const dispatch = useMutation({ mutationFn: () => dispatchShipment(shipmentId, reason || undefined), onSuccess: invalidate, onError });
  const inTransit = useMutation({ mutationFn: () => markShipmentInTransit(shipmentId, reason || undefined), onSuccess: invalidate, onError });
  const delay = useMutation({ mutationFn: () => reportShipmentDelay(shipmentId, reason || "Delay reported"), onSuccess: invalidate, onError });
  const arrive = useMutation({ mutationFn: () => markShipmentArrived(shipmentId, reason || undefined), onSuccess: invalidate, onError });
  const receive = useMutation({ mutationFn: () => receiveShipment(shipmentId, reason || undefined), onSuccess: invalidate, onError });
  const cancel = useMutation({ mutationFn: () => cancelShipment(shipmentId, reason || undefined), onSuccess: invalidate, onError });
  const fail = useMutation({ mutationFn: () => failShipment(shipmentId, reason || undefined), onSuccess: invalidate, onError });

  if (s.isLoading) {
    return (
      <div className="flex items-center gap-2 text-on-surface-variant py-10 justify-center">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading shipment…
      </div>
    );
  }
  if (s.isError || !s.data) {
    return (
      <div className="rounded-xl border border-border-subtle bg-surface-container-lowest p-8 text-center">
        <p className="text-error text-body-sm">{toApiError(s.error).detail}</p>
      </div>
    );
  }

  const shipment = s.data;
  const isOrigin = me.data?.id === shipment.origin_facility_id;
  const isDestination = me.data?.id === shipment.destination_facility_id;
  const allowed = new Set(shipment.allowed_transitions);
  const pending = dispatch.isPending || inTransit.isPending || delay.isPending ||
    arrive.isPending || receive.isPending || cancel.isPending || fail.isPending;

  // Section 21-style authorization matrix, enforced for real on the
  // backend (every one of these calls re-checks) -- this list only
  // decides which buttons are worth showing for THIS actor.
  const actions: { label: string; icon: string; show: boolean; run: () => void; tone?: "danger" }[] = [
    { label: "Dispatch", icon: "local_shipping", show: isOrigin && allowed.has("DISPATCHED"), run: () => dispatch.mutate() },
    { label: "Mark in transit", icon: "conveyor_belt", show: (isOrigin || isDestination) && shipment.status === "DISPATCHED", run: () => inTransit.mutate() },
    { label: "Report delay", icon: "schedule", show: (isOrigin || isDestination) && (shipment.status === "DISPATCHED" || shipment.status === "IN_TRANSIT"), run: () => delay.mutate() },
    { label: "Mark arrived", icon: "flag", show: (isOrigin || isDestination) && shipment.status === "IN_TRANSIT", run: () => arrive.mutate() },
    { label: "Receive all as OK", icon: "inventory", show: isDestination && allowed.has("RECEIVED"), run: () => receive.mutate() },
    { label: "Cancel", icon: "cancel", show: isOrigin && allowed.has("CANCELLED"), run: () => cancel.mutate(), tone: "danger" as const },
    { label: "Report failure", icon: "report", show: (isOrigin || isDestination) && allowed.has("FAILED"), run: () => fail.mutate(), tone: "danger" as const },
  ].filter((a) => a.show);

  return (
    <div className="flex flex-col gap-5">
      <div className="bg-surface-container-lowest rounded-xl p-5 shadow-sm border border-border-subtle">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <p className="font-data-mono text-body-sm text-on-surface-variant">{shipment.shipment_code}</p>
            <h2 className="font-headline-md text-headline-md text-on-surface mt-0.5">
              {shipment.origin_facility_name ?? "Unknown"} → {shipment.destination_facility_name ?? "Unknown"}
            </h2>
          </div>
          <span className={`px-3 py-1 rounded-full text-xs font-bold ${STATUS_STYLE[shipment.status]}`}>
            {shipment.status.replace("_", " ")}
          </span>
        </div>

        {shipment.route_status && (
          <p className="text-body-sm mt-3 flex items-center gap-2">
            <span className="text-on-surface-variant">Route condition:</span>
            <span className={shipment.route_status === "DISRUPTED" ? "text-error font-semibold" : "text-success font-semibold"}>
              {shipment.route_status === "DISRUPTED" ? "Disrupted" : "Operational"}
            </span>
            {shipment.route_disrupted_reason && (
              <span className="text-on-surface-variant">— {shipment.route_disrupted_reason}</span>
            )}
          </p>
        )}
        {shipment.estimated_arrival_at && (
          <p className="text-body-sm text-on-surface-variant mt-1">
            Estimated arrival: {new Date(shipment.estimated_arrival_at).toLocaleString()} (estimate only — no live tracking)
          </p>
        )}
        {shipment.delayed_at && (
          <p className="text-body-sm text-warning mt-1">
            Delayed: {shipment.delay_reason} ({new Date(shipment.delayed_at).toLocaleString()})
          </p>
        )}
        {shipment.cancel_reason && (
          <p className="text-body-sm text-error mt-1">Cancelled: {shipment.cancel_reason}</p>
        )}
        {shipment.failure_reason && (
          <p className="text-body-sm text-error mt-1">Failed: {shipment.failure_reason}</p>
        )}
        {shipment.reconciliation_status && (
          <p className="text-body-sm mt-1 flex items-center gap-2" data-testid="reconciliation-status">
            <span className="text-on-surface-variant">Reconciliation:</span>
            <span className={shipment.reconciliation_status === "REQUIRED" ? "text-warning font-semibold" : "font-semibold"}>
              {RECONCILIATION_LABEL[shipment.reconciliation_status]}
            </span>
            {shipment.reconciliation_note && (
              <span className="text-on-surface-variant">— {shipment.reconciliation_note}</span>
            )}
          </p>
        )}

        <div className="mt-4">
          <p className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant mb-1.5">
            Units carried ({shipment.items.length})
          </p>
          <ul className="flex flex-wrap gap-1.5">
            {shipment.items.map((it) => (
              <li
                key={it.id}
                className={`px-2 py-1 rounded font-data-mono text-[11px] ${
                  it.is_active ? "bg-surface-container text-on-surface" : "bg-surface-container-high text-on-surface-variant line-through"
                }`}
                title={it.is_active ? "Still part of this active shipment" : "No longer active on this shipment"}
              >
                {it.unit_code ?? it.blood_unit_id.slice(0, 8)}
                <span className="ml-1 not-italic opacity-80">
                  · {it.blood_type ? BLOOD_LABEL[it.blood_type] : "?"} {componentLabel(it.component)}
                  {it.outcome && it.outcome !== "PENDING" && ` · ${it.outcome.replace(/_/g, " ").toLowerCase()}`}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {actions.length > 0 && (
        <div className="bg-surface-container-lowest rounded-xl p-5 shadow-sm border border-border-subtle">
          <h3 className="font-title-lg text-title-lg text-on-surface mb-3">Actions</h3>
          <label className="block mb-3">
            <span className="block text-label-sm font-label-sm uppercase tracking-wider text-on-surface-variant mb-1.5">
              Reason / note (optional, required for delay)
            </span>
            <input
              type="text"
              className="w-full px-3 py-2 rounded-lg border border-outline-variant bg-surface text-on-surface text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. traffic on NH48, cross-match confirmed, vehicle breakdown…"
            />
          </label>
          <div className="flex flex-wrap gap-2">
            {actions.map((a) => (
              <button
                key={a.label}
                type="button"
                disabled={pending}
                onClick={a.run}
                className={`px-4 py-2 rounded-lg font-label-md text-label-md flex items-center gap-2 disabled:opacity-50 ${
                  a.tone === "danger"
                    ? "bg-error/10 text-error hover:bg-error/20"
                    : "bg-secondary hover:bg-secondary-container text-on-secondary"
                }`}
              >
                <span aria-hidden="true" className="material-symbols-outlined text-[18px]">{a.icon}</span>
                {a.label}
              </button>
            ))}
          </div>
          {actionError && <p role="alert" className="text-error text-body-sm mt-2">{actionError}</p>}
        </div>
      )}

      <ColdChainPanel shipment={shipment} canAdd={isOrigin || isDestination} />
      {isDestination && allowed.has("RECEIVED") && (
        <ReceiptPanel shipment={shipment} onDone={invalidate} />
      )}

      {shipment.reconciliation_status === "REQUIRED" && isOrigin && (
        <ReconcilePanel shipment={shipment} onDone={invalidate} />
      )}
      {shipment.reconciliation_status === "REQUIRED" && !isOrigin && (
        <p className="text-body-sm text-on-surface-variant">
          The origin facility (or an admin) must decide what happened to these units. Nothing is
          returned to any stock automatically.
        </p>
      )}

      <div className="bg-surface-container-lowest rounded-xl p-5 shadow-sm border border-border-subtle">
        <h3 className="font-title-lg text-title-lg text-on-surface mb-3">Timeline</h3>
        {timeline.isLoading && (
          <div className="flex items-center gap-2 text-on-surface-variant py-4 justify-center">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading…
          </div>
        )}
        {timeline.isError && (
          <p className="text-error text-body-sm" role="alert">{toApiError(timeline.error).detail}</p>
        )}
        {timeline.data && timeline.data.length === 0 && (
          <p className="text-body-sm text-on-surface-variant">No events yet.</p>
        )}
        {timeline.data && timeline.data.length > 0 && (
          <ol className="relative border-l border-border-subtle ml-1 space-y-3">
            {timeline.data.map((e) => (
              <li key={e.id} className="ml-4">
                <span className="absolute -left-1.5 h-3 w-3 rounded-full bg-primary" />
                <p className="text-sm text-on-surface">{EVENT_LABEL[e.event_type] ?? e.event_type}</p>
                {e.note && <p className="text-xs text-on-surface-variant">{e.note}</p>}
                <p className="text-[11px] text-on-surface-variant">{new Date(e.created_at).toLocaleString()}</p>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- #
// Phase E -- failed-shipment reconciliation
// --------------------------------------------------------------------------- #

type Outcome = ShipmentReconcileInput["outcome"];

/** One explicit, attested decision about a FAILED shipment's units. The
 * backend enforces every rule below independently (origin/admin only, one
 * decision, full unit-code confirmation + cold-chain attestation before
 * anything is restocked); this form just makes those requirements visible
 * instead of letting a click silently "undo" a failure. */
export function ReconcilePanel({ shipment, onDone }: { shipment: Shipment; onDone: () => void }) {
  const [outcome, setOutcome] = useState<Outcome>("QUARANTINED");
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set());
  const [coldChain, setColdChain] = useState(false);
  const [error, setError] = useState("");
  const codes = shipment.items.map((i) => i.unit_code).filter((c): c is string => !!c);

  const submit = useMutation({
    mutationFn: () =>
      reconcileShipment(shipment.id, {
        outcome,
        note,
        ...(outcome === "RETURNED_TO_ORIGIN"
          ? { unit_codes: [...confirmed], cold_chain_maintained: coldChain }
          : {}),
      }),
    onSuccess: () => {
      setError("");
      onDone();
    },
    onError: (e) => setError(toApiError(e).detail),
  });

  const returning = outcome === "RETURNED_TO_ORIGIN";
  const allConfirmed = codes.length > 0 && codes.every((c) => confirmed.has(c));
  const canSubmit = note.trim().length >= 10 && (!returning || (allConfirmed && coldChain));

  return (
    <div className="bg-surface-container-lowest rounded-xl p-5 shadow-sm border border-warning/40" data-testid="reconcile-panel">
      <h3 className="font-title-lg text-title-lg text-on-surface">Reconcile failed shipment</h3>
      <p className="text-body-sm text-on-surface-variant mt-1">
        These {codes.length} unit(s) left your stock when dispatched. Record what actually happened to them.
        Units are only returned to your available stock if you confirm every unit came back and the
        cold chain was maintained.
      </p>
      <fieldset className="mt-3 flex flex-col gap-2">
        <legend className="sr-only">Outcome</legend>
        {([
          ["QUARANTINED", "Quarantined / lost / destroyed — never restocked"],
          ["REDIRECTED", "Delivered somewhere else — recorded only"],
          ["RETURNED_TO_ORIGIN", "Physically returned to us intact — restock"],
        ] as [Outcome, string][]).map(([value, label]) => (
          <label key={value} className="flex items-center gap-2 text-body-sm">
            <input type="radio" name="outcome" value={value} checked={outcome === value}
              onChange={() => setOutcome(value)} />
            {label}
          </label>
        ))}
      </fieldset>

      {returning && (
        <div className="mt-3 rounded-lg bg-surface-container p-3">
          <p className="text-label-sm font-label-sm uppercase tracking-wider text-on-surface-variant mb-2">
            Confirm each returned unit
          </p>
          <ul className="flex flex-wrap gap-2">
            {codes.map((c) => (
              <li key={c}>
                <label className="flex items-center gap-1 font-data-mono text-[12px]">
                  <input
                    type="checkbox"
                    checked={confirmed.has(c)}
                    onChange={(e) => {
                      const next = new Set(confirmed);
                      if (e.target.checked) next.add(c);
                      else next.delete(c);
                      setConfirmed(next);
                    }}
                  />
                  {c}
                </label>
              </li>
            ))}
          </ul>
          <label className="flex items-center gap-2 text-body-sm mt-3">
            <input type="checkbox" checked={coldChain} onChange={(e) => setColdChain(e.target.checked)} />
            I attest the cold chain was maintained for every unit above
          </label>
          {!allConfirmed && (
            <p className="text-body-sm text-on-surface-variant mt-1">
              Partial returns can't be restocked — choose “Quarantined” and note which units came back.
            </p>
          )}
        </div>
      )}

      <label className="block mt-3">
        <span className="block text-label-sm font-label-sm uppercase tracking-wider text-on-surface-variant mb-1.5">
          What happened (required, at least 10 characters)
        </span>
        <textarea
          className="w-full px-3 py-2 rounded-lg border border-outline-variant bg-surface text-on-surface text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      <button
        type="button"
        disabled={!canSubmit || submit.isPending}
        onClick={() => submit.mutate()}
        className="mt-3 h-10 px-4 rounded-lg bg-primary text-on-primary font-label-md text-label-md flex items-center gap-2 disabled:opacity-50"
      >
        {submit.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
        Record reconciliation
      </button>
      {error && <p role="alert" className="text-error text-body-sm mt-2">{error}</p>}
    </div>
  );
}


// --------------------------------------------------------------------------- #
// Gap elimination -- cold chain telemetry and per-unit receipt outcomes
// --------------------------------------------------------------------------- #
const COLD_STYLE: Record<string, string> = {
  WITHIN_RANGE: "text-success",
  NO_DATA: "text-on-surface-variant",
  DATA_GAP: "text-warning",
  SENSOR_FAILURE: "text-warning",
  EXCURSION: "text-error",
  REPEATED_EXCURSIONS: "text-error",
};

/** Readings and the evaluator's verdict. Readings come from a data logger
 * export or a manual check entered here -- LifeLink has no device driver,
 * and says so. */
function ColdChainPanel({ shipment, canAdd }: { shipment: Shipment; canAdd: boolean }) {
  const qc = useQueryClient();
  const t = useQuery({ queryKey: ["shipments", shipment.id, "telemetry"], queryFn: () => getTelemetry(shipment.id) });
  const [temp, setTemp] = useState("");
  const [sensor, setSensor] = useState("manual-check");
  const add = useMutation({
    mutationFn: () =>
      addTelemetry(shipment.id, [{ recorded_at: new Date().toISOString(), temperature_c: Number(temp), sensor_id: sensor }], "MANUAL"),
    onSuccess: () => {
      setTemp("");
      qc.invalidateQueries({ queryKey: ["shipments"] });
    },
  });
  const r = t.data?.report;
  return (
    <section aria-labelledby={`cc-${shipment.id}`} className="bg-surface-container-lowest rounded-xl p-5 shadow-sm border border-border-subtle">
      <h3 id={`cc-${shipment.id}`} className="font-title-lg text-title-lg text-on-surface mb-2">Cold chain</h3>
      <p className="text-body-sm">
        Acceptable range: {shipment.temp_min_c ?? "—"} °C to {shipment.temp_max_c ?? "—"} °C (cited storage condition for the
        component). Status:{" "}
        <strong className={COLD_STYLE[r?.status ?? shipment.cold_chain_status ?? "NO_DATA"]}>
          {(r?.status ?? shipment.cold_chain_status ?? "NO_DATA").replace(/_/g, " ")}
        </strong>
      </p>
      {r && (
        <ul className="list-disc pl-5 text-body-sm mt-2">
          {r.findings.map((f, i) => <li key={i}>{f}</li>)}
        </ul>
      )}
      {t.data && t.data.readings.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-body-sm">{t.data.readings.length} reading(s)</summary>
          <ul className="text-body-sm font-data-mono">
            {t.data.readings.map((x, i) => (
              <li key={i}>
                {new Date(x.recorded_at).toLocaleString()} · {x.temperature_c ?? "no value"} °C · {x.sensor_id} · {x.source}
                {x.error_code ? ` · ${x.error_code}` : ""}
              </li>
            ))}
          </ul>
        </details>
      )}
      {canAdd && ["CREATED", "DISPATCHED", "IN_TRANSIT", "ARRIVED"].includes(shipment.status) && (
        <form className="flex flex-wrap gap-2 items-end mt-3" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
          <label className="flex flex-col text-body-sm">Temperature now (°C)
            <input className="border border-border-subtle rounded-lg px-3 py-2" type="number" step="0.1" required value={temp} onChange={(e) => setTemp(e.target.value)} />
          </label>
          <label className="flex flex-col text-body-sm">Sensor / logger id
            <input className="border border-border-subtle rounded-lg px-3 py-2" value={sensor} onChange={(e) => setSensor(e.target.value)} />
          </label>
          <button className="bg-secondary text-on-secondary px-4 py-2 rounded-lg" disabled={add.isPending}>Record reading</button>
          {add.error && <p role="alert" className="text-error text-body-sm w-full">{toApiError(add.error).detail}</p>}
        </form>
      )}
    </section>
  );
}

const RECEIPT_OUTCOMES = ["RECEIVED_OK", "QUARANTINED", "DAMAGED", "SPOILED", "LOST"];

/** Per-unit receipt: each carried unit gets its own outcome. */
function ReceiptPanel({ shipment, onDone }: { shipment: Shipment; onDone: () => void }) {
  const items = shipment.items.filter((i) => i.is_active && i.unit_code);
  const bad = ["EXCURSION", "REPEATED_EXCURSIONS", "SENSOR_FAILURE", "DATA_GAP"].includes(shipment.cold_chain_status ?? "");
  const [choice, setChoice] = useState<Record<string, { outcome: string; reason: string }>>({});
  const submit = useMutation({
    mutationFn: () =>
      receiveShipmentWithOutcomes(
        shipment.id,
        items
          .filter((i) => choice[i.unit_code!])
          .map((i) => ({ unit_code: i.unit_code!, outcome: choice[i.unit_code!].outcome, reason: choice[i.unit_code!].reason || null })),
      ),
    onSuccess: onDone,
  });
  return (
    <section aria-labelledby={`rc-${shipment.id}`} className="bg-surface-container-lowest rounded-xl p-5 shadow-sm border border-border-subtle">
      <h3 id={`rc-${shipment.id}`} className="font-title-lg text-title-lg text-on-surface mb-2">Receive unit by unit</h3>
      <p className="text-body-sm text-on-surface-variant mb-2">
        Units left unset are {bad ? "QUARANTINED (the cold chain is in doubt)" : "received as OK"}. A reason is required for anything else.
      </p>
      <ul className="flex flex-col gap-2">
        {items.map((i) => (
          <li key={i.id} className="flex flex-wrap gap-2 items-center text-body-sm">
            <span className="font-data-mono w-40">{i.unit_code}</span>
            <label className="sr-only" htmlFor={`o-${i.id}`}>Outcome for {i.unit_code}</label>
            <select
              id={`o-${i.id}`}
              className="border border-border-subtle rounded-lg px-2 py-1"
              value={choice[i.unit_code!]?.outcome ?? ""}
              onChange={(e) => setChoice((c) => ({ ...c, [i.unit_code!]: { outcome: e.target.value, reason: c[i.unit_code!]?.reason ?? "" } }))}
            >
              <option value="">default</option>
              {RECEIPT_OUTCOMES.filter((o) => !(bad && o === "RECEIVED_OK")).map((o) => <option key={o} value={o}>{o.replace("_", " ")}</option>)}
            </select>
            <label className="sr-only" htmlFor={`r-${i.id}`}>Reason for {i.unit_code}</label>
            <input
              id={`r-${i.id}`}
              className="border border-border-subtle rounded-lg px-2 py-1 flex-1 min-w-[10rem]"
              placeholder="reason"
              value={choice[i.unit_code!]?.reason ?? ""}
              onChange={(e) => setChoice((c) => ({ ...c, [i.unit_code!]: { outcome: c[i.unit_code!]?.outcome ?? "", reason: e.target.value } }))}
            />
          </li>
        ))}
      </ul>
      <button className="mt-3 bg-primary text-on-primary px-4 py-2 rounded-lg" disabled={submit.isPending} onClick={() => submit.mutate()}>
        Record receipt
      </button>
      {submit.error && <p role="alert" className="text-error text-body-sm mt-2">{toApiError(submit.error).detail}</p>}
    </section>
  );
}
