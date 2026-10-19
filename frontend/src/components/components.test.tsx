import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, RequirePermission } from "@/lib/auth";
import type { Me, Permission } from "@/lib/types";

import { correctionSchema, toUtcIso } from "./CorrectionDialog";
import { visibleNav } from "./nav";
import { TrendChart, niceTicks } from "./TrendChart";

afterEach(() => vi.unstubAllGlobals());

const EMPLOYEE: Permission[] = ["self_service"];
const ADMIN: Permission[] = [
  "self_service",
  "attendance:read_team",
  "attendance:read_all",
  "correction:review",
  "correction:apply_direct",
  "reports:read",
  "users:read",
  "users:manage",
  "schedules:read",
  "schedules:manage",
  "holidays:manage",
  "biometrics:manage",
  "biometrics:delete_any",
  "verifications:read",
  "audit:read",
  "settings:manage",
];

describe("web navigation (administration console only)", () => {
  const labels = (p: Permission[]) => visibleNav(p).flatMap((s) => s.items.map((i) => i.label));

  it("has no self-service pages: employees attend in the mobile app", () => {
    expect(labels(EMPLOYEE)).toEqual([]);
  });

  it("administrators get one compact menu", () => {
    expect(labels(ADMIN)).toEqual(["Ringkasan", "Absensi", "Pengguna", "Jadwal", "Pengaturan"]);
  });
});

function me(permissions: Permission[]): Me {
  return {
    id: "u1",
    email: "e@example.com",
    username: null,
    full_name: "E",
    role: "employee",
    is_active: true,
    must_change_password: false,
    employee_code: null,
    department: null,
    position: null,
    workplace_id: null,
    manager_id: null,
    biometric_status: "active",
    last_login_at: null,
    created_at: "2026-01-01T00:00:00Z",
    permissions,
  };
}

describe("RequirePermission", () => {
  it("blocks pages the user may not open", () => {
    render(
      <AuthProvider me={me(EMPLOYEE)}>
        <RequirePermission permission="audit:read">
          <p>secret</p>
        </RequirePermission>
      </AuthProvider>,
    );
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/tidak punya akses/);
  });
});

describe("correction form schema", () => {
  const base = { shift_id: "6f1c5a9e-6c1d-4a1e-9b3a-2f2e4c1d0a11", shift_date: "2026-10-12", check_in: "", check_out: "", reason: "Forgot badge" };

  it("requires at least one time and a reason", () => {
    expect(correctionSchema.safeParse(base).success).toBe(false);
    expect(correctionSchema.safeParse({ ...base, check_in: "2026-10-12T08:05", reason: "no" }).success).toBe(false);
    expect(correctionSchema.safeParse({ ...base, check_in: "2026-10-12T08:05" }).success).toBe(true);
  });

  it("rejects check-out before check-in", () => {
    const r = correctionSchema.safeParse({ ...base, check_in: "2026-10-12T17:00", check_out: "2026-10-12T08:00" });
    expect(r.success).toBe(false);
  });

  it("converts local datetime to a UTC instant", () => {
    expect(toUtcIso("")).toBeNull();
    expect(toUtcIso("2026-10-12T08:05")).toMatch(/Z$/);
  });
});

describe("TrendChart", () => {
  it("computes clean ticks", () => {
    expect(niceTicks(0)).toEqual([0, 1]);
    expect(niceTicks(7)).toEqual([0, 2, 4, 6, 8]);
    expect(niceTicks(42)).toEqual([0, 20, 40, 60]);
    expect(niceTicks(100)).toEqual([0, 50, 100]);
  });

  it("has a legend and an accessible table view", async () => {
    render(<TrendChart data={[{ day: "2026-10-12", on_time: 5, late: 2, absent: 1 }]} />);
    expect(screen.getByLabelText("Keterangan")).toHaveTextContent(/Tepat waktu.*Terlambat.*Tidak hadir/);
    await userEvent.click(screen.getByRole("button", { name: "Tampilkan tabel" }));
    const table = screen.getByRole("table");
    expect(table).toHaveTextContent("5");
    expect(table).toHaveTextContent("2");
  });
});
