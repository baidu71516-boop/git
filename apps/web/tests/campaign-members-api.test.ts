import { afterEach, describe, expect, it, vi } from "vitest";

import {
  bulkAddCampaignMembers,
  campaignMemberBulkAddPath,
  campaignMemberListPath,
  campaignMemberRemovePath,
  fetchCampaignMemberPage,
  removeCampaignMember,
} from "../src/features/campaigns/api";
import type { CampaignMember } from "../src/features/campaigns/types";

function response(data: unknown) {
  return new Response(
    JSON.stringify({
      success: true,
      data,
      error: null,
      request_id: "campaign-member-test",
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

const member: CampaignMember = {
  id: "member-1",
  campaign_id: "campaign-1",
  influencer_id: "influencer-1",
  preferred_platform_account_id: "account-1",
  version: 4,
  created_at: "2026-08-20T01:00:00Z",
  influencer: { id: "influencer-1", display_name: "小林", status: "active" },
  preferred_platform_account: {
    id: "account-1",
    platform: "douyin",
    platform_account_id: "dy-1",
    account_name: "小林抖音",
    account_handle: "xiaolin",
    is_active: true,
  },
};

afterEach(() => vi.restoreAllMocks());

describe("Campaign member API", () => {
  it("uses the frozen active-member list path, limit, and opaque cursor", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(response({ items: [member], next_cursor: null }));

    await fetchCampaignMemberPage("campaign-1", "opaque.member.cursor");

    expect(campaignMemberListPath("campaign-1", "opaque.member.cursor")).toBe(
      "/campaigns/campaign-1/members?limit=50&cursor=opaque.member.cursor",
    );
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/campaigns/campaign-1/members?limit=50&cursor=opaque.member.cursor",
    );
  });

  it("direct bulk-add sends only influencer/account pairs and its supplied key", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      response({
        campaign_id: "campaign-1",
        requested_count: 1,
        added_count: 1,
        restored_count: 0,
        already_active_count: 0,
      }),
    );

    await bulkAddCampaignMembers(
      "campaign-1",
      [
        {
          influencer_id: "influencer-1",
          preferred_platform_account_id: "account-1",
        },
      ],
      "attempt-key",
    );

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/api/v1/campaigns/campaign-1/members/bulk-add");
    expect(campaignMemberBulkAddPath("campaign-1")).toBe(
      "/campaigns/campaign-1/members/bulk-add",
    );
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(
      "attempt-key",
    );
    expect(init?.body).toBe(
      JSON.stringify({
        members: [
          {
            influencer_id: "influencer-1",
            preferred_platform_account_id: "account-1",
          },
        ],
      }),
    );
  });

  it("removes with the member's exact expected_version", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(response(member));

    await removeCampaignMember("campaign-1", member);

    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe("/api/v1/campaigns/campaign-1/members/member-1/remove");
    expect(campaignMemberRemovePath("campaign-1", "member-1")).toBe(
      "/campaigns/campaign-1/members/member-1/remove",
    );
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ expected_version: 4 }));
  });
});
