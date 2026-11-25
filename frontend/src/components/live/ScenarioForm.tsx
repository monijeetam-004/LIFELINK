import { useEffect, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";

import { useAuth } from "../../lib/auth";
import { BLOOD_LABEL, BLOOD_TYPES, getHospitalDirectory, getHospitalProfile, type BloodType } from "../../lib/domain";
import { COMPONENTS, COMPONENT_LABEL, URGENCIES, type SimulateBody } from "../../lib/gapApi";

/** The inputs every planner page shares. Hospitals always plan for their
 * own facility; administrators pick any facility. */
export default function ScenarioForm({
  onRun,
  busy,
  submitLabel = "Run planner",
  extra,
}: {
  onRun: (body: SimulateBody) => void;
  busy: boolean;
  submitLabel?: string;
  extra?: (dest: string) => React.ReactNode;
}) {
  const { user } = useAuth();
  const isAdmin = user?.role === "ADMIN";
  const directory = useQuery({ queryKey: ["hospital-directory"], queryFn: getHospitalDirectory, enabled: isAdmin });
  const mine = useQuery({ queryKey: ["hospital", "me"], queryFn: getHospitalProfile, enabled: !isAdmin });
  const [dest, setDest] = useState("");
  const [bloodType, setBloodType] = useState<BloodType>("O_NEG");
  const [component, setComponent] = useState<SimulateBody["component"]>("WHOLE_BLOOD");
  const [units, setUnits] = useState(2);
  const [urgency, setUrgency] = useState<SimulateBody["urgency"]>("CRITICAL");
  const [deadline, setDeadline] = useState<string>("");

  useEffect(() => {
    if (!isAdmin && mine.data?.id) setDest(mine.data.id);
  }, [isAdmin, mine.data?.id]);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!dest) return;
    onRun({
      recipient_blood_type: bloodType,
      component,
      units_needed: units,
      urgency,
      deadline_minutes: deadline === "" ? null : Number(deadline),
      destination_facility_id: dest,
      failed_route_ids: [],
    });
  }

  const field = "border border-border-subtle rounded-lg px-3 py-2 bg-surface-container-lowest";
  return (
    <form onSubmit={submit} className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 rounded-xl border border-border-subtle p-4">
      {isAdmin ? (
        <label className="flex flex-col gap-1 text-body-sm">
          Deliver to facility
          <select className={field} value={dest} onChange={(e) => setDest(e.target.value)} required>
            <option value="">Choose…</option>
            {(directory.data ?? []).map((f) => (
              <option key={f.id} value={f.id}>{f.name ?? f.id.slice(0, 8)}{f.city ? ` — ${f.city}` : ""}</option>
            ))}
          </select>
        </label>
      ) : (
        <p className="text-body-sm self-end">Deliver to: <strong>{mine.data?.name ?? "your facility"}</strong></p>
      )}
      <label className="flex flex-col gap-1 text-body-sm">
        Patient blood type
        <select className={field} value={bloodType} onChange={(e) => setBloodType(e.target.value as BloodType)}>
          {BLOOD_TYPES.map((b) => <option key={b} value={b}>{BLOOD_LABEL[b]}</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-body-sm">
        Component
        <select className={field} value={component} onChange={(e) => setComponent(e.target.value as SimulateBody["component"])}>
          {COMPONENTS.map((c) => <option key={c} value={c}>{COMPONENT_LABEL[c]}</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-body-sm">
        Units needed
        <input className={field} type="number" min={1} max={50} value={units} onChange={(e) => setUnits(Number(e.target.value))} />
      </label>
      <label className="flex flex-col gap-1 text-body-sm">
        Urgency (selects the strategy)
        <select className={field} value={urgency} onChange={(e) => setUrgency(e.target.value as SimulateBody["urgency"])}>
          {URGENCIES.map((u) => <option key={u} value={u}>{u}</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-body-sm">
        Deadline in minutes (blank = none)
        <input className={field} type="number" min={0} value={deadline} onChange={(e) => setDeadline(e.target.value)} />
      </label>
      {extra?.(dest)}
      <div className="sm:col-span-2 lg:col-span-3">
        <button type="submit" disabled={busy || !dest} className="bg-primary text-on-primary px-5 py-2 rounded-lg font-label-md disabled:opacity-50">
          {busy ? "Running…" : submitLabel}
        </button>
      </div>
    </form>
  );
}
