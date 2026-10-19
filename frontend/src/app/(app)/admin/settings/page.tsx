"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { ActivityLogPanel } from "@/components/panels/ActivityLogPanel";
import { Alert, Badge, Button, Card, ErrorState, Field, Input, PageHeader, SectionHeader, Spinner, Tabs, Textarea } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { RequirePermission } from "@/lib/auth";
import type { HealthReady } from "@/lib/types";

// Indonesian labels for the server's setting keys (the API describes them in English).
const SETTING_LABEL: Record<string, string> = {
  "enrollment.max_attempts_per_day": "Batas percobaan daftar wajah gagal per 24 jam",
  "retention.verification_attempts_days": "Lama penyimpanan riwayat verifikasi wajah (hari)",
  "retention.audit_days": "Lama penyimpanan log aktivitas (hari)",
  "retention.inactive_biometric_days": "Hapus data wajah akun nonaktif setelah (hari)",
};

const MODEL_ROLE: Record<string, string> = { detector: "Detektor wajah", recognizer: "Pengenal wajah", pad: "Deteksi wajah palsu" };

interface SettingsView {
  values: Record<string, string | number>;
  descriptions: Record<string, string>;
}

export default function SettingsPage() {
  return (
    <RequirePermission permission="settings:manage">
      <Suspense>
        <SettingsTabs />
      </Suspense>
    </RequirePermission>
  );
}

function SettingsTabs() {
  const params = useSearchParams();
  const [tab, setTab] = useState<"general" | "activity">(params.get("tab") === "activity" ? "activity" : "general");
  return (
    <>
      <PageHeader title="Pengaturan" description="Aturan sistem, serta log aktivitas dan keamanan yang tidak bisa diubah." />
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { value: "general", label: "Umum" },
          { value: "activity", label: "Log aktivitas" },
        ]}
      />
      {tab === "general" ? <Settings /> : <ActivityLogPanel />}
    </>
  );
}

function Settings() {
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsView>("/settings") });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  // Re-mount the form when the saved values change, so its draft starts from the server state.
  return <SettingsForm key={JSON.stringify(q.data.values)} data={q.data} />;
}

function SettingsForm({ data }: { data: SettingsView }) {
  const qc = useQueryClient();
  const health = useQuery({ queryKey: ["health"], queryFn: () => api<HealthReady>("/health/ready") });
  const [draft, setDraft] = useState<Record<string, string | number>>(data.values);
  const [saved, setSaved] = useState(false);
  const save = useMutation({
    mutationFn: () => {
      const changed = Object.fromEntries(Object.entries(draft).filter(([k, v]) => data.values[k] !== v));
      return api<SettingsView>("/settings", { method: "PATCH", json: { values: changed } });
    },
    onSuccess: (next) => {
      setSaved(true);
      qc.setQueryData(["settings"], next);
    },
  });
  const dirty = Object.entries(draft).some(([k, v]) => data.values[k] !== v);
  const numeric = Object.keys(data.values).filter((k) => typeof data.values[k] === "number");

  return (
    <>
      <SectionHeader title="Pengaturan sistem" />
      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Kebijakan" className="lg:col-span-2">
          <div className="space-y-4">
            {save.isError && <Alert tone="danger">{save.error instanceof ApiError ? save.error.message : "Gagal menyimpan"}</Alert>}
            {saved && !dirty && <Alert tone="success">Pengaturan disimpan dan tercatat di log aktivitas.</Alert>}
            <div className="grid gap-4 sm:grid-cols-2">
              {numeric.map((key) => (
                <Field key={key} label={SETTING_LABEL[key] ?? data.descriptions[key] ?? key} htmlFor={key}>
                  <Input id={key} type="number" value={draft[key] ?? ""} onChange={(e) => setDraft({ ...draft, [key]: Number(e.target.value) })} />
                </Field>
              ))}
            </div>
            <Field label="Versi persetujuan" htmlFor="consent-version" hint="Ubah setiap kali teks persetujuan berubah; pengguna akan diminta menyetujui ulang.">
              <Input id="consent-version" value={String(draft["consent.version"] ?? "")} onChange={(e) => setDraft({ ...draft, "consent.version": e.target.value })} />
            </Field>
            <Field label="Teks persetujuan data biometrik" htmlFor="consent-text">
              <Textarea id="consent-text" className="min-h-48" value={String(draft["consent.text"] ?? "")} onChange={(e) => setDraft({ ...draft, "consent.text": e.target.value })} />
            </Field>
            <div className="flex gap-2">
              <Button disabled={!dirty} loading={save.isPending} onClick={() => save.mutate()}>
                Simpan pengaturan
              </Button>
              <Button variant="secondary" disabled={!dirty} onClick={() => setDraft(data.values)}>
                Batalkan perubahan
              </Button>
            </div>
          </div>
        </Card>
        <Card title="Status layanan">
          {health.isPending && <Spinner />}
          {health.data && (
            <ul className="space-y-2 text-sm">
              <li className="flex justify-between">
                <span className="text-muted">Database</span>
                <Badge tone={health.data.checks.database === "ok" ? "success" : "danger"}>{health.data.checks.database === "ok" ? "normal" : health.data.checks.database}</Badge>
              </li>
              <li className="flex justify-between">
                <span className="text-muted">Verifikasi wajah</span>
                <Badge tone={health.data.checks.verification === "available" ? "success" : "danger"}>{health.data.checks.verification === "available" ? "tersedia" : "tidak tersedia"}</Badge>
              </li>
              <li className="flex justify-between">
                <span className="text-muted">Deteksi wajah palsu wajib</span>
                <Badge tone={health.data.checks.pad_enforced ? "success" : "warning"}>{health.data.checks.pad_enforced ? "ya" : "tidak"}</Badge>
              </li>
              {health.data.checks.models?.map((m) => (
                <li key={m.role} className="flex justify-between gap-2">
                  <span className="text-muted">{MODEL_ROLE[m.role] ?? m.role}</span>
                  <span className="truncate text-xs" title={m.model_id ?? ""}>
                    {m.model_id ?? "—"} {m.available ? "✓" : "✗"}
                  </span>
                </li>
              ))}
              <li className="flex justify-between">
                <span className="text-muted">Lingkungan</span>
                <span>{health.data.checks.environment}</span>
              </li>
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
