/**
 * Client-side stock-status convenience for hospital inventory screens.
 *
 * The backend (`GET/PUT /hospitals/me/inventory`) only ever returns a raw
 * `{ blood_type, units }[]` — there is no "critical/low/optimal" field, no
 * target level, and no cold-chain/temperature telemetry anywhere in the
 * model (see `backend/app/models/hospital.py`). The tiers and thresholds
 * below are a UI-only convenience for scanning the grid at a glance, not a
 * value the backend computed. They intentionally mirror the thresholds
 * already used in `HospitalDashboard.tsx` (`stockLabel`) so the two screens
 * read consistently.
 */
export type StockTier = "CRITICAL" | "LOW" | "OPTIMAL";

export const CRITICAL_BELOW = 10;
export const LOW_BELOW = 30;

export function stockTier(units: number): StockTier {
  if (units < CRITICAL_BELOW) return "CRITICAL";
  if (units < LOW_BELOW) return "LOW";
  return "OPTIMAL";
}

export const STOCK_TIER_STYLE: Record<
  StockTier,
  { badge: string; bar: string; dot: string; label: string }
> = {
  CRITICAL: {
    badge: "bg-error-container text-on-error-container",
    bar: "bg-error",
    dot: "bg-error",
    label: "Critical",
  },
  LOW: {
    badge: "bg-warning/15 text-warning",
    bar: "bg-warning",
    dot: "bg-warning",
    label: "Low",
  },
  OPTIMAL: {
    badge: "bg-success/15 text-success",
    bar: "bg-success",
    dot: "bg-success",
    label: "Optimal",
  },
};
