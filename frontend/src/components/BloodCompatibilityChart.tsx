import { useQuery } from "@tanstack/react-query";

import { BLOOD_TYPES, BLOOD_LABEL, getBloodCompatibility, type BloodType } from "../lib/domain";

/**
 * Compatibility reference: which donor blood types can give red cells to a
 * given recipient type. Either pass `recipientType` to show one row's
 * summary, or omit it to show the full donor × recipient grid.
 *
 * Phase 9: this used to hand-transcribe the compatibility matrix into a
 * local `CAN_DONATE_TO` table -- the one duplication of the backend's
 * central compatibility engine found in the whole codebase (see
 * LIFELINK_PHASE_9_REPORT.md). It now fetches the real matrix from
 * `GET /matching/blood-compatibility` (app.core.blood_compatibility) so
 * there is exactly one authoritative implementation, not two that could
 * silently drift apart.
 */
export default function BloodCompatibilityChart({ recipientType }: { recipientType?: BloodType }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["blood-compatibility"],
    queryFn: getBloodCompatibility,
    staleTime: Infinity, // a fixed medical fact table -- never goes stale within a session
  });

  if (isLoading) {
    return <div className="h-16 rounded-lg bg-surface-container-lowest animate-pulse" />;
  }
  if (isError || !data) {
    return (
      <p className="font-body-sm text-body-sm text-on-surface-variant">
        Compatibility reference is unavailable right now.
      </p>
    );
  }

  const canReceiveFrom = (recipient: BloodType): BloodType[] =>
    data.groups.find((g) => g.blood_type === recipient)?.can_receive_from ?? [];
  const canDonateTo = (donor: BloodType, recipient: BloodType): boolean =>
    (data.groups.find((g) => g.blood_type === donor)?.can_donate_to ?? []).includes(recipient);

  if (recipientType) {
    const donors = canReceiveFrom(recipientType);
    return (
      <div className="text-sm">
        <p className="text-on-surface-variant">
          A patient who needs <span className="font-label-md text-on-surface">{BLOOD_LABEL[recipientType]}</span>{" "}
          can safely receive red cells from:
        </p>
        <div className="flex flex-wrap gap-1.5 mt-2">
          {donors.map((d) => (
            <span
              key={d}
              className="px-2.5 py-1 rounded-full bg-primary/10 text-primary font-label-sm text-label-sm font-semibold"
            >
              {BLOOD_LABEL[d]}
            </span>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-center border-collapse text-sm min-w-[420px]">
        <caption className="sr-only">Blood type donor-to-recipient compatibility grid</caption>
        <thead>
          <tr>
            <th className="p-2 text-left font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">
              Donor ↓ / Recipient →
            </th>
            {BLOOD_TYPES.map((r) => (
              <th key={r} className="p-2 font-label-sm text-label-sm text-on-surface-variant">
                {BLOOD_LABEL[r]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {BLOOD_TYPES.map((donor) => (
            <tr key={donor} className="border-t border-border-subtle">
              <th scope="row" className="p-2 text-left font-label-md text-label-md text-on-surface">
                {BLOOD_LABEL[donor]}
              </th>
              {BLOOD_TYPES.map((recipient) => {
                const ok = canDonateTo(donor, recipient);
                return (
                  <td key={recipient} className="p-2">
                    <span
                      aria-label={ok ? "Compatible" : "Not compatible"}
                      className={`inline-block w-5 h-5 rounded-full ${ok ? "bg-success" : "bg-surface-container-high"}`}
                    />
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
