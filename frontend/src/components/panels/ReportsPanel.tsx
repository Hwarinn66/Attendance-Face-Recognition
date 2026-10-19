"use client";

import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";

import { Card, EmptyState, ErrorState, Field, Input, SectionHeader, Select, Spinner, Table, Td, Th, buttonClasses } from "@/components/ui";
import { api, downloadUrl } from "@/lib/api";
import { formatMinutes, todayIso } from "@/lib/format";
import type { SummaryRow, Workplace } from "@/lib/types";

function monthStart(): string {
  return `${todayIso().slice(0, 8)}01`;
}

/** Permission: reports:read. */
export function ReportsPanel() {
  const [start, setStart] = useState(monthStart());
  const [end, setEnd] = useState(todayIso());
  const [workplace, setWorkplace] = useState("");
  const workplaces = useQuery({ queryKey: ["workplaces"], queryFn: () => api<Workplace[]>("/workplaces") });
  const q = useQuery({
    queryKey: ["summary", start, end, workplace],
    queryFn: () => api<SummaryRow[]>("/reports/summary", { query: { start, end, workplace_id: workplace || undefined } }),
  });
  const totals = (q.data ?? []).reduce(
    (acc, r) => ({ scheduled: acc.scheduled + r.scheduled, present: acc.present + r.present, late: acc.late + r.late, absent: acc.absent + r.absent }),
    { scheduled: 0, present: 0, late: 0, absent: 0 },
  );
  const query = { start, end, workplace_id: workplace || undefined };

  return (
    <>
      <SectionHeader
        title="Laporan"
        description="Ringkasan absensi per karyawan berdasarkan catatan dan jadwal yang ditetapkan."
        actions={
          <>
            <a className={buttonClasses("secondary")} href={downloadUrl("/reports/summary.csv", query)} download>
              <Download className="size-4" aria-hidden />
              CSV ringkasan
            </a>
            <a className={buttonClasses("secondary")} href={downloadUrl("/reports/attendance.csv", { start, end })} download>
              <Download className="size-4" aria-hidden />
              CSV catatan
            </a>
          </>
        }
      />
      <Card
        title={`${q.data?.length ?? 0} karyawan · ${totals.present}/${totals.scheduled} shift dihadiri · ${totals.late} terlambat · ${totals.absent} tidak hadir`}
        actions={
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Dari" htmlFor="r-start">
              <Input id="r-start" type="date" className="h-8 w-40" value={start} max={end} onChange={(e) => setStart(e.target.value)} />
            </Field>
            <Field label="Sampai" htmlFor="r-end">
              <Input id="r-end" type="date" className="h-8 w-40" value={end} min={start} onChange={(e) => setEnd(e.target.value)} />
            </Field>
            <Field label="Lokasi kerja" htmlFor="r-wp">
              <Select id="r-wp" className="h-8 w-44" value={workplace} onChange={(e) => setWorkplace(e.target.value)}>
                <option value="">Semua lokasi kerja</option>
                {(workplaces.data ?? []).map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
        }
      >
        {q.isPending && <Spinner />}
        {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
        {q.data && q.data.length === 0 && <EmptyState title="Tidak ada karyawan" />}
        {q.data && q.data.length > 0 && (
          <Table caption="Ringkasan absensi">
            <thead>
              <tr>
                <Th>Karyawan</Th>
                <Th>Departemen</Th>
                <Th className="text-right">Terjadwal</Th>
                <Th className="text-right">Hadir</Th>
                <Th className="text-right">Terlambat</Th>
                <Th className="text-right">Tidak hadir</Th>
                <Th className="text-right">Pulang awal</Th>
                <Th className="text-right">Tanpa check-out</Th>
                <Th className="text-right">Durasi kerja</Th>
                <Th className="text-right">Lembur</Th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {q.data.map((r) => (
                <tr key={r.user_id}>
                  <Td>
                    <span className="font-medium">{r.user_name}</span>
                    {r.employee_code && <span className="ml-2 text-xs text-muted">{r.employee_code}</span>}
                  </Td>
                  <Td>{r.department ?? "—"}</Td>
                  <Td className="text-right">{r.scheduled}</Td>
                  <Td className="text-right">{r.present}</Td>
                  <Td className="text-right">{r.late}</Td>
                  <Td className="text-right">{r.absent}</Td>
                  <Td className="text-right">{r.early_departures}</Td>
                  <Td className="text-right">{r.missing_checkouts}</Td>
                  <Td className="text-right">{formatMinutes(r.worked_minutes)}</Td>
                  <Td className="text-right">{formatMinutes(r.overtime_minutes)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
