import { BarChart3, CalendarClock, ClipboardList, type LucideIcon, Settings, Users } from "lucide-react";

import type { Permission } from "@/lib/types";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  permission: Permission;
}

export interface NavSection {
  title: string;
  items: NavItem[];
}

// The web app is the administration console only. Attendance and face enrollment happen in the mobile app
// (the server enforces this too), so there are no self-service pages here.
export const NAV: NavSection[] = [
  {
    title: "Administrasi",
    items: [
      { href: "/hr", label: "Ringkasan", icon: BarChart3, permission: "reports:read" },
      { href: "/hr/attendance", label: "Absensi", icon: ClipboardList, permission: "attendance:read_all" },
      { href: "/admin/users", label: "Pengguna", icon: Users, permission: "users:read" },
      { href: "/admin/schedules", label: "Jadwal", icon: CalendarClock, permission: "schedules:read" },
      { href: "/admin/settings", label: "Pengaturan", icon: Settings, permission: "settings:manage" },
    ],
  },
];

export function visibleNav(permissions: readonly Permission[]): NavSection[] {
  return NAV.map((s) => ({ ...s, items: s.items.filter((i) => permissions.includes(i.permission)) })).filter(
    (s) => s.items.length > 0,
  );
}
