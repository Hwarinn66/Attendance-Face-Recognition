"use client";

import { useState } from "react";

import { formatDate } from "@/lib/format";

import { Button } from "./ui";

export interface TrendPoint {
  day: string;
  on_time: number;
  late: number;
  absent: number;
}

const SERIES = [
  { key: "on_time", label: "Tepat waktu", color: "var(--series-1)" },
  { key: "late", label: "Terlambat", color: "var(--series-2)" },
  { key: "absent", label: "Tidak hadir", color: "var(--series-3)" },
] as const;

const H = 200;
const PAD = { top: 12, right: 8, bottom: 26, left: 32 };
const GAP = 2; // surface gap between stacked segments
const RADIUS = 4;

export function niceTicks(max: number): number[] {
  if (max <= 0) return [0, 1];
  const raw = max / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const top = Math.ceil(max / step) * step;
  return Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
}

/** Column path: square at the baseline, 4px rounded data-end when it is the top of the stack. */
function columnPath(x: number, y: number, w: number, h: number, rounded: boolean): string {
  if (h <= 0) return "";
  const r = rounded ? Math.min(RADIUS, h, w / 2) : 0;
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
}

/** Daily attendance composition. Stacked columns, one axis, legend + hover tooltip + table view. */
export function TrendChart({ data, title = "Absensi 14 hari terakhir" }: { data: TrendPoint[]; title?: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);
  const width = 640;
  const innerW = width - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const totals = data.map((d) => d.on_time + d.late + d.absent);
  const ticks = niceTicks(Math.max(...totals, 0));
  const yMax = ticks[ticks.length - 1] ?? 1;
  const band = innerW / Math.max(1, data.length);
  const colW = Math.min(24, band * 0.6);
  const y = (v: number) => PAD.top + innerH - (v / yMax) * innerH;
  const hovered = hover !== null ? data[hover] : undefined;

  return (
    <figure className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <figcaption className="text-sm font-medium">{title}</figcaption>
        <div className="flex items-center gap-4">
          <ul className="flex flex-wrap gap-4 text-xs text-muted" aria-label="Keterangan">
            {SERIES.map((s) => (
              <li key={s.key} className="flex items-center gap-1.5">
                <span className="inline-block size-2.5 rounded-sm" style={{ background: s.color }} aria-hidden />
                {s.label}
              </li>
            ))}
          </ul>
          <Button variant="ghost" size="sm" onClick={() => setShowTable((v) => !v)} aria-pressed={showTable}>
            {showTable ? "Tampilkan grafik" : "Tampilkan tabel"}
          </Button>
        </div>
      </div>

      {showTable ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">{title}</caption>
            <thead>
              <tr className="text-left text-xs text-muted">
                <th scope="col" className="py-1.5 pr-4 font-medium">Hari</th>
                {SERIES.map((s) => (
                  <th key={s.key} scope="col" className="py-1.5 pr-4 text-right font-medium">{s.label}</th>
                ))}
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {data.map((d) => (
                <tr key={d.day} className="border-t border-border">
                  <td className="py-1.5 pr-4">{formatDate(d.day)}</td>
                  <td className="py-1.5 pr-4 text-right">{d.on_time}</td>
                  <td className="py-1.5 pr-4 text-right">{d.late}</td>
                  <td className="py-1.5 pr-4 text-right">{d.absent}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="relative">
          <svg viewBox={`0 0 ${width} ${H}`} className="h-auto w-full" role="img" aria-label={`${title}. Gunakan “Tampilkan tabel” untuk melihat angkanya.`}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeWidth={1} />
                <text x={PAD.left - 6} y={y(t)} dy="0.32em" textAnchor="end" fontSize={10} fill="var(--muted)" className="tabular-nums">
                  {t}
                </text>
              </g>
            ))}
            {data.map((d, i) => {
              const cx = PAD.left + band * i + band / 2;
              const x = cx - colW / 2;
              const values = [d.on_time, d.late, d.absent];
              const topIndex = values.reduce((acc, v, k) => (v > 0 ? k : acc), -1);
              let acc = 0;
              const day = Number(d.day.slice(8, 10));
              const showLabel = data.length <= 14 || i % 2 === 0;
              return (
                <g key={d.day}>
                  {values.map((v, k) => {
                    if (v <= 0) return null;
                    const y0 = y(acc);
                    const y1 = y(acc + v);
                    acc += v;
                    const h = Math.max(0, y0 - y1 - (k < topIndex ? GAP : 0));
                    return <path key={SERIES[k]!.key} d={columnPath(x, y1 + (k < topIndex ? GAP : 0), colW, h, k === topIndex)} fill={SERIES[k]!.color} opacity={hover === null || hover === i ? 1 : 0.45} />;
                  })}
                  {showLabel && (
                    <text x={cx} y={H - 8} textAnchor="middle" fontSize={10} fill="var(--muted)" className="tabular-nums">
                      {day}
                    </text>
                  )}
                  <rect
                    x={PAD.left + band * i}
                    y={PAD.top}
                    width={band}
                    height={innerH}
                    fill="transparent"
                    tabIndex={0}
                    aria-label={`${formatDate(d.day)}: ${d.on_time} tepat waktu, ${d.late} terlambat, ${d.absent} tidak hadir`}
                    onMouseEnter={() => setHover(i)}
                    onMouseLeave={() => setHover(null)}
                    onFocus={() => setHover(i)}
                    onBlur={() => setHover(null)}
                  />
                </g>
              );
            })}
            <line x1={PAD.left} x2={width - PAD.right} y1={y(0)} y2={y(0)} stroke="var(--border-strong)" strokeWidth={1} />
          </svg>
          {hovered && hover !== null && (
            <div
              role="tooltip"
              className="pointer-events-none absolute top-2 z-10 min-w-36 -translate-x-1/2 rounded-lg border border-border bg-surface px-3 py-2 text-xs shadow-lg"
              style={{ left: `${((PAD.left + band * hover + band / 2) / width) * 100}%` }}
            >
              <p className="mb-1 font-medium">{formatDate(hovered.day)}</p>
              {SERIES.map((s) => (
                <p key={s.key} className="flex items-center justify-between gap-3 tabular-nums">
                  <span className="flex items-center gap-1.5 text-muted">
                    <span className="inline-block size-2 rounded-sm" style={{ background: s.color }} aria-hidden />
                    {s.label}
                  </span>
                  <span>{hovered[s.key]}</span>
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </figure>
  );
}
