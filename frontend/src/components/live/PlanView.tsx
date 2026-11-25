import {
  COMPONENT_LABEL,
  FEASIBILITY_STYLE,
  type CandidatePlan,
  type FacilityOption,
  type PlanResult,
} from "../../lib/gapApi";

const BASIS_LABEL: Record<string, string> = {
  LOCAL: "Own stock (no transport)",
  ROUTED: "Recorded transport route(s)",
  STRAIGHT_LINE_ESTIMATE: "Straight-line ESTIMATE (no recorded route)",
};

function short(id: string) {
  return id.slice(0, 8);
}

export function FeasibilityBadge({ value }: { value: CandidatePlan["feasibility"] }) {
  return (
    <span className={`inline-block border rounded-full px-3 py-0.5 text-label-sm font-bold ${FEASIBILITY_STYLE[value]}`}>
      {value.replace("_", " ")}
    </span>
  );
}

export function CandidatePlanCard({ plan, title, names }: {
  plan: CandidatePlan;
  title: string;
  names: Record<string, string>;
}) {
  return (
    <article className="rounded-xl border border-border-subtle bg-surface-container-lowest p-4 flex flex-col gap-3">
      <header className="flex flex-wrap items-center gap-3 justify-between">
        <h3 className="font-headline-sm text-headline-sm">{title}</h3>
        <FeasibilityBadge value={plan.feasibility} />
      </header>
      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-body-sm">
        <div><dt className="text-on-surface-variant">Units</dt><dd>{plan.units_planned} / {plan.units_requested}</dd></div>
        <div><dt className="text-on-surface-variant">ETA</dt><dd>{plan.eta_minutes != null ? `${plan.eta_minutes} min` : "—"}</dd></div>
        <div><dt className="text-on-surface-variant">Slack</dt><dd>{plan.slack_minutes != null ? `${plan.slack_minutes} min` : "no deadline"}</dd></div>
        <div><dt className="text-on-surface-variant">Legs</dt><dd>{plan.legs.length}</dd></div>
      </dl>
      {plan.legs.map((leg, i) => (
        <div key={i} className="rounded-lg bg-surface-container-low p-3 text-body-sm">
          <p className="font-label-md">
            Leg {i + 1}: {leg.facility_name ?? short(leg.facility_id)} — {leg.units} unit(s){" "}
            ({Object.entries(leg.units_by_type).map(([t, n]) => `${n} ${t}`).join(", ")})
          </p>
          <p className="text-on-surface-variant">{BASIS_LABEL[leg.route_basis ?? ""] ?? leg.route_basis}</p>
          {leg.node_ids.length > 1 && (
            <p className="mt-1 break-words">
              Path: {leg.node_ids.map((n) => names[n] ?? short(n)).join(" → ")}
              {leg.route_ids.length > 0 && (
                <span className="text-on-surface-variant"> (routes {leg.route_ids.map(short).join(", ")})</span>
              )}
            </p>
          )}
        </div>
      ))}
      {plan.explanation.length > 0 && (
        <ul className="list-disc pl-5 text-body-sm text-on-surface-variant">
          {plan.explanation.map((line, i) => <li key={i}>{line}</li>)}
        </ul>
      )}
    </article>
  );
}

export function OptionsTable({ options, names }: { options: FacilityOption[]; names: Record<string, string> }) {
  return (
    <div tabIndex={0} role="region" aria-label="Facilities evaluated (scrollable)" className="overflow-x-auto rounded-xl border border-border-subtle focus:outline-none focus:ring-2 focus:ring-primary/40">
      <table className="min-w-full text-body-sm">
        <caption className="sr-only">Every facility the planner evaluated, including rejected ones</caption>
        <thead className="bg-surface-container-low text-left">
          <tr>
            <th scope="col" className="px-3 py-2">Facility</th>
            <th scope="col" className="px-3 py-2">Usable units</th>
            <th scope="col" className="px-3 py-2">Basis</th>
            <th scope="col" className="px-3 py-2">Route km</th>
            <th scope="col" className="px-3 py-2">Straight-line km</th>
            <th scope="col" className="px-3 py-2">ETA</th>
            <th scope="col" className="px-3 py-2">Decision</th>
          </tr>
        </thead>
        <tbody>
          {options.map((o) => (
            <tr key={o.facility_id} className="border-t border-border-subtle">
              <td className="px-3 py-2">{o.facility_name ?? names[o.facility_id] ?? short(o.facility_id)}{!o.verified && " (unverified)"}</td>
              <td className="px-3 py-2">{o.usable_total}</td>
              <td className="px-3 py-2">{o.route_basis ?? "—"}</td>
              <td className="px-3 py-2">{o.distance_km != null ? o.distance_km.toFixed(1) : "—"}</td>
              <td className="px-3 py-2">{o.straight_line_km != null ? o.straight_line_km.toFixed(1) : "—"}</td>
              <td className="px-3 py-2">{o.eta_minutes != null ? `${o.eta_minutes} min` : "—"}</td>
              <td className="px-3 py-2">{o.rejected_reason ? `Rejected: ${o.rejected_reason}` : "Eligible"}</td>
            </tr>
          ))}
          {options.length === 0 && (
            <tr><td colSpan={7} className="px-3 py-4 text-on-surface-variant">No facility holds a compatible, in-date unit of this component.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export default function PlanView({ result }: { result: PlanResult }) {
  const names: Record<string, string> = {};
  for (const o of result.options) if (o.facility_name) names[o.facility_id] = o.facility_name;
  return (
    <section aria-label="Planner result" className="flex flex-col gap-4">
      <div className="rounded-xl border border-border-subtle bg-surface-container-lowest p-4 text-body-sm flex flex-col gap-1">
        <p>
          <strong>Strategy:</strong> {result.strategy} — {result.strategy_description}
        </p>
        <p>
          <strong>Compatible source types</strong> for a {result.recipient_blood_type.replace("_", " ")} patient
          ({COMPONENT_LABEL[result.component]}): {result.compatible_types.join(", ") || "none"}
        </p>
        <p>
          <strong>Path engine:</strong> {result.pathway_source}
          {result.degraded_reason && <span className="text-on-surface-variant"> — {result.degraded_reason}</span>}
          {result.disrupted_routes_excluded > 0 && ` · ${result.disrupted_routes_excluded} disrupted route(s) excluded`}
          {result.simulated_failed_route_ids.length > 0 && ` · ${result.simulated_failed_route_ids.length} route(s) failed in this simulation`}
        </p>
        <p className="text-on-surface-variant">
          Assumptions: handling {String(result.assumptions.handling_minutes)} min per delivery, deadline-risk margin{" "}
          {String(result.assumptions.deadline_risk_minutes)} min, at most {String(result.assumptions.max_hops)} hops,
          straight-line estimates at {String(result.assumptions.straight_line_speed_kmph)} km/h.
        </p>
      </div>
      <CandidatePlanCard plan={result.primary} title="Primary plan" names={names} />
      {result.backups.map((b, i) => (
        <CandidatePlanCard key={i} plan={b} title={`Backup ${i + 1}`} names={names} />
      ))}
      <h3 className="font-headline-sm text-headline-sm">Every facility evaluated</h3>
      <OptionsTable options={result.options} names={names} />
    </section>
  );
}
