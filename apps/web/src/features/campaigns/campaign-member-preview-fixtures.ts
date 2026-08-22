import type {
  InfluencerListItem,
  PlatformAccountSummary,
} from "@/features/influencers/types";

import type { Campaign, CampaignMember, CampaignRole } from "./types";
import type {
  CampaignDetailPreview,
  CampaignMembersPreview,
} from "./preview-types";

export type Ui2BSceneKey =
  | "member-list"
  | "member-add"
  | "member-selected"
  | "member-multi-account"
  | "member-disabled"
  | "member-remove"
  | "member-conflict"
  | "member-closed"
  | "member-empty"
  | "member-error";

export const UI2B_SCENE_OPTIONS: Array<{
  value: Ui2BSceneKey;
  label: string;
}> = [
  { value: "member-list", label: "拓客活动 · 活动达人" },
  { value: "member-add", label: "拓客活动 · 添加达人" },
  { value: "member-selected", label: "拓客活动 · 查看已选" },
  { value: "member-multi-account", label: "拓客活动 · 多账号选择" },
  { value: "member-disabled", label: "拓客活动 · 停用达人和账号" },
  { value: "member-remove", label: "拓客活动 · 移出达人" },
  { value: "member-conflict", label: "拓客活动 · 成员状态冲突" },
  { value: "member-closed", label: "拓客活动 · 活动已关闭" },
  { value: "member-empty", label: "拓客活动 · 暂无活动达人" },
  { value: "member-error", label: "拓客活动 · 活动达人加载失败" },
];

const CREATED_AT = "2026-08-20T01:15:00+08:00";

function account(
  id: string,
  platform: string,
  name: string,
  handle: string | null,
  isActive = true,
): PlatformAccountSummary {
  return {
    id,
    platform,
    platform_account_id: `${id}-external`,
    account_name: name,
    account_handle: handle,
    profile_url: null,
    source: "preview",
    is_active: isActive,
    source_tags: [],
    last_huitun_observed_at: null,
    last_huitun_imported_at: null,
    freshness_status: "fresh",
    freshness_age_days: 0,
    requires_refresh: false,
  };
}

function candidate(
  id: string,
  displayName: string,
  accounts: PlatformAccountSummary[],
): InfluencerListItem {
  return {
    id,
    display_name: displayName,
    status: "active",
    crm_stage: "QUALIFIED",
    owner: null,
    platform_accounts: accounts,
    current_metrics: [],
    current_contacts: [],
    possible_duplicate_contact: false,
    freshness_status: "fresh",
    requires_refresh: false,
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
  };
}

function member(
  id: string,
  campaignId: string,
  influencerId: string,
  displayName: string,
  influencerStatus: "active" | "disabled",
  preferredAccount: PlatformAccountSummary,
  createdAt = CREATED_AT,
): CampaignMember {
  return {
    id,
    campaign_id: campaignId,
    influencer_id: influencerId,
    preferred_platform_account_id: preferredAccount.id,
    version: 1,
    created_at: createdAt,
    influencer: {
      id: influencerId,
      display_name: displayName,
      status: influencerStatus,
    },
    preferred_platform_account: {
      id: preferredAccount.id,
      platform: preferredAccount.platform,
      platform_account_id: preferredAccount.platform_account_id,
      account_name: preferredAccount.account_name,
      account_handle: preferredAccount.account_handle,
      is_active: preferredAccount.is_active,
    },
  };
}

const techAccount = account(
  "account-tech-xhs",
  "xiaohongshu",
  "科技小王",
  "techwang",
);
const liAccount = account(
  "account-li-xhs",
  "xiaohongshu",
  "数码老李",
  "shumaolaoli",
);
const liDouyinAccount = account(
  "account-li-dy",
  "douyin",
  "数码老李",
  "shumaolaoli_dy",
);
const noHandleAccount = account(
  "account-no-handle-dy",
  "douyin",
  "生活研究员",
  null,
);
const disabledAccount = account(
  "account-disabled-xhs",
  "xiaohongshu",
  "科技小王",
  "techwang-old",
  false,
);

const techCandidate = candidate("candidate-tech", "科技小王", [techAccount]);
const singleLiCandidate = candidate("influencer-li", "数码老李", [liAccount]);
const multiLiCandidate = candidate("influencer-li", "数码老李", [
  liAccount,
  liDouyinAccount,
]);
const unavailableCandidate = candidate("candidate-none", "暂无账号达人", []);

function candidatesForScene(scene: Ui2BSceneKey): InfluencerListItem[] {
  return [
    scene === "member-multi-account" ? multiLiCandidate : singleLiCandidate,
    techCandidate,
    unavailableCandidate,
  ];
}

function activeMembers(campaignId: string): CampaignMember[] {
  return [
    member(
      "member-tech",
      campaignId,
      "influencer-tech",
      "科技小王",
      "active",
      techAccount,
    ),
    member(
      "member-li",
      campaignId,
      "influencer-li",
      "数码老李",
      "active",
      liAccount,
    ),
    member(
      "member-life",
      campaignId,
      "influencer-life",
      "生活研究员",
      "active",
      noHandleAccount,
      "2026-08-19T16:40:00+08:00",
    ),
  ];
}

function disabledMembers(campaignId: string): CampaignMember[] {
  return [
    member(
      "member-disabled",
      campaignId,
      "influencer-disabled",
      "科技小王",
      "disabled",
      disabledAccount,
    ),
    ...activeMembers(campaignId).slice(1),
  ];
}

function membersPreview(
  campaign: Campaign,
  scene: Ui2BSceneKey,
): CampaignMembersPreview {
  const base = activeMembers(campaign.id);
  const candidates = candidatesForScene(scene);
  const primaryCandidate = candidates[0]!;
  const addDrawer = {
    candidates,
    initialSelectedIds:
      scene === "member-selected"
        ? [primaryCandidate.id]
        : scene === "member-multi-account"
          ? [primaryCandidate.id]
          : scene === "member-add"
            ? [primaryCandidate.id]
            : [],
    initialSelectedOnly: scene === "member-selected",
  };

  switch (scene) {
    case "member-empty":
      return {
        state: "empty",
        items: [],
        nextCursor: null,
        addDrawer: { ...addDrawer, initialSelectedIds: [] },
      };
    case "member-error":
      return {
        state: "error",
        items: [],
        nextCursor: null,
      };
    case "member-disabled":
      return {
        state: "ready",
        items: disabledMembers(campaign.id),
        nextCursor: null,
      };
    case "member-remove":
      return {
        state: "ready",
        items: base,
        nextCursor: null,
        removeOutcome: "success",
      };
    case "member-conflict":
      return {
        state: "ready",
        items: base,
        nextCursor: null,
        removeOutcome: "conflict-still-present",
      };
    case "member-add":
    case "member-selected":
    case "member-multi-account":
      return {
        state: "ready",
        items: base,
        nextCursor: null,
        addDrawer: {
          ...addDrawer,
          initialSelectedIds: addDrawer.initialSelectedIds,
        },
        initialDrawerOpen: true,
      };
    case "member-list":
    default:
      return {
        state: "ready",
        items: base,
        nextCursor: "preview-next-cursor",
        addDrawer: {
          candidates,
          initialSelectedIds: [],
        },
      };
  }
}

export function buildCampaignMemberPreview(
  campaign: Campaign,
  scene: Ui2BSceneKey,
): {
  detail: CampaignDetailPreview;
  role: CampaignRole;
  hasSelectedOperator: boolean;
} {
  const role: CampaignRole = scene === "member-closed" ? "viewer" : "operator";
  const campaignForScene =
    scene === "member-closed" ? { ...campaign, status: "CLOSED" } : campaign;
  return {
    detail: {
      campaign: campaignForScene,
      activeTab: "members",
      members: membersPreview(campaignForScene, scene),
    },
    role,
    hasSelectedOperator: role !== "viewer",
  };
}
