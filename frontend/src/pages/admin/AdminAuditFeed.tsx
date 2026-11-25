/**
 * Admin — Audit Feed (Phase 6)
 *
 * A dedicated, filterable view over the same real audit_logs table the
 * Overview screen's "recent activity" panel already reads from
 * (GET /admin/audit-log). Phase 6 extended that endpoint with target_type,
 * actor_role, since/until and severity filters -- `severity` is derived
 * from the action name at read time (see admin.py:_classify_severity), it
 * is never a separately-stored fact that could drift from the action.
 */
import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Menu } from "lucide-react";
import { useMutation, useQuery } from "@tanstack/react-query";

import NotificationsBell from "../../components/NotificationsBell";
import { useAuth } from "../../lib/auth";
import { toApiError } from "../../lib/api";
import { ROLE_LABEL } from "../../lib/roles";
import {
  listAdminAuditLog,
  verifyAuditChain,
  type AuditChainVerifyResult,
  type UserRole,
} from "../../lib/domain";

import { ADMIN_NAV, ACCOUNT_NAV } from "./adminNav";
import MobileNavDrawer from "../../components/MobileNavDrawer";

const SEVERITY_FILTERS: { label: string; value: "INFO" | "WARNING" | "CRITICAL" | undefined }[] = [
  { label: "Any severity", value: undefined },
  { label: "Info", value: "INFO" },
  { label: "Warning", value: "WARNING" },
  { label: "Critical", value: "CRITICAL" },
];

const ROLE_FILTERS: { label: string; value: UserRole | undefined }[] = [
  { label: "Any role", value: undefined },
  { label: "Patient", value: "PATIENT" },
  { label: "Family", value: "FAMILY" },
  { label: "Donor", value: "DONOR" },
  { label: "Hospital", value: "HOSPITAL" },
  { label: "Admin", value: "ADMIN" },
];

const SEVERITY_STYLE: Record<string, string> = {
  INFO: "bg-surface-container text-on-surface-variant",
  WARNING: "bg-tertiary-container text-on-tertiary-container",
  CRITICAL: "bg-error/15 text-error",
};

function toDateInputValue(iso: string | undefined): string {
  return iso ? iso.slice(0, 10) : "";
}

export default function AdminAuditFeed() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  const [action, setAction] = useState("");
  const [targetType, setTargetType] = useState("");
  const [actorRole, setActorRole] = useState<UserRole | undefined>(undefined);
  const [severity, setSeverity] = useState<"INFO" | "WARNING" | "CRITICAL" | undefined>(undefined);
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [cursor, setCursor] = useState<string | undefined>(undefined);
  const [cursorStack, setCursorStack] = useState<(string | undefined)[]>([]);

  const filters = {
    action: action || undefined,
    target_type: targetType || undefined,
    actor_role: actorRole,
    severity,
    since: since ? new Date(since).toISOString() : undefined,
    until: until ? new Date(until).toISOString() : undefined,
    limit: 25,
  };

  const feed = useQuery({
    queryKey: ["admin", "audit-log", "feed", filters, cursor],
    queryFn: () => listAdminAuditLog({ ...filters, cursor }),
  });

  function resetAndRefilter() {
    setCursor(undefined);
    setCursorStack([]);
  }

  async function handleLogout() {
    setLogoutBusy(true);
    await logout();
    navigate("/login", { replace: true });
  }

  const items = feed.data?.items ?? [];

  return (
    <>
      {/* Sidebar */}
      <aside className="fixed left-0 top-0 h-full w-72 bg-surface-container-lowest z-40 flex flex-col justify-between shadow-level-1 hidden lg:flex">
        <div className="flex flex-col">
          <div className="h-16 px-space-6 flex items-center gap-space-3">
            <div className="w-9 h-9 rounded-lg bg-primary-container flex items-center justify-center shadow-level-1">
              <span aria-hidden="true" className="material-symbols-outlined text-on-primary text-[22px]">
                shield_with_heart
              </span>
            </div>
            <div className="flex flex-col">
              <span className="font-headline-sm text-headline-sm text-on-surface leading-none">
                LifeLink
              </span>
              <span className="font-label-sm text-label-sm text-secondary tracking-wider font-semibold uppercase mt-space-1">
                Admin Portal
              </span>
            </div>
          </div>
          <nav className="flex-1 px-space-3 py-space-4 space-y-1">
            <div className="px-space-3 py-space-1">
              <span className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">
                Control Center
              </span>
            </div>
            {ADMIN_NAV.map((n) => (
              <Link
                key={n.path}
                to={n.path}
                aria-current={location.pathname === n.path ? "page" : undefined}
                className={`flex items-center gap-space-3 px-space-3 py-space-3 rounded-lg transition-all font-label-lg text-label-lg ${
                  location.pathname === n.path
                    ? "bg-primary-container text-on-primary font-bold shadow-level-1"
                    : "text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"
                }`}
              >
                <span aria-hidden="true" className="material-symbols-outlined text-[20px]">{n.icon}</span>
                <span>{n.label}</span>
              </Link>
            ))}
            <div className="pt-space-3 px-space-3 py-space-1">
              <span className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">
                Account
              </span>
            </div>
            {ACCOUNT_NAV.map((n) => (
              <Link
                key={n.path}
                to={n.path}
                className="flex items-center gap-space-3 px-space-3 py-space-3 rounded-lg text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface transition-all font-label-lg text-label-lg"
              >
                <span aria-hidden="true" className="material-symbols-outlined text-[20px]">{n.icon}</span>
                <span>{n.label}</span>
              </Link>
            ))}
          </nav>
        </div>
        <div className="p-space-4 space-y-space-2">
          <div className="flex items-center gap-space-3 p-space-3 rounded-lg bg-surface-container-low">
            <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center shrink-0">
              <span aria-hidden="true" className="material-symbols-outlined text-on-primary text-[18px]">verified_user</span>
            </div>
            <div className="flex flex-col min-w-0 flex-1">
              <span className="font-label-md text-label-md text-on-surface font-semibold truncate">
                {user?.full_name ?? "Admin"}
              </span>
              <span className="font-body-sm text-body-sm text-on-surface-variant truncate">
                {user?.email}
              </span>
            </div>
          </div>
          <button
            onClick={handleLogout}
            disabled={logoutBusy}
            className="w-full flex items-center gap-space-3 px-space-3 py-space-2 rounded-lg text-on-surface-variant hover:bg-surface-container-low hover:text-error transition-all disabled:opacity-60"
          >
            <span aria-hidden="true" className="material-symbols-outlined text-[18px]">logout</span>
            <span className="font-label-md text-label-md">Log out</span>
          </button>
        </div>
      </aside>

      <MobileNavDrawer
        open={mobileNavOpen}
        onClose={() => setMobileNavOpen(false)}
        activePath={location.pathname}
        title="LifeLink"
        subtitle="Admin Portal"
        sections={[
          { heading: "Control Center", items: ADMIN_NAV },
          { heading: "Account", items: ACCOUNT_NAV },
        ]}
        footer={
          <>
            <div className="flex items-center gap-space-3 p-space-3 rounded-lg bg-surface-container-low">
              <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center shrink-0">
                <span aria-hidden="true" className="material-symbols-outlined text-on-primary text-[18px]">verified_user</span>
              </div>
              <div className="flex flex-col min-w-0 flex-1">
                <span className="font-label-md text-label-md text-on-surface font-semibold truncate">
                  {user?.full_name ?? "Admin"}
                </span>
                <span className="font-body-sm text-body-sm text-on-surface-variant truncate">
                  {user?.email}
                </span>
              </div>
            </div>
            <button
              type="button"
              onClick={handleLogout}
              disabled={logoutBusy}
              className="w-full flex items-center gap-space-3 px-space-3 py-space-2 rounded-lg text-on-surface-variant hover:bg-surface-container-low hover:text-error transition-all disabled:opacity-60"
            >
              <span aria-hidden="true" className="material-symbols-outlined text-[18px]">logout</span>
              <span className="font-label-md text-label-md">Log out</span>
            </button></>
        }
      />

      <div className="lg:pl-72">
        <header className="sticky top-0 lg:fixed lg:top-0 lg:left-72 lg:right-0 h-16 bg-surface/90 backdrop-blur-xl shadow-level-1 z-30 flex items-center justify-between px-space-4 lg:px-space-8">
          <div className="flex items-center gap-space-3 min-w-0">
            <button
              type="button"
              onClick={() => setMobileNavOpen(true)}
              aria-label="Open navigation menu"
              className="lg:hidden p-2 -ml-2 rounded-full text-on-surface-variant hover:bg-surface-container-low shrink-0"
            >
              <Menu className="h-5 w-5" />
            </button>
            <div className="flex flex-col min-w-0">
            <span className="font-title-lg text-title-lg text-on-surface truncate">Audit Feed</span>
            <span className="font-label-sm text-label-sm text-on-surface-variant hidden sm:block">
              Searchable platform-wide event stream
            </span>
          </div>
          </div>
          <div className="flex items-center gap-space-3">
            <NotificationsBell buttonClassName="relative p-2 rounded-full hover:bg-surface-container-low text-on-surface-variant" />
            <div className="flex items-center gap-space-2 pl-space-2">
              <div className="hidden md:flex flex-col items-end">
                <span className="font-label-lg text-label-lg text-on-surface font-semibold truncate max-w-[160px]">
                  {user?.full_name}
                </span>
                <span className="font-body-sm text-body-sm text-on-surface-variant">
                  {user ? ROLE_LABEL[user.role] : ""}
                </span>
              </div>
              <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center shrink-0">
                <span aria-hidden="true" className="material-symbols-outlined text-on-primary text-[18px]">person</span>
              </div>
            </div>
          </div>
        </header>

        <main className="relative lg:pt-16 bg-background w-full min-h-screen px-space-4 sm:px-space-6 lg:px-space-8 py-space-6">
          <div className="flex flex-col w-full gap-space-6 pb-space-12">
            <div>
              <h1 className="font-headline-lg text-headline-lg text-on-surface">System Audit Feed</h1>
              <p className="font-body-md text-body-md text-on-surface-variant">
                Every recorded admin and system action, filterable by user, role, event, resource,
                severity and date.
              </p>
            </div>

            <AuditChainPanel />

            {/* Filters */}
            <div className="bg-surface-container-lowest p-space-4 rounded-xl shadow-level-1 flex flex-col gap-space-3">
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-space-3">
                <label className="flex flex-col gap-1">
                  <span className="font-label-sm text-label-sm text-on-surface-variant">Event (action)</span>
                  <input
                    className="px-space-3 py-2 bg-surface-container-low border-none rounded-lg text-body-sm"
                    placeholder="e.g. admin.user_suspended"
                    value={action}
                    onChange={(e) => {
                      setAction(e.target.value);
                      resetAndRefilter();
                    }}
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="font-label-sm text-label-sm text-on-surface-variant">Resource (target type)</span>
                  <input
                    className="px-space-3 py-2 bg-surface-container-low border-none rounded-lg text-body-sm"
                    placeholder="e.g. user, transport_route"
                    value={targetType}
                    onChange={(e) => {
                      setTargetType(e.target.value);
                      resetAndRefilter();
                    }}
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="font-label-sm text-label-sm text-on-surface-variant">Actor role</span>
                  <select
                    value={actorRole ?? ""}
                    onChange={(e) => {
                      setActorRole((e.target.value || undefined) as UserRole | undefined);
                      resetAndRefilter();
                    }}
                    className="px-space-3 py-2 bg-surface-container-low border-none rounded-lg text-body-sm"
                  >
                    {ROLE_FILTERS.map((r) => (
                      <option key={r.label} value={r.value ?? ""}>
                        {r.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1">
                  <span className="font-label-sm text-label-sm text-on-surface-variant">Severity</span>
                  <select
                    value={severity ?? ""}
                    onChange={(e) => {
                      setSeverity((e.target.value || undefined) as typeof severity);
                      resetAndRefilter();
                    }}
                    className="px-space-3 py-2 bg-surface-container-low border-none rounded-lg text-body-sm"
                  >
                    {SEVERITY_FILTERS.map((s) => (
                      <option key={s.label} value={s.value ?? ""}>
                        {s.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1">
                  <span className="font-label-sm text-label-sm text-on-surface-variant">Since</span>
                  <input
                    type="date"
                    className="px-space-3 py-2 bg-surface-container-low border-none rounded-lg text-body-sm"
                    value={toDateInputValue(since)}
                    onChange={(e) => {
                      setSince(e.target.value);
                      resetAndRefilter();
                    }}
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="font-label-sm text-label-sm text-on-surface-variant">Until</span>
                  <input
                    type="date"
                    className="px-space-3 py-2 bg-surface-container-low border-none rounded-lg text-body-sm"
                    value={toDateInputValue(until)}
                    onChange={(e) => {
                      setUntil(e.target.value);
                      resetAndRefilter();
                    }}
                  />
                </label>
              </div>
            </div>

            {/* Feed */}
            <div className="rounded-xl bg-surface-container-lowest shadow-level-1 p-space-6 flex flex-col gap-space-4">
              {feed.isLoading && (
                <p className="font-body-sm text-body-sm text-on-surface-variant">Loading…</p>
              )}
              {feed.isError && (
                <p className="font-body-sm text-body-sm text-error">{toApiError(feed.error).detail}</p>
              )}
              {feed.data && items.length === 0 && (
                <p className="font-body-sm text-body-sm text-on-surface-variant">
                  No audit events match this filter.
                </p>
              )}
              {feed.data && items.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-left border-collapse">
                    <thead>
                      <tr className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider border-b border-outline-variant/60">
                        <th className="py-space-2 pr-space-3">When</th>
                        <th className="py-space-2 pr-space-3">Severity</th>
                        <th className="py-space-2 pr-space-3">Action</th>
                        <th className="py-space-2 pr-space-3">Actor</th>
                        <th className="py-space-2 pr-space-3">Resource</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((e) => (
                        <tr key={e.id} className="border-b border-outline-variant/30 align-top">
                          <td className="py-space-2 pr-space-3 font-data-mono text-label-sm text-on-surface-variant whitespace-nowrap">
                            {new Date(e.created_at).toLocaleString()}
                          </td>
                          <td className="py-space-2 pr-space-3">
                            <span
                              className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${SEVERITY_STYLE[e.severity]}`}
                            >
                              {e.severity}
                            </span>
                          </td>
                          <td className="py-space-2 pr-space-3 font-label-md text-label-md text-on-surface">
                            {e.action}
                          </td>
                          <td className="py-space-2 pr-space-3 font-body-sm text-body-sm text-on-surface-variant">
                            {e.actor_email ? (
                              <>
                                {e.actor_email}
                                {e.actor_role && ` (${ROLE_LABEL[e.actor_role]})`}
                              </>
                            ) : (
                              "system"
                            )}
                          </td>
                          <td className="py-space-2 pr-space-3 font-data-mono text-label-sm text-on-surface-variant">
                            {e.target_type ?? "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <div className="flex items-center justify-end gap-space-2 pt-space-2">
                <button
                  type="button"
                  disabled={cursorStack.length === 0}
                  onClick={() => {
                    const stack = [...cursorStack];
                    const prev = stack.pop();
                    setCursorStack(stack);
                    setCursor(prev);
                  }}
                  className="px-space-3 py-1.5 rounded-lg bg-surface-container-low font-label-sm text-label-sm text-on-surface disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  Previous
                </button>
                <button
                  type="button"
                  disabled={!feed.data?.next_cursor}
                  onClick={() => {
                    if (!feed.data?.next_cursor) return;
                    setCursorStack((s) => [...s, cursor]);
                    setCursor(feed.data.next_cursor ?? undefined);
                  }}
                  className="px-space-3 py-1.5 rounded-lg bg-surface-container-low font-label-sm text-label-sm text-on-surface disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  Next
                </button>
              </div>
            </div>
          </div>
        </main>
      </div>
    </>
  );
}

/** Phase E -- recompute the tamper-EVIDENT hash chain on demand. Wording is
 * deliberate: a passing check means "no evidence of tampering among sealed
 * entries", never "immutable". Recording the head (seq + hash) somewhere
 * outside LifeLink and supplying it later is what detects truncation or a
 * fully recomputed chain. */
function AuditChainPanel() {
  const [anchorSeq, setAnchorSeq] = useState("");
  const [anchorHash, setAnchorHash] = useState("");
  const run = useMutation<AuditChainVerifyResult, unknown, void>({
    mutationFn: () =>
      verifyAuditChain(
        anchorSeq && anchorHash ? { seq: Number(anchorSeq), hash: anchorHash.trim() } : undefined,
      ),
  });
  const r = run.data;
  return (
    <div className="bg-surface-container-lowest p-space-4 rounded-xl shadow-level-1 flex flex-col gap-space-2" data-testid="audit-chain-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="font-title-md text-title-md text-on-surface">Audit chain integrity</h2>
          <p className="font-body-sm text-body-sm text-on-surface-variant">
            Each entry is hash-chained to the one before it. This makes edits and deletions
            detectable; it does not make the log immutable.
          </p>
        </div>
        <button
          type="button"
          onClick={() => run.mutate()}
          disabled={run.isPending}
          className="h-9 px-3 rounded-lg bg-secondary text-on-secondary font-label-md text-label-md disabled:opacity-50"
        >
          {run.isPending ? "Verifying…" : "Verify chain"}
        </button>
      </div>
      <details>
        <summary className="font-body-sm text-body-sm text-on-surface-variant cursor-pointer">
          Check against a previously recorded head (optional)
        </summary>
        <div className="flex flex-wrap gap-2 mt-2">
          <input aria-label="Anchor sequence" className="w-28 px-2 py-1 rounded border border-outline-variant bg-surface text-body-sm"
            placeholder="seq" value={anchorSeq} onChange={(e) => setAnchorSeq(e.target.value.replace(/\D/g, ""))} />
          <input aria-label="Anchor hash" className="flex-1 min-w-[16rem] px-2 py-1 rounded border border-outline-variant bg-surface font-data-mono text-[12px]"
            placeholder="64-character hash" value={anchorHash} onChange={(e) => setAnchorHash(e.target.value)} />
        </div>
      </details>
      {run.isError && <p role="alert" className="text-error text-body-sm">{toApiError(run.error).detail}</p>}
      {r && (
        <div role="status" className={`rounded-lg p-3 text-body-sm ${r.ok ? "bg-success/10 text-on-surface" : "bg-error/10 text-error"}`}>
          {r.ok ? (
            <p>
              No evidence of tampering across {r.checked} sealed entr{r.checked === 1 ? "y" : "ies"}
              {r.anchored ? ", and the recorded head matches" : ""}.
            </p>
          ) : (
            <p>
              {r.first_break?.seq != null
                ? `Chain broken at entry #${r.first_break.seq}: ${r.first_break.reason}`
                : `Verification failed: ${r.first_break?.reason ?? "unknown reason"}`}
            </p>
          )}
          {r.checkpoints?.enabled && (
            <p data-testid="audit-checkpoints">
              {r.checkpoints.ok
                ? `${r.checkpoints.checked} signed checkpoint${r.checkpoints.checked === 1 ? "" : "s"} kept outside the database verified${r.checkpoints.latest_seq != null ? ` (latest at entry #${r.checkpoints.latest_seq})` : ""}.`
                : `Signed checkpoints failed: ${r.checkpoints.reason}`}
            </p>
          )}
          {r.unsealed > 0 && <p>{r.unsealed} recent entr{r.unsealed === 1 ? "y is" : "ies are"} not yet sealed and were not checked.</p>}
          {r.head_seq !== null && (
            <p className="font-data-mono text-[11px] break-all mt-1">
              Head #{r.head_seq}: {r.head_hash} — record this outside LifeLink to detect later truncation.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
