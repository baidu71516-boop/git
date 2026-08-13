import type {
  CurrentContactSummary,
  InfluencerContactDetail,
  CurrentMetricsDetail,
  InfluencerDetail,
  InfluencerListItem,
  OwnerSummary,
  SourceIdentityDetail,
  SourceStateDetail,
} from "./types";

const activeOwner: OwnerSummary = {
  id: "00000000-0000-0000-0000-000000000901",
  name: "预览负责人甲",
  status: "active",
};

const secondOwner: OwnerSummary = {
  id: "00000000-0000-0000-0000-000000000902",
  name: "预览负责人乙",
  status: "active",
};

function previewContact(
  row: number,
  type: "email" | "phone",
): CurrentContactSummary {
  return {
    id: `00000000-0000-0000-0000-${String(row).padStart(10, "0")}${type === "email" ? "01" : "02"}`,
    type,
    display_value: "***",
    source: "manual",
    validation_status: "unverified",
    possible_duplicate_contact: false,
  };
}

function previewDetailContact(
  row: number,
  type: "email" | "phone",
): InfluencerContactDetail {
  return {
    id: `00000000-0000-0000-0000-00000000d${String(row).padStart(2, "0")}${type === "email" ? "1" : "2"}`,
    platform_account_id: `00000000-0000-0000-0000-000000001${String(row).padStart(3, "0")}`,
    type,
    display_value: type === "email" ? "abc***@example.com" : "138****1234",
    source: "huitun",
    validation_status: "unverified",
    is_current: true,
    possible_duplicate_contact: false,
    first_seen_at: "2026-08-01T00:00:00.000Z",
    last_seen_at: "2026-08-01T00:00:00.000Z",
    source_updated_at: "2026-08-01T00:00:00.000Z",
    first_import_job_id: "job-preview",
    first_import_row_id: "row-preview",
    last_import_job_id: "job-preview",
    last_import_row_id: "row-preview",
  };
}

function previewDetailMetric(
  platformAccountId: string,
  followers: number | null,
  sourceUpdatedAt: string | null,
): CurrentMetricsDetail {
  return {
    platform_account_id: platformAccountId,
    source: "huitun",
    source_updated_at: sourceUpdatedAt,
    metrics: {
      followers_count: followers,
    },
    last_import_job_id: "job-preview",
    last_import_row_id: "row-preview",
  };
}

function previewSourceStates(row: number): SourceStateDetail[] {
  const accountSuffix = String(row).padStart(3, "0");
  return [
    {
      platform_account_id: `00000000-0000-0000-0000-000000001${accountSuffix}`,
      source: "huitun",
      source_updated_at: "2026-08-01T00:00:00.000Z",
      state_version: 1,
      creator_tags: ["AI筛选", "高匹配"],
      last_import_job_id: "job-preview",
      last_import_row_id: "row-preview",
    },
  ];
}

function previewSourceIdentities(row: number): SourceIdentityDetail[] {
  const accountSuffix = String(row).padStart(3, "0");
  return [
    {
      id: `00000000-0000-0000-0000-000000100${accountSuffix}`,
      platform_account_id: `00000000-0000-0000-0000-000000001${accountSuffix}`,
      platform: "xiaohongshu",
      source: "huitun",
      external_account_id: `external-${accountSuffix}`,
      first_import_job_id: "job-preview",
      first_import_row_id: "row-preview",
      last_import_job_id: "job-preview",
      last_import_row_id: "row-preview",
    },
  ];
}

function timestampDaysAgo(now: Date, days: number): string {
  return new Date(now.getTime() - days * 86_400_000).toISOString();
}

type PreviewItemInput = {
  row: number;
  name: string;
  followers: number | null;
  tags: string[];
  crmStage?: string;
  contacts?: Array<"email" | "phone">;
  owner?: OwnerSummary | null;
  metricsUpdatedAt?: string | null;
};

type PreviewDetailInput = {
  row: number;
  name: string;
  followers: number | null;
  tags: string[];
  stage?: string;
  owner?: OwnerSummary | null;
  contacts?: Array<"email" | "phone">;
  accountCount?: number;
  updatedAt?: string | null;
  metricsUpdatedAt?: string | null;
};

function previewItem(input: PreviewItemInput): InfluencerListItem {
  const suffix = String(input.row).padStart(3, "0");
  const influencerId = `00000000-0000-0000-0000-000000000${suffix}`;
  const accountId = `00000000-0000-0000-0000-000000001${suffix}`;
  return {
    id: influencerId,
    display_name: input.name,
    status: "active",
    crm_stage: input.crmStage ?? "",
    owner: input.owner ?? null,
    platform_accounts: [
      {
        id: accountId,
        platform: "xiaohongshu",
        platform_account_id: `ui-preview-${input.row}`,
        account_name: `${input.name}账号`,
        account_handle: `ui-preview-${input.row}`,
        profile_url: null,
        source: "huitun",
        is_active: true,
        source_tags: input.tags,
      },
    ],
    current_metrics: [
      {
        platform_account_id: accountId,
        source: "huitun",
        source_updated_at: input.metricsUpdatedAt ?? null,
        followers_count: input.followers,
      },
    ],
    current_contacts: (input.contacts ?? []).map((type) =>
      previewContact(input.row, type),
    ),
    possible_duplicate_contact: false,
    created_at: "2026-08-01T00:00:00.000Z",
    updated_at: "2026-08-01T00:00:00.000Z",
  };
}

function createPreviewDetail(input: PreviewDetailInput): InfluencerDetail {
  const suffix = String(input.row).padStart(3, "0");
  const count = input.accountCount ?? 1;
  const accountIds = Array.from({ length: count }, (_, index) =>
    String(index + 1).padStart(3, "0"),
  );
  const platforms = ["xiaohongshu", "douyin", "kuaishou"];

  const platformAccounts = accountIds.map((index, idx) => ({
    id: `00000000-0000-0000-0000-000000001${suffix}${index}`,
    platform: platforms[idx] ?? "xiaohongshu",
    platform_account_id: `ui-preview-${input.row}-${index}`,
    account_name: `${input.name}账号${idx + 1}`,
    account_handle: `handle-${input.row}-${index}`,
    profile_url: null,
    source: "huitun",
    is_active: idx === 0,
    source_tags: input.tags,
    bio: null,
    gender: null,
    region_raw: null,
    verification_info: null,
    mcn_name: null,
    creator_level: null,
    is_brand_partner: null,
  }));

  const metrics: CurrentMetricsDetail[] = platformAccounts.map((account, idx) =>
    previewDetailMetric(
      account.id,
      idx === 0
        ? input.followers
        : idx === 1
          ? Math.floor((input.followers ?? 0) * 0.5)
          : null,
      input.metricsUpdatedAt ?? null,
    ),
  );

  return {
    id: `00000000-0000-0000-0000-000000000${suffix}`,
    display_name: input.name,
    status: "active",
    crm_stage: input.stage ?? "",
    owner: input.owner ?? null,
    created_at: "2026-08-01T08:00:00.000Z",
    updated_at: input.updatedAt ?? "2026-08-01T08:00:00.000Z",
    platform_accounts: platformAccounts,
    contacts:
      input.contacts?.map((type) => previewDetailContact(input.row, type)) ??
      [],
    source_states: previewSourceStates(input.row),
    source_identities: previewSourceIdentities(input.row),
    current_metrics: metrics,
  };
}

export function createInfluencerPreviewItems(
  now: Date = new Date(),
): InfluencerListItem[] {
  return [
    previewItem({
      row: 1,
      name: "星河杂货铺",
      followers: 8_532,
      tags: [],
      contacts: [],
      owner: activeOwner,
      metricsUpdatedAt: timestampDaysAgo(now, 0),
    }),
    previewItem({
      row: 2,
      name: "白桃汽水",
      followers: 206_572,
      tags: ["生活方式"],
      crmStage: "待开发",
      contacts: ["email"],
      metricsUpdatedAt: timestampDaysAgo(now, 1),
    }),
    previewItem({
      row: 3,
      name: "月面电台",
      followers: 482_400,
      tags: ["二次元", "数码"],
      crmStage: "沟通中",
      contacts: ["phone"],
      owner: secondOwner,
      metricsUpdatedAt: timestampDaysAgo(now, 3),
    }),
    previewItem({
      row: 4,
      name: "南风研究所",
      followers: 1_286_000,
      tags: ["美妆", "护肤", "成分党", "测评", "生活方式"],
      crmStage: "高意向",
      contacts: ["email", "phone"],
      owner: activeOwner,
      metricsUpdatedAt: "2026-05-18T06:30:00.000Z",
    }),
    previewItem({
      row: 5,
      name: "小岛日记",
      followers: 0,
      tags: ["旅行"],
      crmStage: "已回复",
      contacts: [],
      metricsUpdatedAt: null,
    }),
    previewItem({
      row: 6,
      name: "橘子宇宙",
      followers: null,
      tags: ["母婴", "家庭生活", "好物分享", "亲子", "成长记录"],
      contacts: ["email"],
      owner: secondOwner,
      metricsUpdatedAt: null,
    }),
    previewItem({
      row: 7,
      name: "蓝莓星期五",
      followers: 12_500,
      tags: ["穿搭", "通勤"],
      crmStage: "长期维护",
      contacts: ["phone"],
      owner: activeOwner,
      metricsUpdatedAt: timestampDaysAgo(now, 5),
    }),
    previewItem({
      row: 8,
      name: "云朵放映室",
      followers: 98_700,
      tags: ["影视"],
      crmStage: "潜在合作",
      contacts: ["email", "phone"],
      metricsUpdatedAt: "2026-06-20T11:20:00.000Z",
    }),
  ];
}

export function createInfluencerPreviewDetailItems(
  now: Date = new Date(),
): InfluencerDetail[] {
  return [
    createPreviewDetail({
      row: 1,
      name: "星河杂货铺",
      followers: 8_532,
      tags: [],
      owner: activeOwner,
      metricsUpdatedAt: timestampDaysAgo(now, 0),
    }),
    createPreviewDetail({
      row: 2,
      name: "白桃汽水",
      followers: 206_572,
      tags: ["生活方式"],
      stage: "待开发",
      contacts: ["email"],
      owner: activeOwner,
      metricsUpdatedAt: timestampDaysAgo(now, 1),
    }),
    createPreviewDetail({
      row: 3,
      name: "月面电台",
      followers: 482_400,
      tags: ["二次元", "数码"],
      stage: "沟通中",
      contacts: ["phone"],
      owner: secondOwner,
      metricsUpdatedAt: timestampDaysAgo(now, 3),
      accountCount: 2,
    }),
    createPreviewDetail({
      row: 4,
      name: "南风研究所",
      followers: 1_286_000,
      tags: ["美妆", "护肤", "成分党", "测评", "生活方式"],
      stage: "高意向",
      contacts: ["email", "phone"],
      owner: activeOwner,
      metricsUpdatedAt: null,
      updatedAt: "2026-05-18T06:30:00.000Z",
    }),
    createPreviewDetail({
      row: 5,
      name: "小岛日记",
      followers: 0,
      tags: ["旅行"],
      owner: null,
      metricsUpdatedAt: null,
    }),
    createPreviewDetail({
      row: 6,
      name: "橘子宇宙",
      followers: null,
      tags: ["母婴", "家庭生活", "好物分享", "亲子", "成长记录"],
      stage: "已回复",
      contacts: ["email"],
      owner: secondOwner,
      metricsUpdatedAt: null,
    }),
    createPreviewDetail({
      row: 7,
      name: "蓝莓星期五",
      followers: 12_500,
      tags: ["穿搭", "通勤"],
      stage: "长期维护",
      contacts: ["phone"],
      owner: activeOwner,
      metricsUpdatedAt: timestampDaysAgo(now, 5),
    }),
    createPreviewDetail({
      row: 8,
      name: "云朵放映室",
      followers: 98_700,
      tags: ["影视"],
      stage: "潜在合作",
      contacts: ["email", "phone"],
      owner: activeOwner,
      metricsUpdatedAt: "2026-06-20T11:20:00.000Z",
      accountCount: 3,
    }),
  ];
}

export function getInfluencerPreviewDetailById(
  influencerId: string,
): InfluencerDetail | undefined {
  return createInfluencerPreviewDetailItems().find(
    (item) => item.id === influencerId,
  );
}

export const influencerPreviewOwners = [activeOwner, secondOwner];

export const influencerPreviewCrmStages = [
  "待开发",
  "已回复",
  "沟通中",
  "潜在合作",
  "高意向",
  "长期维护",
];
