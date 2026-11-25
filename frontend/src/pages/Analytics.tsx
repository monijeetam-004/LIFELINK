/**
 * Analytics -- LIVE demand forecasting.
 *
 * Replaces the former static "command center" mock-up (every number on it
 * was fabricated). Runs POST /forecasts on this deployment's own history:
 * hospitals forecast their facility's usage, administrators may forecast
 * the whole network. Each series shows its history, the forecast with an
 * approximate 80% band, and a backtest against naive baselines -- including
 * when the model does NOT beat them. Synthetic data is labelled as such.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import LivePageShell from "../components/live/LivePageShell";
import { toApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { createForecast, listForecasts, type ForecastRun, type ForecastSeries } from "../lib/gapApi";

function Chart({ s }: { s: ForecastSeries }) {
  const hist = s.history.slice(-42);
  const fc = s.forecast;
  const values = [...hist.map((h) => h.units), ...fc.map((f) => f.hi80)];
  const max = Math.max(1, ...values);
  const n = hist.length + fc.length;
  const W = 640;
  const H = 160;
  const x = (i: number) => (i / Math.max(1, n - 1)) * (W - 20) + 10;
  const y = (v: number) => H - 10 - (v / max) * (H - 20);
  const histPath = hist.map((h, i) => `${i ? "L" : "M"}${x(i)},${y(h.units)}`).join(" ");
  const band =
    fc.length > 0
      ? `M${fc.map((f, i) => `${x(hist.length + i)},${y(f.hi80)}`).join(" L")} L${fc
          .map((f, i) => `${x(hist.length + i)},${y(f.lo80)}`)
          .reverse()
          .join(" L")} Z`
      : "";
  const fcPath = fc.map((f, i) => `${i ? "L" : "M"}${x(hist.length + i)},${y(f.mean)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-40" role="img"
      aria-label={`${s.blood_type} ${s.component}: last ${hist.length} days of history and ${fc.length}-day forecast`}>
      <line x1="10" x2={W - 10} y1={H - 10} y2={H - 10} stroke="currentColor" strokeOpacity="0.2" />
      {band && <path d={band} fill="currentColor" fillOpacity="0.12" />}
      <path d={histPath} fill="none" stroke="currentColor" strokeWidth="2" />
      {fcPath && <path d={fcPath} fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray="5 4" />}
    </svg>
  );
}

/** Audit F9 (2026-10-31): both baselines the API backtests against are shown,
 * each with its own verdict. Before, only the naive verdict was stated. */
export function BacktestVerdict({ b }: { b: NonNullable<ForecastRun["results"][number]["backtest"]> }) {
  const verdict = (beats: boolean) => (beats ? "beats" : "does NOT beat");
  const both = b.beats_naive && b.beats_seasonal_naive;
  return (
    <div className="text-body-sm text-on-surface" data-testid="backtest-verdict">
      <p>
        Backtest over the last {b.holdout_days} day(s), mean absolute error (lower is better):
      </p>
      <table className="text-body-sm my-1">
        <tbody>
          <tr><td className="pr-3">Exponential smoothing (this model)</td><td>{b.ses_mae}</td><td /></tr>
          <tr>
            <td className="pr-3">Naive baseline (yesterday's value)</td><td>{b.naive_mae}</td>
            <td className="pl-3">model {verdict(b.beats_naive)} it</td>
          </tr>
          <tr>
            <td className="pr-3">Seasonal-naive baseline (same weekday last week)</td><td>{b.seasonal_naive_mae}</td>
            <td className="pl-3">model {verdict(b.beats_seasonal_naive)} it</td>
          </tr>
        </tbody>
      </table>
      <p className={both ? "" : "font-semibold"}>
        {both
          ? "The model beat both baselines on this holdout window (one window, synthetic or local data — not a general accuracy claim)."
          : "The model did not beat every baseline here — prefer the better baseline for planning."}
      </p>
    </div>
  );
}

function RunView({ run }: { run: ForecastRun }) {
  return (
    <section aria-label="Forecast run" className="flex flex-col gap-4">
      <p className="text-body-sm text-on-surface-variant">
        {run.method} · {run.history_days} days of history · {run.horizon_days}-day horizon · signal:{" "}
        {String(run.parameters.signal)} · {run.data_is_synthetic ? "SYNTHETIC data" : "data declared real"} ·{" "}
        {new Date(run.created_at).toLocaleString()}
      </p>
      <p role="note" className="text-body-sm">{run.disclaimer}</p>
      {run.results.length === 0 && <p>No demand was recorded in this window.</p>}
      {run.results.map((s) => (
        <article key={`${s.blood_type}-${s.component}`} className="rounded-xl border border-border-subtle p-4 text-primary">
          <h3 className="font-headline-sm text-headline-sm text-on-surface">
            {s.blood_type.replace("_", " ")} · {s.component.replace("_", " ").toLowerCase()} — {s.total_units} unit(s) in window
          </h3>
          {s.status === "OK" ? (
            <>
              <Chart s={s} />
              <p className="text-body-sm text-on-surface">
                Next day ≈ {s.forecast[0]?.mean} unit(s) (80%: {s.forecast[0]?.lo80}–{s.forecast[0]?.hi80}); α = {s.alpha}.
              </p>
              {s.backtest ? (
                <BacktestVerdict b={s.backtest} />
              ) : (
                <p className="text-body-sm text-on-surface-variant">
                  No backtest: not enough history to hold out a test window, so no claim is made that
                  this forecast beats either baseline.
                </p>
              )}
            </>
          ) : (
            <p className="text-body-sm text-on-surface">{s.note}</p>
          )}
        </article>
      ))}
    </section>
  );
}

export default function Analytics() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [historyDays, setHistoryDays] = useState(56);
  const [horizon, setHorizon] = useState(7);
  const [network, setNetwork] = useState(false);
  const runs = useQuery({ queryKey: ["forecasts"], queryFn: listForecasts, enabled: !!user });
  const make = useMutation({
    mutationFn: () => createForecast({ history_days: historyDays, horizon_days: horizon, network }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["forecasts"] }),
  });
  const latest = make.data ?? runs.data?.[0];
  const field = "border border-border-subtle rounded-lg px-3 py-2 bg-surface-container-lowest";
  return (
    <LivePageShell
      title="Demand analytics"
      roles={["HOSPITAL", "ADMIN"]}
      intro={
        <>
          Demand forecasting from this deployment's own records. Operational dashboards with live counts
          are on the <Link className="text-primary underline" to={user?.role === "ADMIN" ? "/admin/analytics" : "/hospital"}>main dashboard</Link>.
        </>
      }
    >
      <form
        className="flex flex-wrap gap-4 items-end rounded-xl border border-border-subtle p-4"
        onSubmit={(e) => { e.preventDefault(); make.mutate(); }}
      >
        <label className="flex flex-col gap-1 text-body-sm">History (days)
          <input className={field} type="number" min={14} max={730} value={historyDays} onChange={(e) => setHistoryDays(Number(e.target.value))} />
        </label>
        <label className="flex flex-col gap-1 text-body-sm">Horizon (days)
          <input className={field} type="number" min={1} max={60} value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} />
        </label>
        {user?.role === "ADMIN" && (
          <label className="flex items-center gap-2 text-body-sm">
            <input type="checkbox" checked={network} onChange={(e) => setNetwork(e.target.checked)} /> Whole network
          </label>
        )}
        <button className="bg-primary text-on-primary px-5 py-2 rounded-lg" type="submit" disabled={make.isPending}>
          {make.isPending ? "Forecasting…" : "Run forecast"}
        </button>
      </form>
      {(make.error || runs.error) && <p role="alert" className="text-error">{toApiError(make.error ?? runs.error).detail}</p>}
      {latest && <RunView run={latest} />}
    </LivePageShell>
  );
}
