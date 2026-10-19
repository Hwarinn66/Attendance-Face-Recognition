import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { describeLiveEvent, parseLiveEvent } from "@/lib/live";

import { LIVE_STREAM_URL, LiveIndicator, LiveToasts, useLiveUpdates } from "./LiveUpdates";

class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];
  readyState = FakeEventSource.CONNECTING;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private listeners = new Map<string, ((e: MessageEvent<string>) => void)[]>();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: MessageEvent<string>) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }
  close() {
    this.readyState = FakeEventSource.CLOSED;
  }
  open() {
    this.readyState = FakeEventSource.OPEN;
    this.onopen?.();
  }
  emit(data: object) {
    for (const fn of this.listeners.get("attendance") ?? []) fn(new MessageEvent("attendance", { data: JSON.stringify(data) }));
  }
  fail({ closed }: { closed: boolean }) {
    this.readyState = closed ? FakeEventSource.CLOSED : FakeEventSource.CONNECTING;
    this.onerror?.();
  }
}

function Harness() {
  const live = useLiveUpdates({ enabled: true, myId: "admin-1" });
  return (
    <>
      <LiveIndicator status={live.status} />
      <LiveToasts toasts={live.toasts} onDismiss={live.dismiss} />
    </>
  );
}

function setup() {
  const qc = new QueryClient();
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <Harness />
    </QueryClientProvider>,
  );
  const es = FakeEventSource.instances.at(-1)!;
  return { invalidate, es, refreshed: () => invalidate.mock.calls.some(([f]) => JSON.stringify(f?.queryKey) === '["overview"]') };
}

const indicator = () => document.querySelector("[data-live-status]")?.getAttribute("data-live-status");
const CHECK_IN = { kind: "check_in", at: "2026-10-12T01:03:00Z", user_id: "u2", user_name: "Dewi Lestari", status: "on_time" };

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("live dashboard updates", () => {
  it("refreshes the dashboard and shows who checked in, without a page reload", () => {
    const { invalidate, es, refreshed } = setup();
    expect(es.url).toBe(LIVE_STREAM_URL);
    expect(LIVE_STREAM_URL).toBe("/api/v1/events/attendance");
    act(() => es.open());
    expect(indicator()).toBe("live");
    expect(screen.getByText("Langsung")).toBeInTheDocument();
    expect(invalidate).not.toHaveBeenCalled(); // the first connection does not refetch what was just loaded

    act(() => es.emit(CHECK_IN));
    expect(screen.getByText(/^Dewi Lestari check-in \d{2}\.\d{2}$/)).toBeInTheDocument();
    act(() => es.emit({ ...CHECK_IN, user_id: "u3", user_name: "Budi Santoso", status: "late", late_minutes: 7 }));
    expect(screen.getByText(/^Budi Santoso check-in \d{2}\.\d{2} · terlambat 7 mnt$/)).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(300));
    expect(refreshed()).toBe(true);
    const overviewRefetches = invalidate.mock.calls.filter(([f]) => JSON.stringify(f?.queryKey) === '["overview"]');
    expect(overviewRefetches).toHaveLength(1); // two events in a burst refetch once
    for (const key of ["records", "exceptions", "summary", "corrections", "attempts"]) {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: [key] });
    }

    act(() => vi.advanceTimersByTime(6_000));
    expect(screen.queryByText(/Dewi Lestari check-in/)).not.toBeInTheDocument(); // notices close by themselves
  });

  it("refreshes quietly for your own actions and ordinary face-check failures, but flags suspected spoofs", () => {
    const { es, refreshed } = setup();
    act(() => es.open());
    act(() => es.emit({ kind: "deleted", at: CHECK_IN.at, user_name: "Dewi Lestari", actor_id: "admin-1" }));
    act(() => es.emit({ kind: "verification_failed", at: CHECK_IN.at, user_name: "Dewi Lestari", suspected_spoof: false }));
    expect(screen.queryByText(/Dewi Lestari/)).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(300));
    expect(refreshed()).toBe(true);

    act(() => es.emit({ kind: "verification_failed", at: CHECK_IN.at, user_name: "Rizky Pratama", suspected_spoof: true }));
    expect(screen.getByRole("alert")).toHaveTextContent("Peringatan: verifikasi wajah Rizky Pratama dicurigai memakai foto/video");
  });

  it("does not flicker on the routine reconnect, but reports a longer outage and polls meanwhile", () => {
    const { es, refreshed, invalidate } = setup();
    act(() => es.open());
    act(() => es.fail({ closed: false }));
    act(() => vi.advanceTimersByTime(4_000));
    expect(indicator()).toBe("live");
    act(() => es.open()); // reconnected: catch up on anything missed in between
    expect(refreshed()).toBe(true);

    invalidate.mockClear();
    act(() => es.fail({ closed: false }));
    act(() => vi.advanceTimersByTime(5_000));
    expect(indicator()).toBe("connecting");
    act(() => vi.advanceTimersByTime(30_000));
    expect(refreshed()).toBe(true); // fallback polling
  });

  it("falls back to polling when the stream is refused and tries again later", () => {
    const { es, refreshed } = setup();
    act(() => es.fail({ closed: true }));
    expect(indicator()).toBe("offline");
    expect(screen.getByText("Terputus")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(15_000));
    expect(FakeEventSource.instances).toHaveLength(2);
    act(() => vi.advanceTimersByTime(15_000));
    expect(refreshed()).toBe(true);
    act(() => FakeEventSource.instances[1]!.open());
    expect(indicator()).toBe("live");
  });
});

describe("live event text", () => {
  it("ignores malformed events", () => {
    expect(parseLiveEvent("not json")).toBeNull();
    expect(parseLiveEvent('{"kind": 1}')).toBeNull();
    expect(parseLiveEvent("null")).toBeNull();
    expect(parseLiveEvent(JSON.stringify(CHECK_IN))?.kind).toBe("check_in");
  });

  it("describes changes in Indonesian", () => {
    const at = "2026-10-12T10:05:00Z";
    expect(describeLiveEvent({ kind: "check_out", at, user_name: "Dewi Lestari" }, "me", "Asia/Jakarta")?.text).toBe("Dewi Lestari check-out 17.05");
    expect(describeLiveEvent({ kind: "correction_requested", at, user_name: "Dewi Lestari", actor_id: "u2" }, "me")?.text).toBe(
      "Permintaan koreksi baru dari Dewi Lestari",
    );
    expect(describeLiveEvent({ kind: "deleted", at, user_name: null, actor_id: "other" }, "me")?.text).toBe("Absensi Karyawan dihapus oleh admin lain");
  });
});
