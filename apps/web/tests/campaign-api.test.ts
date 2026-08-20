import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildCampaignUpdateInput,
  campaignDetailPath,
  campaignListPath,
  createCampaign,
  fetchCampaignOperators,
  fetchCampaignPage,
  updateCampaign,
} from "../src/features/campaigns/api";
import type { Campaign } from "../src/features/campaigns/types";

function response(data: unknown) {
  return new Response(
    JSON.stringify({
      success: true,
      data,
      error: null,
      request_id: "campaign-test",
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

const campaign: Campaign = {
  id: "campaign-1",
  department_id: "department-1",
  owner_operator_id: "operator-1",
  owner: { id: "operator-1", name: "王小明", status: "active" },
  created_by_operator_id: "operator-1",
  name: "秋季新品拓展",
  status: "DRAFT",
  review_mode: "FIRST_N",
  review_count: 50,
  duplicate_history_policy: "ALLOW_WITH_WARNING",
  duplicate_window_days: null,
  version: 3,
  created_at: "2026-08-20T01:00:00Z",
  updated_at: "2026-08-20T02:00:00Z",
};

afterEach(() => vi.restoreAllMocks());

describe("Campaign API", () => {
  it("uses limit=50 and forwards cursor as an opaque token", async () => {
    const cursor = "v1.opaque.cursor";
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(response({ items: [campaign], next_cursor: null }));

    await fetchCampaignPage(cursor);

    expect(campaignListPath(cursor)).toBe(
      "/campaigns?limit=50&cursor=v1.opaque.cursor",
    );
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/campaigns?limit=50&cursor=v1.opaque.cursor",
    );
  });

  it("uses one supplied canonical Department scope for list and owner options", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(response({ items: [], next_cursor: null }))
      .mockResolvedValueOnce(response([]));
    const scope = { departmentId: "department-cross" };

    await fetchCampaignPage(undefined, scope);
    await fetchCampaignOperators(scope);

    for (const [, init] of fetchMock.mock.calls) {
      expect(new Headers(init?.headers).get("X-Department-ID")).toBe(
        "department-cross",
      );
    }
  });

  it("creates with only user fields and an Idempotency-Key", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(response(campaign));

    await createCampaign({ name: "秋季新品拓展" }, "attempt-key");

    const [, init] = fetchMock.mock.calls[0] ?? [];
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(
      "attempt-key",
    );
    expect(init?.body).toBe(JSON.stringify({ name: "秋季新品拓展" }));
  });

  it("constructs lossless full PUT input from a fresh detail baseline", async () => {
    const input = buildCampaignUpdateInput(campaign, {
      name: "更新后的活动",
      owner_operator_id: "operator-2",
    });
    expect(input).toEqual({
      name: "更新后的活动",
      owner_operator_id: "operator-2",
      review_mode: "FIRST_N",
      review_count: 50,
      duplicate_history_policy: "ALLOW_WITH_WARNING",
      duplicate_window_days: null,
      expected_version: 3,
    });

    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(response(campaign));
    await updateCampaign(campaign.id, input);
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/api/v1/campaigns/campaign-1");
    expect(init?.method).toBe("PUT");
    expect(init?.body).toBe(JSON.stringify(input));
    expect(campaignDetailPath(campaign.id)).toBe("/campaigns/campaign-1");
  });
});
