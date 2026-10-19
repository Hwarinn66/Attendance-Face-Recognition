"use client";

import { createContext, type ReactNode, useContext } from "react";

import type { Me, Permission } from "./types";

interface AuthValue {
  me: Me;
  can: (permission: Permission) => boolean;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ me, children }: { me: Me; children: ReactNode }) {
  const value: AuthValue = { me, can: (p) => me.permissions.includes(p) };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

/** UI-level gate only — the server independently enforces every permission. */
export function RequirePermission({ permission, children }: { permission: Permission; children: ReactNode }) {
  const { can } = useAuth();
  if (!can(permission)) {
    return (
      <div role="alert" className="rounded-xl border border-border bg-surface p-8 text-center">
        <p className="font-medium">Anda tidak punya akses ke halaman ini.</p>
        <p className="mt-1 text-sm text-muted">Jika ini keliru, hubungi administrator.</p>
      </div>
    );
  }
  return <>{children}</>;
}
