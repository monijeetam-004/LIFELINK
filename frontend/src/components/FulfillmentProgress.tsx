/**
 * Phase E -- quantity-aware fulfilment, shown from server truth only.
 *
 * `fulfilled_units` / `units_needed` both come from the backend
 * (BloodRequest.fulfilled_units is the single source of truth); "remaining"
 * is derived here for display and is never sent back. A PARTIALLY_FULFILLED
 * request is still open and still needs blood -- this bar makes that
 * visible instead of a status chip alone.
 */
interface Props {
  fulfilled: number;
  needed: number;
  /** Compact single-line variant for lists/tables. */
  compact?: boolean;
}

export function remainingUnits(fulfilled: number, needed: number): number {
  return Math.max(needed - fulfilled, 0);
}

export default function FulfillmentProgress({ fulfilled, needed, compact = false }: Props) {
  const safeNeeded = Math.max(needed, 1);
  const pct = Math.min(100, Math.round((fulfilled / safeNeeded) * 100));
  const remaining = remainingUnits(fulfilled, needed);
  const label =
    remaining === 0
      ? `${fulfilled} of ${needed} unit(s) fulfilled`
      : `${fulfilled} of ${needed} unit(s) fulfilled · ${remaining} still needed`;

  if (compact) {
    return (
      <span className="text-body-sm text-on-surface-variant" data-testid="fulfillment-progress">
        {label}
      </span>
    );
  }
  return (
    <div data-testid="fulfillment-progress">
      <div
        className="h-2 w-full rounded-full bg-surface-container-high overflow-hidden"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={needed}
        aria-valuenow={fulfilled}
        aria-label="Units fulfilled"
      >
        <div
          className={`h-full ${remaining === 0 ? "bg-success" : "bg-secondary"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-body-sm text-on-surface-variant mt-1">{label}</p>
    </div>
  );
}
