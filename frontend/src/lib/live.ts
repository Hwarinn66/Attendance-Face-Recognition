// Live dashboard events, pushed by the server over Server-Sent Events (GET /api/v1/events/attendance).

import { formatTime } from "./format";

export type LiveEventKind =
  | "check_in"
  | "check_out"
  | "deleted"
  | "correction_requested"
  | "correction_applied"
  | "correction_approved"
  | "correction_rejected"
  | "verification_failed";

export interface LiveEvent {
  kind: LiveEventKind;
  /** Server time of the change (UTC ISO). */
  at: string;
  user_id?: string;
  user_name?: string | null;
  /** Who made the change, when it was an administrator acting on someone else's attendance. */
  actor_id?: string;
  record_id?: string;
  status?: string;
  late_minutes?: number | null;
  suspected_spoof?: boolean;
}

export type LiveTone = "success" | "warning" | "danger" | "info";

export interface LiveNotice {
  text: string;
  tone: LiveTone;
}

/** Queries an attendance event can change. Active ones refetch at once; the rest refetch when next shown. */
export const LIVE_QUERY_KEYS = ["overview", "records", "exceptions", "summary", "corrections", "attempts", "audit"] as const;

export function parseLiveEvent(data: string): LiveEvent | null {
  try {
    const value: unknown = JSON.parse(data);
    if (typeof value !== "object" || value === null) return null;
    const ev = value as Partial<LiveEvent>;
    return typeof ev.kind === "string" && typeof ev.at === "string" ? (ev as LiveEvent) : null;
  } catch {
    return null;
  }
}

/** The pop-up text for an event, or null when it should only refresh the data quietly. */
export function describeLiveEvent(ev: LiveEvent, myId: string, timeZone?: string): LiveNotice | null {
  if (ev.actor_id === myId) return null; // your own action: its dialog already confirmed it
  const name = ev.user_name || "Karyawan";
  const time = formatTime(ev.at, timeZone);
  switch (ev.kind) {
    case "check_in":
      return ev.status === "late"
        ? { text: `${name} check-in ${time} · terlambat ${ev.late_minutes ?? 0} mnt`, tone: "warning" }
        : { text: `${name} check-in ${time}`, tone: "success" };
    case "check_out":
      return { text: `${name} check-out ${time}`, tone: "info" };
    case "deleted":
      return { text: `Absensi ${name} dihapus oleh admin lain`, tone: "warning" };
    case "correction_requested":
      return { text: `Permintaan koreksi baru dari ${name}`, tone: "info" };
    case "correction_applied":
      return { text: `Absensi ${name} dikoreksi oleh admin lain`, tone: "info" };
    case "correction_approved":
      return { text: `Koreksi ${name} disetujui`, tone: "info" };
    case "correction_rejected":
      return { text: `Koreksi ${name} ditolak`, tone: "info" };
    case "verification_failed":
      // Ordinary failures (bad light, blur) are common and only refresh the face-check list.
      return ev.suspected_spoof ? { text: `Peringatan: verifikasi wajah ${name} dicurigai memakai foto/video`, tone: "danger" } : null;
    default:
      return null;
  }
}
