"use client";

import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Badge, Card, Dialog, EmptyState, ErrorState, Input, SectionHeader, Pagination, Select, Spinner, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { AuditEvent, Page } from "@/lib/types";

const OUTCOME_LABEL: Record<string, string> = { success: "berhasil", failure: "gagal", denied: "ditolak" };
const CATEGORIES = ["auth", "security", "admin", "attendance", "biometric", "privacy"];

function Audit() {
  const params = useSearchParams();
  const [securityOnly, setSecurityOnly] = useState(params.get("security") === "1");
  const [category, setCategory] = useState("");
  const [severity, setSeverity] = useState("");
  const [action, setAction] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<AuditEvent | null>(null);
  const q = useQuery({
    queryKey: ["audit", securityOnly, category, severity, action, offset],
    queryFn: () =>
      api<Page<AuditEvent>>("/audit/events", {
        query: { security_only: securityOnly || undefined, category, severity, action, limit: 50, offset },
      }),
  });
  const reset = () => setOffset(0);
  return (
    <>
      <SectionHeader title="Log aktivitas & keamanan" description="Catatan tindakan penting yang tidak bisa diubah; perubahan diblokir di tingkat database." />
      <Card
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" className="accent-[var(--primary)]" checked={securityOnly} onChange={(e) => { setSecurityOnly(e.target.checked); reset(); }} />
              Hanya keamanan
            </label>
            <Select aria-label="Kategori" className="h-8 w-36" value={category} onChange={(e) => { setCategory(e.target.value); reset(); }}>
              <option value="">Semua kategori</option>
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </Select>
            <Select aria-label="Tingkat" className="h-8 w-32" value={severity} onChange={(e) => { setSeverity(e.target.value); reset(); }}>
              <option value="">Semua tingkat</option>
              <option value="info">Info</option>
              <option value="warning">Peringatan</option>
              <option value="critical">Kritis</option>
            </Select>
            <Input aria-label="Awalan aksi" placeholder="Awalan aksi, mis. auth." className="h-8 w-48" value={action} onChange={(e) => { setAction(e.target.value); reset(); }} />
          </div>
        }
        title={q.data ? `${q.data.total} kejadian` : "Kejadian"}
      >
        {q.isPending && <Spinner />}
        {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
        {q.data && q.data.items.length === 0 && <EmptyState title="Tidak ada kejadian yang cocok" />}
        {q.data && q.data.items.length > 0 && (
          <>
            <Table caption="Daftar kejadian">
              <thead>
                <tr>
                  <Th>Waktu</Th>
                  <Th>Aksi</Th>
                  <Th>Hasil</Th>
                  <Th>Pelaku</Th>
                  <Th>Sasaran</Th>
                  <Th>IP</Th>
                </tr>
              </thead>
              <tbody>
                {q.data.items.map((e) => (
                  <tr key={e.id} className="cursor-pointer hover:bg-surface-2" onClick={() => setSelected(e)}>
                    <Td className="whitespace-nowrap text-xs">{formatDateTime(e.occurred_at)}</Td>
                    <Td>
                      <button type="button" className="text-left font-mono text-xs hover:underline" onClick={() => setSelected(e)}>
                        {e.action}
                      </button>
                      {e.severity !== "info" && (
                        <span className="ml-2">
                          <Badge tone={e.severity === "critical" ? "danger" : "warning"}>{e.severity}</Badge>
                        </span>
                      )}
                    </Td>
                    <Td>
                      <Badge tone={e.outcome === "success" ? "success" : e.outcome === "denied" ? "danger" : "warning"}>{OUTCOME_LABEL[e.outcome] ?? e.outcome}</Badge>
                    </Td>
                    <Td className="text-xs">{e.actor_email ?? "system"}</Td>
                    <Td className="text-xs text-muted">{e.target_type ? `${e.target_type}:${e.target_id?.slice(0, 8)}` : "—"}</Td>
                    <Td className="text-xs text-muted">{e.ip_address ?? "—"}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <Pagination total={q.data.total} limit={q.data.limit} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>
      <Dialog open={selected !== null} onClose={() => setSelected(null)} title={selected?.action ?? ""}>
        {selected && (
          <dl className="grid grid-cols-[7rem_1fr] gap-y-1.5 text-sm">
            <dt className="text-muted">Waktu</dt>
            <dd>{formatDateTime(selected.occurred_at)}</dd>
            <dt className="text-muted">Kategori</dt>
            <dd>{selected.category}</dd>
            <dt className="text-muted">Pelaku</dt>
            <dd className="break-all">{selected.actor_email ?? "sistem"}</dd>
            <dt className="text-muted">Sasaran</dt>
            <dd className="break-all">{selected.target_type ? `${selected.target_type} ${selected.target_id}` : "—"}</dd>
            <dt className="text-muted">ID permintaan</dt>
            <dd className="break-all font-mono text-xs">{selected.request_id ?? "—"}</dd>
            <dt className="text-muted">Detail</dt>
            <dd>
              <pre className="max-h-64 overflow-auto rounded-lg bg-surface-2 p-2 text-xs">{JSON.stringify(selected.details, null, 2)}</pre>
            </dd>
          </dl>
        )}
      </Dialog>
    </>
  );
}

/** Permission: audit:read. */
export function ActivityLogPanel() {
  return (
    <Suspense>
      <Audit />
    </Suspense>
  );
}
