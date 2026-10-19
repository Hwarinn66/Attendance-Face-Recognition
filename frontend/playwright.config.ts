import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig, devices } from "@playwright/test";

// E2E runs the real backend (APP_ENV=test, labelled fake vision provider) against a dedicated *_e2e
// database, and the Next.js app in dev mode so the /api rewrite target can be set at runtime.
const HERE = dirname(fileURLToPath(import.meta.url));
const BACKEND = resolve(HERE, "../backend");
const API_PORT = 8100;
const WEB_PORT = 3100;
const python = existsSync(join(BACKEND, ".venv/Scripts/python.exe"))
  ? join(BACKEND, ".venv/Scripts/python.exe")
  : join(BACKEND, ".venv/bin/python");

function e2eDatabaseUrl(): string {
  if (process.env.E2E_DATABASE_URL) return process.env.E2E_DATABASE_URL;
  const pgEnv = resolve(HERE, "../.local/pg.env");
  if (existsSync(pgEnv)) {
    const line = readFileSync(pgEnv, "utf8").split(/\r?\n/).find((l) => l.startsWith("E2E_DATABASE_URL="));
    if (line) return line.slice("E2E_DATABASE_URL=".length);
  }
  throw new Error("Set E2E_DATABASE_URL (a database whose name ends in _e2e)");
}

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    // The web app is the administration console; attendance and face enrollment are mobile-only (Flutter tests).
    { name: "app", testMatch: /app\.spec\.ts/, use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: [
    {
      command: `"${python}" -m alembic upgrade head && "${python}" scripts/seed_e2e.py && "${python}" -m uvicorn --factory app.main:app_factory --host 127.0.0.1 --port ${API_PORT}`,
      cwd: BACKEND,
      url: `http://127.0.0.1:${API_PORT}/api/v1/health/live`,
      timeout: 180_000,
      reuseExistingServer: false,
      env: {
        APP_ENV: "test",
        VISION_PROVIDER: "fake",
        PAD_MODE: "required",
        DATABASE_URL: e2eDatabaseUrl(),
        BIOMETRIC_ENCRYPTION_KEYS: `ke2e:${Buffer.from(crypto.getRandomValues(new Uint8Array(32))).toString("base64")}`,
        COOKIE_SECURE: "false",
        ALLOWED_ORIGINS: `http://127.0.0.1:${WEB_PORT}`,
        LOG_LEVEL: "WARNING",
      },
    },
    {
      command: `npx next dev --hostname 127.0.0.1 --port ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}/login`,
      timeout: 180_000,
      reuseExistingServer: false,
      env: { API_ORIGIN: `http://127.0.0.1:${API_PORT}`, NEXT_TELEMETRY_DISABLED: "1" },
    },
  ],
});
