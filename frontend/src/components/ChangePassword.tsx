"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { type FormEvent, useState } from "react";
import { z } from "zod";

import { Alert, Button, Card, Field, Input } from "@/components/ui";
import { ApiError, api } from "@/lib/api";

export const passwordSchema = z
  .object({
    current: z.string().min(1, "Masukkan password saat ini"),
    next: z
      .string()
      .min(12, "Minimal 12 karakter")
      .max(128, "Maksimal 128 karakter")
      .refine(
        (v) => [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((re) => re.test(v)).length >= 3,
        "Gunakan minimal tiga dari: huruf kecil, huruf besar, angka, simbol",
      ),
    confirm: z.string(),
  })
  .refine((v) => v.next === v.confirm, { message: "Password tidak sama", path: ["confirm"] })
  .refine((v) => v.next !== v.current, { message: "Pilih password yang berbeda dari yang sekarang", path: ["next"] });

export function ChangePassword({ forced }: { forced: boolean }) {
  const qc = useQueryClient();
  const [v, setV] = useState({ current: "", next: "", confirm: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const mutation = useMutation({
    mutationFn: () => api("/auth/change-password", { json: { current_password: v.current, new_password: v.next } }),
    onSuccess: () => {
      setV({ current: "", next: "", confirm: "" });
      void qc.invalidateQueries({ queryKey: ["me"] });
      void qc.invalidateQueries({ queryKey: ["sessions"] });
    },
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    const parsed = passwordSchema.safeParse(v);
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
    <Card title="Ganti password">
      <form noValidate onSubmit={submit} className="space-y-3">
        {forced && <Alert tone="warning">Akun Anda memakai password sementara. Buat password baru untuk melanjutkan.</Alert>}
        {mutation.isSuccess && <Alert tone="success">Password sudah diganti. Sesi di perangkat lain telah dikeluarkan.</Alert>}
        {mutation.isError && <Alert tone="danger">{mutation.error instanceof ApiError ? mutation.error.message : "Gagal mengganti password"}</Alert>}
        <Field label="Password saat ini" htmlFor="pw-current" error={errors.current}>
          <Input id="pw-current" type="password" autoComplete="current-password" value={v.current} onChange={(e) => setV({ ...v, current: e.target.value })} invalid={!!errors.current} />
        </Field>
        <Field label="Password baru" htmlFor="pw-next" error={errors.next} hint="Minimal 12 karakter, campuran huruf, angka, dan simbol">
          <Input id="pw-next" type="password" autoComplete="new-password" value={v.next} onChange={(e) => setV({ ...v, next: e.target.value })} invalid={!!errors.next} />
        </Field>
        <Field label="Ulangi password baru" htmlFor="pw-confirm" error={errors.confirm}>
          <Input id="pw-confirm" type="password" autoComplete="new-password" value={v.confirm} onChange={(e) => setV({ ...v, confirm: e.target.value })} invalid={!!errors.confirm} />
        </Field>
        <Button type="submit" loading={mutation.isPending}>
          <KeyRound className="size-4" aria-hidden />
          Simpan password
        </Button>
      </form>
    </Card>
  );
}
