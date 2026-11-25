/**
 * Notification Center (full page)
 * Adapted from: lifelink_0.4_notifications_center/code.html
 * Same data as the header bell (GET /notifications, mark read/all), just a full list
 * with an all/unread filter instead of a dropdown.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";

import { toApiError } from "../../lib/api";
import {
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  type AppNotification,
} from "../../lib/domain";

// Phase 18: kept in sync with every `type=` value actually passed to
// notification_service.notify()/notify_matching_donors() across the backend
// (verified by grep, not guessed) -- an entry here is cosmetic only, so a
// type that's missing safely falls back to the generic "notifications" icon
// below rather than breaking anything.
const TYPE_ICON: Record<string, string> = {
  "blood_request.new": "bloodtype",
  "blood_request.incoming": "bloodtype",
  "blood_request.donor_accepted": "volunteer_activism",
  "blood_request.you_accepted": "check_circle",
  "blood_request.donor_withdrew": "person_remove",
  "blood_request.cancelled": "cancel",
  "blood_request.fulfilled": "task_alt",
  "blood_request.filed_for_you": "person_add",
  "blood_request.hospital_update": "local_hospital",
  "family.link_requested": "group_add",
  "family.link_accepted": "how_to_reg",
  "family.link_rejected": "person_off",
  "family.link_revoked": "link_off",
  "family.link_expired": "schedule",
  "emergency.new": "emergency",
  "emergency.acknowledged": "how_to_reg",
  "emergency.responder_accepted": "volunteer_activism",
  "emergency.you_responded": "check_circle",
  "emergency.responder_withdrew": "person_remove",
  "emergency.assistance_found": "task_alt",
  "emergency.pathway_reoptimized": "alt_route",
  "emergency.cancelled": "cancel",
  "emergency.resolved": "task_alt",
  "verification.approved": "verified",
  "verification.rejected": "gpp_bad",
  "verification.submitted": "fact_check",
  "certificate.ready": "workspace_premium",
  "appointment.requested": "event",
  "appointment.cancelled": "event_busy",
  "account.password_changed": "lock_reset",
};

function timeAgo(iso: string): string {
  const s = Math.max(1, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function NotificationsCenter() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<"all" | "unread">("all");

  const list = useQuery({
    queryKey: ["notifications", "list"],
    queryFn: () => listNotifications(false),
  });

  const [actionError, setActionError] = useState("");

  const readOne = useMutation({
    mutationFn: (id: string) => markNotificationRead(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
      setActionError("");
    },
    // Phase 21 fix: this mutation had no onError -- a failed "mark read"
    // used to fail completely silently, leaving the item shown unread with
    // no indication anything went wrong.
    onError: (e) => setActionError(toApiError(e).detail),
  });
  const readAll = useMutation({
    mutationFn: markAllNotificationsRead,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
      setActionError("");
    },
    onError: (e) => setActionError(toApiError(e).detail),
  });

  const items = (list.data ?? []).filter((n) => (filter === "unread" ? !n.read_at : true));
  const unreadCount = (list.data ?? []).filter((n) => !n.read_at).length;

  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-30 h-16 bg-surface/90 backdrop-blur-md border-b border-border-subtle flex items-center justify-between px-margin-mobile md:px-margin-desktop">
        <div className="flex items-center gap-4">
          <Link
            to="/"
            aria-label="Back to dashboard"
            className="p-2 rounded-full hover:bg-surface-container-low text-on-surface-variant"
          >
            <ArrowLeft className="h-5 w-5" />
          </Link>
          <h1 className="font-headline-md text-headline-md text-on-surface">Notifications</h1>
        </div>
        <button
          className="text-secondary text-sm font-label-md hover:underline disabled:opacity-40"
          disabled={unreadCount === 0 || readAll.isPending}
          onClick={() => readAll.mutate()}
        >
          Mark all read
        </button>
      </header>

      <main className="max-w-2xl mx-auto p-margin-mobile md:p-margin-desktop space-y-4">
        <div className="flex items-center gap-2">
          {(["all", "unread"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-4 py-1.5 rounded-full text-sm font-label-md transition-colors ${
                filter === f ? "bg-primary text-on-primary" : "bg-surface-container text-on-surface-variant hover:bg-surface-container-high"
              }`}
            >
              {f === "all" ? "All" : `Unread (${unreadCount})`}
            </button>
          ))}
        </div>

        {actionError && (
          <p role="alert" className="text-error text-sm text-center">
            {actionError}
          </p>
        )}
        {list.isLoading && <p className="text-center text-on-surface-variant py-8">Loading…</p>}
        {list.isError && (
          <div role="alert" className="text-center py-8">
            <p className="text-error mb-2">Couldn't load notifications.</p>
            <button
              type="button"
              className="text-secondary text-sm font-label-md hover:underline"
              onClick={() => list.refetch()}
            >
              Retry
            </button>
          </div>
        )}
        {list.data && items.length === 0 && (
          <p className="text-center text-on-surface-variant py-12">
            {filter === "unread" ? "You're all caught up." : "No notifications yet."}
          </p>
        )}

        <div className="bg-surface rounded-xl shadow-soft border border-border-subtle divide-y divide-border-subtle overflow-hidden">
          {items.map((n: AppNotification) => (
            <div key={n.id} className={`flex items-start gap-4 px-5 py-4 ${n.read_at ? "opacity-60" : ""}`}>
              <span className="w-9 h-9 rounded-full bg-surface-container text-on-surface flex items-center justify-center flex-shrink-0">
                <span aria-hidden="true" className="material-symbols-outlined text-[18px]">{TYPE_ICON[n.type] ?? "notifications"}</span>
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <p className="font-label-md text-label-md text-on-surface truncate">{n.title}</p>
                  <span className="text-xs text-on-surface-variant flex-shrink-0">{timeAgo(n.created_at)}</span>
                </div>
                {n.body && <p className="text-sm text-on-surface-variant mt-0.5">{n.body}</p>}
              </div>
              {!n.read_at && (
                <button
                  className="text-secondary text-xs font-label-md hover:underline flex-shrink-0 disabled:opacity-50"
                  disabled={readOne.isPending && readOne.variables === n.id}
                  onClick={() => readOne.mutate(n.id)}
                >
                  Mark read
                </button>
              )}
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
