import { useEffect, useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import {
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  unreadCount,
  type AppNotification,
} from "../lib/domain";
import { resolveNotificationPath } from "../lib/notificationLinks";
import { RealtimeIndicator, useLiveFallbackInterval } from "../lib/realtime";
import { useInFlightIds } from "../lib/inflight";

function timeAgo(iso: string): string {
  const s = Math.max(1, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/**
 * Live notifications control. Renders a bell button (using the caller's classes so
 * it drops into each dashboard's existing header) plus a dropdown panel.
 * Polls unread count; loads the list when opened.
 */
export default function NotificationsBell({
  buttonClassName = "",
}: {
  buttonClassName?: string;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const countId = useId();

  const countQuery = useQuery({
    queryKey: ["notifications", "unread"],
    queryFn: unreadCount,
    // Phase F: live updates arrive over the event stream; this interval only
    // applies while that stream is down (graceful degradation).
    refetchInterval: useLiveFallbackInterval(30_000),
  });

  const listQuery = useQuery({
    queryKey: ["notifications", "list"],
    queryFn: () => listNotifications(false),
    enabled: open,
  });

  // Phase G: per-item in-flight tracking. Previously the per-item "Read"
  // button had no pending guard at all, so a double-click fired two
  // requests for the same notification. Each id is locked from click until
  // its own request settles; other items stay clickable.
  const reading = useInFlightIds();
  const readOne = useMutation({
    mutationFn: (id: string) => markNotificationRead(id),
    onMutate: (id: string) => reading.start(id),
    onSettled: (_data, _err, id) => reading.done(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
  const readAll = useMutation({
    mutationFn: markAllNotificationsRead,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    // Phase J: the panel could only be dismissed with a mouse. Escape now
    // closes it from anywhere inside or on the toggle and hands focus back
    // to the bell (keyboard and screen-reader users were left stranded).
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const count = countQuery.data ?? 0;
  const items: AppNotification[] = listQuery.data ?? [];

  return (
    <div className="relative" ref={wrapRef}>
      <button
        ref={buttonRef}
        type="button"
        aria-label="Notifications"
        // Phase J: a disclosure must say whether it is open and what it
        // controls; the unread count (previously only a visual badge) is
        // announced as the button's description.
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-describedby={countId}
        className={buttonClassName}
        onClick={() => setOpen((o) => !o)}
      >
        <span id={countId} className="sr-only">
          {countQuery.isError
            ? "Unread count unavailable"
            : `${count} unread`}
        </span>
        <span aria-hidden="true" className="material-symbols-outlined">notifications</span>
        {/* Phase 18: a failed unread-count fetch must never render as a
            confident "0" -- that would look identical to "you're all caught
            up" when the truth is "unknown, the request failed" (exactly
            the kind of dependency outage Phase 17 hardened the backend to
            report honestly as a 503 rather than a silent success). Shown
            as a small neutral "!" instead of a number so it's visibly
            different from a real unread count of any size. */}
        {countQuery.isError && (
          <span
            title="Couldn't check for new notifications"
            className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 rounded-full bg-on-surface-variant text-white text-[10px] font-bold flex items-center justify-center"
          >
            !
          </span>
        )}
        {!countQuery.isError && count > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 rounded-full bg-primary text-white text-[10px] font-bold flex items-center justify-center">
            {count > 99 ? "99+" : count}
          </span>
        )}
      </button>

      {open && (
        <div
          id={panelId}
          role="region"
          aria-label="Notifications panel"
          className="absolute right-0 mt-2 w-80 max-h-[26rem] overflow-y-auto rounded-xl border border-border-subtle bg-surface shadow-xl z-50 text-on-surface">
          <div className="flex items-center justify-between px-4 py-3 border-b border-border-subtle">
            <span className="flex items-center gap-2">
              <span className="font-label-md text-label-md">Notifications</span>
              <RealtimeIndicator />
            </span>
            <button
              className="text-secondary text-xs font-label-md hover:underline disabled:opacity-40"
              disabled={count === 0 || readAll.isPending}
              onClick={() => readAll.mutate()}
            >
              Mark all read
            </button>
          </div>
          {listQuery.isLoading && (
            <div className="px-4 py-6 text-sm text-on-surface-variant">Loading…</div>
          )}
          {listQuery.isError && (
            <div className="px-4 py-6 text-sm text-center">
              <p className="text-error mb-2">Couldn't load notifications.</p>
              <button
                type="button"
                className="text-secondary text-xs font-label-md hover:underline"
                onClick={() => listQuery.refetch()}
              >
                Retry
              </button>
            </div>
          )}
          {!listQuery.isLoading && !listQuery.isError && items.length === 0 && (
            <div className="px-4 py-8 text-sm text-on-surface-variant text-center">
              You're all caught up.
            </div>
          )}
          <ul className="divide-y divide-border-subtle">
            {items.map((n) => {
              // Phase 19: a type with a real destination page (currently
              // just verification.submitted -> /admin/verifications) makes
              // the title a link; everything else stays plain text rather
              // than linking somewhere that doesn't actually exist yet.
              const path = resolveNotificationPath(n.type);
              return (
                <li
                  key={n.id}
                  className={`px-4 py-3 flex gap-3 ${n.read_at ? "opacity-60" : ""}`}
                >
                  <span
                    className={`mt-1.5 h-2 w-2 rounded-full flex-shrink-0 ${
                      n.read_at ? "bg-transparent" : "bg-primary"
                    }`}
                  />
                  <div className="min-w-0 flex-1">
                    {path ? (
                      <Link
                        to={path}
                        onClick={() => setOpen(false)}
                        className="font-label-md text-label-md truncate block hover:underline text-secondary"
                      >
                        {n.title}
                      </Link>
                    ) : (
                      <p className="font-label-md text-label-md truncate">{n.title}</p>
                    )}
                    {n.body && (
                      <p className="text-xs text-on-surface-variant mt-0.5 line-clamp-2">{n.body}</p>
                    )}
                    <p className="text-[11px] text-on-surface-variant mt-1">{timeAgo(n.created_at)}</p>
                  </div>
                  {!n.read_at && (
                    <button
                      className="text-secondary text-[11px] font-label-md hover:underline self-start disabled:opacity-40"
                      disabled={reading.has(n.id)}
                      onClick={() => {
                        if (reading.has(n.id)) return;
                        readOne.mutate(n.id);
                      }}
                    >
                      {reading.has(n.id) ? "Reading…" : "Read"}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
