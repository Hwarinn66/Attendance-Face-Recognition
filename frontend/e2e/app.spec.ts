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
