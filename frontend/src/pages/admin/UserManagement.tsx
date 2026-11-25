/**
 * Admin — User Management & Role Governance (Stitch 4.10 / 4.12 / 4.14)
 *
 * The Admin API now exists: listAdminUsers (search/filter/paginate),
 * changeAdminUserRole, suspendAdminUser and reactivateAdminUser. This screen
 * is wired to real live data — a real directory, a real row-select, and real
 * role/suspend/reactivate mutations with real success/error states.
 */
import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Menu } from "lucide-react";

import NotificationsBell from "../../components/NotificationsBell";
import { useAuth } from "../../lib/auth";
import { toApiError } from "../../lib/api";
import { ROLE_LABEL } from "../../lib/roles";
import {
  listAdminUsers,
  changeAdminUserRole,
  suspendAdminUser,
  reactivateAdminUser,
  type AdminUser,
  type UserRole,
} from "../../lib/domain";

import { ADMIN_NAV, ACCOUNT_NAV } from "./adminNav";
import MobileNavDrawer from "../../components/MobileNavDrawer";

const ROLE_FILTERS: { label: string; value: UserRole | undefined }[] = [
  { label: "All roles", value: undefined },
  { label: "Patient", value: "PATIENT" },
  { label: "Family", value: "FAMILY" },
  { label: "Donor", value: "DONOR" },
  { label: "Hospital", value: "HOSPITAL" },
  { label: "Admin", value: "ADMIN" },
];

const ALL_ROLES: UserRole[] = ["PATIENT", "FAMILY", "DONOR", "HOSPITAL", "ADMIN"];

const PAGE_SIZE = 25;

export default function UserManagement() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [roleFilter, setRoleFilter] = useState<UserRole | undefined>(undefined);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [newRole, setNewRole] = useState<UserRole | "">("");
  const [roleError, setRoleError] = useState("");
  const [suspendReason, setSuspendReason] = useState("");
  const [statusError, setStatusError] = useState("");

  const directory = useQuery({
    queryKey: ["admin", "users", "list", { query, role: roleFilter, page }],
    queryFn: () =>
      listAdminUsers({
        query: query || undefined,
        role: roleFilter,
        page,
        page_size: PAGE_SIZE,
      }),
  });

  const users = directory.data?.items ?? [];
  const total = directory.data?.total ?? 0;
  const selectedUser: AdminUser | undefined = users.find((u) => u.id === selectedId);

  function refreshDirectory() {
    qc.invalidateQueries({ queryKey: ["admin", "users", "list"] });
    qc.invalidateQueries({ queryKey: ["admin", "users", "stats"] });
  }

  const changeRole = useMutation({
    mutationFn: ({ id, role }: { id: string; role: UserRole }) => changeAdminUserRole(id, role),
    onSuccess: () => {
      setRoleError("");
      setNewRole("");
      refreshDirectory();
    },
    onError: (e) => setRoleError(toApiError(e).detail),
  });

  const suspend = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) => suspendAdminUser(id, reason),
    onSuccess: () => {
      setStatusError("");
      setSuspendReason("");
      refreshDirectory();
    },
    onError: (e) => setStatusError(toApiError(e).detail),
  });

  const reactivate = useMutation({
    mutationFn: (id: string) => reactivateAdminUser(id),
    onSuccess: () => {
      setStatusError("");
      refreshDirectory();
    },
    onError: (e) => setStatusError(toApiError(e).detail),
  });

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
            <span className="font-title-lg text-title-lg text-on-surface truncate">Users &amp; Roles</span>
            <span className="font-label-sm text-label-sm text-on-surface-variant hidden sm:block">
              Directory, role governance &amp; account status
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
                User Identity &amp; Role Access Governance
              </h1>
              <p className="font-body-md text-body-md text-on-surface-variant">
                Search the user directory, manage role assignments, and act on account status.
              </p>
            </div>

            {/* Search + role filter */}
            <div className="bg-surface-container-lowest p-space-4 rounded-xl shadow-level-1 flex flex-col md:flex-row gap-space-4 items-center">
              <div className="relative flex-1 w-full">
                <span aria-hidden="true" className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant">
                  search
                </span>
                <label htmlFor="user-search" className="sr-only">
                  Search by name, email, or user ID
                </label>
                <input
                  id="user-search"
                  className="w-full pl-10 pr-4 py-2 bg-surface-container-low border-none rounded-lg text-body-sm"
                  placeholder="Search by name, email, or user ID…"
                  type="text"
                  value={query}
                  onChange={(e) => {
                    setPage(1);
                    setQuery(e.target.value);
                  }}
                />
              </div>
              <div className="flex items-center gap-space-2 w-full md:w-auto overflow-x-auto">
                {ROLE_FILTERS.map((r) => (
                  <button
                    key={r.label}
                    type="button"
                    aria-pressed={roleFilter === r.value}
                    onClick={() => {
                      setPage(1);
                      setRoleFilter(r.value);
                    }}
                    className={`px-space-4 py-2 rounded-full font-label-md text-label-md whitespace-nowrap transition-colors ${
                      roleFilter === r.value
                        ? "bg-surface-container-high text-on-surface"
                        : "bg-surface-container-low text-on-surface-variant hover:bg-surface-container"
                    }`}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Directory table */}
            <div className="rounded-xl bg-surface-container-lowest shadow-level-1 p-space-6 flex flex-col gap-space-4">
              <div className="flex items-center gap-space-3">
                <div className="w-8 h-8 rounded-lg bg-surface-container flex items-center justify-center text-primary-container">
                  <span aria-hidden="true" className="material-symbols-outlined text-[20px]">groups</span>
                </div>
                <div>
                  <h2 className="font-title-md text-title-md text-on-surface">Verified System Directory</h2>
                  <p className="font-body-sm text-body-sm text-on-surface-variant">
                    Every registered patient, family member, donor, hospital and admin account.
                  </p>
                </div>
              </div>

              {directory.isLoading && (
                <p className="font-body-sm text-body-sm text-on-surface-variant">Loading users…</p>
              )}
              {directory.isError && (
                <p className="font-body-sm text-body-sm text-error">{toApiError(directory.error).detail}</p>
              )}
              {directory.data && users.length === 0 && (
                <p className="font-body-sm text-body-sm text-on-surface-variant">
                  No users match this search / filter.
                </p>
              )}
              {directory.data && users.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-left border-collapse">
                    <thead>
                      <tr className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider border-b border-outline-variant/60">
                        <th className="py-space-2 pr-space-3">Name</th>
                        <th className="py-space-2 pr-space-3">Email</th>
                        <th className="py-space-2 pr-space-3">Role</th>
                        <th className="py-space-2 pr-space-3">Status</th>
                        <th className="py-space-2 pr-space-3">Created</th>
                      </tr>
                    </thead>
                    <tbody>
                      {users.map((u) => (
                        <tr
                          key={u.id}
                          onClick={() => {
                            setSelectedId(u.id);
                            setNewRole("");
                            setRoleError("");
                            setSuspendReason("");
                            setStatusError("");
                          }}
                          aria-selected={selectedId === u.id}
                          className={`cursor-pointer border-b border-outline-variant/40 transition-colors ${
                            selectedId === u.id
                              ? "bg-primary-container/30"
                              : "hover:bg-surface-container-low"
                          }`}
                        >
                          <td className="py-space-3 pr-space-3 font-label-md text-label-md text-on-surface">
                            {u.full_name}
                          </td>
                          <td className="py-space-3 pr-space-3 font-body-sm text-body-sm text-on-surface-variant">
                            {u.email}
                          </td>
                          <td className="py-space-3 pr-space-3 font-body-sm text-body-sm text-on-surface">
                            {ROLE_LABEL[u.role]}
                          </td>
                          <td className="py-space-3 pr-space-3">
                            <span
                              title={!u.is_active ? u.suspended_reason ?? undefined : undefined}
                              className={`px-2.5 py-0.5 rounded-full text-[11px] font-bold ${
                                u.is_active
                                  ? "bg-success/15 text-success"
                                  : "bg-error/15 text-error"
                              }`}
                            >
                              {u.is_active ? "Active" : "Suspended"}
                            </span>
                            {!u.is_active && u.suspended_reason && (
                              <p className="font-body-sm text-[11px] text-on-surface-variant mt-0.5 max-w-[220px] truncate">
                                {u.suspended_reason}
                              </p>
                            )}
                          </td>
                          <td className="py-space-3 pr-space-3 font-body-sm text-body-sm text-on-surface-variant">
                            {new Date(u.created_at).toLocaleDateString()}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {directory.data && total > 0 && (
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

            {/* Role governance + status consoles */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-space-6">
              <div className="rounded-xl bg-surface-container-lowest shadow-level-1 p-space-6 flex flex-col gap-space-4">
                <div className="flex items-center gap-space-3">
                  <div className="w-8 h-8 rounded-lg bg-secondary-fixed flex items-center justify-center text-secondary">
                    <span aria-hidden="true" className="material-symbols-outlined text-[20px]">admin_panel_settings</span>
                  </div>
                  <div>
                    <h2 className="font-title-md text-title-md text-on-surface">Role Governance</h2>
                    <p className="font-body-sm text-body-sm text-on-surface-variant">
                      Promote, demote, or reassign a user's role.
                    </p>
                  </div>
                </div>
                {!selectedUser && (
                  <p className="font-body-sm text-body-sm text-on-surface-variant">
                    Select a user from the directory above.
                  </p>
                )}
                {selectedUser && (
                  <div className="flex flex-col gap-space-3">
                    <p className="font-body-sm text-body-sm text-on-surface">
                      <span className="text-on-surface-variant">Selected:</span>{" "}
                      <span className="font-semibold">{selectedUser.full_name}</span> ·{" "}
                      Current role: <span className="font-semibold">{ROLE_LABEL[selectedUser.role]}</span>
                    </p>
                    <div className="flex flex-col sm:flex-row gap-space-3 items-stretch sm:items-center">
                      <label htmlFor="user-mgmt-new-role" className="sr-only">
                        New role for {selectedUser.full_name}
                      </label>
                      <select
                        id="user-mgmt-new-role"
                        value={newRole}
                        onChange={(e) => setNewRole(e.target.value as UserRole)}
                        className="flex-1 px-space-3 py-2 bg-surface-container-low border-none rounded-lg text-body-sm"
                      >
                        <option value="">Choose a new role…</option>
                        {ALL_ROLES.filter((r) => r !== selectedUser.role).map((r) => (
                          <option key={r} value={r}>
                            {ROLE_LABEL[r]}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        disabled={!newRole || changeRole.isPending}
                        onClick={() =>
                          newRole &&
                          changeRole.mutate({ id: selectedUser.id, role: newRole as UserRole })
                        }
                        className="flex items-center justify-center gap-space-2 px-space-4 py-2 rounded-lg bg-secondary text-on-secondary font-label-md text-label-md disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {changeRole.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                        Change role
                      </button>
                    </div>
                    {roleError && <p className="font-body-sm text-body-sm text-error">{roleError}</p>}
                  </div>
                )}
              </div>

              <div className="rounded-xl bg-surface-container-lowest shadow-level-1 p-space-6 flex flex-col gap-space-4">
                <div className="flex items-center gap-space-3">
                  <div className="w-8 h-8 rounded-lg bg-error-container flex items-center justify-center text-on-error-container">
                    <span aria-hidden="true" className="material-symbols-outlined text-[20px]">block</span>
                  </div>
                  <div>
                    <h2 className="font-title-md text-title-md text-on-surface">Account Status</h2>
                    <p className="font-body-sm text-body-sm text-on-surface-variant">
                      Suspend or reactivate an account.
                    </p>
                  </div>
                </div>
                {!selectedUser && (
                  <p className="font-body-sm text-body-sm text-on-surface-variant">
                    Select a user from the directory above.
                  </p>
                )}
                {selectedUser && selectedUser.is_active && (
                  <div className="flex flex-col gap-space-3">
                    <label className="font-label-sm text-label-sm text-on-surface-variant" htmlFor="suspend-reason">
                      Reason for suspension (min. 3 characters)
                    </label>
                    <textarea
                      id="suspend-reason"
                      value={suspendReason}
                      onChange={(e) => setSuspendReason(e.target.value)}
                      rows={2}
                      className="px-space-3 py-2 bg-surface-container-low border-none rounded-lg text-body-sm resize-none"
                      placeholder="e.g. Repeated policy violations"
                    />
                    <button
                      type="button"
                      disabled={suspendReason.trim().length < 3 || suspend.isPending}
                      onClick={() => suspend.mutate({ id: selectedUser.id, reason: suspendReason.trim() })}
                      className="flex items-center justify-center gap-space-2 px-space-4 py-2 rounded-lg bg-error text-on-error font-label-md text-label-md disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {suspend.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                      Suspend
                    </button>
                    {statusError && <p className="font-body-sm text-body-sm text-error">{statusError}</p>}
                  </div>
                )}
                {selectedUser && !selectedUser.is_active && (
                  <div className="flex flex-col gap-space-3">
                    <p className="font-body-sm text-body-sm text-on-surface-variant">
                      Suspended{selectedUser.suspended_reason ? `: ${selectedUser.suspended_reason}` : "."}
                    </p>
                    <button
                      type="button"
                      disabled={reactivate.isPending}
                      onClick={() => reactivate.mutate(selectedUser.id)}
                      className="flex items-center justify-center gap-space-2 px-space-4 py-2 rounded-lg bg-success text-on-primary font-label-md text-label-md disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {reactivate.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                      Reactivate
                    </button>
                    {statusError && <p className="font-body-sm text-body-sm text-error">{statusError}</p>}
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
