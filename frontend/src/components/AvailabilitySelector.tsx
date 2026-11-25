import type { DonorAvailabilityState } from "../lib/domain";

/**
 * The real three-way donor availability control (PUT /donors/me/availability).
 * Shared by Settings.tsx, ReadyConsole.tsx and DonorDashboard.tsx so the
 * three states read the same way everywhere. AVAILABLE and UNAVAILABLE /
 * NOT_NOW all persist to DonorProfile.availability_state server-side;
 * UNAVAILABLE and NOT_NOW both flip the underlying `is_available` boolean
 * every matching/notification query filters on (see donors.py
 * update_availability) — the difference between them is informational only.
 */
const OPTIONS: { value: DonorAvailabilityState; label: string; dot: string }[] = [
  { value: "AVAILABLE", label: "Available", dot: "bg-success" },
  { value: "NOT_NOW", label: "Not now", dot: "bg-warning" },
  { value: "UNAVAILABLE", label: "Unavailable", dot: "bg-on-surface-variant" },
];

export default function AvailabilitySelector({
  value,
  onChange,
  disabled,
}: {
  value: DonorAvailabilityState | undefined;
  onChange: (v: DonorAvailabilityState) => void;
  disabled?: boolean;
}) {
  return (
    <div className="inline-flex flex-wrap rounded-full border border-border-subtle bg-surface-container-low p-1 gap-1">
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          disabled={disabled}
          aria-pressed={value === o.value}
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full font-label-md text-label-md transition-colors disabled:opacity-60 ${
            value === o.value
              ? "bg-surface shadow-sm text-on-surface"
              : "text-on-surface-variant hover:text-on-surface"
          }`}
        >
          <span className={`h-2 w-2 rounded-full ${o.dot}`} />
          {o.label}
        </button>
      ))}
    </div>
  );
}
