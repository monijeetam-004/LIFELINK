/**
 * Confidence-based donor shortlist (2026-10-31).
 *
 * Shows a SHORT, honest picture instead of a long list of people who may
 * never answer: how many donors are eligible, how many were contacted, who
 * is still awaiting an answer, who accepted, who is confirmed, and what is
 * still missing. Ranking is never presented as availability: a ranked donor
 * is a "candidate", an accepted donor is "accepted (not yet confirmed)", and
 * even a confirmed donor still needs the blood centre's own screening.
 *
 * Data comes from GET /blood-requests/{id}/shortlist or
 * /emergencies/{id}/shortlist; the query key starts with the resource's own
 * prefix, so the existing real-time stream refreshes it whenever the
 * request/emergency changes (responses, contacts, timeouts).
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { toApiError } from "../lib/api";
import { BLOOD_LABEL } from "../lib/domain";
import {
  confirmShortlistContact,
  contactNextBatch,
  getShortlist,
  simulateShortlist,
  RANKING_STRATEGIES,
  type DonorContactStatus,
  type Priority,
  type RankingStrategy,
  type Shortlist,
  type ShortlistCandidate,
  type ShortlistSubjectKind,
} from "../lib/gapApi";
import { useLiveFallbackInterval } from "../lib/realtime";

export const STRATEGY_LABEL: Record<RankingStrategy, string> = {
  UNIFIED_PRIORITY: "Unified priority score",
  NEAREST: "Nearest first",
  TIME_AWARE: "Time-aware",
  TIME_RESILIENCE: "Time + resilience",
  EVENT_DRIVEN: "Event-driven",
};

const AVAILABILITY_LABEL: Record<Priority["availability_status"], string> = {
  ACCEPTED_THIS_REQUEST: "accepted this request (not yet confirmed)",
  CONFIRMED_RECENTLY: "availability confirmed recently",
  CONFIRMED_STALE: "availability confirmed long ago",
  UNCONFIRMED: "availability not confirmed (unknown)",
};

const EVIDENCE_STYLE: Record<Priority["evidence_level"], string> = {
  HIGH: "bg-success/15 text-success",
  MEDIUM: "bg-secondary/15 text-secondary",
  LOW: "bg-warning/15 text-warning",
  INSUFFICIENT: "bg-error/10 text-error",
};

/** "Why this rank": generated from the server's calculation -- the lines
 * add up to the score; a factor without data contributes 0 and its weight
 * goes to the neutral missing-data baseline, shown on its own line. */
function PriorityBreakdown({ c }: { c: ShortlistCandidate }) {
  const p = c.priority;
  // SUMMARY (patients, family): no baseline, and the score is a 10-point band
  const banded = p.missing_data_baseline == null;
  return (
    <details className="mt-0.5">
      <summary className="cursor-pointer text-[11px] text-primary">Why this rank</summary>
      <table className="mt-1 w-full text-[11px]" data-testid="priority-breakdown">
        <caption className="sr-only">Unified priority score breakdown</caption>
        <thead>
          <tr className="text-left text-on-surface-variant">
            <th scope="col" className="font-medium">Factor</th>
            <th scope="col" className="font-medium">Evidence</th>
            <th scope="col" className="font-medium text-right">Points</th>
          </tr>
        </thead>
        <tbody>
          {p.factors.map((f) => (
            <tr key={f.name}>
              <td>{f.label}</td>
              <td className="text-on-surface-variant">
                {f.status === "MISSING" ? "no data" : f.detail}
              </td>
              <td className="text-right">
                {f.contribution == null ? "—" : f.status === "MISSING" ? "0" : `+${f.contribution.toFixed(1)}`}
              </td>
            </tr>
          ))}
          {p.missing_data_baseline != null && p.missing_data_baseline > 0 && (
            <tr>
              <td>Missing-data baseline</td>
              <td className="text-on-surface-variant">neutral value for factors without data</td>
              <td className="text-right">+{p.missing_data_baseline.toFixed(1)}</td>
            </tr>
          )}
          <tr className="font-semibold">
            <td>Priority score</td>
            <td className="text-on-surface-variant">
              evidence coverage {Math.round(p.evidence_coverage * 100)}%
            </td>
            <td className="text-right">{banded ? `about ${p.score.toFixed(0)}` : p.score.toFixed(1)}</td>
          </tr>
        </tbody>
      </table>
    </details>
  );
}

const STATUS_LABEL: Record<DonorContactStatus, string> = {
  QUEUED: "Queued",
  AWAITING_RESPONSE: "Awaiting answer",
  NOTIFICATION_FAILED: "Notification failed",
  ACCEPTED: "Accepted — not yet confirmed",
  DECLINED: "Declined",
  UNAVAILABLE: "Not now",
  TIMED_OUT: "No answer (timed out)",
  ELIGIBILITY_RECHECK_REQUIRED: "Re-check failed",
  CONFIRMED_FOR_COORDINATION: "Confirmed for coordination",
  WITHDRAWN: "Withdrew",
};

const STATUS_STYLE: Partial<Record<DonorContactStatus, string>> = {
  CONFIRMED_FOR_COORDINATION: "bg-success/15 text-success",
  ACCEPTED: "bg-secondary/15 text-secondary",
  AWAITING_RESPONSE: "bg-warning/15 text-warning",
  DECLINED: "bg-error/10 text-error",
  TIMED_OUT: "bg-error/10 text-error",
  ELIGIBILITY_RECHECK_REQUIRED: "bg-error/10 text-error",
  WITHDRAWN: "bg-error/10 text-error",
};

const STATE_TEXT: Record<Shortlist["state"], string> = {
  SATISFIED: "Enough donors confirmed",
  PARTIAL: "Partially covered — still short",
  AWAITING: "Contacted — waiting for answers",
  NO_ELIGIBLE: "No eligible donors found",
  NOT_STARTED: "Not contacted yet",
  CLOSED: "Request closed",
};

const EXCLUDED_LABEL: Record<string, string> = {
  unavailable: "not available",
  donation_interval_not_elapsed: "donated too recently",
  committed_elsewhere: "committed to another request",
  beyond_distance_limit: "beyond the distance limit",
  outside_alert_radius: "outside their own alert radius",
  location_unknown: "location unknown",
  no_request_location: "request has no location",
  travel_exceeds_deadline: "cannot arrive before the deadline",
};

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="rounded-lg border border-border-subtle px-3 py-2">
      <p className={`text-lg font-bold ${tone ?? "text-on-surface"}`}>{value}</p>
      <p className="text-[11px] text-on-surface-variant leading-tight">{label}</p>
    </div>
  );
}

export default function DonorShortlistPanel({
  kind,
  subjectId,
  canManage,
  isAdmin = false,
}: {
  kind: ShortlistSubjectKind;
  subjectId: string;
  canManage: boolean;
  isAdmin?: boolean;
}) {
  const qc = useQueryClient();
  const prefix = kind === "BLOOD_REQUEST" ? "blood-requests" : "emergencies";
  // undefined = the server's choice: the last batch's strategy, else UNIFIED_PRIORITY
  const [strategy, setStrategy] = useState<RankingStrategy | undefined>(undefined);
  const key = [prefix, subjectId, "shortlist", strategy ?? "default"];
  const fallback = useLiveFallbackInterval(20_000);
  const q = useQuery({
    queryKey: key,
    queryFn: () => getShortlist(kind, subjectId, strategy),
    refetchInterval: fallback,
  });
  const onDone = (data: Shortlist) => {
    qc.setQueryData(key, data);
    qc.invalidateQueries({ queryKey: [prefix] });
  };
  const contact = useMutation({
    mutationFn: () => contactNextBatch(kind, subjectId, strategy),
    onSuccess: onDone,
  });
  const confirm = useMutation({
    mutationFn: (contactId: string) => confirmShortlistContact(kind, subjectId, contactId),
    onSuccess: onDone,
  });
  const simulate = useMutation({
    mutationFn: () => simulateShortlist(kind, subjectId),
    onSuccess: (r) => onDone(r.shortlist),
  });

  if (q.isLoading) {
    return (
      <p className="flex items-center gap-2 text-sm text-on-surface-variant">
        <Loader2 className="h-4 w-4 animate-spin" /> Checking eligible donors…
      </p>
    );
  }
  if (q.isError) return <p className="text-error text-sm">{toApiError(q.error).detail}</p>;
  const s = q.data!;
  const err = contact.error ?? confirm.error ?? simulate.error;

  return (
    <section aria-label="Donor shortlist" className="flex flex-col gap-3" data-testid="donor-shortlist">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="font-label-md text-label-md">Donor shortlist</h4>
        <span
          data-testid="shortlist-state"
          className={`px-3 py-1 rounded-full text-xs font-bold ${
            s.state === "SATISFIED"
              ? "bg-success/15 text-success"
              : s.state === "NO_ELIGIBLE" || s.state === "PARTIAL"
                ? "bg-error/10 text-error"
                : "bg-surface-container-high text-on-surface"
          }`}
        >
          {STATE_TEXT[s.state]}
        </span>
      </div>
      {s.demo_mode && (
        <p
          role="note"
          data-testid="demo-mode-banner"
          className="text-xs font-semibold rounded-md bg-warning/15 text-warning px-3 py-2"
        >
          DEMO MODE — rows marked SYNTHETIC are generated test donors. Their answers are simulated
          and say nothing about real donor availability.
        </p>
      )}
      <p className="text-sm text-on-surface" data-testid="shortlist-summary">{s.summary}</p>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <label htmlFor={`ranking-${subjectId}`} className="font-semibold text-on-surface-variant">
          Ranking
        </label>
        <select
          id={`ranking-${subjectId}`}
          data-testid="ranking-strategy"
          className="rounded-md border border-border-subtle bg-surface px-2 py-1 text-xs"
          value={strategy ?? s.strategy}
          onChange={(e) => setStrategy(e.target.value as RankingStrategy)}
        >
          {RANKING_STRATEGIES.map((r) => (
            <option key={r} value={r}>
              {STRATEGY_LABEL[r]}
              {r === "UNIFIED_PRIORITY" ? " (default)" : ""}
            </option>
          ))}
        </select>
        <span className="text-[11px] text-on-surface-variant" title={s.scoring_model}>
          {s.strategy === "UNIFIED_PRIORITY"
            ? "Priority is a ranking measure (0–100), not a probability of donation."
            : "Original lexicographic strategy; the unified priority is shown for reference."}
        </span>
      </div>
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
        <Stat label="eligible found" value={s.counts.eligible_candidates} />
        <Stat label="contacted" value={s.counts.contacted} />
        <Stat label="awaiting answer" value={s.counts.awaiting_response} tone="text-warning" />
        <Stat label="accepted" value={s.counts.accepted} tone="text-secondary" />
        <Stat label="confirmed" value={s.counts.confirmed_for_coordination} tone="text-success" />
        <Stat label="still needed" value={s.remaining_unmet} tone={s.remaining_unmet ? "text-error" : undefined} />
      </div>
      <p className="text-xs text-on-surface-variant">
        Units needed {s.units_needed}
        {s.units_fulfilled ? ` · fulfilled ${s.units_fulfilled}` : ""}
        {s.units_reserved_inventory ? ` · reserved from blood banks ${s.units_reserved_inventory}` : ""}
        {` · donors required ${s.donors_still_needed}`}
      </p>

      {s.escalation.length > 0 && (
        <ul className="text-xs text-error space-y-1" data-testid="shortlist-escalation">
          {s.escalation.map((e) => (
            <li key={e}>• {e}</li>
          ))}
        </ul>
      )}

      {s.contacts.length > 0 && (
        <div>
          <h5 className="text-xs font-semibold text-on-surface-variant mb-1">Contacted donors</h5>
          <ul className="space-y-1.5">
            {s.contacts.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span>
                  #{c.rank_at_contact || "—"} {c.donor_name ?? `Donor ${c.donor_ref}`}
                  {c.blood_type && (
                    <span className="text-on-surface-variant"> · {BLOOD_LABEL[c.blood_type]}</span>
                  )}
                  {c.synthetic && (
                    <span className="ml-1 text-[10px] font-bold text-warning">SYNTHETIC</span>
                  )}
                  {c.simulated && <span className="ml-1 text-[10px] text-warning">(simulated answer)</span>}
                  {c.ranking_strategy && (
                    <span className="ml-1 text-[10px] text-on-surface-variant">
                      · ranked by {STRATEGY_LABEL[c.ranking_strategy as RankingStrategy] ?? c.ranking_strategy},
                      priority {Math.round(c.score_at_contact * 100)}/100 at contact
                    </span>
                  )}
                </span>
                <span className="flex items-center gap-2">
                  <span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${STATUS_STYLE[c.status] ?? "bg-surface-container-high text-on-surface"}`}>
                    {STATUS_LABEL[c.status]}
                  </span>
                  {c.status === "AWAITING_RESPONSE" && c.timeout_at && (
                    <span className="text-[11px] text-on-surface-variant">
                      until {new Date(c.timeout_at).toLocaleTimeString()}
                      {c.notification_state === "READ" ? " · seen" : " · sent, not yet seen"}
                    </span>
                  )}
                  {canManage && c.status === "ACCEPTED" && (
                    <button
                      className="text-[11px] font-semibold text-primary underline disabled:opacity-50"
                      disabled={confirm.isPending}
                      onClick={() => confirm.mutate(c.id)}
                    >
                      Confirm after re-check
                    </button>
                  )}
                </span>
                {c.status_reason && c.status !== "AWAITING_RESPONSE" && (
                  <span className="basis-full text-[11px] text-on-surface-variant">{c.status_reason}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {s.next_to_contact.length > 0 && (
        <div>
          <h5 className="text-xs font-semibold text-on-surface-variant mb-1">
            Next ranked candidates (not contacted — not confirmed)
          </h5>
          <ul className="space-y-1.5">
            {s.next_to_contact.map((c) => (
              <li key={c.donor_ref} className="text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span>
                    #{c.rank} {c.donor_name ?? `Donor ${c.donor_ref}`}
                    {c.blood_type && (
                      <span className="text-on-surface-variant"> · {BLOOD_LABEL[c.blood_type]}</span>
                    )}
                    {c.synthetic && (
                      <span className="ml-1 text-[10px] font-bold text-warning">SYNTHETIC</span>
                    )}
                  </span>
                  <span className="flex items-center gap-1 text-[11px] text-on-surface-variant">
                    {c.priority.score_available ? (
                      <span data-testid="priority-score">
                        priority {c.priority.missing_data_baseline == null ? "about " : ""}
                        {c.priority.score.toFixed(0)}/100
                      </span>
                    ) : (
                      <span data-testid="priority-score">priority not scored (too little data)</span>
                    )}
                    <span className={`px-1.5 rounded-full font-semibold ${EVIDENCE_STYLE[c.priority.evidence_level]}`}>
                      {c.priority.evidence_level.toLowerCase()} evidence
                    </span>
                  </span>
                </div>
                <p className="text-[11px] text-on-surface-variant">
                  {AVAILABILITY_LABEL[c.priority.availability_status]} · {c.explanation[0]}
                  {c.travel_minutes_estimate != null && ` · ~${c.travel_minutes_estimate} min`}
                  {" · next: "}
                  {c.next_action}
                </p>
                <PriorityBreakdown c={c} />
              </li>
            ))}
          </ul>
        </div>
      )}

      {s.state === "NO_ELIGIBLE" && Object.keys(s.excluded).length > 0 && (
        <p className="text-xs text-on-surface-variant" data-testid="shortlist-excluded">
          Not eligible:{" "}
          {Object.entries(s.excluded)
            .map(([k, n]) => `${n} ${EXCLUDED_LABEL[k] ?? k}`)
            .join(", ")}
          .
        </p>
      )}

      {canManage && s.is_open && (
        <div className="flex flex-wrap gap-2">
          <button
            className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-primary text-on-primary disabled:opacity-50"
            disabled={contact.isPending || s.counts.not_yet_contacted === 0 || s.remaining_unmet === 0}
            onClick={() => contact.mutate()}
          >
            {contact.isPending && <Loader2 className="inline h-3 w-3 animate-spin mr-1" />}
            Contact next ranked donors
          </button>
          {isAdmin && s.demo_mode && (
            <button
              className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-warning text-warning disabled:opacity-50"
              disabled={simulate.isPending || s.counts.awaiting_response === 0}
              onClick={() => simulate.mutate()}
            >
              Simulate synthetic answers (demo)
            </button>
          )}
        </div>
      )}
      {err && <p className="text-error text-xs">{toApiError(err).detail}</p>}
      <p className="text-[11px] text-on-surface-variant">{s.disclaimer}</p>
    </section>
  );
}
