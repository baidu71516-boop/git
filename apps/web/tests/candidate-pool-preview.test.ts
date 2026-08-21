import { describe, expect, it, vi } from "vitest";

import { notFound } from "next/navigation";

import CandidatePoolVisualPreviewPage from "../src/app/dev-ui-preview/candidate-pools/page";
import {
  PREVIEW_CAMPAIGNS,
  PREVIEW_MEMBERS,
  PREVIEW_POLICIES,
  PREVIEW_POOL_IDS,
  PREVIEW_POOLS,
} from "../src/features/candidate-pools/preview-fixtures";

vi.mock("next/navigation", () => ({
  notFound: vi.fn(),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

describe("Candidate Pool visual preview", () => {
  it("keeps preview fixtures within the approved platform and typed policy contracts", () => {
    expect(
      PREVIEW_POOLS.every((pool) =>
        ["ACTIVE", "ARCHIVED"].includes(pool.status),
      ),
    ).toBe(true);
    expect(
      PREVIEW_MEMBERS.every(
        (member) => member.platform_account.platform === "xiaohongshu",
      ),
    ).toBe(true);
    expect(
      PREVIEW_POLICIES[PREVIEW_POOL_IDS.seller]?.every(
        (item) => item.definition.policy_type === "SELLER_V1",
      ),
    ).toBe(true);
    expect(
      PREVIEW_POLICIES[PREVIEW_POOL_IDS.buyer]?.every(
        (item) => item.definition.policy_type === "BUYER_V1",
      ),
    ).toBe(true);
    expect(PREVIEW_CAMPAIGNS.map((campaign) => campaign.status)).toEqual([
      "DRAFT",
      "ACTIVE",
      "PAUSED",
      "CLOSED",
    ]);
    expect(
      PREVIEW_MEMBERS.find(
        (member) => member.id === "member-buyer-category-mismatch",
      )?.result,
    ).toBe("MATCH");
  });

  it("is development-only and returns notFound in production", async () => {
    vi.stubEnv("NODE_ENV", "production");
    await CandidatePoolVisualPreviewPage();
    expect(notFound).toHaveBeenCalledTimes(1);

    vi.stubEnv("NODE_ENV", "development");
    const page = await CandidatePoolVisualPreviewPage();
    expect(page).toBeTruthy();
    vi.unstubAllEnvs();
  });
});
