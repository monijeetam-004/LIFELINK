/**
 * Admin — Facilities (Phase 6)
 *
 * Real facility oversight: GET /admin/facilities (search/filter/paginate
 * across every HospitalProfile row -- hospitals and blood banks alike) and
 * GET /admin/facilities/{id} for a detail drawer with that facility's real,
 * live per-blood-type inventory (the same HospitalInventory rows the
 * facility's own dashboard reads/writes). Nothing here is fabricated: a
 * facility with no inventory rows yet shows an empty table, not zeros padded
 * in to look populated.
 */
import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Menu } from "lucide-react";

import NotificationsBell from "../../components/NotificationsBell";
import { useAuth } from "../../lib/auth";
import { toApiError } from "../../lib/api";
import { revokeFacilityVerification } from "../../lib/gapApi";
import { ROLE_LABEL } from "../../lib/roles";
import {
  BLOOD_LABEL,
  componentLabel,
  listAdminFacilities,
  getAdminFacility,
  type FacilityType,
} from "../../lib/domain";

import { ADMIN_NAV, ACCOUNT_NAV } from "./adminNav";
import MobileNavDrawer from "../../components/MobileNavDrawer";

const TYPE_FILTERS: { label: string; value: FacilityType | undefined }[] = [
  { label: "All facilities", value: undefined },
  { label: "Hospitals", value: "HOSPITAL" },
  { label: "Blood banks", value: "BLOOD_BANK" },
];

const VERIFIED_FILTERS: { label: string; value: boolean | undefined }[] = [
  { label: "Any status", value: undefined },
  { label: "Verified", value: true },
  { label: "Unverified", value: false },
];

const PAGE_SIZE = 25;

export default function AdminFacilities() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<FacilityType | undefined>(undefined);
  const [verifiedFilter, setVerifiedFilter] = useState<boolean | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["admin", "facilities", "list", { query, typeFilter, verifiedFilter, page }],
    queryFn: () =>
      listAdminFacilities({
        query: query || undefined,
        facility_type: typeFilter,
        verified: verifiedFilter,
        page,
        page_size: PAGE_SIZE,
      }),
  });

  const detail = useQuery({
    queryKey: ["admin", "facilities", "detail", selectedId],
    queryFn: () => getAdminFacility(selectedId as string),
    enabled: !!selectedId,
  });

  const facilities = list.data?.items ?? [];
  const total = list.data?.total ?? 0;

  async function handleLogout() {
    setLogoutBusy(true);
    await logout();
    navigate("/login", { replace: true });
  }

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
            {logoutBusy ? (
              <Loader2 className="h-[18px] w-[18px] animate-spin" />
            ) : (
              <span aria-hidden="true" className="material-symbols-outlined text-[18px]">logout</span>
            )}
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
        {/* Header */}
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
            <span className="font-title-lg text-title-lg text-on-surface truncate">Facilities</span>
            <span className="font-label-sm text-label-sm text-on-surface-variant hidden sm:block">
              Hospitals &amp; blood banks · verification &amp; status
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
              <h1 className="font-headline-lg text-headline-lg text-on-surface">
                Facility Directory &amp; Verification
              </h1>
              <p className="font-body-md text-body-md text-on-surface-variant">
                Every registered hospital and blood bank, its verification state, and its current
                inventory footprint.
              </p>
            </div>

            {/* Search + filters */}
            <div className="bg-surface-container-lowest p-space-4 rounded-xl shadow-level-1 flex flex-col md:flex-row gap-space-4 items-center">
              <div className="relative flex-1 w-full">
                <span aria-hidden="true" className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant">
                  search
                </span>
                <label htmlFor="facility-search" className="sr-only">
                  Search by facility name or city
                </label>
                <input
                  id="facility-search"
                  className="w-full pl-10 pr-4 py-2 bg-surface-container-low border-none rounded-lg text-body-sm"
                  placeholder="Search by facility name or city…"
                  type="text"
                  value={query}
                  onChange={(e) => {
                    setPage(1);
                    setQuery(e.target.value);
                  }}
                />
              </div>
              <div className="flex items-center gap-space-2 w-full md:w-auto overflow-x-auto">
                {TYPE_FILTERS.map((f) => (
                  <button
                    key={f.label}
                    type="button"
                    aria-pressed={typeFilter === f.value}
                    onClick={() => {
                      setPage(1);
                      setTypeFilter(f.value);
                    }}
                    className={`px-space-4 py-2 rounded-full font-label-md text-label-md whitespace-nowrap transition-colors ${
                      typeFilter === f.value
                        ? "bg-surface-container-high text-on-surface"
                        : "bg-surface-container-low text-on-surface-variant hover:bg-surface-container"
                    }`}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-space-2 w-full md:w-auto overflow-x-auto">
                {VERIFIED_FILTERS.map((f) => (
                  <button
                    key={f.label}
                    type="button"
                    aria-pressed={verifiedFilter === f.value}
                    onClick={() => {
                      setPage(1);
                      setVerifiedFilter(f.value);
                    }}
                    className={`px-space-4 py-2 rounded-full font-label-md text-label-md whitespace-nowrap transition-colors ${
                      verifiedFilter === f.value
                        ? "bg-surface-container-high text-on-surface"
                        : "bg-surface-container-low text-on-surface-variant hover:bg-surface-container"
                    }`}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-12 gap-space-6 items-start">
              {/* Directory table */}
              <div className="xl:col-span-8 rounded-xl bg-surface-container-lowest shadow-level-1 p-space-6 flex flex-col gap-space-4">
                <div className="flex items-center gap-space-3">
                  <div className="w-8 h-8 rounded-lg bg-surface-container flex items-center justify-center text-primary-container">
                    <span aria-hidden="true" className="material-symbols-outlined text-[20px]">local_hospital</span>
                  </div>
                  <div>
                    <h2 className="font-title-md text-title-md text-on-surface">Registered Facilities</h2>
                    <p className="font-body-sm text-body-sm text-on-surface-variant">
                      Click a row to open its inventory detail.
                    </p>
                  </div>
                </div>

                {list.isLoading && (
                  <p className="font-body-sm text-body-sm text-on-surface-variant">Loading…</p>
                )}
                {list.isError && (
                  <p className="font-body-sm text-body-sm text-error">{toApiError(list.error).detail}</p>
                )}
                {list.data && facilities.length === 0 && (
                  <p className="font-body-sm text-body-sm text-on-surface-variant">
                    No facilities match this search / filter.
                  </p>
                )}
                {list.data && facilities.length > 0 && (
                  <div tabIndex={0} role="region" aria-label="Facilities (scrollable)" className="overflow-x-auto focus:outline-none focus:ring-2 focus:ring-primary/40">
                    <table className="w-full text-left border-collapse">
                      <thead>
                        <tr className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider border-b border-outline-variant/60">
                          <th className="py-space-2 pr-space-3">Name</th>
                          <th className="py-space-2 pr-space-3">Type</th>
                          <th className="py-space-2 pr-space-3">City</th>
                          <th className="py-space-2 pr-space-3">Verified</th>
                          <th className="py-space-2 pr-space-3">Account</th>
                          <th className="py-space-2 pr-space-3">Units on hand</th>
                        </tr>
                      </thead>
                      <tbody>
                        {facilities.map((f) => (
                          <tr
                            key={f.id}
                            onClick={() => setSelectedId(f.id)}
                            aria-selected={selectedId === f.id}
                            className={`cursor-pointer border-b border-outline-variant/40 transition-colors ${
                              selectedId === f.id
                                ? "bg-primary-container/30"
                                : "hover:bg-surface-container-low"
                            }`}
                          >
                            <td className="py-space-3 pr-space-3 font-label-md text-label-md text-on-surface">
                              {f.name ?? <span className="text-on-surface-variant italic">Unnamed</span>}
                            </td>
                            <td className="py-space-3 pr-space-3 font-body-sm text-body-sm text-on-surface">
                              {f.facility_type === "BLOOD_BANK" ? "Blood bank" : "Hospital"}
                            </td>
                            <td className="py-space-3 pr-space-3 font-body-sm text-body-sm text-on-surface-variant">
                              {f.city ?? "—"}
                            </td>
                            <td className="py-space-3 pr-space-3">
                              <span
                                className={`px-2.5 py-0.5 rounded-full text-[11px] font-bold ${
                                  f.verified ? "bg-success/15 text-success" : "bg-surface-container text-on-surface-variant"
                                }`}
                              >
                                {f.verified ? "Verified" : "Unverified"}
                              </span>
                            </td>
                            <td className="py-space-3 pr-space-3">
                              <span
                                className={`px-2.5 py-0.5 rounded-full text-[11px] font-bold ${
                                  f.is_active ? "bg-success/15 text-success" : "bg-error/15 text-error"
                                }`}
                              >
                                {f.is_active ? "Active" : "Suspended"}
                              </span>
                            </td>
                            <td className="py-space-3 pr-space-3 font-data-mono text-body-sm text-on-surface">
                              {f.total_units}
                              {f.total_reserved_units > 0 && (
                                <span className="text-on-surface-variant"> ({f.total_reserved_units} reserved)</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {list.data && total > 0 && (
                  <div className="flex items-center justify-between pt-space-2">
                    <span className="font-body-sm text-body-sm text-on-surface-variant">
                      Page {page} of {Math.max(1, Math.ceil(total / PAGE_SIZE))} · {total} total
                    </span>
                    <div className="flex gap-space-2">
                      <button
                        type="button"
                        onClick={() => setPage((p) => Math.max(1, p - 1))}
                        disabled={page <= 1}
                        className="px-space-3 py-1.5 rounded-lg bg-surface-container-low font-label-sm text-label-sm text-on-surface disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        Previous
                      </button>
                      <button
                        type="button"
                        onClick={() => setPage((p) => (p * PAGE_SIZE < total ? p + 1 : p))}
                        disabled={page * PAGE_SIZE >= total}
                        className="px-space-3 py-1.5 rounded-lg bg-surface-container-low font-label-sm text-label-sm text-on-surface disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        Next
                      </button>
                    </div>
                  </div>
                )}
              </div>

              {/* Detail drawer */}
              <div className="xl:col-span-4 rounded-xl bg-surface-container-lowest shadow-level-1 p-space-6 flex flex-col gap-space-4">
                <div className="flex items-center gap-space-3">
                  <div className="w-8 h-8 rounded-lg bg-secondary-fixed flex items-center justify-center text-secondary">
                    <span aria-hidden="true" className="material-symbols-outlined text-[20px]">inventory_2</span>
                  </div>
                  <div>
                    <h2 className="font-title-md text-title-md text-on-surface">Facility Detail</h2>
                    <p className="font-body-sm text-body-sm text-on-surface-variant">
                      Per-blood-type inventory snapshot.
                    </p>
                  </div>
                </div>
                {!selectedId && (
                  <p className="font-body-sm text-body-sm text-on-surface-variant">
                    Select a facility from the directory.
                  </p>
                )}
                {selectedId && detail.isLoading && (
                  <p className="font-body-sm text-body-sm text-on-surface-variant">Loading…</p>
                )}
                {selectedId && detail.isError && (
                  <p className="font-body-sm text-body-sm text-error">{toApiError(detail.error).detail}</p>
                )}
                {detail.data && (
                  <div className="flex flex-col gap-space-4">
                    <div className="flex flex-col gap-1">
                      <span className="font-label-lg text-label-lg text-on-surface font-semibold">
                        {detail.data.facility.name ?? "Unnamed facility"}
                      </span>
                      <span className="font-body-sm text-body-sm text-on-surface-variant">
                        {detail.data.facility.address ?? "No address on file"}
                      </span>
                      {detail.data.facility.license_no && (
                        <span className="font-data-mono text-label-sm text-on-surface-variant">
                          License: {detail.data.facility.license_no}
                        </span>
                      )}
                      {detail.data.facility.verified && (
                        <RevokeVerification facilityId={detail.data.facility.id} />
                      )}
                    </div>
                    {detail.data.inventory.length === 0 ? (
                      <p className="font-body-sm text-body-sm text-on-surface-variant">
                        No inventory recorded yet.
                      </p>
                    ) : (
                      <table className="w-full text-left border-collapse">
                        <thead>
                          <tr className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider border-b border-outline-variant/60">
                            <th className="py-space-1 pr-space-2">Type</th>
                            <th className="py-space-1 pr-space-2">Component</th>
                            <th className="py-space-1 pr-space-2">Units</th>
                            <th className="py-space-1 pr-space-2">Reserved</th>
                            <th className="py-space-1 pr-space-2">Available</th>
                          </tr>
                        </thead>
                        <tbody>
                          {detail.data.inventory.map((line) => (
                            <tr key={`${line.blood_type}-${line.component}`} className="border-b border-outline-variant/30">
                              <td className="py-space-2 pr-space-2 font-bold text-primary">
                                {BLOOD_LABEL[line.blood_type]}
                              </td>
                              <td className="py-space-2 pr-space-2 font-body-sm text-body-sm text-on-surface">
                                {componentLabel(line.component)}
                              </td>
                              <td className="py-space-2 pr-space-2 font-data-mono text-body-sm text-on-surface">
                                {line.units}
                              </td>
                              <td className="py-space-2 pr-space-2 font-data-mono text-body-sm text-on-surface-variant">
                                {line.reserved_units}
                              </td>
                              <td className="py-space-2 pr-space-2 font-data-mono text-body-sm text-on-surface">
                                {line.available_units}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        </main>
      </div>
    </>
  );
}


/** Gap elimination #7: revoke a facility's verification. The backend fails
 * over every live emergency pathway sourcing from it, withdraws its
 * emergency responses and releases their reservations, all audited. */
function RevokeVerification({ facilityId }: { facilityId: string }) {
  const qc = useQueryClient();
  const [reason, setReason] = useState("");
  const revoke = useMutation({
    mutationFn: () => revokeFacilityVerification(facilityId, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "facilities"] }),
  });
  return (
    <form
      className="mt-2 flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        revoke.mutate();
      }}
    >
      <label className="text-body-sm" htmlFor={`revoke-${facilityId}`}>Revoke verification (reason, 10+ characters)</label>
      <input
        id={`revoke-${facilityId}`}
        className="border border-border-subtle rounded-lg px-3 py-2"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        minLength={10}
        required
      />
      <button className="self-start bg-error/10 text-error px-3 py-1.5 rounded-lg" disabled={revoke.isPending}>
        Revoke verification
      </button>
      {revoke.isSuccess && <p role="status" className="text-body-sm">Verification revoked; affected pathways were re-planned.</p>}
      {revoke.error && <p role="alert" className="text-error text-body-sm">{toApiError(revoke.error).detail}</p>}
    </form>
  );
}
