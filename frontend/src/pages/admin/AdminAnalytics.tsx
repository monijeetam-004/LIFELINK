/**
 * Admin — Analytics & Certificates Registry (Phase 6)
 *
 * Both sections are now real. The funnel/breakdown numbers come from
 * GET /admin/analytics/overview -- real GROUP BY aggregates over
 * BloodRequest, Emergency, DonorResponse, HospitalRequestResponse and
 * VerificationRequest, computed fresh on every request. A status this
 * platform has never recorded a row for legitimately reads as 0; nothing is
 * padded to look busier than it is, and there is no separate "funnel step"
 * concept invented beyond the real statuses these tables already have.
 *
 * The certificate registry is GET /admin/certificates -- a real,
 * admin-scoped search across every donor's certificates (the donor-only
 * GET /certificates a donor sees is still scoped to their own).
 */
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Menu } from "lucide-react";

import Modal, { fieldClass, ghostButtonClass, labelClass, primaryButtonClass } from "../../components/Modal";
import NotificationsBell from "../../components/NotificationsBell";
import { useAuth } from "../../lib/auth";
import { ROLE_LABEL } from "../../lib/roles";
import { toApiError } from "../../lib/api";
import {
  BLOOD_LABEL,
  getAdminAnalyticsOverview,
  listAdminCertificates,
  revokeAdminCertificate,
  verifyCertificate,
  type AdminCertificate,
  type CertificateVerification,
} from "../../lib/domain";

import { ADMIN_NAV, ACCOUNT_NAV } from "./adminNav";
import MobileNavDrawer from "../../components/MobileNavDrawer";

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  } catch {
    return iso;
  }
}

function BreakdownList({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  if (entries.length === 0) {
    return <p className="font-body-sm text-body-sm text-on-surface-variant">No rows recorded yet.</p>;
  }
  return (
    <div className="flex flex-col gap-space-2">
      {entries.map(([key, value]) => (
        <div key={key} className="flex items-center gap-space-3">
          <span className="font-label-sm text-label-sm text-on-surface-variant w-40 shrink-0 truncate">
            {key}
          </span>
          <div className="flex-1 h-2 rounded-full bg-surface-container-low overflow-hidden">
            <div
              className="h-full bg-primary rounded-full"
              style={{ width: `${(value / max) * 100}%` }}
            />
          </div>
          <span className="font-data-mono text-label-sm text-on-surface w-8 text-right shrink-0">
            {value}
          </span>
        </div>
      ))}
    </div>
  );
}

/**
 * Phase 31.1: admin-only revoke action, wired to the existing
 * POST /admin/certificates/{id}/revoke endpoint (no new backend route).
 * Explains the consequence, requires a real (min. 3 character) reason, and
 * never shows an optimistic "revoked" state before the backend confirms it
 * -- the registry list is only invalidated (re-fetched) on real success.
 */
function RevokeCertificateModal({
  cert,
  onClose,
}: {
  cert: AdminCertificate | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [reason, setReason] = useState("");

  useEffect(() => {
    setReason("");
  }, [cert?.id]);

  const revoke = useMutation({
    mutationFn: (r: string) => revokeAdminCertificate(cert!.id, r),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "certificates", "list"] });
      onClose();
    },
  });

  return (
    <Modal
      open={!!cert}
      onClose={revoke.isPending ? () => {} : onClose}
      title="Revoke certificate"
      width="max-w-md"
    >
      {cert && (
        <div className="flex flex-col gap-4">
          <div className="flex items-start gap-3 rounded-lg bg-error/10 text-error p-3">
            <span aria-hidden="true" className="material-symbols-outlined text-[20px] flex-shrink-0">warning</span>
            <p className="text-sm">
              This marks certificate <span className="font-mono">{cert.serial}</span>
              {cert.donor_name ? ` (issued to ${cert.donor_name})` : ""} as no longer valid.
              Anyone who checks it afterwards -- including the public verification page -- will
              immediately see it as revoked. This doesn't delete the certificate or the
              underlying donation record, and can't be undone from here.
            </p>
          </div>
          <div>
            <label className={labelClass} htmlFor="revoke-reason">
              Reason for revocation (required, min. 3 characters)
            </label>
            <textarea
              id="revoke-reason"
              className={fieldClass}
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. Donation could not be confirmed by the hospital"
            />
          </div>
          {revoke.isError && (
            <p className="text-error text-body-sm" role="alert">
              {toApiError(revoke.error).detail}
            </p>
          )}
          <div className="flex justify-end gap-3 pt-1">
            <button type="button" className={ghostButtonClass} onClick={onClose} disabled={revoke.isPending}>
              Cancel
            </button>
            <button
              type="button"
              className="bg-error hover:opacity-90 text-on-error font-label-md text-label-md px-5 py-2.5 rounded-lg transition-all active:scale-[0.99] disabled:opacity-60 flex items-center gap-2"
              disabled={reason.trim().length < 3 || revoke.isPending}
              onClick={() => revoke.mutate(reason.trim())}
            >
              {revoke.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              Revoke certificate
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}

export default function AdminAnalytics() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [code, setCode] = useState("");
  const [certQuery, setCertQuery] = useState("");
  const [revokingCert, setRevokingCert] = useState<AdminCertificate | null>(null);

  const overview = useQuery({
    queryKey: ["admin", "analytics", "overview"],
    queryFn: () => getAdminAnalyticsOverview(14),
  });

  const certificates = useQuery({
    queryKey: ["admin", "certificates", "list", certQuery],
    queryFn: () => listAdminCertificates({ query: certQuery || undefined, page_size: 20 }),
  });

  const verify = useMutation<CertificateVerification, unknown, string>({
    mutationFn: (c: string) => verifyCertificate(c.trim()),
  });

  async function handleLogout() {
    setLogoutBusy(true);
    await logout();
    navigate("/login", { replace: true });
  }

  function handleVerify() {
    if (!code.trim()) return;
    verify.mutate(code);
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
            <span className="font-title-lg text-title-lg text-on-surface truncate">Analytics</span>
            <span className="font-label-sm text-label-sm text-on-surface-variant hidden sm:block">
              Funnel &amp; certificates registry
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
                Analytics Command Center &amp; Certificate Registry
              </h1>
              <p className="font-body-md text-body-md text-on-surface-variant">
                Clinical logistics telemetry, dispatch funnels, and the donation certificate ledger.
              </p>
            </div>

            {/* Real analytics */}
            <div className="rounded-xl bg-surface-container-lowest shadow-level-1 p-space-6 flex flex-col gap-space-6">
              <div className="flex items-center gap-space-3">
                <div className="w-8 h-8 rounded-lg bg-surface-container flex items-center justify-center text-primary-container">
                  <span aria-hidden="true" className="material-symbols-outlined text-[20px]">query_stats</span>
                </div>
                <div>
                  <h2 className="font-title-lg text-title-lg text-on-surface">
                    Platform Analytics
                  </h2>
                  <p className="font-body-sm text-body-sm text-on-surface-variant">
                    Real aggregate counts from the current database, computed fresh on every load.
                  </p>
                </div>
              </div>

              {overview.isLoading && (
                <p className="font-body-sm text-body-sm text-on-surface-variant">Loading…</p>
              )}
              {overview.isError && (
                <p className="font-body-sm text-body-sm text-error">{toApiError(overview.error).detail}</p>
              )}
              {overview.data && (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-space-6">
                  <div>
                    <h3 className="font-title-sm text-title-sm text-on-surface mb-space-2">
                      Blood request status
                    </h3>
                    <BreakdownList data={overview.data.request_status_breakdown} />
                  </div>
                  <div>
                    <h3 className="font-title-sm text-title-sm text-on-surface mb-space-2">
                      Blood type demand
                    </h3>
                    <BreakdownList
                      data={Object.fromEntries(
                        Object.entries(overview.data.request_blood_type_breakdown).map(([k, v]) => [
                          BLOOD_LABEL[k as keyof typeof BLOOD_LABEL] ?? k,
                          v,
                        ])
                      )}
                    />
                  </div>
                  <div>
                    <h3 className="font-title-sm text-title-sm text-on-surface mb-space-2">
                      Emergency status
                    </h3>
                    <BreakdownList data={overview.data.emergency_status_breakdown} />
                  </div>
                  <div>
                    <h3 className="font-title-sm text-title-sm text-on-surface mb-space-2">
                      Donor responses
                    </h3>
                    <BreakdownList data={overview.data.donor_response_breakdown} />
                  </div>
                  <div>
                    <h3 className="font-title-sm text-title-sm text-on-surface mb-space-2">
                      Hospital handling status
                    </h3>
                    <BreakdownList data={overview.data.hospital_response_breakdown} />
                  </div>
                  <div>
                    <h3 className="font-title-sm text-title-sm text-on-surface mb-space-2">
                      Verifications (kind:status)
                    </h3>
                    <BreakdownList data={overview.data.verification_breakdown} />
                  </div>
                  <div className="lg:col-span-2">
                    <h3 className="font-title-sm text-title-sm text-on-surface mb-space-2">
                      Requests filed per day (last 14 days)
                    </h3>
                    {overview.data.requests_per_day.length === 0 ? (
                      <p className="font-body-sm text-body-sm text-on-surface-variant">
                        No requests filed in this window.
                      </p>
                    ) : (
                      <div className="flex items-end gap-1 h-24">
                        {overview.data.requests_per_day.map((d) => {
                          const max = Math.max(1, ...overview.data.requests_per_day.map((x) => x.count));
                          return (
                            <div
                              key={d.day}
                              title={`${d.day}: ${d.count}`}
                              className="flex-1 bg-primary rounded-t"
                              style={{ height: `${(d.count / max) * 100}%`, minHeight: d.count > 0 ? 4 : 0 }}
                            />
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* Certificates registry */}
            <div className="rounded-xl bg-surface-container-lowest shadow-level-1 p-space-6 flex flex-col gap-space-4">
              <div className="flex items-center gap-space-3">
                <div className="w-8 h-8 rounded-lg bg-primary-fixed flex items-center justify-center text-primary">
                  <span aria-hidden="true" className="material-symbols-outlined text-[20px]">workspace_premium</span>
                </div>
                <div>
                  <h2 className="font-title-lg text-title-lg text-on-surface">
                    Donation Certificates Registry
                  </h2>
                  <p className="font-body-sm text-body-sm text-on-surface-variant">
                    Cryptographically-issued donation certificates, platform-wide.
                  </p>
                </div>
              </div>

              <div className="relative flex-1 w-full">
                <span aria-hidden="true" className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant">
                  search
                </span>
                <label htmlFor="cert-registry-search" className="sr-only">
                  Search certificate serial, donor, or hospital
                </label>
                <input
                  id="cert-registry-search"
                  className="w-full pl-10 pr-4 py-2 bg-surface-container-low border-none rounded-lg text-body-sm"
                  placeholder="Search certificate serial, donor, or hospital…"
                  type="text"
                  value={certQuery}
                  onChange={(e) => setCertQuery(e.target.value)}
                />
              </div>

              {certificates.isLoading && (
                <p className="font-body-sm text-body-sm text-on-surface-variant">Loading…</p>
              )}
              {certificates.isError && (
                <p className="font-body-sm text-body-sm text-error">
                  {toApiError(certificates.error).detail}
                </p>
              )}
              {certificates.data && certificates.data.items.length === 0 && (
                <p className="font-body-sm text-body-sm text-on-surface-variant">
                  No certificates match this search.
                </p>
              )}
              {certificates.data && certificates.data.items.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-left border-collapse">
                    <thead>
                      <tr className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider border-b border-outline-variant/60">
                        <th className="py-space-2 pr-space-3">Serial</th>
                        <th className="py-space-2 pr-space-3">Donor</th>
                        <th className="py-space-2 pr-space-3">Donated on</th>
                        <th className="py-space-2 pr-space-3">Units</th>
                        <th className="py-space-2 pr-space-3">Hospital</th>
                        <th className="py-space-2 pr-space-3">Status</th>
                        <th className="py-space-2 pr-space-3 text-right">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {certificates.data.items.map((c) => (
                        <tr key={c.id} className="border-b border-outline-variant/30">
                          <td className="py-space-2 pr-space-3 font-data-mono text-body-sm text-on-surface">
                            {c.serial}
                          </td>
                          <td className="py-space-2 pr-space-3 font-body-sm text-body-sm text-on-surface">
                            {c.donor_name}
                            <span className="block text-label-sm text-on-surface-variant">{c.donor_email}</span>
                          </td>
                          <td className="py-space-2 pr-space-3 font-body-sm text-body-sm text-on-surface-variant">
                            {c.donated_on ? formatDate(c.donated_on) : "—"}
                          </td>
                          <td className="py-space-2 pr-space-3 font-data-mono text-body-sm text-on-surface">
                            {c.units ?? "—"}
                          </td>
                          <td className="py-space-2 pr-space-3 font-body-sm text-body-sm text-on-surface-variant">
                            {c.hospital_name ?? "—"}
                          </td>
                          <td className="py-space-2 pr-space-3">
                            <span
                              className={`px-2 py-0.5 rounded-full font-label-sm text-label-sm font-semibold whitespace-nowrap ${
                                c.revoked_at ? "bg-error/10 text-error" : "bg-success/15 text-success"
                              }`}
                              title={c.revoked_at && c.revocation_reason ? c.revocation_reason : undefined}
                            >
                              {c.revoked_at ? "Revoked" : "Valid"}
                            </span>
                          </td>
                          <td className="py-space-2 pr-space-3 text-right">
                            {!c.revoked_at && (
                              <button
                                type="button"
                                className="font-label-sm text-label-sm text-error hover:underline whitespace-nowrap"
                                onClick={() => setRevokingCert(c)}
                              >
                                Revoke
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {certificates.data.total > certificates.data.items.length && (
                    <p className="font-body-sm text-body-sm text-on-surface-variant pt-space-2">
                      Showing {certificates.data.items.length} of {certificates.data.total} — refine your
                      search to narrow further.
                    </p>
                  )}
                </div>
              )}

              {/* Real, working sub-tool: public certificate verification by code */}
              <div className="rounded-xl bg-surface-container-low p-space-5 flex flex-col gap-space-3">
                <div className="flex items-center gap-space-2">
                  <span aria-hidden="true" className="material-symbols-outlined text-[18px] text-secondary">verified</span>
                  <span className="font-title-md text-title-md text-on-surface">
                    Verify a certificate by code
                  </span>
                  <span className="font-label-sm text-label-sm text-secondary bg-secondary-fixed px-space-2 py-0.5 rounded-full">
                    Real
                  </span>
                </div>
                <p className="font-body-sm text-body-sm text-on-surface-variant">
                  Not a registry, but this is real and works today: paste a certificate's verify code
                  to confirm it's authentic — the same public check used at{" "}
                  <Link to="/verify/EXAMPLE" className="text-secondary underline">
                    /verify/:code
                  </Link>
                  .
                </p>
                <div className="flex flex-col sm:flex-row gap-space-2">
                  <label htmlFor="verify-code" className="sr-only">
                    Certificate verify code
                  </label>
                  <input
                    id="verify-code"
                    className={fieldClass}
                    placeholder="e.g. 8f3a1c9d…"
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleVerify()}
                  />
                  <button
                    type="button"
                    className={primaryButtonClass}
                    disabled={!code.trim() || verify.isPending}
                    onClick={handleVerify}
                  >
                    {verify.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                    Verify
                  </button>
                </div>

                {verify.isError && (
                  <div className="flex items-start gap-space-2 text-error">
                    <span aria-hidden="true" className="material-symbols-outlined text-[20px]">cancel</span>
                    <p className="font-body-sm text-body-sm">
                      {toApiError(verify.error).detail || "No certificate matches that code."}
                    </p>
                  </div>
                )}

                {verify.data && (
                  <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-space-6 gap-y-space-2 bg-surface-container-lowest rounded-lg p-space-4">
                    {verify.data.valid ? (
                      <div className="flex items-center gap-space-2 sm:col-span-2 text-success">
                        <span aria-hidden="true"
                          className="material-symbols-outlined text-[18px]"
                          style={{ fontVariationSettings: "'FILL' 1" }}
                        >
                          check_circle
                        </span>
                        <span className="font-label-md text-label-md font-bold">Authentic certificate</span>
                      </div>
                    ) : (
                      <div className="flex flex-col gap-1 sm:col-span-2 text-error">
                        <div className="flex items-center gap-space-2">
                          <span aria-hidden="true" className="material-symbols-outlined text-[18px]">gpp_bad</span>
                          <span className="font-label-md text-label-md font-bold">Certificate revoked</span>
                        </div>
                        <p className="font-body-sm text-body-sm">
                          The certificate has been revoked
                          {verify.data.revocation_reason ? `: ${verify.data.revocation_reason}` : "."}
                          {verify.data.revoked_at
                            ? ` (${new Date(verify.data.revoked_at).toLocaleDateString()})`
                            : ""}
                        </p>
                      </div>
                    )}
                    <div>
                      <dt className="font-label-sm text-label-sm text-on-surface-variant">Donor</dt>
                      <dd className="font-body-md text-body-md text-on-surface">{verify.data.donor_name}</dd>
                    </div>
                    <div>
                      <dt className="font-label-sm text-label-sm text-on-surface-variant">Serial</dt>
                      <dd className="font-data-mono text-body-sm text-on-surface">{verify.data.serial}</dd>
                    </div>
                    <div>
                      <dt className="font-label-sm text-label-sm text-on-surface-variant">Donated on</dt>
                      <dd className="font-body-md text-body-md text-on-surface">
                        {formatDate(verify.data.donated_on)}
                      </dd>
                    </div>
                    <div>
                      <dt className="font-label-sm text-label-sm text-on-surface-variant">Units</dt>
                      <dd className="font-body-md text-body-md text-on-surface">{verify.data.units}</dd>
                    </div>
                    {verify.data.hospital_name && (
                      <div className="sm:col-span-2">
                        <dt className="font-label-sm text-label-sm text-on-surface-variant">Hospital</dt>
                        <dd className="font-body-md text-body-md text-on-surface">
                          {verify.data.hospital_name}
                        </dd>
                      </div>
                    )}
                  </dl>
                )}
              </div>
            </div>
          </div>
        </main>
      </div>

      <RevokeCertificateModal cert={revokingCert} onClose={() => setRevokingCert(null)} />
    </>
  );
}
