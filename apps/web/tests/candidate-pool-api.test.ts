import { afterEach, describe, expect, it, vi } from "vitest";

import {
  addCandidatesToCampaign,
  candidatePoolPath,
  candidateRunPath,
  createCandidateRun,
  fetchCandidateMembers,
  fetchCandidatePoolPage,
} from "../src/features/candidate-pools/api";

if (typeof document === "undefined") {
  Object.defineProperty(globalThis, "document", {
    configurable: true,
    value: { cookie: "" },
  });
}

function response(data: unknown) {
  return new Response(
    JSON.stringify({
      success: true,
      data,
      error: null,
      request_id: "candidate-test",
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  document.cookie = "outreach_csrf=; Max-Age=0; path=/";
});

describe("Candidate Pool API", () => {
  it("keeps all cursors opaque and uses the frozen page size", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response({ items: [], next_cursor: null }))
      .mockResolvedValueOnce(response({ items: [], next_cursor: null }));
    await fetchCandidatePoolPage("opaque-cursor");
    await fetchCandidateMembers("pool/a", "run/b", "opaque-member", "UNKNOWN");
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/candidate-pools?limit=50&cursor=opaque-cursor",
    );
    expect(String(fetchMock.mock.calls[1]?.[0])).toBe(
      "/api/v1/candidate-pools/pool%2Fa/runs/run%2Fb/members?limit=50&cursor=opaque-member&result=UNKNOWN",
    );
  });

  it("posts the exact empty run body with CSRF and the supplied attempt key", async () => {
    document.cookie = "outreach_csrf=candidate-csrf; path=/";
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(response({ id: "run-1" }));
    await createCandidateRun("pool-1", "same-attempt-key");
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/api/v1/candidate-pools/pool-1/runs");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe("{}");
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(
      "same-attempt-key",
    );
    expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe(
      "candidate-csrf",
    );
  });

  it("submits only run_id and member_ids to the existing Campaign endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      response({
        campaign_id: "campaign-1",
        added_count: 1,
        restored_count: 0,
        already_active_count: 0,
      }),
    );
    await addCandidatesToCampaign(
      "campaign-1",
      "run-1",
      ["member-1"],
      "attempt-key",
    );
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/api/v1/campaigns/campaign-1/members/from-candidate-run");
    expect(JSON.parse(String(init?.body))).toEqual({
      run_id: "run-1",
      member_ids: ["member-1"],
    });
  });

  it("encodes route identifiers without exposing any additional endpoint", () => {
    expect(candidatePoolPath("pool/a")).toBe("/candidate-pools/pool%2Fa");
    expect(candidateRunPath("pool/a", "run/b")).toBe(
      "/candidate-pools/pool%2Fa/runs/run%2Fb",
    );
  });
});
