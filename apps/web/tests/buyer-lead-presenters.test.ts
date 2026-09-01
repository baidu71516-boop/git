import { describe, expect, it } from "vitest";

import {
  buyerCurrentCategories,
  buyerLeadTierOrder,
  buyerLeadTierPresentation,
  buyerOriginalCategories,
  buyerRelationLabel,
  buyerRelationPairs,
} from "../src/features/candidate-pools/buyer-lead-presenters";
import type { CandidateMember } from "../src/features/candidate-pools/types";

function buyerMember(): CandidateMember {
  return {
    id: "buyer-member",
    run_id: "buyer-run",
    influencer_id: "buyer-influencer",
    platform_account_id: "buyer-account",
    result: "NOT_MATCH",
    buyer_lead_tier: "RELATED",
    buyer_relation_summary: {
      client_categories: ["母婴", "玩具"],
      creator_categories: ["亲子", "益智玩具"],
      pairs: [
        {
          client_category_id: "母婴",
          creator_category_id: "亲子",
          relation: "PARENT_CHILD",
        },
        {
          client_category_id: "玩具",
          creator_category_id: "益智玩具",
          relation: "COMPATIBLE",
        },
      ],
    },
    reason_codes: ["CATEGORY_MISMATCH"],
    redacted_evidence: {},
    evidence_hash: "buyer-hash",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    influencer: {
      id: "buyer-influencer",
      display_name: "买家达人",
      status: "active",
    },
    platform_account: {
      id: "buyer-account",
      platform: "douyin",
      platform_account_id: "buyer-account",
      account_name: "买家账号",
      account_handle: "buyer",
      is_active: true,
    },
  };
}

describe("Buyer lead presenters", () => {
  it("uses the frozen five-tier sales priority and wording", () => {
    expect(buyerLeadTierOrder).toEqual([
      "HIGH",
      "CHANGED",
      "RELATED",
      "SAME_CATEGORY",
      "UNKNOWN",
    ]);
    expect(buyerLeadTierPresentation("HIGH")).toMatchObject({
      label: "强潜客",
      reason: "类目方向存在明确差异，建议优先联系",
    });
    expect(buyerLeadTierPresentation("CHANGED")).toMatchObject({
      label: "变化潜客",
      reason: "类目方向出现变化迹象，建议进一步沟通",
    });
    expect(buyerLeadTierPresentation("RELATED")).toMatchObject({
      label: "相关潜客",
      reason: "当前方向与原方向相关，可能存在同赛道扩张或转型需求",
    });
    expect(buyerLeadTierPresentation("SAME_CATEGORY")).toMatchObject({
      label: "同类潜客",
      reason: "当前方向与原方向一致，仍可能存在同赛道买号、矩阵扩张或换号需求",
    });
    expect(buyerLeadTierPresentation("UNKNOWN")).toMatchObject({
      label: "待判断",
      reason: "当前资料不足，暂无法可靠判断",
    });
  });

  it("keeps all category pairs and renders relation labels for market staff", () => {
    const member = buyerMember();
    expect(buyerOriginalCategories(member)).toEqual(["母婴", "玩具"]);
    expect(buyerCurrentCategories(member)).toEqual(["亲子", "益智玩具"]);
    expect(buyerRelationPairs(member)).toHaveLength(2);
    expect(buyerRelationLabel("EXACT")).toBe("同类目");
    expect(buyerRelationLabel("PARENT_CHILD")).toBe("上下级/细分类目关系");
    expect(buyerRelationLabel("COMPATIBLE")).toBe("相关类目");
    expect(buyerRelationLabel("INCOMPATIBLE")).toBe("明确跨类目");
    expect(buyerRelationLabel("NO_RULE")).toBe("暂无明确关系规则");
  });
});
