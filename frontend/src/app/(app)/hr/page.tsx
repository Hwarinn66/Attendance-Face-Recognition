"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { TrendChart } from "@/components/TrendChart";
import { Card, ErrorState, Field, Input, PageHeader, Spinner, StatCard } from "@/components/ui";
import { api } from "@/lib/api";
import { RequirePermission, useAuth } from "@/lib/auth";
import { BIOMETRIC_LABEL, formatTime, todayIso } from "@/lib/format";
import type { Overview } from "@/lib/types";

export default function HrOverviewPage() {
  return (
    <RequirePermission permission="reports:read">
      <Overview />
    </RequirePermission>
  );
}

function Overview() {
  const { can } = useAuth();
  const [day, setDay] = useState(todayIso());
  const q = useQuery({ queryKey: ["overview", day], queryFn: () => api<Overview>("/reports/overview", { query: { day } }), refetchInterval: 60_000 });

  return (
    <>
      <PageHeader
        title="Ringkasan hari ini"
        description={
          <>
            {can("attendance:read_all") ? "Semua karyawan" : "Bawahan langsung Anda"}
            {q.data && <span data-testid="overview-updated"> · diperbarui {formatTime(new Date(q.dataUpdatedAt).toISOString())}</span>}
          </>
        }
        actions={
          <Field label="Hari" htmlFor="ov-day">
            <Input id="ov-day" type="date" className="h-9 w-44" value={day} max={todayIso()} onChange={(e) => setDay(e.target.value || todayIso())} />
          </Field>
        }
      />
      {q.isPending && <Spinner />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
            <StatCard label="Terjadwal" value={q.data.scheduled} />
            <StatCard label="Sudah check-in" value={q.data.checked_in} tone="primary" />
            <StatCard label="Terlambat" value={q.data.late} tone={q.data.late ? "warning" : "neutral"} />
            <StatCard label="Belum hadir" value={q.data.absent_so_far} tone={q.data.absent_so_far ? "danger" : "neutral"} hint="Jendela check-in ditutup" />
            <StatCard label="Sudah check-out" value={q.data.checked_out} />
            <StatCard
              label="Koreksi menunggu"
              value={q.data.pending_corrections}
              hint={<Link className="text-primary hover:underline" href="/hr/attendance?tab=corrections">Tinjau</Link>}
            />
          </div>

          <div className="grid gap-6 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <TrendChart
                data={q.data.trend.map((p) => ({ day: p.day, on_time: Math.max(0, p.present - p.late), late: p.late, absent: p.absent }))}
              />
            </Card>
            <Card title="Pendaftaran wajah">
              <ul className="space-y-2 text-sm">
                {Object.entries(q.data.enrollment)
                  .filter(([, n]) => n > 0)
                  .map(([status, n]) => (
                    <li key={status} className="flex items-center justify-between">
                      <span className="text-muted">{BIOMETRIC_LABEL[status] ?? status}</span>
                      <span className="font-medium tabular-nums">{n}</span>
                    </li>
                  ))}
              </ul>
              {can("audit:read") && (
                <p className="mt-4 border-t border-border pt-3 text-sm">
                  <span className="font-medium tabular-nums">{q.data.security_events_24h}</span>{" "}
                  <span className="text-muted">peringatan keamanan dalam 24 jam ·</span>{" "}
                  <Link className="text-primary hover:underline" href="/admin/settings?tab=activity">
                    Lihat
                  </Link>
                </p>
              )}
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
