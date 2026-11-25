/**
 * Admin — Donor Governance (Phase 6)
 *
 * Real donor oversight via GET /admin/donors: identity, verification state,
 * blood type, city and availability. Deliberately does NOT surface
 * DonorProfile.medical_conditions or .weight_kg (health data) or exact
 * latitude/longitude (precise location) -- the backend schema
 * (AdminDonorOut) never sends them here at all, per the Phase 6 spec's
 * "do not expose unnecessary private medical data". This is oversight of
 * identity/verification/eligibility signals only, not a medical record.
 */
import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Menu } from "lucide-react";

import NotificationsBell from "../../components/NotificationsBell";
import { useAuth } from "../../lib/auth";
import { toApiError } from "../../lib/api";
import { ROLE_LABEL } from "../../lib/roles";
import { BLOOD_LABEL, BLOOD_TYPES, listAdminDonors, type BloodType } from "../../lib/domain";

import { ADMIN_NAV, ACCOUNT_NAV } from "./adminNav";
import MobileNavDrawer from "../../components/MobileNavDrawer";
import DonationReviewQueue from "../../components/DonationReviewQueue";

const VERIFIED_FILTERS: { label: string; value: boolean | undefined }[] = [
  { label: "Any verification", value: undefined },
  { label: "Identity verified", value: true },
  { label: "Not verified", value: false },
];

const AVAILABLE_FILTERS: { label: string; value: boolean | undefined }[] = [
  { label: "Any availability", value: undefined },
  { label: "Available", value: true },
  { label: "Unavailable", value: false },
];

const PAGE_SIZE = 25;

export default function AdminDonors() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [bloodType, setBloodType] = useState<BloodType | "">("");
  const [identityVerified, setIdentityVerified] = useState<boolean | undefined>(undefined);
  const [isAvailable, setIsAvailable] = useState<boolean | undefined>(undefined);
  const [page, setPage] = useState(1);

  const list = useQuery({
    queryKey: [
      "admin",
      "donors",
      "list",
      { query, bloodType, identityVerified, isAvailable, page },
    ],
    queryFn: () =>
      listAdminDonors({
        query: query || undefined,
        blood_type: bloodType || undefined,
        identity_verified: identityVerified,
        is_available: isAvailable,
        page,
        page_size: PAGE_SIZE,
      }),
  });

  const donors = list.data?.items ?? [];
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
            <span className="font-title-lg text-title-lg text-on-surface truncate">Donors</span>
            <span className="font-label-sm text-label-sm text-on-surface-variant hidden sm:block">
              Verification &amp; eligibility oversight
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
              <h1 className="font-headline-lg text-headline-lg text-on-surface">Donor Governance</h1>
              <p className="font-body-md text-body-md text-on-surface-variant">
                Identity, verification and eligibility signals only — never medical detail.
              </p>
            </div>

            {/* Search + filters */}
            <div className="bg-surface-container-lowest p-space-4 rounded-xl shadow-level-1 flex flex-col gap-space-4">
              <div className="relative flex-1 w-full">
                <span aria-hidden="true" className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant">
                  search
                </span>
                <label htmlFor="donor-search" className="sr-only">
                  Search by name or email
                </label>
                <input
                  id="donor-search"
                  className="w-full pl-10 pr-4 py-2 bg-surface-container-low border-none rounded-lg text-body-sm"
                  placeholder="Search by name or email…"
                  type="text"
                  value={query}
                  onChange={(e) => {
                    setPage(1);
                    setQuery(e.target.value);
                  }}
                />
              </div>
              <div className="flex flex-wrap items-center gap-space-2">
                <label htmlFor="donor-blood-type-filter" className="sr-only">
                  Filter by blood type
                </label>
                <select
                  id="donor-blood-type-filter"
                  value={bloodType}
                  onChange={(e) => {
                    setPage(1);
                    setBloodType(e.target.value as BloodType | "");
                  }}
                  className="px-space-3 py-2 bg-surface-container-low border-none rounded-lg text-body-sm"
                >
                  <option value="">All blood types</option>
                  {BLOOD_TYPES.map((bt) => (
                    <option key={bt} value={bt}>
                      {BLOOD_LABEL[bt]}
                    </option>
                  ))}
                </select>
                {VERIFIED_FILTERS.map((f) => (
                  <button
                    key={f.label}
                    type="button"
                    aria-pressed={identityVerified === f.value}
                    onClick={() => {
                      setPage(1);
                      setIdentityVerified(f.value);
                    }}
                    className={`px-space-4 py-2 rounded-full font-label-md text-label-md whitespace-nowrap transition-colors ${
                      identityVerified === f.value
                        ? "bg-surface-container-high text-on-surface"
                        : "bg-surface-container-low text-on-surface-variant hover:bg-surface-container"
                    }`}
                  >
                    {f.label}
                  </button>
                ))}
                {AVAILABLE_FILTERS.map((f) => (
                  <button
                    key={f.label}
                    type="button"
                    aria-pressed={isAvailable === f.value}
                    onClick={() => {
                      setPage(1);
                      setIsAvailable(f.value);
                    }}
                    className={`px-space-4 py-2 rounded-full font-label-md text-label-md whitespace-nowrap transition-colors ${
                      isAvailable === f.value
                        ? "bg-surface-container-high text-on-surface"
                        : "bg-surface-container-low text-on-surface-variant hover:bg-surface-container"
                    }`}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Directory table */}
            <div className="rounded-xl bg-surface-container-lowest shadow-level-1 p-space-6 flex flex-col gap-space-4">
              <div className="flex items-center gap-space-3">
                <div className="w-8 h-8 rounded-lg bg-surface-container flex items-center justify-center text-primary-container">
                  <span aria-hidden="true" className="material-symbols-outlined text-[20px]">volunteer_activism</span>
                </div>
                <div>
                  <h2 className="font-title-md text-title-md text-on-surface">Donor Directory</h2>
                  <p className="font-body-sm text-body-sm text-on-surface-variant">
                    Every registered donor account and its verification / availability state.
                  </p>
                </div>
              </div>

              {list.isLoading && (
                <p className="font-body-sm text-body-sm text-on-surface-variant">Loading…</p>
              )}
              {list.isError && (
                <p className="font-body-sm text-body-sm text-error">{toApiError(list.error).detail}</p>
              )}
              {list.data && donors.length === 0 && (
                <p className="font-body-sm text-body-sm text-on-surface-variant">
                  No donors match this search / filter.
                </p>
              )}
              {list.data && donors.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-left border-collapse">
                    <thead>
                      <tr className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider border-b border-outline-variant/60">
                        <th className="py-space-2 pr-space-3">Name</th>
                        <th className="py-space-2 pr-space-3">Email</th>
                        <th className="py-space-2 pr-space-3">Blood type</th>
                        <th className="py-space-2 pr-space-3">City</th>
                        <th className="py-space-2 pr-space-3">Identity</th>
                        <th className="py-space-2 pr-space-3">Availability</th>
                        <th className="py-space-2 pr-space-3">Last donation</th>
                      </tr>
                    </thead>
                    <tbody>
                      {donors.map((d) => (
                        <tr key={d.user_id} className="border-b border-outline-variant/40">
                          <td className="py-space-3 pr-space-3 font-label-md text-label-md text-on-surface">
                            {d.full_name}
                          </td>
                          <td className="py-space-3 pr-space-3 font-body-sm text-body-sm text-on-surface-variant">
                            {d.email}
                          </td>
                          <td className="py-space-3 pr-space-3 font-bold text-primary">
                            {d.blood_type ? BLOOD_LABEL[d.blood_type] : "—"}
                          </td>
                          <td className="py-space-3 pr-space-3 font-body-sm text-body-sm text-on-surface-variant">
                            {d.city ?? "—"}
                          </td>
                          <td className="py-space-3 pr-space-3">
                            <span
                              className={`px-2.5 py-0.5 rounded-full text-[11px] font-bold ${
                                d.identity_verified
                                  ? "bg-success/15 text-success"
                                  : "bg-surface-container text-on-surface-variant"
                              }`}
                            >
                              {d.identity_verified ? "Verified" : "Unverified"}
                            </span>
                          </td>
                          <td className="py-space-3 pr-space-3 font-body-sm text-body-sm text-on-surface">
                            {d.availability_state === "AVAILABLE"
                              ? "Available"
                              : d.availability_state === "NOT_NOW"
                              ? "Not right now"
                              : "Unavailable"}
                          </td>
                          <td className="py-space-3 pr-space-3 font-body-sm text-body-sm text-on-surface-variant">
                            {d.last_donation_date
                              ? new Date(d.last_donation_date).toLocaleDateString()
                              : "Never"}
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
            <div className="mt-space-6">
              <DonationReviewQueue isAdmin />
            </div>
          </div>
        </main>
      </div>
    </>
  );
}
