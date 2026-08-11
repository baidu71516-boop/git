import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildInfluencerListPath,
  fetchInfluencerDetail,
  fetchInfluencerList,
  fetchMetricSnapshots,
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

describe("influencer detail API", () => {
  it("requests detail and independently paginated snapshots with the Session cookie", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            success: true,
            data: { id: "influencer-1" },
            error: null,
            request_id: "request-detail",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            success: true,
            data: { items: [], page: 2, page_size: 50, total: 0 },
            error: null,
            request_id: "request-snapshots",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );

    await fetchInfluencerDetail("influencer-1");
    await fetchMetricSnapshots("influencer-1", 2, 50);

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/influencers/influencer-1",
    );
    expect(String(fetchMock.mock.calls[1]?.[0])).toBe(
      "/api/v1/influencers/influencer-1/metric-snapshots?page=2&page_size=50",
    );
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      credentials: "include",
    });
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({
      credentials: "include",
    });
  });

  it("converts the detail 404 envelope without treating it as empty data", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          success: false,
          data: null,
          error: {
            code: "INFLUENCER_NOT_FOUND",
            message: "Influencer not found",
            details: null,
          },
          request_id: "request-not-found",
        }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(fetchInfluencerDetail("missing-id")).rejects.toMatchObject({
      status: 404,
      code: "INFLUENCER_NOT_FOUND",
    });
  });
});
