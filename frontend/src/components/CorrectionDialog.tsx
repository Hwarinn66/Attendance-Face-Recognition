"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { z } from "zod";

import { ApiError, api } from "@/lib/api";

import { Alert, Button, Dialog, Field, Input, Select, Textarea } from "./ui";

export const correctionSchema = z
  .object({
    shift_id: z.string().uuid("Pilih shift"),
    shift_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Pilih tanggal"),
    check_in: z.string(),
    check_out: z.string(),
    reason: z.string().trim().min(5, "Jelaskan alasannya (minimal 5 karakter)").max(2000),
  })
  .refine((v) => v.check_in || v.check_out, { message: "Isi jam check-in dan/atau check-out", path: ["check_in"] })
  .refine((v) => !v.check_in || !v.check_out || new Date(v.check_out) >= new Date(v.check_in), {
    message: "Check-out harus setelah check-in",
    path: ["check_out"],
  });

export type CorrectionValues = z.input<typeof correctionSchema>;

/** datetime-local values are interpreted in the device time zone and sent as UTC instants. */
export function toUtcIso(local: string): string | null {
  return local ? new Date(local).toISOString() : null;
}

export function CorrectionDialog({
  open,
  onClose,
  initial,
  shifts,
  userId,
  applyImmediately = false,
}: {
  open: boolean;
  onClose: () => void;
  initial: Partial<CorrectionValues>;
  shifts: { id: string; name: string }[];
  userId?: string;
  applyImmediately?: boolean;
}) {
  const qc = useQueryClient();
  const [values, setValues] = useState<CorrectionValues>({ shift_id: "", shift_date: "", check_in: "", check_out: "", reason: "", ...initial });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const mutation = useMutation({
    mutationFn: (v: CorrectionValues) =>
      api("/attendance/corrections", {
        json: {
          ...(userId ? { user_id: userId } : {}),
          shift_id: v.shift_id,
          shift_date: v.shift_date,
          requested_check_in_at: toUtcIso(v.check_in),
          requested_check_out_at: toUtcIso(v.check_out),
          reason: v.reason,
          apply_immediately: applyImmediately,
        },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["corrections"] });
      void qc.invalidateQueries({ queryKey: ["my-attendance"] });
      void qc.invalidateQueries({ queryKey: ["records"] });
      onClose();
    },
  });

  function submit() {
    const parsed = correctionSchema.safeParse(values);
    if (!parsed.success) {
      const errs: Record<string, string> = {};
      for (const issue of parsed.error.issues) errs[String(issue.path[0])] ??= issue.message;
      setErrors(errs);
      return;
    }
    setErrors({});
    mutation.mutate(values);
  }

  const set = (k: keyof CorrectionValues) => (e: { target: { value: string } }) => setValues((v) => ({ ...v, [k]: e.target.value }));

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={applyImmediately ? "Terapkan koreksi absensi" : "Ajukan koreksi absensi"}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={mutation.isPending}>
            Batal
          </Button>
          <Button onClick={submit} loading={mutation.isPending}>
            {applyImmediately ? "Terapkan koreksi" : "Kirim permintaan"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {mutation.isError && <Alert tone="danger">{mutation.error instanceof ApiError ? mutation.error.message : "Permintaan gagal"}</Alert>}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Shift" htmlFor="c-shift" error={errors.shift_id}>
            <Select id="c-shift" value={values.shift_id} onChange={set("shift_id")} invalid={!!errors.shift_id}>
              <option value="">Pilih…</option>
              {shifts.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Tanggal shift" htmlFor="c-date" error={errors.shift_date}>
            <Input id="c-date" type="date" value={values.shift_date} onChange={set("shift_date")} invalid={!!errors.shift_date} />
          </Field>
          <Field label="Jam check-in" htmlFor="c-in" error={errors.check_in} hint="Zona waktu perangkat Anda">
            <Input id="c-in" type="datetime-local" value={values.check_in} onChange={set("check_in")} invalid={!!errors.check_in} />
          </Field>
          <Field label="Jam check-out" htmlFor="c-out" error={errors.check_out}>
            <Input id="c-out" type="datetime-local" value={values.check_out} onChange={set("check_out")} invalid={!!errors.check_out} />
          </Field>
        </div>
        <Field label="Alasan" htmlFor="c-reason" error={errors.reason}>
          <Textarea id="c-reason" value={values.reason} onChange={set("reason")} invalid={!!errors.reason} />
        </Field>
        <p className="text-xs text-muted">
          {applyImmediately
            ? "Nilai sebelumnya tetap disimpan dan perubahan tercatat di log aktivitas."
            : "Administrator meninjau permintaan ini. Catatan asli tetap disimpan."}
        </p>
      </div>
    </Dialog>
  );
}
