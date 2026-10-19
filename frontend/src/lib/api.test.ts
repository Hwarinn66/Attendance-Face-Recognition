import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, readCsrfToken } from "./api";

function mockFetch(response: Response) {
  // A Response body can be read once: hand out a fresh clone per call.
  const fn = vi.fn().mockImplementation(() => Promise.resolve(response.clone()));
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("readCsrfToken", () => {
  it("reads either cookie name", () => {
    expect(readCsrfToken("a=1; absen_csrf=tok%3D1")).toBe("tok=1");
    expect(readCsrfToken("__Host-absen_csrf=secure")).toBe("secure");
    expect(readCsrfToken("other=1")).toBeNull();
  });
});

describe("api", () => {
  it("sends the CSRF header on unsafe methods only", async () => {
    document.cookie = "absen_csrf=abc123; path=/";
    const fetchFn = mockFetch(new Response(JSON.stringify({ ok: true }), { headers: { "content-type": "application/json" } }));
    await api("/thing");
    expect((fetchFn.mock.calls[0]![1] as RequestInit).headers).not.toHaveProperty("X-CSRF-Token");
    await api("/thing", { json: { a: 1 } });
    const init = fetchFn.mock.calls[1]![1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(init.headers).toMatchObject({ "X-CSRF-Token": "abc123", "Content-Type": "application/json" });
  });

  it("builds query strings and skips empty values", async () => {
    const fetchFn = mockFetch(new Response("[]", { headers: { "content-type": "application/json" } }));
    await api("/x", { query: { a: 1, b: "", c: undefined, d: true } });
    expect(fetchFn.mock.calls[0]![0]).toBe("/api/v1/x?a=1&d=true");
  });

  it("parses the error envelope", async () => {
    mockFetch(
      new Response(JSON.stringify({ error: { code: "already_checked_in", message: "Already", request_id: "r1" } }), {
        status: 409,
        headers: { "content-type": "application/json" },
      }),
    );
    const err = await api("/x", { json: {} }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: "already_checked_in", message: "Already", requestId: "r1" });
  });

  it("maps network failures and gateway errors to friendly errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api("/x")).rejects.toMatchObject({ code: "network_error", status: 0 });
    mockFetch(new Response("bad gateway", { status: 502 }));
    await expect(api("/x")).rejects.toMatchObject({ code: "unreachable" });
  });

  it("returns undefined for 204", async () => {
    mockFetch(new Response(null, { status: 204 }));
    await expect(api("/x", { method: "DELETE" })).resolves.toBeUndefined();
  });
});
