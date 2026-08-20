import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CampaignMemberTable } from "../src/features/campaigns/components/campaign-member-table";
import type { CampaignMember } from "../src/features/campaigns/types";

const baseMember: CampaignMember = {
  id: "member-1",
  campaign_id: "campaign-1",
  influencer_id: "influencer-1",
  preferred_platform_account_id: "account-1",
  version: 1,
  created_at: "2026-08-20T01:00:00Z",
  influencer: { id: "influencer-1", display_name: "小林", status: "active" },
  preferred_platform_account: {
    id: "account-1",
    platform: "douyin",
    platform_account_id: "dy-1",
    account_name: "小林抖音",
    account_handle: "xiaolin",
    is_active: false,
  },
};

describe("Campaign member table", () => {
  it("uses the four frozen columns for writers and preserves account history", () => {
    render(<CampaignMemberTable items={[baseMember]} canRemove />);
    expect(screen.getByRole("link", { name: "小林" })).toHaveAttribute(
      "href",
      "/influencers/influencer-1",
    );
    expect(screen.getByText("抖音 · 小林抖音")).toBeInTheDocument();
    expect(screen.getByText("@xiaolin")).toBeInTheDocument();
    expect(screen.getByText("账号已停用")).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "操作" }),
    ).toBeInTheDocument();
  });

  it("keeps Viewer read-only and renders disabled influencers as plain text", () => {
    render(
      <CampaignMemberTable
        items={[
          {
            ...baseMember,
            influencer: {
              id: "influencer-1",
              display_name: "小林",
              status: "disabled",
            },
          },
        ]}
      />,
    );
    expect(
      screen.queryByRole("columnheader", { name: "操作" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "小林" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("已停用")).toBeInTheDocument();
  });
});
