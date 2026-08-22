import { describe, expect, it } from "vitest";

import {
  campaignStatusPresentation,
  formatCampaignDateTime,
  isDisabledCampaignOwner,
} from "../src/features/campaigns/formatters";

describe("Campaign presenters", () => {
  it.each([
    ["DRAFT", "草稿"],
    ["ACTIVE", "进行中"],
    ["PAUSED", "已暂停"],
    ["CLOSED", "已关闭"],
    ["FUTURE", "未知状态"],
  ])("presents %s safely", (status, label) => {
    expect(campaignStatusPresentation(status).label).toBe(label);
  });

  it("uses Shanghai time and owner status rather than owner IDs", () => {
    expect(formatCampaignDateTime("2026-08-20T01:30:00Z")).toBe(
      "2026-08-20 09:30",
    );
    expect(
      isDisabledCampaignOwner({
        id: "opaque-id",
        name: "王小明",
        status: "disabled",
      }),
    ).toBe(true);
  });
});
