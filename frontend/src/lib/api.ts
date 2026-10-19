// Minimal typed client for the same-origin FastAPI backend (proxied at /api by next.config.ts).

export const API_BASE = "/api/v1";
const CSRF_COOKIES = ["__Host-absen_csrf", "absen_csrf"];

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly details?: unknown,
    public readonly requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function readCsrfToken(cookieString: string = typeof document === "undefined" ? "" : document.cookie): string | null {
  for (const part of cookieString.split(";")) {
    const [rawName, ...rest] = part.trim().split("=");
    if (rawName && CSRF_COOKIES.includes(rawName)) return decodeURIComponent(rest.join("="));
  }
  return null;
}

type Json = Record<string, unknown> | unknown[];

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  json?: Json;
  form?: FormData;
  query?: Record<string, string | number | boolean | null | undefined>;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = `${API_BASE}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

// The API answers in English; the console shows Indonesian text for known error codes and falls back to the
// server's message (which may carry specifics, e.g. a shift-policy rule) for the rest.
const ERROR_TEXT: Record<string, string> = {
  invalid_credentials: "ID/email atau password salah.",
  use_mobile_app: "Akun ini hanya bisa dipakai di aplikasi HP. Web hanya untuk administrator.",
  rate_limited: "Terlalu banyak percobaan. Coba lagi beberapa saat lagi.",
  not_authenticated: "Silakan masuk terlebih dahulu.",
  session_expired: "Sesi Anda sudah berakhir. Silakan masuk lagi.",
  csrf_failed: "Sesi keamanan tidak valid. Muat ulang halaman lalu coba lagi.",
  permission_denied: "Anda tidak punya izin untuk tindakan ini.",
  password_change_required: "Ganti password Anda terlebih dahulu.",
  not_found: "Data tidak ditemukan.",
  conflict: "Data bentrok dengan data lain (misalnya email, ID, atau kode sudah dipakai).",
  weak_password: "Password tidak memenuhi aturan: minimal 12 karakter, campuran huruf besar, huruf kecil, angka atau simbol, dan tidak memuat nama/email.",
  password_reused: "Password baru tidak boleh sama dengan password lama.",
  invalid_current_password: "Password saat ini salah.",
  self_modification: "Anda tidak bisa mengubah peran atau menonaktifkan akun sendiri.",
  self_approval: "Anda tidak bisa menyetujui koreksi milik sendiri.",
  already_reviewed: "Permintaan ini sudah ditinjau.",
  self_deletion: "Anda tidak bisa menghapus absensi Anda sendiri.",
  correction_out_of_range: "Jam koreksi berada di luar batas shift.",
  correction_order: "Jam check-out harus setelah jam check-in.",
  validation_failed: "Data yang diisi tidak valid.",
  validation_error: "Data yang diisi tidak valid.",
  service_unavailable: "Layanan sedang tidak tersedia. Coba lagi nanti.",
  payload_too_large: "Data yang dikirim terlalu besar.",
};

async function parseError(res: Response): Promise<ApiError> {
  let code = "http_error";
  let message = res.statusText || "Permintaan gagal";
  let details: unknown;
  let requestId: string | undefined = res.headers.get("x-request-id") ?? undefined;
  try {
    const body = (await res.json()) as { error?: { code?: string; message?: string; details?: unknown; request_id?: string } };
    if (body.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      details = body.error.details;
      requestId = body.error.request_id ?? requestId;
    }
  } catch {
    // non-JSON error body (e.g. proxy failure); keep defaults
  }
  message = ERROR_TEXT[code] ?? message;
  if (res.status === 502 || res.status === 504) {
    message = "Server tidak dapat dihubungi. Periksa koneksi lalu coba lagi.";
    code = "unreachable";
  }
  return new ApiError(res.status, code, message, details, requestId);
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? (options.json || options.form ? "POST" : "GET");
  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  let body: BodyInit | undefined;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  } else if (options.form) {
    body = options.form;
  }
  if (method !== "GET") {
    const csrf = readCsrfToken();
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  let res: Response;
  try {
    res = await fetch(buildUrl(path, options.query), {
      method,
      headers,
      body,
      credentials: "same-origin",
      cache: "no-store",
      signal: options.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "network_error", "Gangguan jaringan. Periksa koneksi lalu coba lagi.");
  }
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  const type = res.headers.get("content-type") ?? "";
  if (type.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as T;
}

export function downloadUrl(path: string, query?: RequestOptions["query"]): string {
  return buildUrl(path, query);
}

export function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
