// Types mirroring the FastAPI response schemas (backend/app/schemas).

export type Role = "employee" | "admin";
export type BiometricStatus =
  | "not_enrolled"
  | "in_progress"
  | "verification_failed"
  | "pending_retry"
  | "active"
  | "suspended"
  | "deleted";

export type Permission =
  | "self_service"
  | "attendance:read_team"
  | "attendance:read_all"
  | "correction:review"
  | "correction:apply_direct"
  | "reports:read"
  | "users:read"
  | "users:manage"
  | "schedules:read"
  | "schedules:manage"
  | "holidays:manage"
  | "biometrics:manage"
  | "biometrics:delete_any"
  | "verifications:read"
  | "audit:read"
  | "settings:manage";

export interface User {
  id: string;
  email: string;
  username: string | null;
  full_name: string;
  role: Role;
  is_active: boolean;
  must_change_password: boolean;
  employee_code: string | null;
  department: string | null;
  position: string | null;
  workplace_id: string | null;
  manager_id: string | null;
  biometric_status: BiometricStatus;
  last_login_at: string | null;
  created_at: string;
}

export interface Me extends User {
  permissions: Permission[];
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Occurrence {
  shift_id: string;
  shift_name: string;
  workplace_id: string;
  timezone: string;
  shift_date: string;
  start_at: string;
  end_at: string;
  check_in_opens_at: string;
  check_in_closes_at: string;
  check_out_opens_at: string;
  check_out_closes_at: string;
  record_id: string | null;
}

export interface AttendanceRecord {
  id: string;
  user_id: string;
  user_name: string | null;
  shift_id: string;
  shift_name: string | null;
  shift_date: string;
  scheduled_start_at: string;
  scheduled_end_at: string;
  check_in_at: string;
  check_in_status: "on_time" | "late";
  late_minutes: number;
  check_out_at: string | null;
  check_out_status: "on_time" | "early" | "overtime" | null;
  early_departure_minutes: number;
  overtime_minutes: number;
  worked_minutes: number | null;
  check_in_distance_m: number | null;
  is_corrected: boolean;
  source: string;
  missing_checkout: boolean;
}

export interface AttendanceStatus {
  server_time: string;
  biometric_status: BiometricStatus;
  can_check_in: boolean;
  can_check_out: boolean;
  check_in_blocker: string | null;
  check_out_blocker: string | null;
  open_record: AttendanceRecord | null;
  today: Occurrence[];
  next_occurrence: Occurrence | null;
  geofence_required: boolean;
}

export type ChallengeStep = "turn_left" | "turn_right" | "look_up" | "look_down";
export type ChallengePurpose = "enrollment" | "check_in" | "check_out";

export interface Challenge {
  id: string;
  purpose: ChallengePurpose;
  steps: ChallengeStep[];
  expires_at: string;
  issued_at: string;
  min_frames: number;
  max_frames: number;
  recommended_fps: number;
  step_seconds: number;
}

export interface VerificationResult {
  challenge_id: string;
  accepted: boolean;
  category: string | null;
  message: string;
  hints: string[];
  biometric_status: BiometricStatus | null;
  valid_until: string | null;
}

export interface BiometricStatusView {
  status: BiometricStatus;
  consent: { id: string; consent_version: string; granted_at: string; withdrawn_at: string | null } | null;
  consent_current: boolean;
  template: { id: string; model_name: string; created_at: string; sample_count: number } | null;
  can_start_enrollment: boolean;
  attempts_remaining_today: number;
  verification_available: boolean;
  pad_enforced: boolean;
  test_fixture: boolean;
}

export interface QualityCheck {
  ok: boolean;
  face_count: number;
  hints: string[];
}

export interface Workplace {
  id: string;
  name: string;
  timezone: string;
  address: string | null;
  geofence_enabled: boolean;
  latitude: number | null;
  longitude: number | null;
  geofence_radius_m: number | null;
  is_active: boolean;
}

export interface Shift {
  id: string;
  workplace_id: string;
  name: string;
  start_time: string;
  end_time: string;
  check_in_opens_before_min: number;
  check_in_closes_after_min: number;
  grace_min: number;
  check_out_opens_before_min: number;
  check_out_closes_after_min: number;
  early_departure_grace_min: number;
  overtime_threshold_min: number;
  break_min: number;
  workdays: number[];
  is_active: boolean;
  is_overnight: boolean;
}

export interface Assignment {
  id: string;
  user_id: string;
  shift_id: string;
  shift_name: string;
  effective_from: string;
  effective_to: string | null;
}

export interface Holiday {
  id: string;
  holiday_date: string;
  name: string;
  workplace_id: string | null;
}

export interface Correction {
  id: string;
  user_id: string;
  user_name: string | null;
  attendance_record_id: string | null;
  shift_id: string;
  shift_date: string;
  requested_check_in_at: string | null;
  requested_check_out_at: string | null;
  reason: string;
  status: "pending" | "approved" | "rejected";
  requested_by_id: string;
  reviewed_by_id: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  created_at: string;
}

export interface AttendanceException {
  kind: "late" | "early_departure" | "missing_checkout" | "absent";
  user_id: string;
  user_name: string;
  shift_id: string;
  shift_name: string;
  shift_date: string;
  minutes: number | null;
  record_id: string | null;
}

export interface SummaryRow {
  user_id: string;
  user_name: string;
  employee_code: string | null;
  department: string | null;
  scheduled: number;
  present: number;
  on_time: number;
  late: number;
  early_departures: number;
  missing_checkouts: number;
  absent: number;
  late_minutes: number;
  worked_minutes: number;
  overtime_minutes: number;
}

export interface Overview {
  date: string;
  scheduled: number;
  checked_in: number;
  late: number;
  checked_out: number;
  absent_so_far: number;
  pending_corrections: number;
  enrollment: Record<BiometricStatus, number>;
  trend: { day: string; scheduled: number; present: number; late: number; absent: number }[];
  security_events_24h: number;
}

export interface AuditEvent {
  id: number;
  occurred_at: string;
  category: string;
  action: string;
  outcome: "success" | "failure" | "denied";
  severity: "info" | "warning" | "critical";
  actor_user_id: string | null;
  actor_email: string | null;
  target_type: string | null;
  target_id: string | null;
  ip_address: string | null;
  request_id: string | null;
  details: Record<string, unknown>;
}

export interface VerificationAttempt {
  id: string;
  user_id: string;
  user_name: string | null;
  purpose: ChallengePurpose;
  decision: "accepted" | "rejected" | "error";
  reason_codes: string[];
  frame_count: number;
  usable_frame_count: number;
  pad_score: number | null;
  match_score: number | null;
  provider: string;
  processing_ms: number | null;
  created_at: string;
  details: Record<string, unknown>;
}

export interface SessionInfo {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  ip_address: string | null;
  user_agent: string | null;
  current: boolean;
}

export interface HealthReady {
  status: "ok" | "degraded";
  checks: {
    database?: string;
    verification?: "available" | "unavailable";
    pad_enforced?: boolean;
    test_fixture?: boolean;
    environment?: string;
    models?: { role: string; model_id: string | null; available: boolean }[];
  };
}
