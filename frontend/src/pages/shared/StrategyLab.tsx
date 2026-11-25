/**
 * Strategy Lab (real matching-engine screen, Phase 9)
 *
 * This is a genuinely new, authenticated screen -- not a redesign of the
 * existing Stitch "Research Mode" / "Mathematical Model" pages
 * (ResearchMode.tsx / MathematicalModel.tsx), which stay exactly as they
 * were ported: static, public, illustrative mockups with invented numbers
 * (see App.tsx's "kept as-is from the Stitch port" comment and
 * LIFELINK_PHASE_9_REPORT.md). Those pages name four comparison
 * strategies -- nearest, time-aware, time + resilience, event-driven --
 * but nothing in this codebase ever computed them for real before Phase 9.
 *
 * This screen is where the real thing lives: it calls
 * `GET /blood-requests/{id}/match-strategies` or
 * `GET /emergencies/{id}/match-strategies` (app/core/matching_engine.py)
 * and shows the same four strategies actually run over live candidate
 * data, each ranked candidate carrying machine-readable factors plus a
 * human-readable explanation -- never a bare, unexplained score.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { toApiError } from "../../lib/api";
import {
  BLOOD_LABEL,
  getBloodRequestStrategyComparison,
  getEmergencyStrategyComparison,
  listBloodRequests,
  listEmergencies,
  type BloodRequest,
  type Emergency,
  type MatchStrategy,
  type RankedMatchCandidate,
} from "../../lib/domain";

const STRATEGY_LABEL: Record<MatchStrategy, string> = {
  NEAREST: "Nearest",
  TIME_AWARE: "Time-aware",
  TIME_RESILIENCE: "Time + resilience",
  EVENT_DRIVEN: "Event-driven",
};

const STRATEGY_BLURB: Record<MatchStrategy, string> = {
  NEAREST: "Accepted responders first, then closest by real distance.",
  TIME_AWARE: "Prioritizes donors whose donation-eligibility rest period has already elapsed.",
  TIME_RESILIENCE: "Time-aware, plus identity-verified donors rank ahead of unverified ones.",
  EVENT_DRIVEN: "Reacts to what's already happened: accepted, then currently available, then fresh availability data.",
};

function CandidateCard({ c }: { c: RankedMatchCandidate }) {
  return (
    <li className="rounded-lg border border-border-subtle bg-surface p-3 flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <span className="font-label-md text-label-md text-on-surface">
          #{c.rank} · {c.donor_ref}
          {c.blood_type && (
            <span className="ml-2 text-primary font-semibold">{BLOOD_LABEL[c.blood_type]}</span>
          )}
        </span>
      </div>
      <p className="font-body-sm text-body-sm text-on-surface-variant">{c.explanation}</p>
      <div className="flex flex-wrap gap-1.5 mt-1">
        {c.factors.has_accepted && <Tag label="accepted" tone="success" />}
        <Tag label={c.factors.available ? "available" : "not available"} tone={c.factors.available ? "success" : "muted"} />
        {c.factors.eligible !== null && (
          <Tag label={c.factors.eligible ? "eligible" : "not yet eligible"} tone={c.factors.eligible ? "success" : "warning"} />
        )}
        {c.factors.verified !== null && (
          <Tag label={c.factors.verified ? "verified" : "unverified"} tone={c.factors.verified ? "success" : "warning"} />
        )}
        {c.factors.distance_km !== null && <Tag label={`${c.factors.distance_km.toFixed(1)} km`} tone="muted" />}
        {c.factors.stale_availability && <Tag label="stale availability" tone="warning" />}
      </div>
    </li>
  );
}

function Tag({ label, tone }: { label: string; tone: "success" | "warning" | "muted" }) {
  const cls =
    tone === "success"
      ? "bg-success/15 text-success"
      : tone === "warning"
        ? "bg-warning/15 text-warning"
        : "bg-surface-container-high text-on-surface-variant";
  return <span className={`px-2 py-0.5 rounded-full text-label-sm font-semibold ${cls}`}>{label}</span>;
}

export default function StrategyLab() {
  const [kind, setKind] = useState<"request" | "emergency">("request");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const requests = useQuery({ queryKey: ["strategy-lab", "requests"], queryFn: () => listBloodRequests() });
  const emergencies = useQuery({ queryKey: ["strategy-lab", "emergencies"], queryFn: listEmergencies });

  const comparison = useQuery({
    queryKey: ["strategy-lab", "comparison", kind, selectedId],
    queryFn: () =>
      kind === "request"
        ? getBloodRequestStrategyComparison(selectedId as string)
        : getEmergencyStrategyComparison(selectedId as string),
    enabled: !!selectedId,
  });

  const items: (BloodRequest | Emergency)[] = kind === "request" ? requests.data ?? [] : emergencies.data ?? [];
  const picker = kind === "request" ? requests : emergencies;

  return (
    <div className="min-h-screen bg-surface">
      <header className="px-4 sm:px-6 lg:px-10 py-6 border-b border-border-subtle">
        <h1 className="font-headline-lg text-headline-lg text-on-surface">Strategy Lab</h1>
        <p className="font-body-md text-body-md text-on-surface-variant mt-1 max-w-2xl">
          Real matching-engine output, side by side. Pick one of your own requests or emergencies below to
          see all four Phase 9 strategies -- nearest, time-aware, time + resilience, event-driven -- run over
          the same live candidate pool. Every rank carries the real factors it was computed from and a
          plain-language explanation; there is no hidden score.
        </p>
      </header>

      <main className="px-4 sm:px-6 lg:px-10 py-6 max-w-container-max mx-auto flex flex-col gap-6">
        <div className="flex flex-col sm:flex-row gap-3 sm:items-center">
          <div className="flex rounded-lg border border-border-subtle overflow-hidden w-fit">
            {(["request", "emergency"] as const).map((k) => (
              <button
                key={k}
                type="button"
                className={`px-4 py-2 font-label-md text-label-md ${
                  kind === k ? "bg-primary text-on-primary" : "bg-surface text-on-surface-variant"
                }`}
                onClick={() => {
                  setKind(k);
                  setSelectedId(null);
                }}
              >
                {k === "request" ? "Blood requests" : "Emergencies"}
              </button>
            ))}
          </div>

          <select
            aria-label={kind === "request" ? "Select a blood request" : "Select an emergency"}
            className="h-10 px-3 rounded-lg bg-surface-container-low text-on-surface font-label-md text-label-md outline-none border border-border-subtle min-w-[240px] focus:ring-2 focus:ring-primary/40 focus:border-primary"
            value={selectedId ?? ""}
            disabled={picker.isLoading || picker.isError}
            onChange={(e) => setSelectedId(e.target.value || null)}
          >
            <option value="">
              {picker.isLoading
                ? "Loading…"
                : picker.isError
                  ? "Couldn't load"
                  : items.length === 0
                    ? "No items available"
                    : "Select one..."}
            </option>
            {items.map((it) => (
              <option key={it.id} value={it.id}>
                {it.blood_type ? BLOOD_LABEL[it.blood_type] : "Any type"} · {it.status} ·{" "}
                {new Date(it.created_at).toLocaleDateString()}
              </option>
            ))}
          </select>
        </div>

        {/* Phase 21 fix: `requests`/`emergencies` used to have no error
            handling at all -- a failed fetch left the picker showing
            "No items available", indistinguishable from a genuinely empty
            list. */}
        {picker.isError && (
          <p role="alert" className="text-error text-body-sm bg-error-container/40 rounded-lg px-4 py-2">
            Couldn't load your {kind === "request" ? "blood requests" : "emergencies"}.{" "}
            {toApiError(picker.error).detail}{" "}
            <button type="button" onClick={() => picker.refetch()} className="underline font-label-md">
              Retry
            </button>
          </p>
        )}

        {comparison.isLoading && (
          <div className="h-40 rounded-xl border border-border-subtle bg-surface-container-lowest animate-pulse flex items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-on-surface-variant" />
          </div>
        )}
        {comparison.isError && (
          <p role="alert" className="text-error text-body-sm bg-error-container/40 rounded-lg px-4 py-2">
            {toApiError(comparison.error).detail}{" "}
            <button type="button" onClick={() => comparison.refetch()} className="underline font-label-md">
              Retry
            </button>
          </p>
        )}

        {comparison.data && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {comparison.data.strategies.map((result) => (
              <section key={result.strategy} className="rounded-xl border border-border-subtle p-4 flex flex-col gap-3">
                <div>
                  <h2 className="font-headline-md text-headline-md text-on-surface">
                    {STRATEGY_LABEL[result.strategy]}
                  </h2>
                  <p className="font-body-sm text-body-sm text-on-surface-variant">
                    {STRATEGY_BLURB[result.strategy]}
                  </p>
                </div>
                {result.candidates.length === 0 ? (
                  <p className="font-body-sm text-body-sm text-on-surface-variant">No compatible candidates.</p>
                ) : (
                  <ul className="flex flex-col gap-2">
                    {result.candidates.map((c) => (
                      <CandidateCard key={c.donor_ref} c={c} />
                    ))}
                  </ul>
                )}
              </section>
            ))}
          </div>
        )}

        {!selectedId && (
          <p className="font-body-md text-body-md text-on-surface-variant bg-surface-container-lowest border border-border-subtle rounded-xl p-6 text-center">
            Select a request or emergency above to run the comparison.
          </p>
        )}
      </main>
    </div>
  );
}
