import { afterEach, describe, expect, it, vi } from "vitest";

import {
  addCandidatesToCampaign,
  candidateMemberPagePath,
  candidatePoolPath,
  candidateRunPath,
  CANDIDATE_MEMBER_PAGE_SIZES,
  createCandidatePool,
  createCandidateRun,
  fetchCandidateMembers,
  fetchCandidatePoolPage,
} from "../src/features/candidate-pools/api";
import { candidatePoolQueryKeys } from "../src/features/candidate-pools/queries";

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
  it("keeps all cursors opaque and uses the requested bounded member page size", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response({ items: [], next_cursor: null }))
      .mockResolvedValueOnce(response({ items: [], next_cursor: null }));
    await fetchCandidatePoolPage("opaque-cursor");
    await fetchCandidateMembers(
      "pool/a",
      "run/b",
      "opaque-member",
      "UNKNOWN",
      200,
    );
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/candidate-pools?limit=50&cursor=opaque-cursor",
    );
    expect(String(fetchMock.mock.calls[1]?.[0])).toBe(
      "/api/v1/candidate-pools/pool%2Fa/runs/run%2Fb/members?limit=200&cursor=opaque-member&result=UNKNOWN",
    );
  });

  it("posts the exact empty run body with CSRF and the supplied attempt key", async () => {
    document.cookie = "outreach_csrf=candidate-csrf; path=/";
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response({ id: "run-1" }))
      .mockResolvedValueOnce(response({ id: "run-2" }));
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

  it("posts only the two explicit historical and adjusted rerun shapes", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response({ id: "run-1" }))
      .mockResolvedValueOnce(response({ id: "run-2" }));
    await createCandidateRun("pool-1", "historical-key", {
      policy_id: "policy-v1",
    });
    await createCandidateRun("pool-1", "adjusted-key", {
      base_policy_id: "policy-v1",
      expected_pool_version: 3,
      policy: {
        schema_version: 1,
        policy_type: "SELLER_V1",
        notes_7d: { minimum: 1 },
      },
    });
    expect(fetchMock.mock.calls[0]?.[1]?.body).toBe(
      '{"policy_id":"policy-v1"}',
    );
    expect(fetchMock.mock.calls[1]?.[1]?.body).toBe(
      '{"base_policy_id":"policy-v1","expected_pool_version":3,"policy":{"schema_version":1,"policy_type":"SELLER_V1","notes_7d":{"minimum":1}}}',
    );
  });

  it("creates only a Seller Pool with its inline SELLER_V1 rule", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(response({ id: "pool-1" }));
    await createCandidatePool(
      {
        name: "Seller pool",
        kind: "POTENTIAL_SELLER",
        policy: {
          schema_version: 1,
          policy_type: "SELLER_V1",
          contact_availability: "has_email",
        },
      },
      "pool-create-key",
    );
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/api/v1/candidate-pools");
    expect(init?.body).toBe(
      '{"name":"Seller pool","kind":"POTENTIAL_SELLER","policy":{"schema_version":1,"policy_type":"SELLER_V1","contact_availability":"has_email"}}',
    );
  });

  it("keeps the explicit Campaign request body byte-for-byte compatible", async () => {
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
    expect(init?.body).toBe('{"run_id":"run-1","member_ids":["member-1"]}');
  });

  it("submits server-resolved ALL_MATCH with only explicit exclusions", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      response({
        campaign_id: "campaign-1",
        added_count: 0,
        restored_count: 0,
        already_active_count: 0,
      }),
    );
    await addCandidatesToCampaign(
      "campaign-1",
      {
        run_id: "run-1",
        selection_mode: "ALL_MATCH",
        excluded_member_ids: ["member-2"],
      },
      "attempt-key",
    );
    const [, init] = fetchMock.mock.calls[0] ?? [];
    expect(JSON.parse(String(init?.body))).toEqual({
      run_id: "run-1",
      selection_mode: "ALL_MATCH",
      excluded_member_ids: ["member-2"],
    });
  });

  it("encodes route identifiers without exposing any additional endpoint", () => {
    expect(candidatePoolPath("pool/a")).toBe("/candidate-pools/pool%2Fa");
    expect(candidateRunPath("pool/a", "run/b")).toBe(
      "/candidate-pools/pool%2Fa/runs/run%2Fb",
    );
    expect(candidateMemberPagePath("pool/a", "run/b", null, "MATCH", 20)).toBe(
      "/candidate-pools/pool%2Fa/runs/run%2Fb/members?limit=20&result=MATCH",
    );
  });

  it("keeps member pages isolated by one of the four supported page sizes", () => {
    expect(CANDIDATE_MEMBER_PAGE_SIZES).toEqual([20, 50, 100, 200]);
    expect(
      candidatePoolQueryKeys.members("pool-1", "run-1", "MATCH", 20),
    ).toEqual(["candidate-pools", "members", "pool-1", "run-1", "MATCH", 20]);
    expect(
      candidatePoolQueryKeys.members("pool-1", "run-1", "MATCH", 200),
    ).not.toEqual(
      candidatePoolQueryKeys.members("pool-1", "run-1", "MATCH", 20),
    );
  });
});
