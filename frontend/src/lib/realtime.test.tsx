/**
 * Phase F -- RealtimeProvider behaviour with an injected fake EventSource
 * (no network). The provider's own logic -- status, dedupe by seq,
 * batching, resync, session end, reconnect/backoff -- runs for real.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import {
  RealtimeIndicator,
  RealtimeProvider,
  useLiveFallbackInterval,
  type EventSourceLike,
} from "./realtime";

class FakeSource implements EventSourceLike {
  readyState = 0;
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  closed = false;
  listeners = new Map<string, ((ev: MessageEvent) => void)[]>();
  constructor(public url: string) {}
  addEventListener(type: string, l: (ev: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), l]);
  }
  close() {
    this.closed = true;
    this.readyState = 2;
  }
  emit(type: string, data: unknown) {
    (this.listeners.get(type) ?? []).forEach((l) =>
      l(new MessageEvent(type, { data: JSON.stringify(data) })),
    );
  }
  fail(closed: boolean) {
    this.readyState = closed ? 2 : 0;
    this.onerror?.(new Event("error"));
  }
}

let sources: FakeSource[];
const factory = (url: string) => {
  const s = new FakeSource(url);
  sources.push(s);
  return s;
};

function Probe() {
  const interval = useLiveFallbackInterval(20_000);
  return (
    <div>
      <span data-testid="interval">{String(interval)}</span>
      <RealtimeIndicator />
    </div>
  );
}

function setup(onSessionEnded = vi.fn()) {
  const qc = new QueryClient();
  const spy = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <RealtimeProvider enabled factory={factory} onSessionEnded={onSessionEnded}>
        <Probe />
      </RealtimeProvider>
    </QueryClientProvider>,
  );
  return { spy, onSessionEnded };
}

beforeEach(() => {
  sources = [];
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("RealtimeProvider", () => {
  it("connects to the event stream and polls only until it is live", () => {
    setup();
    expect(sources).toHaveLength(1);
    expect(sources[0].url).toMatch(/\/events\/stream$/);
    expect(screen.getByTestId("interval")).toHaveTextContent("20000");
    act(() => sources[0].emit("ready", { head_seq: 5 }));
    expect(screen.getByTestId("interval")).toHaveTextContent("false");
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("Live");
  });

  it("invalidates the mapped queries for an event, batched, and drops duplicates", () => {
    const { spy } = setup();
    act(() => sources[0].emit("ready", { head_seq: 0 }));
    spy.mockClear();
    act(() => {
      sources[0].emit("blood_request.updated", { seq: 1, type: "blood_request.updated", resource_type: "blood_request", resource_id: "r1" });
      sources[0].emit("blood_request.updated", { seq: 1, type: "blood_request.updated", resource_type: "blood_request", resource_id: "r1" });
      sources[0].emit("notification.created", { seq: 2, type: "notification.created", resource_type: "notification", resource_id: "n1" });
    });
    expect(spy).not.toHaveBeenCalled(); // batched
    act(() => vi.advanceTimersByTime(200));
    const keys = spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(["blood-requests"]));
    expect(keys).toContain(JSON.stringify(["notifications"]));
    expect(keys.filter((k) => k === JSON.stringify(["blood-requests"]))).toHaveLength(1);

    spy.mockClear();
    act(() => {
      sources[0].emit("blood_request.updated", { seq: 2, type: "blood_request.updated", resource_type: "blood_request", resource_id: "r1" });
      vi.advanceTimersByTime(200);
    });
    expect(spy).not.toHaveBeenCalled(); // seq <= last seen -> duplicate
  });

  it("hardening: certificate and family-link events refresh their screens", () => {
    const { spy } = setup();
    act(() => sources[0].emit("ready", { head_seq: 0 }));
    spy.mockClear();
    act(() => {
      sources[0].emit("certificate.updated", { seq: 1, type: "certificate.updated", resource_type: "certificate", resource_id: "c1" });
      sources[0].emit("family_link.updated", { seq: 2, type: "family_link.updated", resource_type: "family_link", resource_id: "f1" });
      vi.advanceTimersByTime(200);
    });
    const keys = spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(["certificates"]));
    expect(keys).toContain(JSON.stringify(["admin", "certificates"]));
    expect(keys).toContain(JSON.stringify(["family"]));
  });

  it("events older than the ready head are ignored (already reflected)", () => {
    const { spy } = setup();
    act(() => sources[0].emit("ready", { head_seq: 10 }));
    spy.mockClear();
    act(() => {
      sources[0].emit("shipment.updated", { seq: 9, type: "shipment.updated", resource_type: "shipment", resource_id: "s" });
      vi.advanceTimersByTime(200);
    });
    expect(spy).not.toHaveBeenCalled();
  });

  it("resync refetches everything", () => {
    const { spy } = setup();
    spy.mockClear();
    act(() => sources[0].emit("resync", { head_seq: 3, reason: "pruned" }));
    expect(spy).toHaveBeenCalledWith();
  });

  it("session_ended closes the stream and reports it", () => {
    const { onSessionEnded } = setup();
    act(() => sources[0].emit("ready", { head_seq: 0 }));
    act(() => sources[0].emit("session_ended", {}));
    expect(sources[0].closed).toBe(true);
    expect(onSessionEnded).toHaveBeenCalled();
    expect(screen.queryByTestId("realtime-status")).toBeNull();
  });

  it("falls back to polling while reconnecting and refetches on return", () => {
    const { spy } = setup();
    act(() => sources[0].emit("ready", { head_seq: 0 }));
    act(() => sources[0].fail(false)); // browser auto-reconnect in progress
    expect(screen.getByTestId("realtime-status")).toHaveTextContent("Reconnecting");
    expect(screen.getByTestId("interval")).toHaveTextContent("20000");
    spy.mockClear();
    act(() => sources[0].emit("ready", { head_seq: 4 }));
    expect(spy).toHaveBeenCalledWith(); // stale-state insurance after a gap
    expect(screen.getByTestId("interval")).toHaveTextContent("false");
  });

  it("retries with backoff when the browser gives up (e.g. 401/5xx on connect)", () => {
    setup();
    act(() => sources[0].fail(true));
    expect(sources[0].closed).toBe(true);
    expect(sources).toHaveLength(1);
    act(() => vi.advanceTimersByTime(2_000));
    expect(sources).toHaveLength(2);
  });

  it("does not connect when signed out", () => {
    const qc = new QueryClient();
    render(
      <QueryClientProvider client={qc}>
        <RealtimeProvider enabled={false} factory={factory}>
          <Probe />
        </RealtimeProvider>
      </QueryClientProvider>,
    );
    expect(sources).toHaveLength(0);
    expect(screen.getByTestId("interval")).toHaveTextContent("20000");
  });
});
