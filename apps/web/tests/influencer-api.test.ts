import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildInfluencerListPath,
  fetchInfluencerList,
  influencerListQueryKey,
} from "../src/features/influencers/api";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("influencer list API", () => {
  it("encodes only frozen, non-empty query parameters", () => {
    const query = {
      q: "美妆 达人",
      tag: "国风/动画",
      followers_min: "0",
      followers_max: "10000",
      owner_operator_id: "00000000-0000-0000-0000-000000000001",
      crm_stage: "高意向",
      page: "2",
      page_size: "50",
    };

    const path = buildInfluencerListPath({
      ...query,
      q: `  ${query.q}  `,
      ignored: "must-not-be-sent",
    } as typeof query & { ignored: string });
    const url = new URL(path, "https://example.invalid");

    expect(url.pathname).toBe("/influencers");
    expect(Object.fromEntries(url.searchParams)).toEqual(query);
    expect(path).not.toContain("ignored");
    expect(influencerListQueryKey(query)).toEqual([
      "influencers",
      "list",
      query,
    ]);
  });

  it("omits blank values and converts an API error envelope", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          success: false,
          data: null,
          error: {
            code: "VALIDATION_ERROR",
            message: "Request validation failed",
            details: null,
          },
          request_id: "request-test",
        }),
        { status: 422, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(
      fetchInfluencerList({ q: "   ", tag: "", page: "1", page_size: "50" }),
    ).rejects.toMatchObject({ status: 422, code: "VALIDATION_ERROR" });
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/influencers?page=1&page_size=50",
    );
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      credentials: "include",
    });
  });
});
