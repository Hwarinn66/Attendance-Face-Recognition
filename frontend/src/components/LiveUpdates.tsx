"use client";

// Keeps the console current without a page refresh: the server pushes attendance events (SSE) and the
// affected queries refetch. If the stream is unavailable, the same queries are polled instead.

import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, CheckCircle2, Info, ShieldAlert, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { API_BASE } from "@/lib/api";
import { LIVE_QUERY_KEYS, type LiveNotice, type LiveTone, describeLiveEvent, parseLiveEvent } from "@/lib/live";

export type LiveStatus = "connecting" | "live" | "offline";

export const LIVE_STREAM_URL = `${API_BASE}/events/attendance`;
const FALLBACK_POLL_MS = 30_000;
const RETRY_AFTER_FAILURE_MS = 15_000;
// The server ends each stream every couple of minutes and the browser reconnects at once; only a gap
// longer than this counts as "reconnecting" in the indicator.
const RECONNECT_GRACE_MS = 5_000;
const BATCH_MS = 300;
const TOAST_MS = 6_000;
const MAX_TOASTS = 4;

export interface LiveToast extends LiveNotice {
  id: number;
}

export function useLiveUpdates({ enabled, myId }: { enabled: boolean; myId: string }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<LiveStatus>("connecting");
  const [toasts, setToasts] = useState<LiveToast[]>([]);
  const seq = useRef(0);

  const dismiss = useCallback((id: number) => setToasts((all) => all.filter((t) => t.id !== id)), []);

  const supported = typeof EventSource !== "undefined";

  useEffect(() => {
    if (!enabled) return;
    let source: EventSource | null = null;
    let disposed = false;
    let everOpened = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let grace: ReturnType<typeof setTimeout> | undefined;
    let batch: ReturnType<typeof setTimeout> | undefined;
    let poll: ReturnType<typeof setInterval> | undefined;

    const refresh = () => {
      for (const key of LIVE_QUERY_KEYS) void qc.invalidateQueries({ queryKey: [key] });
    };
    const refreshSoon = () => {
      // A burst of events (several people checking in together) refetches once.
      batch ??= setTimeout(() => {
        batch = undefined;
        refresh();
      }, BATCH_MS);
    };
    const startPolling = () => {
      poll ??= setInterval(refresh, FALLBACK_POLL_MS);
    };
    const stopPolling = () => {
      clearInterval(poll);
      poll = undefined;
    };
    const notify = (notice: LiveNotice) => {
      const id = ++seq.current;
      setToasts((all) => [...all.slice(-(MAX_TOASTS - 1)), { ...notice, id }]);
      setTimeout(() => dismiss(id), TOAST_MS);
    };

    const connect = () => {
      if (!supported) {
        startPolling();
        return;
      }
      const es = new EventSource(LIVE_STREAM_URL);
      source = es;
      es.onopen = () => {
        clearTimeout(grace);
        grace = undefined;
        stopPolling();
        setStatus("live");
        if (everOpened) refresh(); // catch up on anything that changed while reconnecting
        everOpened = true;
      };
      es.addEventListener("attendance", (e) => {
        const ev = parseLiveEvent((e as MessageEvent<string>).data);
        if (!ev) return;
        refreshSoon();
        const notice = describeLiveEvent(ev, myId);
        if (notice) notify(notice);
      });
      es.onerror = () => {
        if (disposed) return;
        if (es.readyState === EventSource.CLOSED) {
          // Refused (e.g. session ended) or the server is down: poll, and try the stream again later.
          es.close();
          clearTimeout(grace);
          grace = undefined;
          setStatus("offline");
          startPolling();
          retry = setTimeout(connect, RETRY_AFTER_FAILURE_MS);
          return;
        }
        // The browser is already reconnecting by itself.
        grace ??= setTimeout(() => {
          setStatus("connecting");
          startPolling();
        }, RECONNECT_GRACE_MS);
      };
    };

    connect();
    return () => {
      disposed = true;
      source?.close();
      clearTimeout(retry);
      clearTimeout(grace);
      clearTimeout(batch);
      stopPolling();
    };
  }, [enabled, supported, myId, qc, dismiss]);

  return { status: enabled && supported ? status : ("offline" as LiveStatus), toasts, dismiss };
}

const STATUS_TEXT: Record<LiveStatus, { label: string; title: string }> = {
  live: { label: "Langsung", title: "Data absensi diperbarui otomatis saat ada perubahan" },
  connecting: { label: "Menyambung…", title: "Menyambung ulang; sementara itu data diperbarui tiap 30 detik" },
  offline: { label: "Terputus", title: "Pembaruan langsung tidak tersedia; data diperbarui tiap 30 detik" },
};

export function LiveIndicator({ status, className }: { status: LiveStatus; className?: string }) {
  const { label, title } = STATUS_TEXT[status];
  return (
    <span
      role="status"
      title={title}
      data-live-status={status}
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium",
        status === "live" && "bg-success-soft text-success",
        status === "connecting" && "bg-warning-soft text-warning",
        status === "offline" && "bg-surface-2 text-muted",
        className,
      )}
    >
      <span className="relative flex size-2" aria-hidden>
        {status === "live" && <span className="absolute inline-flex size-full animate-ping rounded-full bg-success opacity-60 motion-reduce:hidden" />}
        <span
          className={clsx(
            "relative inline-flex size-2 rounded-full",
            status === "live" && "bg-success",
            status === "connecting" && "bg-warning",
            status === "offline" && "bg-muted",
          )}
        />
      </span>
      {label}
    </span>
  );
}

const TOAST_ICON: Record<LiveTone, typeof Info> = { success: CheckCircle2, info: Info, warning: AlertTriangle, danger: ShieldAlert };

export function LiveToasts({ toasts, onDismiss }: { toasts: LiveToast[]; onDismiss: (id: number) => void }) {
  return (
    <div
      aria-live="polite"
      aria-label="Pemberitahuan absensi"
      className="pointer-events-none fixed inset-x-4 bottom-4 z-50 flex flex-col items-end gap-2 sm:left-auto sm:w-96"
    >
      {toasts.map((t) => {
        const Icon = TOAST_ICON[t.tone];
        return (
          <div
            key={t.id}
            role={t.tone === "danger" ? "alert" : "status"}
            className={clsx(
              "pointer-events-auto flex w-full items-start gap-3 rounded-xl border bg-surface p-3 text-sm shadow-lg",
              t.tone === "success" && "border-success/30",
              t.tone === "info" && "border-info/30",
              t.tone === "warning" && "border-warning/40",
              t.tone === "danger" && "border-danger/40",
            )}
          >
            <Icon
              aria-hidden
              className={clsx(
                "mt-0.5 size-4 shrink-0",
                t.tone === "success" && "text-success",
                t.tone === "info" && "text-info",
                t.tone === "warning" && "text-warning",
                t.tone === "danger" && "text-danger",
              )}
            />
            <p className="min-w-0 flex-1">{t.text}</p>
            <button type="button" onClick={() => onDismiss(t.id)} className="-m-1 rounded p-1 text-muted hover:bg-surface-2 hover:text-text" aria-label="Tutup pemberitahuan">
              <X className="size-3.5" aria-hidden />
            </button>
          </div>
        );
      })}
    </div>
  );
}
