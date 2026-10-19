import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { type Page, expect } from "@playwright/test";

interface Seed {
  password: string;
  users: Record<"admin" | "employee" | "leaver" | "spoofer", string>;
}

export function seed(): Seed {
  return JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), ".fixtures", "seed.json"), "utf8")) as Seed;
}

export async function login(page: Page, who: keyof Seed["users"]): Promise<void> {
  const s = seed();
  await page.goto("/login");
  await page.getByLabel("ID atau email").fill(s.users[who]);
  await page.getByLabel("Password").fill(s.password);
  await page.getByRole("button", { name: "Masuk" }).click();
  await expect(page).toHaveURL(/\/hr$/);
}
