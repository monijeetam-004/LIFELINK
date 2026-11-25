/**
 * Admin — Verifications Queue & Review Drawer (Stitch 4.3 / 4.11)
 *
 * The verification system now exists end-to-end: listAdminVerifications,
 * getAdminVerification, adminVerificationDocumentUrl, approveAdminVerification
 * and rejectAdminVerification are real API calls. The backend only has two
 * verification kinds — DONOR_IDENTITY and HOSPITAL_FACILITY — so the type
 * tabs reflect that (no invented "Practitioner" kind). "Export Log" and
 * "Advanced Filter" stay disabled: no such endpoints exist.
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
  listAdminVerifications,
  getAdminVerification,
  adminVerificationDocumentUrl,
  approveAdminVerification,
  rescanAdminVerificationDocument,
  rejectAdminVerification,
  type VerificationKind,
  type VerificationStatus,
} from "../../lib/domain";

import { ADMIN_NAV, ACCOUNT_NAV } from "./adminNav";
import MobileNavDrawer from "../../components/MobileNavDrawer";

const VERIFICATION_TABS: { label: string; value: VerificationKind | undefined }[] = [
  { label: "All types", value: undefined },
  { label: "Donor identity", value: "DONOR_IDENTITY" },
  { label: "Hospital facility", value: "HOSPITAL_FACILITY" },
];

const KIND_LABEL: Record<VerificationKind, string> = {
  DONOR_IDENTITY: "Donor identity",
  HOSPITAL_FACILITY: "Hospital facility",
};

const STATUS_BADGE: Record<VerificationStatus, string> = {
  PENDING: "bg-warning/15 text-warning",
  APPROVED: "bg-success/15 text-success",
  REJECTED: "bg-error/15 text-error",
};

function timeAgo(iso: string): string {
  const s = Math.max(1, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

const SCAN_LABEL: Record<string, string> = {
  CLEAN: "Malware scan: clean",
  PENDING_SCAN: "Malware scan: pending — withheld until scanned",
  SCAN_FAILED: "Malware scan failed — quarantined, not viewable",
  INFECTED: "Malware detected — permanently withheld",
};

export default function VerificationsQueue() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [activeTab, setActiveTab] = useState<VerificationKind | undefined>(undefined);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [approveNote, setApproveNote] = useState("");
  const [actionError, setActionError] = useState("");

  const queue = useQuery({
    queryKey: ["admin", "verifications", "queue", activeTab],
    queryFn: () => listAdminVerifications(activeTab ? { kind: activeTab } : undefined),
  });

  const detail = useQuery({
    queryKey: ["admin", "verifications", "detail", selectedId],
    queryFn: () => getAdminVerification(selectedId as string),
    enabled: !!selectedId,
  });

  function selectItem(id: string) {
    setSelectedId(id);
    setDrawerOpen(true);
    setRejectOpen(false);
    setRejectReason("");
    setApproveNote("");
    setActionError("");
  }

  function refreshQueue() {
    qc.invalidateQueries({ queryKey: ["admin", "verifications"] });
  }

  const allClean = (detail.data?.documents ?? []).every((d) => d.scan_status === "CLEAN");

  const approve = useMutation({
    mutationFn: (id: string) => approveAdminVerification(id, approveNote.trim() || undefined),
    onSuccess: () => {
      setActionError("");
      setApproveNote("");
      refreshQueue();
    },
    onError: (e) => setActionError(toApiError(e).detail),
  });

  const rescan = useMutation({
    mutationFn: ({ id, index }: { id: string; index: number }) => rescanAdminVerificationDocument(id, index),
    onSuccess: () => {
      setActionError("");
      refreshQueue();
    },
    onError: (e) => setActionError(toApiError(e).detail),
  });

  const reject = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) => rejectAdminVerification(id, reason),
    onSuccess: () => {
      setActionError("");
      setRejectOpen(false);
      setRejectReason("");
      refreshQueue();
    },
    onError: (e) => setActionError(toApiError(e).detail),
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
            <span className="font-title-lg text-title-lg text-on-surface truncate">Verifications</span>
            <span className="font-label-sm text-label-sm text-on-surface-variant hidden sm:block">
              Review queue &amp; credential drawer
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
            {/* Page header */}
            <div className="flex flex-col md:flex-row md:items-end justify-between gap-space-4">
              <div>
                <h1 className="font-headline-lg text-headline-lg text-on-surface">Verification Queue</h1>
                <p className="font-body-md text-body-md text-on-surface-variant">
                  Review and validate clinical credentials and donor identities.
                </p>
              </div>
              <div className="flex gap-space-2">
                <button
                  type="button"
                  disabled
                  title="No admin API to filter by yet"
                  aria-disabled="true"
                  className="bg-surface-container-lowest border border-outline-variant text-on-surface-variant px-space-4 py-2 rounded-xl font-label-md text-label-md flex items-center gap-space-2 cursor-not-allowed opacity-60"
                >
                  <span aria-hidden="true" className="material-symbols-outlined text-[18px]">filter_list</span>
                  Advanced Filter
                </button>
                <button
                  type="button"
                  disabled
                  title="No audit-log/export endpoint yet"
                  aria-disabled="true"
                  className="bg-surface-container-lowest border border-outline-variant text-on-surface-variant px-space-4 py-2 rounded-xl font-label-md text-label-md flex items-center gap-space-2 cursor-not-allowed opacity-60"
                >
                  <span aria-hidden="true" className="material-symbols-outlined text-[18px]">download</span>
                  Export Log
                </button>
              </div>
            </div>

            {/* Type filter */}
            <div className="bg-surface-container-lowest p-space-4 rounded-xl shadow-level-1 flex flex-col md:flex-row gap-space-4 items-center">
              <div className="flex items-center gap-space-2 w-full overflow-x-auto">
                {VERIFICATION_TABS.map((tab) => (
                  <button
                    key={tab.label}
                    type="button"
                    aria-pressed={activeTab === tab.value}
                    onClick={() => setActiveTab(tab.value)}
                    className={`px-space-4 py-2 rounded-full font-label-md text-label-md whitespace-nowrap transition-colors ${
                      activeTab === tab.value
                        ? "bg-surface-container-high text-on-surface"
                        : "bg-surface-container-low text-on-surface-variant hover:bg-surface-container"
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Split: queue list + review drawer */}
            <div className="grid grid-cols-1 xl:grid-cols-12 gap-space-6 items-start">
              <div className={drawerOpen ? "xl:col-span-7" : "xl:col-span-12"}>
                <div className="rounded-xl bg-surface-container-lowest shadow-level-1 p-space-6 flex flex-col gap-space-2">
                  {queue.isLoading && (
                    <p className="font-body-sm text-body-sm text-on-surface-variant">Loading queue…</p>
                  )}
                  {queue.isError && (
                    <p className="font-body-sm text-body-sm text-error">{toApiError(queue.error).detail}</p>
                  )}
                  {queue.data && queue.data.length === 0 && (
                    <p className="font-body-sm text-body-sm text-on-surface-variant">
                      No verification requests match this filter.
                    </p>
                  )}
                  {queue.data &&
                    queue.data.map((v) => (
                      <button
                        key={v.id}
                        type="button"
                        onClick={() => selectItem(v.id)}
                        aria-pressed={selectedId === v.id}
                        className={`w-full text-left flex items-center justify-between gap-space-3 p-space-4 rounded-xl border transition-colors ${
                          selectedId === v.id
                            ? "border-secondary bg-primary-container/20"
                            : "border-outline-variant/40 hover:bg-surface-container-low"
                        }`}
                      >
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-space-2">
                            <span className="font-label-lg text-label-lg text-on-surface font-semibold">
                              {KIND_LABEL[v.kind]}
                            </span>
                            <span className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${STATUS_BADGE[v.status]}`}>
                              {v.status}
                            </span>
                          </div>
                          <p className="font-body-sm text-body-sm text-on-surface-variant truncate">
                            {v.note || "No note provided."}
                          </p>
                        </div>
                        <span className="font-data-mono text-label-sm text-on-surface-variant shrink-0">
                          {timeAgo(v.created_at)}
                        </span>
                      </button>
                    ))}
                  {!drawerOpen && (
                    <div className="mt-space-4 flex justify-center">
                      <button
                        type="button"
                        onClick={() => setDrawerOpen(true)}
                        className="flex items-center gap-space-2 px-space-5 py-2.5 rounded-full bg-surface-container hover:bg-surface-container-high transition-colors font-label-md text-label-md text-on-surface"
                      >
                        <span aria-hidden="true" className="material-symbols-outlined text-[18px]">open_in_new</span>
                        Open review drawer
                      </button>
                    </div>
                  )}
                </div>
              </div>

              {drawerOpen && (
                <div className="xl:col-span-5">
                  <div className="rounded-xl bg-surface-container-lowest shadow-level-2 overflow-hidden flex flex-col">
                    <div className="flex items-center justify-between px-space-6 py-space-4 border-b border-outline-variant/50">
                      <div className="flex items-center gap-space-3">
                        <span aria-hidden="true" className="material-symbols-outlined text-[20px] text-on-surface-variant">
                          fact_check
                        </span>
                        <div>
                          <h2 className="font-title-lg text-title-lg text-on-surface">Review Drawer</h2>
                          <span className="font-data-mono text-label-sm text-on-surface-variant">
                            {selectedId ? `#${selectedId.slice(0, 8)}` : "No item selected"}
                          </span>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => setDrawerOpen(false)}
                        aria-label="Close review drawer"
                        className="w-8 h-8 rounded-lg bg-surface-container-low hover:bg-surface-container flex items-center justify-center text-on-surface transition-colors"
                      >
                        <span aria-hidden="true" className="material-symbols-outlined text-[20px]">close</span>
                      </button>
                    </div>
                    <div className="p-space-6 flex flex-col gap-space-4">
                      {!selectedId && (
                        <p className="font-body-sm text-body-sm text-on-surface-variant">
                          Select a queue item on the left to review its details.
                        </p>
                      )}
                      {selectedId && detail.isLoading && (
                        <p className="font-body-sm text-body-sm text-on-surface-variant">Loading…</p>
                      )}
                      {selectedId && detail.isError && (
                        <p className="font-body-sm text-body-sm text-error">{toApiError(detail.error).detail}</p>
                      )}
                      {selectedId && detail.data && (
                        <>
                          <div className="flex items-center gap-space-2">
                            <span className="font-label-lg text-label-lg text-on-surface font-semibold">
                              {KIND_LABEL[detail.data.kind]}
                            </span>
                            <span
                              className={`px-2 py-0.5 rounded-full text-[11px] font-bold ${STATUS_BADGE[detail.data.status]}`}
                            >
                              {detail.data.status}
                            </span>
                          </div>
                          <p className="font-body-sm text-body-sm text-on-surface-variant">
                            Submitted {timeAgo(detail.data.created_at)} · version {detail.data.version}
                            {detail.data.supersedes_request_id && " (resubmission — earlier versions are kept unchanged)"}
                          </p>
                          {detail.data.note && (
                            <div>
                              <span className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">
                                Submitter note
                              </span>
                              <p className="font-body-sm text-body-sm text-on-surface mt-1">{detail.data.note}</p>
                            </div>
                          )}
                          <div>
                            <span className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">
                              Documents
                            </span>
                            {detail.data.documents.length === 0 && (
                              <p className="font-body-sm text-body-sm text-on-surface-variant mt-1">
                                No documents attached.
                              </p>
                            )}
                            <ul className="flex flex-col gap-space-1 mt-1">
                              {detail.data.documents.map((doc, idx) => (
                                <li key={idx} data-testid={`verification-doc-${idx}`}>
                                  {doc.scan_status === "CLEAN" ? (
                                    <a
                                      href={adminVerificationDocumentUrl(detail.data!.id, idx)}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="flex items-center gap-space-2 font-label-md text-label-md text-secondary hover:underline"
                                    >
                                      <span className="material-symbols-outlined text-[18px]" aria-hidden="true">description</span>
                                      {doc.filename}
                                      <span className="font-body-sm text-body-sm text-on-surface-variant">
                                        ({Math.round(doc.size / 1024)} KB)
                                      </span>
                                    </a>
                                  ) : (
                                    // Hardening: a document that has not passed malware
                                    // scanning is never offered as a link (the backend
                                    // refuses it too: 409).
                                    <span className="flex items-center gap-space-2 font-label-md text-label-md text-on-surface-variant">
                                      <span className="material-symbols-outlined text-[18px]" aria-hidden="true">lock</span>
                                      {doc.filename}
                                    </span>
                                  )}
                                  <p className="font-body-sm text-[12px] mt-0.5" data-testid="scan-status">
                                    {SCAN_LABEL[doc.scan_status]}
                                    {doc.scan_status === "CLEAN" && !doc.malware_scanned && " (not scanned — scanning disabled in this environment)"}
                                  </p>
                                  {(doc.scan_status === "SCAN_FAILED" || doc.scan_status === "PENDING_SCAN") && (
                                    <button
                                      type="button"
                                      disabled={rescan.isPending}
                                      onClick={() => rescan.mutate({ id: detail.data!.id, index: idx })}
                                      className="mt-1 text-secondary font-label-sm text-label-sm hover:underline disabled:opacity-50"
                                    >
                                      {rescan.isPending ? "Rescanning…" : "Retry malware scan"}
                                    </button>
                                  )}
                                  <p
                                    className="font-data-mono text-[11px] text-on-surface-variant break-all"
                                    title="SHA-256 recorded at submission; re-checked on every download"
                                  >
                                    {doc.sha256
                                      ? `sha256 ${doc.sha256.slice(0, 16)}…`
                                      : "No checksum (submitted before integrity tracking)"}
                                  </p>
                                </li>
                              ))}
                            </ul>
                          </div>
                          {detail.data.status !== "PENDING" && (
                            <div className="rounded-lg bg-surface-container-low p-space-3">
                              <span className="font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">
                                Review outcome
                              </span>
                              <p className="font-body-sm text-body-sm text-on-surface mt-1">
                                {detail.data.review_note || "No review note left."}
                              </p>
                              {detail.data.reviewer_user_id && (
                                <p className="font-data-mono text-label-sm text-on-surface-variant mt-1">
                                  Reviewer: {detail.data.reviewer_user_id.slice(0, 8)}
                                </p>
                              )}
                            </div>
                          )}
                          {!rejectOpen && detail.data.status === "PENDING" && (
                            <div className="flex flex-col gap-space-2">
                              <label
                                htmlFor="approve-note"
                                className="font-label-sm text-label-sm text-on-surface-variant"
                              >
                                Approval note (optional)
                              </label>
                              <input
                                id="approve-note"
                                value={approveNote}
                                onChange={(e) => setApproveNote(e.target.value)}
                                className="px-space-3 py-2 bg-surface-container-low border-none rounded-lg text-body-sm"
                                placeholder="Optional note for the record"
                              />
                            </div>
                          )}
                          {rejectOpen && detail.data.status === "PENDING" && (
                            <div className="flex flex-col gap-space-2">
                              <label
                                htmlFor="reject-reason"
                                className="font-label-sm text-label-sm text-on-surface-variant"
                              >
                                Rejection reason (min. 3 characters)
                              </label>
                              <textarea
                                id="reject-reason"
                                value={rejectReason}
                                onChange={(e) => setRejectReason(e.target.value)}
                                rows={2}
                                className="px-space-3 py-2 bg-surface-container-low border-none rounded-lg text-body-sm resize-none"
                                placeholder="Why is this being rejected?"
                              />
                            </div>
                          )}
                          {actionError && <p className="font-body-sm text-body-sm text-error">{actionError}</p>}
                        </>
                      )}
                    </div>
                    <div className="px-space-6 pb-space-6 flex gap-space-3">
                      <button
                        type="button"
                        disabled={
                          !detail.data || detail.data.status !== "PENDING" || approve.isPending || !allClean
                        }
                        onClick={() => selectedId && approve.mutate(selectedId)}
                        title={
                          !detail.data || detail.data.status !== "PENDING"
                            ? "No pending verification selected"
                            : !allClean
                              ? "Every document must pass malware scanning before approval"
                              : undefined
                        }
                        className="flex-1 flex items-center justify-center gap-space-2 bg-success text-on-primary py-2 rounded-lg font-label-md text-label-md disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {approve.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                        Approve
                      </button>
                      {!rejectOpen && (
                        <button
                          type="button"
                          disabled={!detail.data || detail.data.status !== "PENDING"}
                          onClick={() => setRejectOpen(true)}
                          title={!detail.data || detail.data.status !== "PENDING" ? "No pending verification selected" : undefined}
                          className="flex-1 border border-error text-error py-2 rounded-lg font-label-md text-label-md disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          Reject
                        </button>
                      )}
                      {rejectOpen && (
                        <button
                          type="button"
                          disabled={rejectReason.trim().length < 3 || reject.isPending}
                          onClick={() => selectedId && reject.mutate({ id: selectedId, reason: rejectReason.trim() })}
                          className="flex-1 flex items-center justify-center gap-space-2 bg-error text-on-error py-2 rounded-lg font-label-md text-label-md disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {reject.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                          Confirm reject
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </main>
      </div>
    </>
  );
}
