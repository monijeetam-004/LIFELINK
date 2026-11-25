import { useQuery } from "@tanstack/react-query";

import { useAuth } from "../../lib/auth";
import { listActiveEmergencies, listAdminEmergencies, listEmergencies } from "../../lib/domain";

/** Emergencies the signed-in user may look at: their own (patient/family),
 * the open ones offered to them (donor/hospital), or all (admin). */
export default function EmergencyPicker({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const { user } = useAuth();
  const role = user?.role;
  const q = useQuery({
    queryKey: ["emergency-picker", role],
    queryFn: () =>
      role === "ADMIN"
        ? listAdminEmergencies()
        : role === "HOSPITAL" || role === "DONOR"
          ? listActiveEmergencies()
          : listEmergencies(),
    enabled: !!user,
  });
  const rows = (q.data ?? []) as { id: string; blood_type: string | null; status: string; created_at: string }[];
  return (
    <label className="flex flex-col gap-1 text-body-sm max-w-xl">
      Emergency
      <select
        className="border border-border-subtle rounded-lg px-3 py-2 bg-surface-container-lowest"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">{q.isLoading ? "Loading…" : rows.length ? "Choose an emergency…" : "No emergencies available"}</option>
        {rows.map((e) => (
          <option key={e.id} value={e.id}>
            {e.blood_type?.replace("_", " ") ?? "type unknown"} · {e.status} · {new Date(e.created_at).toLocaleString()} · {e.id.slice(0, 8)}
          </option>
        ))}
      </select>
    </label>
  );
}
