import type { Campaign } from "@/features/campaigns/types";

import type {
  CandidateMember,
  CandidatePool,
  CandidatePoolPage,
  CandidatePoolRun,
  CandidatePoolRunPage,
  TargetingPolicy,
} from "./types";

const sellerOwner = {
  id: "preview-owner-1",
  name: "周宁",
  status: "active" as const,
};
const disabledOwner = {
  id: "preview-owner-2",
  name: "林思远",
  status: "disabled" as const,
};

export const PREVIEW_POOL_IDS = {
  seller: "preview-pool-seller",
  buyer: "preview-pool-buyer",
} as const;

export const PREVIEW_RUN_IDS = {
  completed: "preview-run-completed",
  pending: "preview-run-pending",
  running: "preview-run-running",
  failed: "preview-run-failed",
  empty: "preview-run-empty",
  buyerCompleted: "preview-run-buyer-completed",
} as const;

function pool(
  id: string,
  name: string,
  kind: string,
  owner: typeof sellerOwner | typeof disabledOwner = sellerOwner,
): CandidatePool {
  return {
    id,
    department_id: "preview-department",
    owner_operator_id: owner.id,
    owner,
    name,
    kind,
    source_collection_job_id: null,
    status: "ACTIVE",
    current_policy_id: `${id}-policy-v3`,
    version: 3,
    created_at: "2026-08-18T02:00:00Z",
    updated_at: "2026-08-21T02:30:00Z",
  };
}

export const PREVIEW_POOLS: CandidatePool[] = [
  pool(PREVIEW_POOL_IDS.seller, "美妆达人筛选池", "POTENTIAL_SELLER"),
  pool(PREVIEW_POOL_IDS.buyer, "家居买家变化池", "POTENTIAL_BUYER"),
  pool(
    "preview-pool-archived",
    "历史美妆候选池",
    "POTENTIAL_SELLER",
    disabledOwner,
  ),
];

export const PREVIEW_POOL_PAGE: CandidatePoolPage = {
  items: PREVIEW_POOLS,
  next_cursor: "preview-pools-next",
};

const sellerDefinition = (version: number): TargetingPolicy["definition"] => ({
  schema_version: 1,
  policy_type: "SELLER_V1",
  followers: { minimum: 10000, maximum: 500000 },
  notes_7d: { minimum: 2 },
  notes_60d: { minimum: 12 },
  tags_exact_any: ["美妆", "护肤"],
  platforms: ["小红书"],
  sources: ["近期开源采集"],
  freshness: { allowed_statuses: ["新鲜"] },
  contact_availability: { types: ["邮箱"] },
  ...({ _preview_version: version } as Record<string, unknown>),
});

const buyerDefinition = (version: number): TargetingPolicy["definition"] => ({
  schema_version: 1,
  policy_type: "BUYER_V1",
  taxonomy: {
    taxonomy_version: `2026.08.${version}`,
    reviewed: true,
    categories: ["家居", "生活方式"],
    aliases: [{ label: "家居生活", category_id: "家居" }],
  },
  freshness: { allowed_statuses: ["新鲜", "可复核"] },
});

function policy(
  poolId: string,
  version: number,
  definition: TargetingPolicy["definition"],
): TargetingPolicy {
  return {
    id: `${poolId}-policy-v${version}`,
    pool_id: poolId,
    version,
    schema_version: 1,
    definition,
    canonical_hash: `preview-policy-${poolId}-${version}`,
    created_by_operator_id: sellerOwner.id,
    created_at: `2026-08-${15 + version}T02:00:00Z`,
    updated_at: `2026-08-${15 + version}T02:00:00Z`,
  };
}

export const PREVIEW_POLICIES: Record<string, TargetingPolicy[]> = {
  [PREVIEW_POOL_IDS.seller]: [1, 2, 3].map((version) =>
    policy(PREVIEW_POOL_IDS.seller, version, sellerDefinition(version)),
  ),
  [PREVIEW_POOL_IDS.buyer]: [1, 2, 3].map((version) =>
    policy(PREVIEW_POOL_IDS.buyer, version, buyerDefinition(version)),
  ),
};

function run(
  id: string,
  status: string,
  counts: [number, number, number],
  poolId: string = PREVIEW_POOL_IDS.seller,
  policyId = `${PREVIEW_POOL_IDS.seller}-policy-v3`,
): CandidatePoolRun {
  return {
    id,
    pool_id: poolId,
    policy_id: policyId,
    as_of: "2026-08-21T01:45:00Z",
    input_watermark: null,
    status,
    match_count: counts[0],
    unknown_count: counts[1],
    not_match_count: counts[2],
    error_code: status === "FAILED" ? "TARGETING_MATERIALIZATION_FAILED" : null,
    error_message: null,
    created_at: "2026-08-21T02:00:00Z",
    updated_at: "2026-08-21T02:20:00Z",
    idempotent_replay: false,
  };
}

export const PREVIEW_RUNS: CandidatePoolRun[] = [
  run(PREVIEW_RUN_IDS.completed, "COMPLETED", [86, 14, 312]),
  run(PREVIEW_RUN_IDS.pending, "PENDING", [0, 0, 0]),
  run(PREVIEW_RUN_IDS.running, "RUNNING", [0, 0, 0]),
  run(PREVIEW_RUN_IDS.failed, "FAILED", [0, 0, 0]),
  run(PREVIEW_RUN_IDS.empty, "COMPLETED", [0, 0, 0]),
  run(
    PREVIEW_RUN_IDS.buyerCompleted,
    "COMPLETED",
    [42, 8, 120],
    PREVIEW_POOL_IDS.buyer,
    `${PREVIEW_POOL_IDS.buyer}-policy-v3`,
  ),
];

export const PREVIEW_RUN_PAGES: Record<string, CandidatePoolRunPage> = {
  [PREVIEW_POOL_IDS.seller]: {
    items: PREVIEW_RUNS.filter(
      (item) => item.pool_id === PREVIEW_POOL_IDS.seller,
    ),
    next_cursor: null,
  },
  [PREVIEW_POOL_IDS.buyer]: {
    items: [PREVIEW_RUNS[4]!],
    next_cursor: null,
  },
};

type MemberSeed = {
  id: string;
  influencerId: string;
  displayName: string;
  influencerStatus?: "active" | "disabled";
  accountId: string;
  accountName: string;
  handle: string;
  accountActive?: boolean;
  result: "MATCH" | "UNKNOWN";
  reasons: string[];
  evidence: Record<string, unknown>;
};

function sellerEvidence(reasons: string[]): Record<string, unknown> {
  return {
    schema_version: 1,
    policy_type: "SELLER_V1",
    criteria: [
      {
        criterion: "粉丝数",
        result: "符合",
        configured: { minimum: 10000, maximum: 500000 },
        observed: { value: 128000 },
        reason_code: reasons[0] ?? "FOLLOWERS_IN_RANGE",
      },
      {
        criterion: "平台",
        result: "符合",
        configured: { allowed: ["小红书"] },
        observed: { value: "小红书" },
        reason_code: "PLATFORM_MATCH",
      },
    ],
  };
}

function buyerEvidence(): Record<string, unknown> {
  return {
    schema_version: 1,
    policy_type: "BUYER_V1",
    taxonomy_version: "2026.08.3",
    collection_context: {
      industry: "家居",
      subdirection: "生活方式",
      normalized_categories: ["家居", "生活方式"],
    },
    creator_classification: { normalized_categories: ["家居收纳"] },
    reason: "CATEGORY_MISMATCH",
  };
}

function member(
  runId: string,
  seed: MemberSeed,
  poolId: string = PREVIEW_POOL_IDS.seller,
): CandidateMember {
  return {
    id: seed.id,
    run_id: runId,
    influencer_id: seed.influencerId,
    platform_account_id: seed.accountId,
    result: seed.result,
    reason_codes: seed.reasons,
    redacted_evidence: seed.evidence,
    evidence_hash: `preview-evidence-${seed.id}`,
    created_at: "2026-08-21T02:10:00Z",
    updated_at: "2026-08-21T02:10:00Z",
    influencer: {
      id: seed.influencerId,
      display_name: seed.displayName,
      status: seed.influencerStatus ?? "active",
    },
    platform_account: {
      id: seed.accountId,
      platform: "xiaohongshu",
      platform_account_id: seed.accountId,
      account_name: seed.accountName,
      account_handle: seed.handle,
      is_active: seed.accountActive ?? true,
    },
    ...(poolId ? {} : {}),
  };
}

export const PREVIEW_MEMBERS: CandidateMember[] = [
  member(PREVIEW_RUN_IDS.completed, {
    id: "member-active-match",
    influencerId: "influencer-chen-yu",
    displayName: "陈雨",
    accountId: "account-chen-yu",
    accountName: "陈雨美妆",
    handle: "chenyu_beauty",
    result: "MATCH",
    reasons: ["FOLLOWERS_IN_RANGE", "PLATFORM_MATCH"],
    evidence: sellerEvidence(["FOLLOWERS_IN_RANGE"]),
  }),
  member(PREVIEW_RUN_IDS.completed, {
    id: "member-unknown",
    influencerId: "influencer-zhao-lu",
    displayName: "赵露",
    accountId: "account-zhao-lu",
    accountName: "赵露日常",
    handle: "zhaolu_daily",
    result: "UNKNOWN",
    reasons: ["CREATOR_CLASSIFICATION_MISSING"],
    evidence: sellerEvidence(["CREATOR_CLASSIFICATION_MISSING"]),
  }),
  member(PREVIEW_RUN_IDS.completed, {
    id: "member-disabled-influencer",
    influencerId: "influencer-sun-yan",
    displayName: "孙妍",
    influencerStatus: "disabled",
    accountId: "account-sun-yan",
    accountName: "孙妍护肤记录",
    handle: "sunyan_skincare",
    result: "MATCH",
    reasons: ["FOLLOWERS_IN_RANGE"],
    evidence: sellerEvidence(["FOLLOWERS_IN_RANGE"]),
  }),
  member(PREVIEW_RUN_IDS.completed, {
    id: "member-inactive-account",
    influencerId: "influencer-he-jing",
    displayName: "何静",
    accountId: "account-he-jing",
    accountName: "何静的生活方式",
    handle: "hejing_life",
    accountActive: false,
    result: "MATCH",
    reasons: ["FOLLOWERS_IN_RANGE"],
    evidence: sellerEvidence(["FOLLOWERS_IN_RANGE"]),
  }),
  member(PREVIEW_RUN_IDS.completed, {
    id: "member-lin-xia-selected",
    influencerId: "influencer-lin-xia",
    displayName: "林夏",
    accountId: "account-lin-xia-makeup",
    accountName: "林夏美妆",
    handle: "linxia_makeup",
    result: "MATCH",
    reasons: ["FOLLOWERS_IN_RANGE"],
    evidence: sellerEvidence(["FOLLOWERS_IN_RANGE"]),
  }),
  member(PREVIEW_RUN_IDS.completed, {
    id: "member-lin-xia-second",
    influencerId: "influencer-lin-xia",
    displayName: "林夏",
    accountId: "account-lin-xia-daily",
    accountName: "林夏生活记录",
    handle: "linxia_daily",
    result: "MATCH",
    reasons: ["FOLLOWERS_IN_RANGE"],
    evidence: sellerEvidence(["FOLLOWERS_IN_RANGE"]),
  }),
  member(
    PREVIEW_RUN_IDS.buyerCompleted,
    {
      id: "member-buyer-category-mismatch",
      influencerId: "influencer-buyer-qiang",
      displayName: "陈强",
      accountId: "account-buyer-qiang",
      accountName: "陈强家居观察",
      handle: "chenqiang_home",
      result: "MATCH",
      reasons: ["CATEGORY_MISMATCH"],
      evidence: buyerEvidence(),
    },
    PREVIEW_POOL_IDS.buyer,
  ),
];

export const PREVIEW_CAMPAIGNS: Campaign[] = [
  {
    id: "preview-campaign-draft",
    department_id: "preview-department",
    owner_operator_id: "preview-owner-1",
    owner: { id: "preview-owner-1", name: "周宁", status: "active" },
    created_by_operator_id: "preview-owner-1",
    name: "新客拓展（春季）",
    status: "DRAFT",
    review_mode: "FIRST_N",
    review_count: 50,
    duplicate_history_policy: "ALLOW_WITH_WARNING",
    duplicate_window_days: 14,
    version: 2,
    created_at: "2026-08-20T00:00:00Z",
    updated_at: "2026-08-20T01:00:00Z",
  },
  {
    id: "preview-campaign-active",
    department_id: "preview-department",
    owner_operator_id: "preview-owner-1",
    owner: { id: "preview-owner-1", name: "周宁", status: "active" },
    created_by_operator_id: "preview-owner-1",
    name: "大客户拓展（北区）",
    status: "ACTIVE",
    review_mode: "FIRST_N",
    review_count: 50,
    duplicate_history_policy: "ALLOW_WITH_WARNING",
    duplicate_window_days: 30,
    version: 6,
    created_at: "2026-08-20T02:30:00Z",
    updated_at: "2026-08-20T11:10:00Z",
  },
  {
    id: "preview-campaign-paused",
    department_id: "preview-department",
    owner_operator_id: "preview-owner-1",
    owner: { id: "preview-owner-1", name: "周宁", status: "active" },
    created_by_operator_id: "preview-owner-1",
    name: "重点跟进（秋季）",
    status: "PAUSED",
    review_mode: "MANUAL",
    review_count: 20,
    duplicate_history_policy: "ALLOW_WITH_WARNING",
    duplicate_window_days: 30,
    version: 3,
    created_at: "2026-08-19T10:30:00Z",
    updated_at: "2026-08-19T18:40:00Z",
  },
  {
    id: "preview-campaign-closed",
    department_id: "preview-department",
    owner_operator_id: "preview-owner-1",
    owner: { id: "preview-owner-1", name: "周宁", status: "active" },
    created_by_operator_id: "preview-owner-1",
    name: "历史活动（测试）",
    status: "CLOSED",
    review_mode: "FIRST_N",
    review_count: 50,
    duplicate_history_policy: "ALLOW_WITH_WARNING",
    duplicate_window_days: null,
    version: 1,
    created_at: "2026-08-18T00:00:00Z",
    updated_at: "2026-08-18T12:00:00Z",
  },
];

export const PREVIEW_MEMBER_PAGE = (
  runId: string,
): { items: CandidateMember[]; next_cursor: string | null } => ({
  items: PREVIEW_MEMBERS.filter((item) => item.run_id === runId),
  next_cursor: null,
});
