// Formatting helpers. Timestamps arrive as UTC ISO strings; they are shown in the workplace time zone
// when one is known, otherwise in the viewer's local zone.

/** The console is Indonesian: dates, times and numbers use Indonesian formatting. */
export const LOCALE = "id-ID";

export function formatTime(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat(LOCALE, { hour: "2-digit", minute: "2-digit", timeZone }).format(new Date(iso));
}

export function formatDateTime(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat(LOCALE, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone,
  }).format(new Date(iso));
}

export function formatDate(isoDate: string | null | undefined): string {
  if (!isoDate) return "—";
  // Plain calendar dates (YYYY-MM-DD) must not be shifted by the viewer's time zone.
  const [y, m, d] = isoDate.split("-").map(Number);
  if (!y || !m || !d) return isoDate;
  return new Intl.DateTimeFormat(LOCALE, { weekday: "short", year: "numeric", month: "short", day: "numeric", timeZone: "UTC" })
    .format(new Date(Date.UTC(y, m - 1, d)));
}

export function formatMinutes(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return "—";
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m} mnt`;
  return m === 0 ? `${h} j` : `${h} j ${m} mnt`;
}

export function todayIso(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

export function daysAgoIso(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  const local = new Date(d.getTime() - d.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

export const ROLE_LABEL: Record<string, string> = {
  employee: "Karyawan",
  admin: "Administrator",
};

export const BIOMETRIC_LABEL: Record<string, string> = {
  not_enrolled: "Belum terdaftar",
  in_progress: "Sedang diproses",
  verification_failed: "Terkunci — perlu administrator",
  pending_retry: "Perlu daftar ulang",
  active: "Aktif",
  suspended: "Ditangguhkan",
  deleted: "Dihapus",
};

export const BLOCKER_LABEL: Record<string, string> = {
  no_schedule: "Belum ada shift. Hubungi administrator.",
  outside_check_in_window: "Check-in belum dibuka.",
  already_checked_in: "Sudah check-in untuk shift ini.",
  no_open_check_in: "Tidak ada check-in yang perlu ditutup.",
  check_out_not_open_yet: "Check-out belum dibuka.",
  biometric_not_enrolled: "Daftarkan wajah untuk mulai absen.",
  biometric_pending_retry: "Pendaftaran wajah terakhir gagal. Daftar ulang.",
  biometric_in_progress: "Selesaikan pendaftaran wajah terlebih dahulu.",
  biometric_verification_failed: "Pendaftaran wajah terkunci. Hubungi administrator.",
  biometric_suspended: "Verifikasi wajah ditangguhkan. Hubungi administrator.",
  biometric_deleted: "Data wajah telah dihapus. Daftar ulang untuk absen.",
};

export const WEEKDAYS = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"];
