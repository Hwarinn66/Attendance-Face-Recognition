"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ScanFace } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, useState } from "react";
import { z } from "zod";

import { Alert, Button, Field, Input } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { Me } from "@/lib/types";

export const loginSchema = z.object({
  login: z.string().trim().min(1, "Masukkan ID atau email"),
  password: z.string().min(1, "Masukkan password"),
});

type Errors = Partial<Record<"login" | "password", string>>;

/** Only same-origin relative paths are allowed as post-login destinations (no open redirect). */
export function safeNext(next: string | null): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/hr";
  return next;
}

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    const parsed = loginSchema.safeParse({ login, password });
    if (!parsed.success) {
      const fieldErrors: Errors = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0] as keyof Errors;
        fieldErrors[key] ??= issue.message;
      }
      setErrors(fieldErrors);
      return;
    }
    setErrors({});
    setLoading(true);
    try {
      const res = await api<{ user: Me; csrf_token: string }>("/auth/login", { json: parsed.data });
      qc.setQueryData(["me"], res.user);
      router.replace(res.user.must_change_password ? "/account" : safeNext(params.get("next")));
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : "Gagal masuk. Silakan coba lagi.");
      setLoading(false);
    }
  }

  return (
    <div className="w-full max-w-sm">
      <div className="mb-8 flex flex-col items-center text-center">
        <span className="flex size-12 items-center justify-center rounded-xl bg-primary text-primary-fg shadow-sm">
          <ScanFace className="size-7" aria-hidden />
        </span>
        <h1 className="mt-4 text-2xl font-semibold tracking-tight">Masuk ke Absen</h1>
        <p className="mt-1 text-sm text-muted">Konsol administrator absensi</p>
      </div>
      <form noValidate onSubmit={onSubmit} className="space-y-4 rounded-xl border border-border bg-surface p-6 shadow-sm">
        {formError && <Alert tone="danger">{formError}</Alert>}
        <Field label="ID atau email" htmlFor="login" error={errors.login}>
          <Input
            id="login"
            type="text"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            value={login}
            onChange={(e) => setLogin(e.target.value)}
            invalid={!!errors.login}
            aria-describedby={errors.login ? "login-error" : undefined}
          />
        </Field>
        <Field label="Password" htmlFor="password" error={errors.password}>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            invalid={!!errors.password}
            aria-describedby={errors.password ? "password-error" : undefined}
          />
        </Field>
        <Button type="submit" className="w-full" loading={loading}>
          Masuk
        </Button>
      </form>
      <p className="mt-6 text-center text-xs text-muted">Lupa password? Minta administrator untuk mengaturnya ulang.</p>
    </div>
  );
}
