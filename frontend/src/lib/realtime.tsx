/**
 * Phase F -- real-time client for `GET /events/stream` (Server-Sent Events).
 *
 * Contract (see backend app/api/routes/events.py):
 *  - every event is a HINT to re-fetch a resource through the normal,
 *    authorized REST API; it is never applied as state. React Query's cache
 *    is invalidated for the affected keys and the backend stays the source
 *    of truth.
 *  - events carry a global `seq` (SSE `id:`); the browser resends the last
 *    one as `Last-Event-ID` on reconnect and the server replays what this
 *    user missed. Duplicates (seq <= last seen) are ignored here too.
 *  - `resync` (too much missed / unknown cursor) -> re-fetch everything.
 *  - `session_ended` -> stop; the auth layer handles the expired session.
 *
 * Polling is NOT the transport any more: screens that used to poll call
 * `useLiveFallbackInterval(ms)`, which returns `false` while the stream is
 * live and the old interval only while it is down -- graceful degradation,
 * not a second mechanism running all the time.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient, type QueryKey } from "@tanstack/react-query";

import { API_BASE_URL } from "./api";

export type RealtimeStatus = "off" | "connecting" | "live" | "reconnecting";

export interface RealtimeEventPayload {
  seq: number;
  type: string;
  resource_type: string;
  resource_id: string;
}

/** Which cached queries an event about `resource_type` can make stale.
 * Prefix keys -- React Query matches every query starting with them. */
export const INVALIDATION_MAP: Record<string, QueryKey[]> = {
  blood_request: [
    ["blood-requests"], ["donor", "requests"], ["donor", "responses"],
    ["hospital", "requests"], ["hospital", "activity"], ["family"],
    ["strategy-lab", "requests"], ["admin", "blood-requests"],
  ],
  emergency: [
    ["emergencies"], ["donor", "emergencies"], ["hospital", "emergencies"],
    ["admin", "emergencies"], ["family"], ["strategy-lab", "emergencies"],
  ],
  inventory: [
    ["hospital", "inventory"], ["hospital", "activity"], ["admin", "facilities"], ["map"],
  ],
  shipment: [["shipments"], ["admin", "shipments"], ["hospital", "inventory"]],
  donation: [["donor", "donations"], ["donation-reviews"], ["certificates"]],
  verification: [["verifications"], ["admin", "verifications"], ["donor", "me"], ["hospital", "me"]],
  notification: [["notifications"]],
  // Hardening: newly evented resources.
  family_link: [["family"]],
  certificate: [["certificates"], ["admin", "certificates"]],
};

export const ALL_EVENT_TYPES = [
  "blood_request.updated", "emergency.updated", "inventory.updated", "shipment.updated",
  "donation.updated", "verification.updated", "notification.created", "notification.updated",
  "family_link.updated", "certificate.updated",
];

interface RealtimeContextValue {
  status: RealtimeStatus;
  lastSeq: number | null;
}

const RealtimeContext = createContext<RealtimeContextValue>({ status: "off", lastSeq: null });

/** Minimal surface of the browser EventSource this module uses -- lets tests
 * inject a fake without touching the network. */
export interface EventSourceLike {
  readyState: number;
  onopen: ((ev: Event) => void) | null;
  onerror: ((ev: Event) => void) | null;
  addEventListener(type: string, listener: (ev: MessageEvent) => void): void;
  close(): void;
}

export type EventSourceFactory = (url: string) => EventSourceLike;

const defaultFactory: EventSourceFactory = (url) =>
  new EventSource(url, { withCredentials: true }) as unknown as EventSourceLike;

const CLOSED = 2;
const FLUSH_MS = 150;

export function RealtimeProvider({
  enabled,
  children,
  factory = defaultFactory,
  onSessionEnded,
}: {
  enabled: boolean;
  children: ReactNode;
  factory?: EventSourceFactory;
  onSessionEnded?: () => void;
}) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<RealtimeStatus>("off");
  const [lastSeq, setLastSeq] = useState<number | null>(null);
  const lastSeqRef = useRef<number>(0);
  const pending = useRef<Map<string, QueryKey>>(new Map());
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback(() => {
    flushTimer.current = null;
    const keys = [...pending.current.values()];
    pending.current.clear();
    keys.forEach((queryKey) => qc.invalidateQueries({ queryKey }));
  }, [qc]);

  const schedule = useCallback(
    (keys: QueryKey[]) => {
      keys.forEach((k) => pending.current.set(JSON.stringify(k), k));
      if (!flushTimer.current) flushTimer.current = setTimeout(flush, FLUSH_MS);
    },
    [flush],
  );

  useEffect(() => {
    if (!enabled) {
      setStatus("off");
      return;
    }
    let source: EventSourceLike | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let disposed = false;
    let hadConnection = false;

    const handle = (ev: MessageEvent) => {
      let data: RealtimeEventPayload;
      try {
        data = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (typeof data.seq !== "number" || data.seq <= lastSeqRef.current) return; // duplicate
      lastSeqRef.current = data.seq;
      setLastSeq(data.seq);
      schedule(INVALIDATION_MAP[data.resource_type] ?? []);
    };

    const connect = () => {
      if (disposed) return;
      setStatus(hadConnection ? "reconnecting" : "connecting");
      const s = factory(`${API_BASE_URL}/events/stream`);
      source = s;
      ALL_EVENT_TYPES.forEach((t) => s.addEventListener(t, handle));
      s.addEventListener("ready", (ev: MessageEvent) => {
        attempt = 0;
        try {
          const head = JSON.parse(ev.data).head_seq;
          if (typeof head === "number" && head > lastSeqRef.current) lastSeqRef.current = head;
        } catch {
          /* ignore */
        }
        if (hadConnection) {
          // Back after a gap: replay already delivered what was missed, but
          // re-fetching what is on screen is cheap insurance against stale UI.
          qc.invalidateQueries();
        }
        hadConnection = true;
        setStatus("live");
      });
      s.addEventListener("resync", () => {
        lastSeqRef.current = 0;
        qc.invalidateQueries();
      });
      s.addEventListener("session_ended", () => {
        disposed = true;
        s.close();
        setStatus("off");
        onSessionEnded?.();
      });
      s.onerror = () => {
        if (disposed) return;
        if (s.readyState === CLOSED) {
          // The browser gave up (e.g. 401/5xx on connect): retry ourselves
          // with capped backoff.
          s.close();
          setStatus("reconnecting");
          attempt += 1;
          retryTimer = setTimeout(connect, Math.min(30_000, 1000 * 2 ** Math.min(attempt, 5)));
        } else {
          setStatus("reconnecting"); // browser is auto-reconnecting with Last-Event-ID
        }
      };
    };
    connect();
    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      source?.close();
      if (flushTimer.current) {
        clearTimeout(flushTimer.current);
        flushTimer.current = null;
      }
    };
  }, [enabled, factory, qc, schedule, onSessionEnded]);

  const value = useMemo(() => ({ status, lastSeq }), [status, lastSeq]);
  return <RealtimeContext.Provider value={value}>{children}</RealtimeContext.Provider>;
}

export function useRealtimeStatus(): RealtimeStatus {
  return useContext(RealtimeContext).status;
}

/** Polling interval to use ONLY while the live stream is unavailable. */
export function useLiveFallbackInterval(ms: number): number | false {
  return useRealtimeStatus() === "live" ? false : ms;
}

/** Small, accessible connection indicator. */
export function RealtimeIndicator({ className = "" }: { className?: string }) {
  const status = useRealtimeStatus();
  if (status === "off") return null;
  const live = status === "live";
  return (
    <span
      className={`inline-flex items-center gap-1 text-[11px] font-semibold ${live ? "text-success" : "text-warning"} ${className}`}
      role="status"
      aria-live="polite"
      data-testid="realtime-status"
      title={live ? "Live updates connected" : "Live updates reconnecting — refreshing periodically"}
    >
      <span className={`h-2 w-2 rounded-full ${live ? "bg-success" : "bg-warning animate-pulse"}`} aria-hidden />
      {live ? "Live" : "Reconnecting…"}
    </span>
  );
}
