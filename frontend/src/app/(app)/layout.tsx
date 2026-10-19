"use client";

import { useQuery } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect } from "react";

import { AppShell } from "@/components/AppShell";
import { ErrorState, Spinner } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { AuthProvider } from "@/lib/auth";
import type { Me } from "@/lib/types";

export default function AuthenticatedLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/auth/me") });
  const unauthenticated = me.error instanceof ApiError && me.error.status === 401;

  useEffect(() => {
    if (unauthenticated) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [unauthenticated, router, pathname]);

  if (me.isPending || unauthenticated) return <Spinner label="Memeriksa sesi Anda" />;
  if (me.isError) {
    return (
      <div className="mx-auto max-w-md p-8">
        <ErrorState error={me.error} onRetry={() => void me.refetch()} />
      </div>
    );
  }
  return (
    <AuthProvider me={me.data}>
      <AppShell>{children}</AppShell>
    </AuthProvider>
  );
}
