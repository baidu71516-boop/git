import { afterEach, describe, expect, it, vi } from "vitest";

import { apiRequest } from "../src/lib/api/client";

function okEnvelope() {
  return new Response(
    JSON.stringify({
      success: true,
      data: { accepted: true },
      error: null,
      request_id: "request-test",
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  document.cookie = "outreach_csrf=; Max-Age=0; path=/";
});

describe("apiRequest", () => {
  it("lets the browser create the multipart boundary for FormData", async () => {
    document.cookie = "outreach_csrf=csrf-test; path=/";
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(okEnvelope());
    const body = new FormData();
    body.append("collection_job_id", "collection-1");
    body.append(
      "file",
      new File(["name\nfixture"], "fixture.csv", { type: "text/csv" }),
    );

    await apiRequest("/import-jobs", { method: "POST", body });

    const init = fetchMock.mock.calls[0]?.[1];
    const headers = new Headers(init?.headers);
    expect(headers.has("Content-Type")).toBe(false);
    expect(headers.get("X-CSRF-Token")).toBe("csrf-test");
    expect(init?.body).toBe(body);
  });

  it("sets JSON content type for JSON mutation bodies", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(okEnvelope());

    await apiRequest("/collection-jobs", {
      method: "POST",
      body: JSON.stringify({ name: "fixture" }),
    });

    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("turns a non-JSON gateway 413 into a stable client error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("Request Entity Too Large", { status: 413 }),
    );

    const request = apiRequest("/import-jobs", {
      method: "POST",
      body: new FormData(),
    });

    await expect(request).rejects.toMatchObject({
      status: 413,
      code: "FILE_TOO_LARGE",
    });
  });
});
