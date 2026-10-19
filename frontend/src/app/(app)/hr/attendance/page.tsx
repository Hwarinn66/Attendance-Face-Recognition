"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { CheckInBadge, CheckOutBadge } from "@/components/attendance";
import { CorrectionDialog, type CorrectionValues } from "@/components/CorrectionDialog";
import { FaceChecksPanel } from "@/components/panels/FaceChecksPanel";
import { ReportsPanel } from "@/components/panels/ReportsPanel";
import {
  Alert,
  Badge,
  Button,
  Card,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  Input,
  PageHeader,
  Pagination,
  Select,
  Spinner,
  Table,
  Tabs,
  Td,
  Textarea,
  Th,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { RequirePermission, useAuth } from "@/lib/auth";
import { daysAgoIso, formatDate, formatDateTime, formatMinutes, formatTime, todayIso } from "@/lib/format";
import type { AttendanceException, AttendanceRecord, Correction, Page, Shift } from "@/lib/types";

const TABS = ["records", "exceptions", "corrections", "reports", "face"] as const;
type Tab = (typeof TABS)[number];

export default function HrAttendancePage() {
  return (
    <RequirePermission permission="attendance:read_all">
      <Suspense>
        <AttendanceManagement />
      </Suspense>
    </RequirePermission>
  );
}

function AttendanceManagement() {
  const params = useSearchParams();
  const initial = (params.get("tab") as Tab | null) ?? "records";
  const [tab, setTab] = useState<Tab>(TABS.includes(initial) ? initial : "records");
  const [start, setStart] = useState(daysAgoIso(6));
  const [end, setEnd] = useState(todayIso());

  return (
    <>
      <PageHeader
        title="Absensi"
        description="Catatan, pengecualian, koreksi, laporan, dan cek wajah dalam satu tempat."
        actions={
          (tab === "records" || tab === "exceptions") && (
            <div className="flex items-end gap-2">
              <Field label="Dari" htmlFor="a-start">
                <Input id="a-start" type="date" className="h-9 w-40" value={start} max={end} onChange={(e) => setStart(e.target.value)} />
              </Field>
              <Field label="Sampai" htmlFor="a-end">
                <Input id="a-end" type="date" className="h-9 w-40" value={end} min={start} onChange={(e) => setEnd(e.target.value)} />
              </Field>
            </div>
          )
        }
      />
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { value: "records", label: "Catatan" },
          { value: "exceptions", label: "Pengecualian" },
          { value: "corrections", label: "Koreksi" },
          { value: "reports", label: "Laporan" },
          { value: "face", label: "Cek wajah" },
        ]}
      />
      {tab === "records" && <Records start={start} end={end} />}
      {tab === "exceptions" && <Exceptions start={start} end={end} />}
      {tab === "reports" && <ReportsPanel />}
      {tab === "face" && <FaceChecksPanel />}
      {tab === "corrections" && <Corrections />}
    </>
  );
}

function useShifts() {
  const { can } = useAuth();
  return useQuery({ queryKey: ["shifts"], queryFn: () => api<Shift[]>("/shifts"), enabled: can("schedules:read") });
}

function Records({ start, end }: { start: string; end: string }) {
  const { can, me } = useAuth();
  const [offset, setOffset] = useState(0);
  const [dialog, setDialog] = useState<{ userId: string; initial: Partial<CorrectionValues> } | null>(null);
  const q = useQuery({
    queryKey: ["records", start, end, offset],
    queryFn: () => api<Page<AttendanceRecord>>("/attendance/records", { query: { start, end, limit: 50, offset } }),
  });
  const shifts = useShifts();
  const canApply = can("correction:apply_direct");
  return (
    <Card>
      {q.isPending && <Spinner />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && q.data.items.length === 0 && <EmptyState title="Tidak ada catatan absensi pada periode ini" />}
      {q.data && q.data.items.length > 0 && (
        <>
          <Table caption="Catatan absensi">
            <thead>
              <tr>
                <Th>Karyawan</Th>
                <Th>Tanggal</Th>
                <Th>Shift</Th>
                <Th>Check-in</Th>
                <Th>Check-out</Th>
                <Th>Durasi kerja</Th>
                {canApply && (
                  <Th>
                    <span className="sr-only">Aksi</span>
                  </Th>
                )}
              </tr>
            </thead>
            <tbody>
              {q.data.items.map((r) => (
                <tr key={r.id}>
                  <Td className="font-medium">{r.user_name}</Td>
                  <Td>
                    {formatDate(r.shift_date)} {r.is_corrected && <Badge tone="info">Dikoreksi</Badge>}
                  </Td>
                  <Td>{r.shift_name}</Td>
                  <Td>
                    <span className="mr-2 tabular-nums">{formatTime(r.check_in_at)}</span>
                    <CheckInBadge record={r} />
                  </Td>
                  <Td>
                    <span className="mr-2 tabular-nums">{r.check_out_at ? formatTime(r.check_out_at) : ""}</span>
                    <CheckOutBadge record={r} />
                  </Td>
                  <Td className="tabular-nums">{formatMinutes(r.worked_minutes)}</Td>
                  {canApply && (
                    <Td className="text-right">
                      {r.user_id !== me.id && (
                        <Button variant="ghost" size="sm" onClick={() => setDialog({ userId: r.user_id, initial: { shift_id: r.shift_id, shift_date: r.shift_date } })}>
                          Koreksi
                        </Button>
                      )}
                    </Td>
                  )}
                </tr>
              ))}
            </tbody>
          </Table>
          <Pagination total={q.data.total} limit={q.data.limit} offset={offset} onChange={setOffset} />
        </>
      )}
      {dialog && (
        <CorrectionDialog
          open
          applyImmediately
          userId={dialog.userId}
          initial={dialog.initial}
          shifts={(shifts.data ?? []).map((s) => ({ id: s.id, name: s.name }))}
          onClose={() => setDialog(null)}
        />
      )}
    </Card>
  );
}

const KIND_LABEL: Record<AttendanceException["kind"], { label: string; tone: "warning" | "danger" | "info" }> = {
  late: { label: "Terlambat", tone: "warning" },
  early_departure: { label: "Pulang awal", tone: "warning" },
  missing_checkout: { label: "Tanpa check-out", tone: "danger" },
  absent: { label: "Tidak hadir", tone: "danger" },
};

function Exceptions({ start, end }: { start: string; end: string }) {
  const [kind, setKind] = useState<string>("");
  const q = useQuery({
    queryKey: ["exceptions", start, end],
    queryFn: () => api<AttendanceException[]>("/attendance/exceptions", { query: { start, end } }),
  });
  const items = (q.data ?? []).filter((e) => !kind || e.kind === kind);
  return (
    <Card
      actions={
        <Select aria-label="Saring berdasarkan jenis" className="h-8 w-48" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">Semua pengecualian</option>
          {Object.entries(KIND_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v.label}
            </option>
          ))}
        </Select>
      }
      title={`${items.length} exception${items.length === 1 ? "" : "s"}`}
    >
      {q.isPending && <Spinner />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && items.length === 0 && <EmptyState title="Tidak ada pengecualian pada periode ini" />}
      {items.length > 0 && (
        <Table caption="Pengecualian absensi">
          <thead>
            <tr>
              <Th>Tanggal</Th>
              <Th>Karyawan</Th>
              <Th>Shift</Th>
              <Th>Jenis</Th>
              <Th>Menit</Th>
            </tr>
          </thead>
          <tbody>
            {items.map((e, i) => (
              <tr key={`${e.user_id}-${e.shift_id}-${e.shift_date}-${e.kind}-${i}`}>
                <Td>{formatDate(e.shift_date)}</Td>
                <Td className="font-medium">{e.user_name}</Td>
                <Td>{e.shift_name}</Td>
                <Td>
                  <Badge tone={KIND_LABEL[e.kind].tone}>{KIND_LABEL[e.kind].label}</Badge>
                </Td>
                <Td className="tabular-nums">{e.minutes !== null ? formatMinutes(e.minutes) : "—"}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

function Corrections() {
  const qc = useQueryClient();
  const { me } = useAuth();
  const [status, setStatus] = useState<"pending" | "approved" | "rejected" | "">("pending");
  const [offset, setOffset] = useState(0);
  const [review, setReview] = useState<{ correction: Correction; approve: boolean } | null>(null);
  const [note, setNote] = useState("");
  const q = useQuery({
    queryKey: ["corrections", "team", status, offset],
    queryFn: () => api<Page<Correction>>("/attendance/corrections", { query: { status, limit: 25, offset } }),
  });
  const mutation = useMutation({
    mutationFn: ({ id, approve }: { id: string; approve: boolean }) =>
      api(`/attendance/corrections/${id}/review`, { json: { approve, note: note || null } }),
    onSuccess: () => {
      setReview(null);
      setNote("");
      void qc.invalidateQueries({ queryKey: ["corrections"] });
      void qc.invalidateQueries({ queryKey: ["records"] });
      void qc.invalidateQueries({ queryKey: ["overview"] });
    },
  });
  return (
    <Card
      actions={
        <Select aria-label="Saring berdasarkan status" className="h-8 w-40" value={status} onChange={(e) => { setStatus(e.target.value as typeof status); setOffset(0); }}>
          <option value="pending">Menunggu</option>
          <option value="approved">Disetujui</option>
          <option value="rejected">Ditolak</option>
          <option value="">Semua</option>
        </Select>
      }
      title="Permintaan koreksi"
    >
      {q.isPending && <Spinner />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && q.data.items.length === 0 && <EmptyState title="Tidak ada permintaan koreksi" />}
      {q.data && q.data.items.length > 0 && (
        <>
          <ul className="divide-y divide-border">
            {q.data.items.map((c) => (
              <li key={c.id} className="flex flex-wrap items-start justify-between gap-3 py-3 text-sm">
                <div className="min-w-0">
                  <p className="font-medium">
                    {c.user_name} · {formatDate(c.shift_date)}
                  </p>
                  <p className="text-muted">
                    Requested in {formatDateTime(c.requested_check_in_at)} · out {formatDateTime(c.requested_check_out_at)}
                  </p>
                  <p className="mt-1 break-words">{c.reason}</p>
                </div>
                <div className="flex items-center gap-2">
                  {c.status === "pending" && c.user_id !== me.id && c.requested_by_id !== me.id ? (
                    <>
                      <Button size="sm" onClick={() => setReview({ correction: c, approve: true })}>
                        Setujui
                      </Button>
                      <Button size="sm" variant="secondary" onClick={() => setReview({ correction: c, approve: false })}>
                        Tolak
                      </Button>
                    </>
                  ) : (
                    <Badge tone={c.status === "approved" ? "success" : c.status === "rejected" ? "danger" : "warning"}>{c.status}</Badge>
                  )}
                </div>
              </li>
            ))}
          </ul>
          <Pagination total={q.data.total} limit={q.data.limit} offset={offset} onChange={setOffset} />
        </>
      )}
      <Dialog
        open={review !== null}
        onClose={() => setReview(null)}
        title={review?.approve ? "Setujui koreksi" : "Tolak koreksi"}
        footer={
          <>
            <Button variant="secondary" onClick={() => setReview(null)} disabled={mutation.isPending}>
              Batal
            </Button>
            <Button
              variant={review?.approve ? "primary" : "danger"}
              loading={mutation.isPending}
              onClick={() => review && mutation.mutate({ id: review.correction.id, approve: review.approve })}
            >
              {review?.approve ? "Setujui dan terapkan" : "Tolak"}
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm">
          {mutation.isError && <Alert tone="danger">{mutation.error instanceof ApiError ? mutation.error.message : "Gagal meninjau"}</Alert>}
          {review?.approve && <p className="text-muted">Catatan absensi akan diperbarui; nilai sebelumnya tetap tersimpan di log aktivitas.</p>}
          <Field label="Catatan (opsional)" htmlFor="review-note">
            <Textarea id="review-note" value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
        </div>
      </Dialog>
    </Card>
  );
}
