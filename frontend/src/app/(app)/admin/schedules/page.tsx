"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";

import {
  Alert,
  Badge,
  Button,
  Card,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  Input,
  PageHeader,
  Select,
  Spinner,
  Table,
  Tabs,
  Td,
  Th,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { RequirePermission, useAuth } from "@/lib/auth";
import { WEEKDAYS, formatDate } from "@/lib/format";
import type { Holiday, Shift, Workplace } from "@/lib/types";

function errText(err: unknown): string {
  if (!(err instanceof ApiError)) return "Permintaan gagal";
  if (Array.isArray(err.details)) {
    return `${err.message}: ${(err.details as { loc: string[]; msg: string }[]).map((d) => `${d.loc.slice(1).join(".")} ${d.msg}`).join("; ")}`;
  }
  return err.message;
}

export default function SchedulesPage() {
  return (
    <RequirePermission permission="schedules:read">
      <Schedules />
    </RequirePermission>
  );
}

function Schedules() {
  const [tab, setTab] = useState<"shifts" | "workplaces" | "holidays">("shifts");
  return (
    <>
      <PageHeader title="Jadwal" description="Lokasi kerja, aturan shift, dan hari libur. Semua jam memakai waktu setempat lokasi kerja." />
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { value: "shifts", label: "Shift" },
          { value: "workplaces", label: "Lokasi kerja" },
          { value: "holidays", label: "Hari libur" },
        ]}
      />
      {tab === "shifts" && <Shifts />}
      {tab === "workplaces" && <Workplaces />}
      {tab === "holidays" && <Holidays />}
    </>
  );
}

function useWorkplaces() {
  return useQuery({ queryKey: ["workplaces"], queryFn: () => api<Workplace[]>("/workplaces") });
}

// --- shifts --------------------------------------------------------------------------------------------

type ShiftForm = Omit<Shift, "id" | "is_overnight">;

const DEFAULT_SHIFT: ShiftForm = {
  workplace_id: "",
  name: "",
  start_time: "08:00",
  end_time: "17:00",
  check_in_opens_before_min: 60,
  check_in_closes_after_min: 60,
  grace_min: 15,
  check_out_opens_before_min: 60,
  check_out_closes_after_min: 120,
  early_departure_grace_min: 0,
  overtime_threshold_min: 30,
  break_min: 60,
  workdays: [0, 1, 2, 3, 4],
  is_active: true,
};

export function addMinutes(hhmm: string, minutes: number): string {
  const [h = 0, m = 0] = hhmm.split(":").map(Number);
  const total = (((h * 60 + m + minutes) % 1440) + 1440) % 1440;
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function Shifts() {
  const { can } = useAuth();
  const shifts = useQuery({ queryKey: ["shifts"], queryFn: () => api<Shift[]>("/shifts") });
  const workplaces = useWorkplaces();
  const [editing, setEditing] = useState<{ id?: string; form: ShiftForm } | null>(null);
  const wpName = (id: string) => workplaces.data?.find((w) => w.id === id)?.name ?? "—";
  return (
    <Card
      title="Aturan shift"
      actions={
        can("schedules:manage") && (
          <Button size="sm" onClick={() => setEditing({ form: { ...DEFAULT_SHIFT, workplace_id: workplaces.data?.[0]?.id ?? "" } })} disabled={!workplaces.data?.length}>
            <Plus className="size-4" aria-hidden />
            Shift baru
          </Button>
        )
      }
    >
      {shifts.isPending && <Spinner />}
      {shifts.isError && <ErrorState error={shifts.error} />}
      {workplaces.data?.length === 0 && <Alert tone="info">Buat lokasi kerja terlebih dahulu.</Alert>}
      {shifts.data && shifts.data.length === 0 && <EmptyState title="Belum ada shift" />}
      {shifts.data && shifts.data.length > 0 && (
        <Table caption="Shift">
          <thead>
            <tr>
              <Th>Nama</Th>
              <Th>Lokasi kerja</Th>
              <Th>Jam</Th>
              <Th>Check-in</Th>
              <Th>Check-out</Th>
              <Th>Hari</Th>
              <Th>
                <span className="sr-only">Aksi</span>
              </Th>
            </tr>
          </thead>
          <tbody>
            {shifts.data.map((s) => (
              <tr key={s.id}>
                <Td className="font-medium">
                  {s.name} {!s.is_active && <Badge>Nonaktif</Badge>} {s.is_overnight && <Badge tone="info">Lintas hari</Badge>}
                </Td>
                <Td>{wpName(s.workplace_id)}</Td>
                <Td className="tabular-nums">
                  {s.start_time.slice(0, 5)}–{s.end_time.slice(0, 5)}
                </Td>
                <Td className="text-xs tabular-nums">
                  {addMinutes(s.start_time, -s.check_in_opens_before_min)}–{addMinutes(s.start_time, s.check_in_closes_after_min)} · toleransi {s.grace_min} mnt
                </Td>
                <Td className="text-xs tabular-nums">
                  {addMinutes(s.end_time, -s.check_out_opens_before_min)}–{addMinutes(s.end_time, s.check_out_closes_after_min)}
                </Td>
                <Td className="text-xs">{s.workdays.map((d) => WEEKDAYS[d]).join(" ")}</Td>
                <Td className="text-right">
                  {can("schedules:manage") && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        const { id, is_overnight: _o, ...form } = s;
                        setEditing({ id, form: { ...form, start_time: s.start_time.slice(0, 5), end_time: s.end_time.slice(0, 5) } });
                      }}
                    >
                      Ubah
                    </Button>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {editing && <ShiftDialog initial={editing.form} id={editing.id} workplaces={workplaces.data ?? []} onClose={() => setEditing(null)} />}
    </Card>
  );
}

function ShiftDialog({ initial, id, workplaces, onClose }: { initial: ShiftForm; id?: string; workplaces: Workplace[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState<ShiftForm>(initial);
  const save = useMutation({
    mutationFn: () => api(id ? `/shifts/${id}` : "/shifts", { method: id ? "PUT" : "POST", json: f as unknown as Record<string, unknown> }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["shifts"] });
      onClose();
    },
  });
  const num = (k: keyof ShiftForm) => (e: { target: { value: string } }) => setF({ ...f, [k]: Number(e.target.value) });
  const overnight = f.end_time <= f.start_time;
  return (
    <Dialog
      open
      onClose={onClose}
      title={id ? "Ubah shift" : "Shift baru"}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Batal
          </Button>
          <Button onClick={() => save.mutate()} loading={save.isPending} disabled={!f.name || !f.workplace_id || f.workdays.length === 0}>
            Simpan
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {save.isError && <Alert tone="danger">{errText(save.error)}</Alert>}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Nama" htmlFor="s-name">
            <Input id="s-name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          </Field>
          <Field label="Lokasi kerja" htmlFor="s-wp">
            <Select id="s-wp" value={f.workplace_id} onChange={(e) => setF({ ...f, workplace_id: e.target.value })}>
              {workplaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name} ({w.timezone})
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Mulai" htmlFor="s-start">
            <Input id="s-start" type="time" step={60} value={f.start_time} onChange={(e) => setF({ ...f, start_time: e.target.value })} />
          </Field>
          <Field label="Selesai" htmlFor="s-end" hint={overnight ? "Selesai keesokan harinya (shift lintas hari)" : undefined}>
            <Input id="s-end" type="time" step={60} value={f.end_time} onChange={(e) => setF({ ...f, end_time: e.target.value })} />
          </Field>
          <Field label="Check-in dibuka (menit sebelum mulai)" htmlFor="s-cio">
            <Input id="s-cio" type="number" min={0} max={720} value={f.check_in_opens_before_min} onChange={num("check_in_opens_before_min")} />
          </Field>
          <Field label="Check-in ditutup (menit setelah mulai)" htmlFor="s-cic">
            <Input id="s-cic" type="number" min={0} max={720} value={f.check_in_closes_after_min} onChange={num("check_in_closes_after_min")} />
          </Field>
          <Field label="Toleransi terlambat (menit)" htmlFor="s-grace">
            <Input id="s-grace" type="number" min={0} max={240} value={f.grace_min} onChange={num("grace_min")} />
          </Field>
          <Field label="Istirahat tidak dibayar (menit)" htmlFor="s-break">
            <Input id="s-break" type="number" min={0} max={480} value={f.break_min} onChange={num("break_min")} />
          </Field>
          <Field label="Check-out dibuka (menit sebelum selesai)" htmlFor="s-coo">
            <Input id="s-coo" type="number" min={0} max={720} value={f.check_out_opens_before_min} onChange={num("check_out_opens_before_min")} />
          </Field>
          <Field label="Check-out ditutup (menit setelah selesai)" htmlFor="s-coc">
            <Input id="s-coc" type="number" min={0} max={720} value={f.check_out_closes_after_min} onChange={num("check_out_closes_after_min")} />
          </Field>
          <Field label="Toleransi pulang awal (menit)" htmlFor="s-eg">
            <Input id="s-eg" type="number" min={0} max={240} value={f.early_departure_grace_min} onChange={num("early_departure_grace_min")} />
          </Field>
          <Field label="Lembur setelah (menit lewat jam selesai)" htmlFor="s-ot">
            <Input id="s-ot" type="number" min={0} max={720} value={f.overtime_threshold_min} onChange={num("overtime_threshold_min")} />
          </Field>
        </div>
        <fieldset>
          <legend className="mb-1.5 text-sm font-medium">Hari kerja (hari shift dimulai)</legend>
          <div className="flex flex-wrap gap-2">
            {WEEKDAYS.map((label, d) => (
              <label key={label} className="flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-sm">
                <input
                  type="checkbox"
                  className="accent-[var(--primary)]"
                  checked={f.workdays.includes(d)}
                  onChange={(e) => setF({ ...f, workdays: e.target.checked ? [...f.workdays, d].sort() : f.workdays.filter((x) => x !== d) })}
                />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="accent-[var(--primary)]" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} />
          Aktif
        </label>
        <p className="rounded-lg bg-surface-2 p-3 text-xs text-muted">
          Preview: check-in {addMinutes(f.start_time, -f.check_in_opens_before_min)}–{addMinutes(f.start_time, f.check_in_closes_after_min)} (late after{" "}
          {addMinutes(f.start_time, f.grace_min)}), check-out {addMinutes(f.end_time, -f.check_out_opens_before_min)}–
          {addMinutes(f.end_time, f.check_out_closes_after_min)}. The server rejects overlapping or inverted windows.
        </p>
      </div>
    </Dialog>
  );
}

// --- workplaces ------------------------------------------------------------------------------------------

type WorkplaceForm = Omit<Workplace, "id">;

function timeZones(): string[] {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch {
    return ["UTC", "Asia/Jakarta"];
  }
}

function Workplaces() {
  const { can } = useAuth();
  const q = useWorkplaces();
  const [editing, setEditing] = useState<{ id?: string; form: WorkplaceForm } | null>(null);
  return (
    <Card
      title="Lokasi kerja"
      actions={
        can("schedules:manage") && (
          <Button
            size="sm"
            onClick={() =>
              setEditing({
                form: { name: "", timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, address: "", geofence_enabled: false, latitude: null, longitude: null, geofence_radius_m: null, is_active: true },
              })
            }
          >
            <Plus className="size-4" aria-hidden />
            Lokasi kerja baru
          </Button>
        )
      }
    >
      {q.isPending && <Spinner />}
      {q.isError && <ErrorState error={q.error} />}
      {q.data && q.data.length === 0 && <EmptyState title="Belum ada lokasi kerja" />}
      {q.data && q.data.length > 0 && (
        <Table caption="Lokasi kerja">
          <thead>
            <tr>
              <Th>Nama</Th>
              <Th>Zona waktu</Th>
              <Th>Geofence</Th>
              <Th>
                <span className="sr-only">Aksi</span>
              </Th>
            </tr>
          </thead>
          <tbody>
            {q.data.map((w) => (
              <tr key={w.id}>
                <Td className="font-medium">
                  {w.name} {!w.is_active && <Badge>Nonaktif</Badge>}
                  {w.address && <p className="text-xs font-normal text-muted">{w.address}</p>}
                </Td>
                <Td>{w.timezone}</Td>
                <Td>{w.geofence_enabled ? <Badge tone="info">{w.geofence_radius_m} m radius</Badge> : <span className="text-muted">Mati</span>}</Td>
                <Td className="text-right">
                  {can("schedules:manage") && (
                    <Button variant="ghost" size="sm" onClick={() => setEditing({ id: w.id, form: { ...w } })}>
                      Ubah
                    </Button>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {editing && <WorkplaceDialog initial={editing.form} id={editing.id} onClose={() => setEditing(null)} />}
    </Card>
  );
}

function WorkplaceDialog({ initial, id, onClose }: { initial: WorkplaceForm; id?: string; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState<WorkplaceForm>(initial);
  const save = useMutation({
    mutationFn: () =>
      api(id ? `/workplaces/${id}` : "/workplaces", {
        method: id ? "PUT" : "POST",
        json: { ...f, address: f.address || null, ...(f.geofence_enabled ? {} : { latitude: f.latitude, longitude: f.longitude, geofence_radius_m: f.geofence_radius_m }) },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["workplaces"] });
      onClose();
    },
  });
  const optNum = (v: string) => (v === "" ? null : Number(v));
  return (
    <Dialog
      open
      onClose={onClose}
      title={id ? "Ubah lokasi kerja" : "Lokasi kerja baru"}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Batal
          </Button>
          <Button onClick={() => save.mutate()} loading={save.isPending} disabled={!f.name}>
            Simpan
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {save.isError && <Alert tone="danger">{errText(save.error)}</Alert>}
        <Field label="Nama" htmlFor="w-name">
          <Input id="w-name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        </Field>
        <Field label="Zona waktu" htmlFor="w-tz" hint="Jendela absensi dihitung dalam zona waktu ini">
          <Select id="w-tz" value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })}>
            {timeZones().map((tz) => (
              <option key={tz} value={tz}>
                {tz}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Alamat" htmlFor="w-addr">
          <Input id="w-addr" value={f.address ?? ""} onChange={(e) => setF({ ...f, address: e.target.value })} />
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="accent-[var(--primary)]" checked={f.geofence_enabled} onChange={(e) => setF({ ...f, geofence_enabled: e.target.checked })} />
          Wajib berada dalam radius lokasi (geofence)
        </label>
        {f.geofence_enabled && (
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Garis lintang" htmlFor="w-lat">
              <Input id="w-lat" type="number" step="any" value={f.latitude ?? ""} onChange={(e) => setF({ ...f, latitude: optNum(e.target.value) })} />
            </Field>
            <Field label="Garis bujur" htmlFor="w-lon">
              <Input id="w-lon" type="number" step="any" value={f.longitude ?? ""} onChange={(e) => setF({ ...f, longitude: optNum(e.target.value) })} />
            </Field>
            <Field label="Radius (m)" htmlFor="w-rad">
              <Input id="w-rad" type="number" min={10} max={50000} value={f.geofence_radius_m ?? ""} onChange={(e) => setF({ ...f, geofence_radius_m: optNum(e.target.value) })} />
            </Field>
          </div>
        )}
        {f.geofence_enabled && <p className="text-xs text-muted">Lokasi perangkat bukan bukti kehadiran dan bisa dipalsukan. Anggap sebagai pencegah saja.</p>}
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="accent-[var(--primary)]" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} />
          Aktif
        </label>
      </div>
    </Dialog>
  );
}

// --- holidays -------------------------------------------------------------------------------------------------

function Holidays() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const year = new Date().getFullYear();
  const q = useQuery({ queryKey: ["holidays", year], queryFn: () => api<Holiday[]>("/holidays", { query: { start: `${year}-01-01`, end: `${year + 1}-12-31` } }) });
  const workplaces = useWorkplaces();
  const [form, setForm] = useState({ holiday_date: "", name: "", workplace_id: "" });
  const [deleting, setDeleting] = useState<Holiday | null>(null);
  const invalidate = () => void qc.invalidateQueries({ queryKey: ["holidays"] });
  const add = useMutation({
    mutationFn: () => api("/holidays", { json: { ...form, workplace_id: form.workplace_id || null } }),
    onSuccess: () => {
      setForm({ holiday_date: "", name: "", workplace_id: "" });
      invalidate();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/holidays/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setDeleting(null);
      invalidate();
    },
  });
  const manage = can("holidays:manage");
  return (
    <Card title={`Hari libur ${year}–${year + 1}`}>
      {manage && (
        <div className="mb-5 space-y-3">
          {add.isError && <Alert tone="danger">{errText(add.error)}</Alert>}
          <div className="grid gap-3 sm:grid-cols-[10rem_1fr_14rem_auto] sm:items-end">
            <Field label="Tanggal" htmlFor="h-date">
              <Input id="h-date" type="date" value={form.holiday_date} onChange={(e) => setForm({ ...form, holiday_date: e.target.value })} />
            </Field>
            <Field label="Nama" htmlFor="h-name">
              <Input id="h-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label="Berlaku untuk" htmlFor="h-wp">
              <Select id="h-wp" value={form.workplace_id} onChange={(e) => setForm({ ...form, workplace_id: e.target.value })}>
                <option value="">Semua lokasi kerja</option>
                {(workplaces.data ?? []).map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Button onClick={() => add.mutate()} disabled={!form.holiday_date || !form.name} loading={add.isPending}>
              Tambah
            </Button>
          </div>
        </div>
      )}
      {q.isPending && <Spinner />}
      {q.isError && <ErrorState error={q.error} />}
      {q.data && q.data.length === 0 && <EmptyState title="Belum ada hari libur" />}
      {q.data && q.data.length > 0 && (
        <ul className="divide-y divide-border text-sm">
          {q.data.map((h) => (
            <li key={h.id} className="flex items-center justify-between py-2.5">
              <span>
                <span className="font-medium">{formatDate(h.holiday_date)}</span> · {h.name}{" "}
                <span className="text-muted">({h.workplace_id ? (workplaces.data?.find((w) => w.id === h.workplace_id)?.name ?? "workplace") : "all workplaces"})</span>
              </span>
              {manage && (
                <Button variant="ghost" size="sm" onClick={() => setDeleting(h)}>
                  Hapus
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      <ConfirmDialog
        open={deleting !== null}
        title="Hapus hari libur?"
        message={deleting ? `${formatDate(deleting.holiday_date)} · ${deleting.name} akan menjadi hari kerja biasa.` : ""}
        confirmLabel="Hapus"
        loading={remove.isPending}
        onCancel={() => setDeleting(null)}
        onConfirm={() => deleting && remove.mutate(deleting.id)}
      />
    </Card>
  );
}
