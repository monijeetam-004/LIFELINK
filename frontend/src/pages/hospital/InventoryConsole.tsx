/**
 * Full per-blood-type inventory editor.
 *
 * Adapted from the Stitch export
 * `lifelink_3.1_hospital_full_inventory_cold_storage_console/code.html`, but
 * every fabricated cold-chain/telemetry element from that mock (cryo-sensor
 * temperatures, agitator readings, "avg cross-match time", consumption
 * trends, "safe window" days) has been removed — none of it exists in the
 * backend. Real data sources: `GET /hospitals/me/inventory` (now
 * `{ blood_type, units, reserved_units, available_units }[]`, one row per
 * one of the 8 real ABO/Rh types), the six real audited action endpoints
 * (`POST /hospitals/me/inventory/{add,reserve,allocate,release,expire,
 * reconcile}`), and `GET /hospitals/me/inventory/log` for the real,
 * append-only action history. Status tiers (Critical/Low/Optimal) are a
 * client-side convenience threshold, clearly labeled as such — see
 * `inventoryStatus.ts`.
 */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import HospitalSubNav from "./HospitalSubNav";
import Modal from "../../components/Modal";
import { CRITICAL_BELOW, LOW_BELOW, STOCK_TIER_STYLE, stockTier, type StockTier } from "./inventoryStatus";
import { toApiError } from "../../lib/api";
import {
  BLOOD_COMPONENTS,
  BLOOD_LABEL,
  BLOOD_TYPES,
  COMPONENT_LABEL,
  getComponentInventory,
  getInventory,
  getInventoryLog,
  inventoryAction,
  reconcileInventoryOne,
  type BloodComponent,
  type BloodType,
  type ComponentInventoryItem,
  type HospitalInventoryLogEntry,
  type InventoryActionKind,
} from "../../lib/domain";

type SortMode = "criticality" | "units-asc" | "units-desc" | "alpha";
type FilterMode = "ALL" | StockTier;

const ACTION_LABEL: Record<InventoryActionKind, string> = {
  add: "Add stock", reserve: "Reserve", allocate: "Allocate", release: "Release", expire: "Expire",
};
const ACTION_ICON: Record<InventoryActionKind, string> = {
  add: "add_circle", reserve: "bookmark_added", allocate: "local_shipping",
  release: "bookmark_remove", expire: "delete_history",
};
const ACTION_HINT: Record<InventoryActionKind, string> = {
  add: "New stock received — increases units on hand.",
  reserve: "Earmark on-hand units for a request without dispatching yet.",
  allocate: "Physically dispatch units — decreases units on hand.",
  release: "Give up a reservation without dispatching it.",
  expire: "Remove unusable units — decreases units on hand.",
};
const LOG_ACTION_LABEL: Record<string, string> = {
  ADD: "Added", RESERVE: "Reserved", ALLOCATE: "Allocated", RELEASE: "Released",
  EXPIRE: "Expired", RECONCILE: "Reconciled",
};

export default function InventoryConsole() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["hospital", "inventory"], queryFn: getInventory });
  const log = useQuery({ queryKey: ["hospital", "inventory", "log"], queryFn: () => getInventoryLog(20) });
  // Phase G: every component this facility actually holds. The per-type
  // grid below is the legacy whole-blood view; without this, plasma /
  // platelet / red-cell stock (which shipments can now carry) was
  // invisible in the console.
  const components = useQuery({
    queryKey: ["hospital", "inventory", "components"],
    queryFn: getComponentInventory,
  });

  const [draft, setDraft] = useState<Record<string, number>>({});
  const [filter, setFilter] = useState<FilterMode>("ALL");
  const [sort, setSort] = useState<SortMode>("criticality");
  const [error, setError] = useState("");
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [actionFor, setActionFor] = useState<{ bt: BloodType; kind: InventoryActionKind } | null>(null);
  const [actionUnits, setActionUnits] = useState(1);
  const [actionComponent, setActionComponent] = useState<BloodComponent>("WHOLE_BLOOD");
  const [actionNote, setActionNote] = useState("");
  const [actionError, setActionError] = useState("");

  useEffect(() => {
    if (q.data) setDraft(Object.fromEntries(q.data.map((i) => [i.blood_type, i.units])));
  }, [q.data]);

  const save = useMutation({
    mutationFn: (item: { blood_type: BloodType; units: number }) => reconcileInventoryOne(item),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hospital"] });
      setError("");
      setSavedAt(Date.now());
    },
    onError: (e) => setError(toApiError(e).detail),
  });

  const runAction = useMutation({
    mutationFn: () => {
      if (!actionFor) throw new Error("no action selected");
      return inventoryAction(actionFor.kind, {
        blood_type: actionFor.bt, units: actionUnits, note: actionNote.trim() || undefined,
        component: actionComponent,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hospital"] });
      setActionFor(null);
      setActionNote("");
      setActionError("");
    },
    onError: (e) => setActionError(toApiError(e).detail),
  });

  const items = q.data ?? [];
  const totalUnits = items.reduce((n, i) => n + i.units, 0);
  const tierCounts = useMemo(() => {
    const counts: Record<StockTier, number> = { CRITICAL: 0, LOW: 0, OPTIMAL: 0 };
    for (const i of items) counts[stockTier(i.units)]++;
    return counts;
  }, [items]);

  const dirtyEntries = useMemo(
    () =>
      items
        .filter((i) => draft[i.blood_type] !== undefined && draft[i.blood_type] !== i.units)
        .map((i) => ({ blood_type: i.blood_type, units: draft[i.blood_type] })),
    [items, draft],
  );

  const visible = useMemo(() => {
    let list = items.slice();
    if (filter !== "ALL") list = list.filter((i) => stockTier(i.units) === filter);
    switch (sort) {
      case "units-asc":
        list.sort((a, b) => a.units - b.units);
        break;
      case "units-desc":
        list.sort((a, b) => b.units - a.units);
        break;
      case "alpha":
        list.sort((a, b) => BLOOD_LABEL[a.blood_type].localeCompare(BLOOD_LABEL[b.blood_type]));
        break;
      case "criticality":
      default: {
        const rank: Record<StockTier, number> = { CRITICAL: 0, LOW: 1, OPTIMAL: 2 };
        list.sort((a, b) => rank[stockTier(a.units)] - rank[stockTier(b.units)] || a.units - b.units);
        break;
      }
    }
    return list;
  }, [items, filter, sort]);

  const maxUnits = Math.max(1, ...items.map((i) => i.units));

  function setUnits(bt: string, units: number) {
    setDraft((d) => ({ ...d, [bt]: Math.max(0, Math.round(units) || 0) }));
  }

  function saveOne(bt: BloodType) {
    setError("");
    save.mutate({ blood_type: bt, units: draft[bt] ?? 0 });
  }

  async function saveAllChanges() {
    if (dirtyEntries.length === 0) return;
    setError("");
    try {
      // Reconciled sequentially, one real audited action per changed type
      // (not a single bulk write) — matches POST /inventory/reconcile.
      for (const entry of dirtyEntries) {
        await save.mutateAsync(entry);
      }
      setSavedAt(Date.now());
    } catch (e) {
      setError(toApiError(e).detail);
    }
  }

  return (
    <div className="min-h-screen bg-surface">
      <HospitalSubNav title="Blood Inventory" subtitle="Per-type unit counts, editable in place" />

      <main className="px-4 sm:px-6 lg:px-10 py-6 max-w-container-max mx-auto flex flex-col gap-6">
        <p className="text-body-sm">
          Need to thaw plasma, quarantine or discard a specific unit?{" "}
          <Link to="/hospital/units" className="text-primary underline">Open the per-unit view</Link>.
        </p>
        <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-4">
          <div>
            <h1 className="font-headline-lg text-headline-lg text-on-surface">Blood Product Inventory</h1>
            <p className="font-body-md text-body-md text-on-surface-variant mt-1 max-w-2xl">
              Real per-type whole-blood unit counts from your hospital's inventory record (other
              components are listed under “Stock by component” below). Edit a count and save it —
              changes are written straight to <code className="font-data-mono text-label-sm">/hospitals/me/inventory</code>.
            </p>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            {savedAt && !save.isPending && (
              <span className="text-success text-body-sm flex items-center gap-1">
                <span aria-hidden="true" className="material-symbols-outlined text-[18px]">check_circle</span>
                Saved
              </span>
            )}
            <button
              type="button"
              className="h-10 px-5 rounded-lg bg-primary hover:bg-primary-container text-on-primary font-label-md text-label-md transition-all disabled:opacity-50 flex items-center gap-2"
              disabled={dirtyEntries.length === 0 || save.isPending}
              onClick={saveAllChanges}
            >
              {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              Save changes{dirtyEntries.length > 0 ? ` (${dirtyEntries.length})` : ""}
            </button>
          </div>
        </div>

        {error && (
          <p role="alert" className="text-error text-body-sm bg-error-container/40 rounded-lg px-4 py-2">
            {error}
          </p>
        )}

        {/* Real KPI summary */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="bg-surface-container-lowest p-4 rounded-xl border border-border-subtle shadow-sm">
            <p className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
              Total units
            </p>
            <p className="font-headline-lg text-headline-lg text-on-surface mt-1">
              {q.isLoading ? "—" : totalUnits.toLocaleString()}
            </p>
          </div>
          <div className="bg-surface-container-lowest p-4 rounded-xl border border-border-subtle shadow-sm">
            <p className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
              Critical (&lt; {CRITICAL_BELOW})
            </p>
            <p className="font-headline-lg text-headline-lg text-error mt-1">
              {q.isLoading ? "—" : tierCounts.CRITICAL}
            </p>
          </div>
          <div className="bg-surface-container-lowest p-4 rounded-xl border border-border-subtle shadow-sm">
            <p className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
              Low (&lt; {LOW_BELOW})
            </p>
            <p className="font-headline-lg text-headline-lg text-warning mt-1">
              {q.isLoading ? "—" : tierCounts.LOW}
            </p>
          </div>
          <div className="bg-surface-container-lowest p-4 rounded-xl border border-border-subtle shadow-sm">
            <p className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
              Optimal
            </p>
            <p className="font-headline-lg text-headline-lg text-success mt-1">
              {q.isLoading ? "—" : tierCounts.OPTIMAL}
            </p>
          </div>
        </div>

        {/* Filter + sort bar */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-surface-container-lowest p-3 rounded-xl border border-border-subtle shadow-sm">
          <div className="flex items-center gap-2 overflow-x-auto">
            {(["ALL", "CRITICAL", "LOW", "OPTIMAL"] as FilterMode[]).map((f) => (
              <button
                key={f}
                type="button"
                aria-pressed={filter === f}
                onClick={() => setFilter(f)}
                className={`h-9 px-3 rounded-lg font-label-sm text-label-sm font-semibold whitespace-nowrap transition-all ${
                  filter === f
                    ? "bg-primary-container text-on-primary"
                    : "bg-surface-container text-on-surface-variant hover:bg-surface-container-high"
                }`}
              >
                {f === "ALL" ? `All (${items.length})` : `${STOCK_TIER_STYLE[f].label} (${tierCounts[f]})`}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-2 shrink-0">
            <span className="font-label-sm text-label-sm text-on-surface-variant">Sort by</span>
            <select
              className="h-9 px-3 rounded-lg bg-surface-container-low text-on-surface font-label-sm text-label-sm outline-none focus:ring-2 focus:ring-primary/40"
              value={sort}
              onChange={(e) => setSort(e.target.value as SortMode)}
            >
              <option value="criticality">Criticality</option>
              <option value="units-asc">Units: low to high</option>
              <option value="units-desc">Units: high to low</option>
              <option value="alpha">ABO/Rh type</option>
            </select>
          </label>
        </div>

        {/* Blood product grid */}
        {q.isLoading && (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
            {BLOOD_TYPES.map((bt) => (
              <div
                key={bt}
                className="h-48 rounded-xl border border-border-subtle bg-surface-container-lowest animate-pulse"
              />
            ))}
          </div>
        )}

        {q.isError && (
          <p className="text-error text-body-sm" role="alert">
            {toApiError(q.error).detail}{" "}
            <button type="button" onClick={() => q.refetch()} className="underline font-label-md">
              Retry
            </button>
          </p>
        )}

        {!q.isLoading && !q.isError && visible.length === 0 && (
          <p className="text-on-surface-variant text-body-sm px-1">No blood types match this filter.</p>
        )}

        {!q.isLoading && !q.isError && visible.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
            {visible.map((i) => {
              const tier = stockTier(i.units);
              const style = STOCK_TIER_STYLE[tier];
              const draftUnits = draft[i.blood_type] ?? i.units;
              const dirty = draftUnits !== i.units;
              const barPct = Math.max(4, Math.round((i.units / maxUnits) * 100));
              return (
                <div
                  key={i.blood_type}
                  className="flex flex-col justify-between rounded-xl bg-surface-container-lowest p-5 shadow-sm border border-border-subtle relative overflow-hidden"
                >
                  <div className={`absolute top-0 left-0 right-0 h-1.5 ${style.bar}`} />
                  <div>
                    <div className="flex items-start justify-between gap-2 mb-3">
                      <div className="flex items-center gap-3">
                        <div className="w-12 h-12 rounded-xl bg-primary-container text-on-primary flex items-center justify-center font-headline-md text-headline-md font-extrabold shadow-sm">
                          {BLOOD_LABEL[i.blood_type]}
                        </div>
                        <div>
                          <h3 className="font-title-lg text-title-lg text-on-surface leading-snug">
                            {BLOOD_LABEL[i.blood_type]}
                          </h3>
                        </div>
                      </div>
                      <span
                        className={`px-2 py-1 rounded-full text-label-sm font-semibold flex items-center gap-1 shrink-0 ${style.badge}`}
                      >
                        <span className={`w-1.5 h-1.5 rounded-full ${style.dot}`} />
                        {style.label}
                      </span>
                    </div>

                    <div className="my-3 p-3 rounded-lg bg-surface-container-low space-y-2">
                      <div className="flex items-baseline justify-between">
                        <span className="font-label-sm text-label-sm text-on-surface-variant">In stock</span>
                        <span className="font-headline-lg text-headline-lg font-bold text-on-surface">
                          {i.units} <span className="font-body-sm text-body-sm text-on-surface-variant">units</span>
                        </span>
                      </div>
                      <div className="w-full h-2 rounded-full bg-surface-container-high overflow-hidden">
                        <div className={`h-full rounded-full ${style.bar}`} style={{ width: `${barPct}%` }} />
                      </div>
                      <div className="flex items-center justify-between font-label-sm text-label-sm text-on-surface-variant">
                        <span>{i.reserved_units} reserved</span>
                        <span>{i.available_units} available to promise</span>
                      </div>
                    </div>

                    {/* Real audited actions — each is its own endpoint, not
                        a fake decoration (see inventory_service.py). */}
                    <div className="grid grid-cols-3 gap-1.5 mb-3">
                      {(["add", "reserve", "allocate", "release", "expire"] as const).map((kind) => (
                        <button
                          key={kind}
                          type="button"
                          title={ACTION_HINT[kind]}
                          onClick={() => {
                            setActionFor({ bt: i.blood_type, kind });
                            setActionUnits(1);
                            setActionComponent("WHOLE_BLOOD");
                            setActionNote("");
                            setActionError("");
                          }}
                          className="h-9 rounded-lg bg-surface-container hover:bg-surface-container-high text-on-surface-variant hover:text-on-surface font-label-sm text-label-sm font-semibold transition-colors flex items-center justify-center gap-1 px-1"
                        >
                          <span aria-hidden="true" className="material-symbols-outlined text-[16px]">{ACTION_ICON[kind]}</span>
                          {ACTION_LABEL[kind]}
                        </button>
                      ))}
                    </div>

                    <label className="block">
                      <span className={STOCK_LABEL_CLASS}>Reconcile (stock-take correction)</span>
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          min={0}
                          aria-label={`Reconcile units in stock for ${BLOOD_LABEL[i.blood_type]}`}
                          className="w-full px-3 py-2 rounded-lg border border-outline-variant bg-surface text-on-surface text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
                          value={draftUnits}
                          onChange={(e) => setUnits(i.blood_type, Number(e.target.value))}
                        />
                      </div>
                    </label>
                  </div>

                  <div className="pt-3 flex items-center gap-2">
                    <button
                      type="button"
                      className="flex-1 h-9 rounded-lg bg-surface-container hover:bg-surface-container-high text-on-surface font-label-md text-label-md font-semibold transition-colors disabled:opacity-40"
                      disabled={!dirty || save.isPending}
                      onClick={() => setUnits(i.blood_type, i.units)}
                    >
                      Reset
                    </button>
                    <button
                      type="button"
                      className="flex-1 h-9 rounded-lg bg-primary-container hover:bg-primary text-on-primary font-label-md text-label-md font-semibold transition-colors disabled:opacity-40 flex items-center justify-center gap-1"
                      disabled={!dirty || save.isPending}
                      onClick={() => saveOne(i.blood_type)}
                    >
                      {save.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                      Save
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Phase G: real per-component stock — GET /hospitals/me/inventory/components */}
        <section
          className="bg-surface-container-lowest rounded-xl border border-border-subtle shadow-sm p-5"
          aria-labelledby="component-stock-heading"
        >
          <h2 id="component-stock-heading" className="font-title-lg text-title-lg text-on-surface mb-1">
            Stock by component
          </h2>
          <p className="text-body-sm text-on-surface-variant mb-3">
            The grid above is whole blood. Every component this facility holds is listed here; use
            an action's component selector to add or move plasma, platelets or red cells.
          </p>
          {components.isLoading && (
            <div className="flex items-center gap-2 text-on-surface-variant py-4">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading…
            </div>
          )}
          {components.isError && (
            <p role="alert" className="text-error text-body-sm">
              Couldn't load component stock. {toApiError(components.error).detail}{" "}
              <button type="button" onClick={() => components.refetch()} className="underline font-label-md">
                Retry
              </button>
            </p>
          )}
          {components.data && components.data.length === 0 && (
            <p className="text-body-sm text-on-surface-variant">No stock recorded for any component yet.</p>
          )}
          {components.data && components.data.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-body-sm" data-testid="component-stock">
                <thead>
                  <tr className="text-on-surface-variant font-label-sm text-label-sm uppercase tracking-wider">
                    <th className="py-2 pr-3">Blood type</th>
                    <th className="py-2 pr-3">Component</th>
                    <th className="py-2 pr-3 text-right">On hand</th>
                    <th className="py-2 pr-3 text-right">Reserved</th>
                    <th className="py-2 text-right">Available</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {components.data.map((c: ComponentInventoryItem) => (
                    <tr key={`${c.blood_type}-${c.component}`}>
                      <td className="py-2 pr-3 font-semibold">{BLOOD_LABEL[c.blood_type]}</td>
                      <td className="py-2 pr-3">{COMPONENT_LABEL[c.component]}</td>
                      <td className="py-2 pr-3 text-right font-data-mono">{c.units}</td>
                      <td className="py-2 pr-3 text-right font-data-mono">{c.reserved_units}</td>
                      <td className="py-2 text-right font-data-mono">{c.available_units}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Real, append-only recent activity — GET /hospitals/me/inventory/log */}
        <section className="bg-surface-container-lowest rounded-xl border border-border-subtle shadow-sm p-5">
          <h2 className="font-title-lg text-title-lg text-on-surface mb-3">Recent inventory activity</h2>
          {log.isLoading && (
            <div className="flex items-center gap-2 text-on-surface-variant py-4">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading…
            </div>
          )}
          {log.isError && (
            <p role="alert" className="text-error text-body-sm">
              Couldn't load recent activity. {toApiError(log.error).detail}{" "}
              <button type="button" onClick={() => log.refetch()} className="underline font-label-md">
                Retry
              </button>
            </p>
          )}
          {log.data && log.data.length === 0 && (
            <p className="text-body-sm text-on-surface-variant">No inventory actions recorded yet.</p>
          )}
          {log.data && log.data.length > 0 && (
            <ul className="divide-y divide-border-subtle">
              {log.data.map((entry: HospitalInventoryLogEntry) => (
                <li key={entry.id} className="py-2.5 flex items-center justify-between gap-3 text-body-sm">
                  <div className="min-w-0">
                    <span className="font-semibold text-on-surface">
                      {LOG_ACTION_LABEL[entry.action] ?? entry.action}
                    </span>{" "}
                    <span className="text-on-surface-variant">
                      {BLOOD_LABEL[entry.blood_type]}
                      {entry.delta_units !== 0
                        ? ` · ${entry.delta_units > 0 ? "+" : ""}${entry.delta_units} unit(s) on hand`
                        : ""}
                      {entry.delta_reserved !== 0
                        ? ` · ${entry.delta_reserved > 0 ? "+" : ""}${entry.delta_reserved} reserved`
                        : ""}
                    </span>
                    {entry.note && <p className="text-on-surface-variant text-[12px] mt-0.5">{entry.note}</p>}
                  </div>
                  <span className="text-on-surface-variant text-[11px] shrink-0">
                    {new Date(entry.created_at).toLocaleString()}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>

      {/* Action prompt for add/reserve/allocate/release/expire. Phase G: uses
          the shared accessible Modal (role="dialog", focus trap, Escape,
          focus restore) instead of a hand-rolled overlay without any of those. */}
      <Modal
        open={!!actionFor}
        onClose={() => !runAction.isPending && setActionFor(null)}
        title={actionFor ? `${ACTION_LABEL[actionFor.kind]} — ${BLOOD_LABEL[actionFor.bt]}` : ""}
      >
        {actionFor && (
          <div>
            <p className="text-body-sm text-on-surface-variant mb-3">{ACTION_HINT[actionFor.kind]}</p>
            <label className="block mb-3">
              <span className={STOCK_LABEL_CLASS}>Component</span>
              <select
                className="w-full px-3 py-2 rounded-lg border border-outline-variant bg-surface text-on-surface text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
                value={actionComponent}
                onChange={(e) => setActionComponent(e.target.value as BloodComponent)}
              >
                {BLOOD_COMPONENTS.map((c) => (
                  <option key={c} value={c}>{COMPONENT_LABEL[c]}</option>
                ))}
              </select>
            </label>
            <label className="block mb-3">
              <span className={STOCK_LABEL_CLASS}>Units</span>
              <input
                type="number"
                min={1}
                className="w-full px-3 py-2 rounded-lg border border-outline-variant bg-surface text-on-surface text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
                value={actionUnits}
                onChange={(e) => setActionUnits(Math.max(1, Math.round(Number(e.target.value)) || 1))}
              />
            </label>
            <label className="block mb-3">
              <span className={STOCK_LABEL_CLASS}>Note (optional)</span>
              <input
                type="text"
                className="w-full px-3 py-2 rounded-lg border border-outline-variant bg-surface text-on-surface text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
                value={actionNote}
                onChange={(e) => setActionNote(e.target.value)}
                placeholder="e.g. Delivery from regional blood bank"
              />
            </label>
            {actionError && (
              <p role="alert" className="text-error text-body-sm mb-3">{actionError}</p>
            )}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                className="h-9 px-4 rounded-lg bg-surface-container hover:bg-surface-container-high text-on-surface font-label-md text-label-md"
                disabled={runAction.isPending}
                onClick={() => setActionFor(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="h-9 px-4 rounded-lg bg-primary hover:bg-primary-container text-on-primary font-label-md text-label-md flex items-center gap-2 disabled:opacity-50"
                disabled={runAction.isPending}
                onClick={() => runAction.mutate()}
              >
                {runAction.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                Confirm
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

const STOCK_LABEL_CLASS =
  "block text-label-sm font-label-sm uppercase tracking-wider text-on-surface-variant mb-1.5";
