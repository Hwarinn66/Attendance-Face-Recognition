"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UserPlus } from "lucide-react";
import { useState } from "react";
import { z } from "zod";

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
  Pagination,
  Select,
  Spinner,
  Table,
  Tabs,
  Td,
  Th,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { RequirePermission, useAuth } from "@/lib/auth";
import { BIOMETRIC_LABEL, ROLE_LABEL, formatDate, formatDateTime, todayIso } from "@/lib/format";
import type { Assignment, Page, Role, Shift, User, Workplace } from "@/lib/types";

const ROLES: Role[] = ["employee", "admin"];

// Sign-in ID rules mirror the server: 3-64 chars, letters/digits/._-, starting with a letter or digit.
export const usernameSchema = z
  .string()
  .trim()
  .toLowerCase()
  .refine((v) => v === "" || /^[a-z0-9][a-z0-9._-]{2,63}$/.test(v), "3–64 karakter: huruf, angka, . _ - (tanpa spasi atau @)");

export const userSchema = z.object({
  email: z.string().trim().email("Masukkan email yang valid"),
  username: usernameSchema,
  full_name: z.string().trim().min(1, "Masukkan nama").max(200),
  role: z.enum(["employee", "admin"]),
  temporary_password: z.string().min(12, "Minimal 12 karakter"),
  employee_code: z.string().trim().max(50).optional(),
  department: z.string().trim().max(120).optional(),
  position: z.string().trim().max(120).optional(),
  workplace_id: z.string().optional(),
});

function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const details = err.details as { temporary_password?: string[] } | undefined;
    return details?.temporary_password ? `${err.message}: ${details.temporary_password.join(", ")}` : err.message;
  }
  return "Permintaan gagal";
}

export default function UsersPage() {
  return (
    <RequirePermission permission="users:read">
      <Users />
    </RequirePermission>
  );
}

function Users() {
  const { can } = useAuth();
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [active, setActive] = useState("true");
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const users = useQuery({
    queryKey: ["users", q, role, active, offset],
    queryFn: () => api<Page<User>>("/users", { query: { q, role, active, limit: 50, offset } }),
  });

  return (
    <>
      <PageHeader
        title="Pengguna"
        actions={
          can("users:manage") && (
            <Button onClick={() => setCreating(true)}>
              <UserPlus className="size-4" aria-hidden />
              Pengguna baru
            </Button>
          )
        }
      />
      <Card
        actions={
          <div className="flex flex-wrap gap-2">
            <Input aria-label="Cari pengguna" placeholder="Cari nama, email, ID, kode" className="h-8 w-56" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
            <Select aria-label="Peran" className="h-8 w-36" value={role} onChange={(e) => { setRole(e.target.value); setOffset(0); }}>
              <option value="">Semua peran</option>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {ROLE_LABEL[r]}
                </option>
              ))}
            </Select>
            <Select aria-label="Status" className="h-8 w-32" value={active} onChange={(e) => { setActive(e.target.value); setOffset(0); }}>
              <option value="true">Aktif</option>
              <option value="false">Nonaktif</option>
              <option value="">Semua</option>
            </Select>
          </div>
        }
        title={users.data ? `${users.data.total} pengguna` : "Pengguna"}
      >
        {users.isPending && <Spinner />}
        {users.isError && <ErrorState error={users.error} onRetry={() => void users.refetch()} />}
        {users.data && users.data.items.length === 0 && <EmptyState title="Tidak ada pengguna yang cocok" />}
        {users.data && users.data.items.length > 0 && (
          <>
            <Table caption="Pengguna">
              <thead>
                <tr>
                  <Th>Nama</Th>
                  <Th>ID login</Th>
                  <Th>Peran</Th>
                  <Th>Departemen</Th>
                  <Th>Wajah</Th>
                  <Th>Login terakhir</Th>
                  <Th>
                    <span className="sr-only">Aksi</span>
                  </Th>
                </tr>
              </thead>
              <tbody>
                {users.data.items.map((u) => (
                  <tr key={u.id}>
                    <Td>
                      <p className="font-medium">
                        {u.full_name} {!u.is_active && <Badge tone="danger">Nonaktif</Badge>}
                      </p>
                      <p className="text-xs text-muted">{u.email}</p>
                    </Td>
                    <Td>
                      <span className="font-mono text-sm">{u.username ?? "—"}</span>
                      {u.must_change_password && (
                        <p>
                          <Badge tone="warning">Password sementara</Badge>
                        </p>
                      )}
                    </Td>
                    <Td>{ROLE_LABEL[u.role]}</Td>
                    <Td>{u.department ?? "—"}</Td>
                    <Td>
                      <Badge tone={u.biometric_status === "active" ? "success" : u.biometric_status === "suspended" || u.biometric_status === "verification_failed" ? "danger" : "neutral"}>
                        {BIOMETRIC_LABEL[u.biometric_status]}
                      </Badge>
                    </Td>
                    <Td className="text-xs text-muted">{formatDateTime(u.last_login_at)}</Td>
                    <Td className="text-right">
                      <Button variant="ghost" size="sm" onClick={() => setEditing(u)}>
                        Kelola
                      </Button>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <Pagination total={users.data.total} limit={users.data.limit} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>
      {creating && <CreateUserDialog onClose={() => setCreating(false)} />}
      {editing && <ManageUserDialog user={editing} onClose={() => setEditing(null)} />}
    </>
  );
}

function useRefs() {
  const workplaces = useQuery({ queryKey: ["workplaces"], queryFn: () => api<Workplace[]>("/workplaces") });
  return { workplaces: workplaces.data ?? [] };
}

function CreateUserDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const { workplaces } = useRefs();
  const [v, setV] = useState({ email: "", username: "", full_name: "", role: "employee" as Role, temporary_password: "", employee_code: "", department: "", position: "", workplace_id: "" });
  const [mustChange, setMustChange] = useState(true);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const mutation = useMutation({
    mutationFn: () =>
      api<User>("/users", {
        json: {
          ...v,
          username: v.username.trim().toLowerCase() || null,
          must_change_password: mustChange,
          employee_code: v.employee_code || null,
          department: v.department || null,
          position: v.position || null,
          workplace_id: v.workplace_id || null,
        },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["users"] });
      onClose();
    },
  });
  const set = (k: keyof typeof v) => (e: { target: { value: string } }) => setV({ ...v, [k]: e.target.value });
  function submit() {
    const parsed = userSchema.safeParse(v);
    if (!parsed.success) {
      const errs: Record<string, string> = {};
      for (const i of parsed.error.issues) errs[String(i.path[0])] ??= i.message;
      setErrors(errs);
      return;
    }
    setErrors({});
    mutation.mutate();
  }
  return (
    <Dialog
      open
      onClose={onClose}
      title="Pengguna baru"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Batal
          </Button>
          <Button onClick={submit} loading={mutation.isPending}>
            Buat pengguna
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        {mutation.isError && (
          <div className="sm:col-span-2">
            <Alert tone="danger">{errorText(mutation.error)}</Alert>
          </div>
        )}
        <Field label="Nama lengkap" htmlFor="u-name" error={errors.full_name}>
          <Input id="u-name" value={v.full_name} onChange={set("full_name")} invalid={!!errors.full_name} />
        </Field>
        <Field label="Email" htmlFor="u-email" error={errors.email}>
          <Input id="u-email" type="email" value={v.email} onChange={set("email")} invalid={!!errors.email} />
        </Field>
        <Field label="Peran" htmlFor="u-role">
          <Select id="u-role" value={v.role} onChange={set("role")}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="ID login" htmlFor="u-id" error={errors.username} hint="Opsional; dipakai di aplikasi sebagai pengganti email">
          <Input id="u-id" value={v.username} onChange={set("username")} invalid={!!errors.username} placeholder="e.g. dewi01" />
        </Field>
        <Field label="Password" htmlFor="u-pw" error={errors.temporary_password} hint="Minimal 12 karakter, campuran huruf, angka, dan simbol">
          <Input id="u-pw" type="password" autoComplete="new-password" value={v.temporary_password} onChange={set("temporary_password")} invalid={!!errors.temporary_password} />
        </Field>
        <label className="flex items-center gap-2 text-sm sm:col-span-2">
          <input type="checkbox" checked={mustChange} onChange={(e) => setMustChange(e.target.checked)} className="size-4 accent-[var(--color-primary)]" />
          Wajib ganti password saat login pertama
        </label>
        <Field label="Kode karyawan" htmlFor="u-code">
          <Input id="u-code" value={v.employee_code} onChange={set("employee_code")} />
        </Field>
        <Field label="Departemen" htmlFor="u-dept">
          <Input id="u-dept" value={v.department} onChange={set("department")} />
        </Field>
        <Field label="Jabatan" htmlFor="u-pos">
          <Input id="u-pos" value={v.position} onChange={set("position")} />
        </Field>
        <Field label="Lokasi kerja" htmlFor="u-wp">
          <Select id="u-wp" value={v.workplace_id} onChange={set("workplace_id")}>
            <option value="">—</option>
            {workplaces.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </Select>
        </Field>
      </div>
    </Dialog>
  );
}

function ManageUserDialog({ user, onClose }: { user: User; onClose: () => void }) {
  const { can, me } = useAuth();
  const [tab, setTab] = useState<"profile" | "signin" | "schedule" | "biometrics">("profile");
  return (
    <Dialog open onClose={onClose} title={user.full_name} footer={<Button variant="secondary" onClick={onClose}>Tutup</Button>}>
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { value: "profile", label: "Profil" },
          ...(can("users:manage") ? [{ value: "signin" as const, label: "Login" }] : []),
          ...(can("schedules:read") ? [{ value: "schedule" as const, label: "Jadwal" }] : []),
          ...(can("biometrics:manage") ? [{ value: "biometrics" as const, label: "Data wajah" }] : []),
        ]}
      />
      {tab === "profile" && <ProfileTab user={user} editable={can("users:manage")} self={user.id === me.id} />}
      {tab === "signin" && <SignInTab user={user} self={user.id === me.id} />}
      {tab === "schedule" && <ScheduleTab user={user} editable={can("schedules:manage")} />}
      {tab === "biometrics" && <BiometricsTab user={user} canDelete={can("biometrics:delete_any")} />}
    </Dialog>
  );
}

function ProfileTab({ user, editable, self }: { user: User; editable: boolean; self: boolean }) {
  const qc = useQueryClient();
  const { workplaces } = useRefs();
  const [v, setV] = useState({
    full_name: user.full_name,
    role: user.role,
    employee_code: user.employee_code ?? "",
    department: user.department ?? "",
    position: user.position ?? "",
    workplace_id: user.workplace_id ?? "",
  });
  const [confirm, setConfirm] = useState<"deactivate" | "activate" | null>(null);
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<User>(`/users/${user.id}`, { method: "PATCH", json: body }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["users"] });
      setConfirm(null);
    },
  });
  const set = (k: keyof typeof v) => (e: { target: { value: string } }) => setV({ ...v, [k]: e.target.value });
  return (
    <div className="space-y-4">
      {save.isError && <Alert tone="danger">{errorText(save.error)}</Alert>}
      {save.isSuccess && <Alert tone="success">Tersimpan.</Alert>}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Nama lengkap" htmlFor="e-name">
          <Input id="e-name" value={v.full_name} onChange={set("full_name")} disabled={!editable} />
        </Field>
        <Field label="Peran" htmlFor="e-role" hint={self ? "Anda tidak bisa mengubah peran sendiri" : "Mengubah peran akan mengeluarkan pengguna dari semua perangkat"}>
          <Select id="e-role" value={v.role} onChange={set("role")} disabled={!editable || self}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Kode karyawan" htmlFor="e-code">
          <Input id="e-code" value={v.employee_code} onChange={set("employee_code")} disabled={!editable} />
        </Field>
        <Field label="Departemen" htmlFor="e-dept">
          <Input id="e-dept" value={v.department} onChange={set("department")} disabled={!editable} />
        </Field>
        <Field label="Jabatan" htmlFor="e-pos">
          <Input id="e-pos" value={v.position} onChange={set("position")} disabled={!editable} />
        </Field>
        <Field label="Lokasi kerja" htmlFor="e-wp">
          <Select id="e-wp" value={v.workplace_id} onChange={set("workplace_id")} disabled={!editable}>
            <option value="">—</option>
            {workplaces.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      {editable && (
        <div className="flex flex-wrap justify-between gap-2 border-t border-border pt-4">
          <Button
            loading={save.isPending && !confirm}
            onClick={() =>
              save.mutate({
                full_name: v.full_name,
                ...(self ? {} : { role: v.role }),
                employee_code: v.employee_code || null,
                department: v.department || null,
                position: v.position || null,
                workplace_id: v.workplace_id || null,
              })
            }
          >
            Simpan perubahan
          </Button>
          {!self && (
            <Button variant={user.is_active ? "danger" : "secondary"} onClick={() => setConfirm(user.is_active ? "deactivate" : "activate")}>
              {user.is_active ? "Nonaktifkan akun" : "Aktifkan kembali akun"}
            </Button>
          )}
        </div>
      )}
      <ConfirmDialog
        open={confirm !== null}
        title={confirm === "deactivate" ? "Nonaktifkan akun ini?" : "Aktifkan kembali akun ini?"}
        message={
          confirm === "deactivate"
            ? "Pengguna dikeluarkan dari semua perangkat dan tidak bisa login. Data wajahnya dihapus setelah masa penyimpanan berakhir."
            : "Pengguna akan bisa login kembali."
        }
        confirmLabel={confirm === "deactivate" ? "Nonaktifkan" : "Aktifkan kembali"}
        tone={confirm === "deactivate" ? "danger" : "primary"}
        loading={save.isPending}
        onCancel={() => setConfirm(null)}
        onConfirm={() => save.mutate({ is_active: confirm === "activate" })}
      />
    </div>
  );
}

/** Sign-in ID and password. Passwords are stored hashed, so they can be set here but never shown. */
function SignInTab({ user, self }: { user: User; self: boolean }) {
  const qc = useQueryClient();
  const [id, setId] = useState(user.username ?? "");
  const [idError, setIdError] = useState<string | null>(null);
  const [pw, setPw] = useState("");
  const [mustChange, setMustChange] = useState(true);
  const saveId = useMutation({
    mutationFn: (username: string | null) => api<User>(`/users/${user.id}`, { method: "PATCH", json: { username } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["users"] }),
  });
  const setPassword = useMutation({
    mutationFn: () => api(`/users/${user.id}/reset-password`, { json: { temporary_password: pw, must_change: mustChange } }),
    onSuccess: () => {
      setPw("");
      void qc.invalidateQueries({ queryKey: ["users"] });
    },
  });
  function submitId() {
    const parsed = usernameSchema.safeParse(id);
    if (!parsed.success) {
      setIdError(parsed.error.issues[0]?.message ?? "ID tidak valid");
      return;
    }
    setIdError(null);
    saveId.mutate(parsed.data || null);
  }
  return (
    <div className="space-y-5">
      <section className="space-y-2">
        <p className="text-sm font-medium">ID login</p>
        <p className="text-xs text-muted">
          Karyawan login ke aplikasi HP dengan ID ini (atau email <span className="font-mono">{user.email}</span>). Kosongkan untuk menghapus ID.
        </p>
        {saveId.isError && <Alert tone="danger">{errorText(saveId.error)}</Alert>}
        {saveId.isSuccess && <Alert tone="success">ID login tersimpan.</Alert>}
        <div className="flex gap-2">
          <Input aria-label="ID login" value={id} onChange={(e) => setId(e.target.value)} invalid={!!idError} placeholder="e.g. dewi01" className="font-mono" />
          <Button variant="secondary" className="shrink-0 whitespace-nowrap" loading={saveId.isPending} onClick={submitId}>
            Simpan ID
          </Button>
        </div>
        {idError && <p className="text-xs text-danger">{idError}</p>}
      </section>
      <section className="space-y-2 border-t border-border pt-4">
        <p className="text-sm font-medium">Atur password baru</p>
        <p className="text-xs text-muted">
          Password disimpan terenkripsi (hash) sehingga tidak bisa dilihat, hanya bisa diganti.
          {self ? " Untuk mengganti password Anda sendiri, buka Akun saya." : " Mengatur password akan mengeluarkan pengguna dari semua perangkat."}
        </p>
        {!self && (
          <>
            {setPassword.isError && <Alert tone="danger">{errorText(setPassword.error)}</Alert>}
            {setPassword.isSuccess && <Alert tone="success">Password tersimpan. Berikan langsung kepada pengguna.</Alert>}
            <div className="flex gap-2">
              <Input aria-label="Password baru" type="password" autoComplete="new-password" placeholder="Minimal 12 karakter" value={pw} onChange={(e) => setPw(e.target.value)} />
              <Button variant="secondary" className="shrink-0 whitespace-nowrap" disabled={pw.length < 12} loading={setPassword.isPending} onClick={() => setPassword.mutate()}>
                Simpan password
              </Button>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={mustChange} onChange={(e) => setMustChange(e.target.checked)} className="size-4 accent-[var(--color-primary)]" />
              Wajib ganti password saat login berikutnya
            </label>
          </>
        )}
      </section>
    </div>
  );
}

function ScheduleTab({ user, editable }: { user: User; editable: boolean }) {
  const qc = useQueryClient();
  const assignments = useQuery({ queryKey: ["assignments", user.id], queryFn: () => api<Assignment[]>(`/users/${user.id}/assignments`) });
  const shifts = useQuery({ queryKey: ["shifts"], queryFn: () => api<Shift[]>("/shifts") });
  const [shiftId, setShiftId] = useState("");
  const [from, setFrom] = useState(todayIso());
  const [to, setTo] = useState("");
  const invalidate = () => void qc.invalidateQueries({ queryKey: ["assignments", user.id] });
  const add = useMutation({
    mutationFn: () => api("/assignments", { json: { user_id: user.id, shift_id: shiftId, effective_from: from, effective_to: to || null } }),
    onSuccess: invalidate,
  });
  const end = useMutation({
    mutationFn: (id: string) => api(`/assignments/${id}`, { method: "PATCH", json: { effective_to: todayIso() } }),
    onSuccess: invalidate,
  });
  return (
    <div className="space-y-4">
      {assignments.isPending && <Spinner />}
      {assignments.data && assignments.data.length === 0 && <EmptyState title="Belum ada shift" />}
      {assignments.data && assignments.data.length > 0 && (
        <ul className="divide-y divide-border text-sm">
          {assignments.data.map((a) => (
            <li key={a.id} className="flex items-center justify-between py-2">
              <span>
                <span className="font-medium">{a.shift_name}</span>{" "}
                <span className="text-muted">
                  {formatDate(a.effective_from)} → {a.effective_to ? formatDate(a.effective_to) : "open-ended"}
                </span>
              </span>
              {editable && !a.effective_to && (
                <Button variant="ghost" size="sm" onClick={() => end.mutate(a.id)} loading={end.isPending && end.variables === a.id}>
                  Akhiri hari ini
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {editable && (
        <div className="space-y-3 border-t border-border pt-4">
          {add.isError && <Alert tone="danger">{errorText(add.error)}</Alert>}
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Shift" htmlFor="as-shift">
              <Select id="as-shift" value={shiftId} onChange={(e) => setShiftId(e.target.value)}>
                <option value="">Pilih…</option>
                {(shifts.data ?? []).filter((s) => s.is_active).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Dari" htmlFor="as-from">
              <Input id="as-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
            </Field>
            <Field label="Sampai (opsional)" htmlFor="as-to">
              <Input id="as-to" type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} />
            </Field>
          </div>
          <Button size="sm" disabled={!shiftId || !from} loading={add.isPending} onClick={() => add.mutate()}>
            Tetapkan shift
          </Button>
        </div>
      )}
    </div>
  );
}

function BiometricsTab({ user, canDelete }: { user: User; canDelete: boolean }) {
  const qc = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const action = useMutation({
    mutationFn: (a: "suspend" | "reinstate" | "reset" | "delete") =>
      a === "delete" ? api(`/biometrics/users/${user.id}`, { method: "DELETE" }) : api(`/biometrics/users/${user.id}/${a}`, { method: "POST" }),
    onSuccess: () => {
      setConfirmDelete(false);
      void qc.invalidateQueries({ queryKey: ["users"] });
    },
  });
  const status = user.biometric_status;
  return (
    <div className="space-y-4 text-sm">
      <p>
        Status saat ini: <Badge>{BIOMETRIC_LABEL[status]}</Badge>
      </p>
      {action.isError && <Alert tone="danger">{errorText(action.error)}</Alert>}
      {action.isSuccess && <Alert tone="success">Selesai. Buka ulang pengguna untuk melihat status terbaru.</Alert>}
      <div className="flex flex-wrap gap-2">
        {status === "active" && (
          <Button variant="secondary" onClick={() => action.mutate("suspend")} loading={action.isPending}>
            Tangguhkan verifikasi wajah
          </Button>
        )}
        {status === "suspended" && (
          <Button variant="secondary" onClick={() => action.mutate("reinstate")} loading={action.isPending}>
            Aktifkan kembali
          </Button>
        )}
        {(status === "verification_failed" || status === "pending_retry" || status === "in_progress") && (
          <Button variant="secondary" onClick={() => action.mutate("reset")} loading={action.isPending}>
            Reset pendaftaran wajah
          </Button>
        )}
        {canDelete && status !== "deleted" && status !== "not_enrolled" && (
          <Button variant="danger" onClick={() => setConfirmDelete(true)}>
            Hapus data wajah
          </Button>
        )}
      </div>
      <p className="text-xs text-muted">Setiap tindakan tercatat di log aktivitas.</p>
      <ConfirmDialog
        open={confirmDelete}
        title="Hapus data wajah pengguna ini?"
        message="Templat wajah terenkripsi dihapus permanen. Pengguna harus mendaftarkan wajah lagi untuk bisa absen."
        confirmLabel="Hapus"
        loading={action.isPending}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => action.mutate("delete")}
      />
    </div>
  );
}
