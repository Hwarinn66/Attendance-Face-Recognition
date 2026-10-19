import { expect, test } from "@playwright/test";

import { login, seed } from "./helpers";

test("unauthenticated visitors are sent to sign-in", async ({ page }) => {
  await page.goto("/hr");
  await expect(page).toHaveURL(/\/login\?next=%2Fhr/);
});

test("wrong credentials show a generic error", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("ID atau email").fill(seed().users.employee);
  await page.getByLabel("Password").fill("definitely-wrong");
  await page.getByRole("button", { name: "Masuk" }).click();
  // Next.js renders its own (empty) role="alert" route announcer, so match the error by text.
  await expect(page.getByRole("alert").filter({ hasText: "ID/email atau password salah." })).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
});

test("employees cannot use the web console: they are sent to the mobile app", async ({ page }) => {
  const s = seed();
  await page.goto("/login");
  await page.getByLabel("ID atau email").fill(s.users.employee);
  await page.getByLabel("Password").fill(s.password);
  await page.getByRole("button", { name: "Masuk" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "hanya bisa dipakai di aplikasi HP" })).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
  expect((await page.request.get("/api/v1/auth/me")).status()).toBe(401);
});

test("administrators get one compact menu and cannot attend from the web", async ({ page }) => {
  await login(page, "admin");
  const nav = page.getByRole("navigation", { name: "Menu utama" });
  for (const label of ["Ringkasan", "Absensi", "Pengguna", "Jadwal", "Pengaturan"]) {
    await expect(nav.getByRole("link", { name: label })).toBeVisible();
  }
  await expect(nav.getByRole("link", { name: /Check in|Face enrollment|My attendance/ })).toHaveCount(0);
  const csrf = (await page.context().cookies()).find((c) => c.name === "absen_csrf")?.value ?? "";
  const res = await page.request.post("/api/v1/attendance/challenges", {
    data: { purpose: "check_in" },
    headers: { "X-CSRF-Token": csrf, Origin: new URL(page.url()).origin },
  });
  expect(res.status()).toBe(403);
  expect((await res.json()).error.code).toBe("use_mobile_app");
});

test("attendance page holds reports and CSV export", async ({ page }) => {
  await login(page, "admin");
  await page.goto("/hr/attendance");
  await page.getByRole("tab", { name: "Laporan" }).click();
  await expect(page.getByRole("table", { name: "Ringkasan absensi" })).toContainText("Erin Employee");
  const csv = await page.request.get("/api/v1/reports/summary.csv");
  expect(csv.status()).toBe(200);
  expect(csv.headers()["content-type"]).toContain("text/csv");
  expect(await csv.text()).toContain("employee_code,name,department");
});

test("users: the administrator sets a sign-in ID from one dialog", async ({ page }) => {
  await login(page, "admin");
  await page.goto("/admin/users");
  const row = page.getByRole("row").filter({ hasText: "Erin Employee" });
  await row.getByRole("button", { name: "Kelola" }).click();
  await page.getByRole("tab", { name: "Login" }).click();
  await page.getByLabel("ID login").fill("erin01");
  await page.getByRole("button", { name: "Simpan ID" }).click();
  await expect(page.getByText("ID login tersimpan.")).toBeVisible();
  await expect(page.getByText("tidak bisa dilihat, hanya bisa diganti", { exact: false })).toBeVisible();
});

test("settings include the activity (audit) log", async ({ page }) => {
  await login(page, "admin");
  await page.goto("/admin/settings");
  await page.getByRole("tab", { name: "Log aktivitas" }).click();
  await expect(page.getByRole("table", { name: "Daftar kejadian" })).toContainText("auth.login.success");
});

test("administrators can delete an invalid attendance record with a reason", async ({ page }) => {
  await login(page, "admin");
  await page.goto("/hr/attendance");
  const row = page.getByRole("row").filter({ hasText: "Lee Leaver" });
  await expect(row).toHaveCount(1);
  await row.getByRole("button", { name: "Hapus" }).click();
  await page.getByRole("button", { name: "Hapus absensi" }).click();
  await expect(page.getByText("Tulis alasan penghapusan (minimal 5 karakter).")).toBeVisible();
  await page.getByLabel("Alasan penghapusan").fill("Absen dari luar kantor");
  await page.getByRole("button", { name: "Hapus absensi" }).click();
  await expect(page.getByRole("row").filter({ hasText: "Lee Leaver" })).toHaveCount(0);
  await page.goto("/admin/settings?tab=activity");
  await expect(page.getByRole("table", { name: "Daftar kejadian" })).toContainText("attendance.record.deleted");
});

test("the dashboard updates live while the administrator watches, without a reload", async ({ page, playwright, baseURL }) => {
  const s = seed();
  await login(page, "admin");
  await expect(page.locator("aside [data-live-status]")).toHaveAttribute("data-live-status", "live");
  const stat = (label: string) => page.getByText(label, { exact: true }).locator("xpath=following-sibling::p[1]");
  const pendingBefore = Number(await stat("Koreksi menunggu").textContent());
  const checkedInBefore = Number(await stat("Sudah check-in").textContent());
  await page.evaluate(() => ((window as unknown as { __sameDocument: boolean }).__sameDocument = true));

  // The employee, in the mobile app, asks for a correction of today's check-in.
  const app = await playwright.request.newContext({ baseURL });
  const token = (await (await app.post("/api/v1/auth/token", { data: { login: s.users.employee, password: s.password } })).json()).access_token as string;
  const auth = { Authorization: `Bearer ${token}` };
  const status = await (await app.get("/api/v1/attendance/status", { headers: auth })).json();
  const occ = (status.today as { shift_name: string; shift_id: string; shift_date: string }[]).find((o) => o.shift_name === "Current shift")!;
  const corr = await app.post("/api/v1/attendance/corrections", {
    headers: auth,
    data: { shift_id: occ.shift_id, shift_date: occ.shift_date, requested_check_in_at: new Date(Date.now() - 60_000).toISOString(), reason: "Kamera HP rusak tadi pagi" },
  });
  expect(corr.status()).toBe(201);
  await expect(page.getByRole("status").filter({ hasText: "Permintaan koreksi baru dari Erin Employee" })).toBeVisible();
  await expect(stat("Koreksi menunggu")).toHaveText(String(pendingBefore + 1));

  // Approved from another tab: the numbers here follow along.
  const csrf = (await page.context().cookies()).find((c) => c.name === "absen_csrf")?.value ?? "";
  const review = await page.request.post(`/api/v1/attendance/corrections/${(await corr.json()).id}/review`, {
    data: { approve: true },
    headers: { "X-CSRF-Token": csrf, Origin: new URL(page.url()).origin },
  });
  expect(review.status()).toBe(200);
  await expect(stat("Sudah check-in")).toHaveText(String(checkedInBefore + 1));
  await expect(stat("Koreksi menunggu")).toHaveText(String(pendingBefore));
  expect(await page.evaluate(() => (window as unknown as { __sameDocument?: boolean }).__sameDocument)).toBe(true);
  await app.dispose();
});
