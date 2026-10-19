"use client";

import type { AttendanceRecord } from "@/lib/types";
import { formatMinutes } from "@/lib/format";

import { Badge } from "./ui";

export function CheckInBadge({ record }: { record: Pick<AttendanceRecord, "check_in_status" | "late_minutes"> }) {
  return record.check_in_status === "late" ? (
    <Badge tone="warning">Terlambat {formatMinutes(record.late_minutes)}</Badge>
  ) : (
    <Badge tone="success">Tepat waktu</Badge>
  );
}

export function CheckOutBadge({ record }: { record: AttendanceRecord }) {
  if (!record.check_out_at) return record.missing_checkout ? <Badge tone="danger">Tanpa check-out</Badge> : <Badge>Belum check-out</Badge>;
  if (record.check_out_status === "early") return <Badge tone="warning">Pulang awal {formatMinutes(record.early_departure_minutes)}</Badge>;
  if (record.check_out_status === "overtime") return <Badge tone="info">Lembur {formatMinutes(record.overtime_minutes)}</Badge>;
  return <Badge tone="success">Tepat waktu</Badge>;
}

