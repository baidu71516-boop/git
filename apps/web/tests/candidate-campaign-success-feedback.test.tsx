import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CandidateCampaignSuccessFeedback } from "../src/features/candidate-pools/candidate-run-detail-view";

describe("Candidate to Campaign success feedback", () => {
  it("keeps real counts and links to the selected Campaign members tab", () => {
    render(
      <CandidateCampaignSuccessFeedback
        campaignId="campaign-selected"
        result={{
          campaign_id: "campaign-selected",
          added_count: 2,
          restored_count: 1,
          already_active_count: 3,
        }}
      />,
    );

    expect(screen.getByText(/新增 2/)).toBeInTheDocument();
    expect(screen.getByText(/重新加入 1/)).toBeInTheDocument();
    expect(screen.getByText(/已在活动中 3/)).toBeInTheDocument();
    expect(screen.getByText(/已在活动中的达人未做修改/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看拓客活动" })).toHaveAttribute(
      "href",
      "/campaigns/campaign-selected?tab=members",
    );
  });
});
