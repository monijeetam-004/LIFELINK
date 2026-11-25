/**
 * Donor Recommendation -- LIVE ranking for a real emergency.
 *
 * Calls GET /emergencies/{id}/match-candidates: compatible, available
 * donors (and verified in-area facilities) ranked by real distance from the
 * emergency's coordinates, with the MAX_DONOR_DISTANCE_KM policy applied and
 * reported. Unknown distances are shown as unknown, never as 0 km.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import EmergencyPicker from "../components/live/EmergencyPicker";
import LivePageShell from "../components/live/LivePageShell";
import { toApiError } from "../lib/api";
import { getEmergencyCandidates } from "../lib/domain";

export default function DonorRecommendation() {
  const [eid, setEid] = useState("");
  const q = useQuery({ queryKey: ["match", eid], queryFn: () => getEmergencyCandidates(eid), enabled: !!eid });
  const m = q.data;
  return (
    <LivePageShell
      title="Donor Recommendation"
      roles={["PATIENT", "FAMILY", "HOSPITAL", "ADMIN"]}
      intro="The live responder ranking for one of your emergencies, with the distance policy that was applied."
    >
      <EmergencyPicker value={eid} onChange={setEid} />
      {q.isError && <p role="alert" className="text-error">{toApiError(q.error).detail}</p>}
      {m && (
        <>
          <p className="text-body-sm rounded-lg bg-surface-container-low p-3">
            Compatible donor types: {m.compatible_donor_types.join(", ") || "any (no blood type recorded)"} ·
            distance limit {m.distance_limit_km ?? "—"} km{m.distance_override_active ? " (OVERRIDDEN by an administrator)" : ""} ·{" "}
            {m.excluded_beyond_limit ?? 0} donor(s) beyond the limit not shown · {m.unknown_distance ?? 0} with unknown distance ·{" "}
            {m.emergency_has_coordinates ? "emergency has coordinates" : "emergency has NO coordinates, so distances are unknown"}
          </p>
          <div tabIndex={0} role="region" aria-label="Ranked responders (scrollable)" className="overflow-x-auto rounded-xl border border-border-subtle focus:outline-none focus:ring-2 focus:ring-primary/40">
            <table className="min-w-full text-body-sm">
              <caption className="sr-only">Ranked responders</caption>
              <thead className="bg-surface-container-low text-left">
                <tr>
                  <th scope="col" className="px-3 py-2">#</th>
                  <th scope="col" className="px-3 py-2">Responder</th>
                  <th scope="col" className="px-3 py-2">Role</th>
                  <th scope="col" className="px-3 py-2">Type</th>
                  <th scope="col" className="px-3 py-2">Distance</th>
                  <th scope="col" className="px-3 py-2">Why</th>
                </tr>
              </thead>
              <tbody>
                {m.candidates.map((c) => (
                  <tr key={c.responder_ref} className="border-t border-border-subtle align-top">
                    <td className="px-3 py-2">{c.rank}</td>
                    <td className="px-3 py-2">{c.responder_name ?? c.responder_ref}{c.has_accepted && " · accepted"}</td>
                    <td className="px-3 py-2">{c.role}{c.facility_verified === false ? " (unverified)" : ""}</td>
                    <td className="px-3 py-2">{c.blood_type?.replace("_", " ") ?? "—"}</td>
                    <td className="px-3 py-2">{c.distance_km != null ? `${c.distance_km} km` : "unknown"}</td>
                    <td className="px-3 py-2">{c.explanation}</td>
                  </tr>
                ))}
                {m.candidates.length === 0 && (
                  <tr><td colSpan={6} className="px-3 py-4 text-on-surface-variant">No eligible responder right now.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </LivePageShell>
  );
}
