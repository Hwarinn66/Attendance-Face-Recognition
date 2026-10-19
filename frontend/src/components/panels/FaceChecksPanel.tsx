"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Card, EmptyState, ErrorState, SectionHeader, Pagination, Select, Spinner, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { Page, VerificationAttempt } from "@/lib/types";

const PURPOSE_LABEL: Record<string, string> = { enrollment: "Daftar wajah", check_in: "Check-in", check_out: "Check-out" };
const DECISION_LABEL: Record<string, string> = { accepted: "Diterima", rejected: "Ditolak", error: "Galat" };

/** Permission: verifications:read. */
export function FaceChecksPanel() {
  const [decision, setDecision] = useState("");
  const [offset, setOffset] = useState(0);
  const q = useQuery({
    queryKey: ["attempts", decision, offset],
    queryFn: () => api<Page<VerificationAttempt>>("/biometrics/attempts", { query: { decision, limit: 50, offset } }),
  });
  return (
    <>
      <SectionHeader
        title="Percobaan verifikasi wajah"
        description="Keputusan verifikasi wajah di server beserta alasan dan skornya. Gambar wajah tidak disimpan."
      />
      <Card
        actions={
          <Select aria-label="Saring berdasarkan keputusan" className="h-8 w-40" value={decision} onChange={(e) => { setDecision(e.target.value); setOffset(0); }}>
            <option value="">Semua keputusan</option>
            <option value="accepted">Diterima</option>
            <option value="rejected">Ditolak</option>
            <option value="error">Galat</option>
          </Select>
        }
        title="Percobaan terbaru"
      >
        {q.isPending && <Spinner />}
        {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
        {q.data && q.data.items.length === 0 && <EmptyState title="Belum ada percobaan verifikasi" />}
        {q.data && q.data.items.length > 0 && (
          <>
            <Table caption="Percobaan verifikasi wajah">
              <thead>
                <tr>
                  <Th>Waktu</Th>
                  <Th>Karyawan</Th>
                  <Th>Tujuan</Th>
                  <Th>Keputusan</Th>
                  <Th>Alasan</Th>
                  <Th className="text-right">PAD</Th>
                  <Th className="text-right">Kecocokan</Th>
                  <Th className="text-right">Frame</Th>
                  <Th>Penyedia</Th>
                </tr>
              </thead>
              <tbody>
                {q.data.items.map((a) => (
                  <tr key={a.id}>
                    <Td className="whitespace-nowrap">{formatDateTime(a.created_at)}</Td>
                    <Td className="font-medium">{a.user_name}</Td>
                    <Td>{PURPOSE_LABEL[a.purpose] ?? a.purpose}</Td>
                    <Td>
                      <Badge tone={a.decision === "accepted" ? "success" : a.decision === "error" ? "warning" : "danger"}>{DECISION_LABEL[a.decision] ?? a.decision}</Badge>
                    </Td>
                    <Td className="max-w-56 text-xs text-muted">{a.reason_codes.join(", ") || "—"}</Td>
                    <Td className="text-right tabular-nums">{a.pad_score?.toFixed(3) ?? "—"}</Td>
                    <Td className="text-right tabular-nums">{a.match_score?.toFixed(3) ?? "—"}</Td>
                    <Td className="text-right tabular-nums">
                      {a.usable_frame_count}/{a.frame_count}
                    </Td>
                    <Td className="max-w-48 truncate text-xs text-muted" title={a.provider}>
                      {a.provider}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <Pagination total={q.data.total} limit={q.data.limit} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>
    </>
  );
}
