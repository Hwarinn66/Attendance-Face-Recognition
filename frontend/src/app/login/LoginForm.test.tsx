import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { makeQueryClient } from "@/app/providers";

import { LoginForm, safeNext } from "./LoginForm";

const replace = vi.fn();
let search = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => search,
}));

function renderForm() {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <LoginForm />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  replace.mockReset();
  search = new URLSearchParams();
});
afterEach(() => vi.unstubAllGlobals());

describe("safeNext", () => {
  it.each([
    [null, "/hr"],
    ["/history", "/history"],
    ["//evil.example", "/hr"],
    ["https://evil.example", "/hr"],
    ["/\\evil.example", "/hr"],
  ])("%s -> %s", (input, expected) => {
    expect(safeNext(input)).toBe(expected);
  });
});

describe("LoginForm", () => {
  it("validates before calling the server", async () => {
    const fetchFn = vi.fn();
    vi.stubGlobal("fetch", fetchFn);
    renderForm();
    await userEvent.click(screen.getByRole("button", { name: "Masuk" }));
    expect(await screen.findByText("Masukkan ID atau email")).toBeInTheDocument();
    expect(screen.getByText("Masukkan password")).toBeInTheDocument();
    expect(screen.getByLabelText("ID atau email")).toHaveAttribute("aria-invalid", "true");
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("sends a user ID as the login identifier", async () => {
    const user = { id: "1", email: "a@example.com", must_change_password: false, permissions: [] };
    const fetchFn = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ user, csrf_token: "t" }), { headers: { "content-type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchFn);
    renderForm();
    await userEvent.type(screen.getByLabelText("ID atau email"), "Admin123");
    await userEvent.type(screen.getByLabelText("Password"), "Admin");
    await userEvent.click(screen.getByRole("button", { name: "Masuk" }));
    await waitFor(() => expect(fetchFn).toHaveBeenCalled());
    const init = fetchFn.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({ login: "Admin123", password: "Admin" });
  });

  it("shows the server's generic error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: { code: "invalid_credentials", message: "Invalid sign-in ID or password" } }), {
          status: 401,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    renderForm();
    await userEvent.type(screen.getByLabelText("ID atau email"), "a@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Masuk" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("ID/email atau password salah.");
    expect(replace).not.toHaveBeenCalled();
  });

  it("redirects to a safe next path, or to the profile when a password change is required", async () => {
    search = new URLSearchParams("next=/history");
    const user = { id: "1", email: "a@example.com", must_change_password: false, permissions: [] };
    const fetchFn = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ user, csrf_token: "t" }), { headers: { "content-type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchFn);
    const { unmount } = renderForm();
    await userEvent.type(screen.getByLabelText("ID atau email"), "a@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "secret");
    await userEvent.click(screen.getByRole("button", { name: "Masuk" }));
    await vi.waitFor(() => expect(replace).toHaveBeenCalledWith("/history"));
    unmount();

    fetchFn.mockResolvedValue(
      new Response(JSON.stringify({ user: { ...user, must_change_password: true }, csrf_token: "t" }), {
        headers: { "content-type": "application/json" },
      }),
    );
    renderForm();
    await userEvent.type(screen.getByLabelText("ID atau email"), "a@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "secret");
    await userEvent.click(screen.getByRole("button", { name: "Masuk" }));
    await vi.waitFor(() => expect(replace).toHaveBeenLastCalledWith("/account"));
  });
});
