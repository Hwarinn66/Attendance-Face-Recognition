"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { FlaskConical, LogOut, Menu, ScanFace, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useState } from "react";

import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ROLE_LABEL } from "@/lib/format";
import type { HealthReady } from "@/lib/types";

import { LiveIndicator, LiveToasts, useLiveUpdates } from "./LiveUpdates";
import { visibleNav } from "./nav";

export function AppShell({ children }: { children: ReactNode }) {
  const { me, can } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const sections = visibleNav(me.permissions);
  const live = useLiveUpdates({ enabled: can("attendance:read_all") && !me.must_change_password, myId: me.id });
  const showLive = can("attendance:read_all");
  const health = useQuery({ queryKey: ["health"], queryFn: () => api<HealthReady>("/health/ready"), staleTime: 60_000 });
  const logout = useMutation({
    mutationFn: () => api("/auth/logout", { method: "POST" }),
    onSettled: () => {
      qc.clear();
      router.replace("/login");
    },
  });

  const nav = (
    <nav aria-label="Menu utama" className="flex flex-col gap-6 p-4">
      {sections.map((section) => (
        <div key={section.title}>
          <p className="px-2 pb-1.5 text-xs font-semibold uppercase tracking-wider text-muted">{section.title}</p>
          <ul className="space-y-0.5">
            {section.items.map((item) => {
              const active = pathname === item.href || (item.href !== "/hr" && pathname.startsWith(`${item.href}/`));
              const Icon = item.icon;
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={() => setOpen(false)}
                    aria-current={active ? "page" : undefined}
                    className={clsx(
                      "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors",
                      active ? "bg-primary-soft font-medium text-primary" : "text-text/80 hover:bg-surface-2 hover:text-text",
                    )}
                  >
                    <Icon className="size-4" aria-hidden />
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );

  return (
    <div className="flex min-h-dvh">
      <aside className="hidden w-64 shrink-0 flex-col border-r border-border bg-surface lg:flex">
        <Brand>{showLive && <LiveIndicator status={live.status} />}</Brand>
        <div className="flex-1 overflow-y-auto">{nav}</div>
        <UserFooter name={me.full_name} role={ROLE_LABEL[me.role] ?? me.role} onLogout={() => logout.mutate()} />
      </aside>

      {open && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigasi">
          <button type="button" aria-label="Tutup navigasi" className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <div className="absolute inset-y-0 left-0 flex w-72 flex-col bg-surface shadow-xl">
            <div className="flex items-center justify-between pr-3">
              <Brand />
              <button type="button" className="rounded-lg p-2 hover:bg-surface-2" aria-label="Tutup navigasi" onClick={() => setOpen(false)}>
                <X className="size-5" aria-hidden />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto">{nav}</div>
            <UserFooter name={me.full_name} role={ROLE_LABEL[me.role] ?? me.role} onLogout={() => logout.mutate()} />
          </div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-surface/90 px-4 backdrop-blur lg:hidden">
          <button type="button" className="rounded-lg p-2 hover:bg-surface-2" aria-label="Buka navigasi" onClick={() => setOpen(true)}>
            <Menu className="size-5" aria-hidden />
          </button>
          <span className="font-semibold">Absen</span>
          {showLive && <LiveIndicator status={live.status} className="ml-auto" />}
        </header>
        {health.data?.checks.test_fixture && (
          <div role="status" className="flex items-center gap-2 border-b border-warning/40 bg-warning-soft px-4 py-2 text-sm text-warning">
            <FlaskConical className="size-4" aria-hidden />
            Lingkungan uji: verifikasi wajah memakai data uji, bukan model biometrik sungguhan.
          </div>
        )}
        {me.must_change_password && pathname !== "/account" && (
          <div role="alert" className="border-b border-warning/40 bg-warning-soft px-4 py-2 text-sm">
            Anda wajib mengganti password sementara.{" "}
            <Link href="/account" className="font-medium text-primary underline">
              Ganti sekarang
            </Link>
          </div>
        )}
        <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6 lg:py-8">
          {children}
        </main>
      </div>
      <LiveToasts toasts={live.toasts} onDismiss={live.dismiss} />
    </div>
  );
}

function Brand({ children }: { children?: ReactNode }) {
  return (
    <div className="flex h-16 items-center gap-2.5 px-5">
      <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-fg">
        <ScanFace className="size-5" aria-hidden />
      </span>
      <span className="text-base font-semibold tracking-tight">Absen</span>
      {children && <span className="ml-auto">{children}</span>}
    </div>
  );
}

function UserFooter({ name, role, onLogout }: { name: string; role: string; onLogout: () => void }) {
  return (
    <div className="flex items-center justify-between gap-2 border-t border-border p-4">
      <Link href="/account" className="min-w-0 rounded-lg px-1 py-0.5 hover:bg-surface-2" title="Akun saya">
        <p className="truncate text-sm font-medium">{name}</p>
        <p className="text-xs text-muted">{role}</p>
      </Link>
      <button type="button" onClick={onLogout} className="rounded-lg p-2 text-muted hover:bg-surface-2 hover:text-text" aria-label="Keluar">
        <LogOut className="size-4" aria-hidden />
      </button>
    </div>
  );
}
