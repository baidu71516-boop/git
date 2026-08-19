import type {
  CampaignOption,
  OperatorOption,
  TodayFilters,
  TodayItem,
  TodayPage,
} from "./types";

export type TodayPreviewSceneKey =
  | "default"
  | "more-filters"
  | "history"
  | "filtered-empty"
  | "empty"
  | "loading"
  | "error";

export type TodayPreviewScene = {
  key: TodayPreviewSceneKey;
  label: string;
  state: "loading" | "error" | "empty" | "ready";
  page: TodayPage;
  filters: TodayFilters;
  moreFiltersOpen?: boolean;
};

const operators: OperatorOption[] = [
  { id: "operator-wang", name: "王小明", status: "active" },
  { id: "operator-liu", name: "刘思远", status: "active" },
];

const campaigns: CampaignOption[] = [
  { id: "campaign-3c", name: "8月3C达人开发", status: "ACTIVE" },
  { id: "campaign-tech", name: "科技达人拓展", status: "ACTIVE" },
];

export const todayPreviewOptions = {
  operators,
  campaigns,
  tracks: ["数码", "科技", "生活方式"],
};

const basePage: Omit<TodayPage, "items"> = {
  business_date: "2026-08-19",
  timezone: "Asia/Shanghai",
  as_of: "2026-08-19T01:46:00Z",
  next_cursor: "preview-next",
};

function createItem(overrides: Partial<TodayItem>): TodayItem {
  return {
    task_id: "preview-task",
    target_id: "preview-target",
    campaign_id: "campaign-3c",
    campaign_name: "8月3C达人开发",
    member_id: "preview-member",
    influencer_id: "preview-influencer",
    crm_stage: "待开发",
    preferred_platform_account: {
      id: "preview-account",
      platform: "xiaohongshu",
      account_name: "数码老李",
      account_handle: "shumaolaoli",
      source_tags: ["数码"],
      followers_count: 100000,
    },
    assigned_operator_id: "operator-wang",
    kind: "FIRST_TOUCH",
    state: "READY",
    channel: "EMAIL",
    priority: "HIGH",
    due_at: "2026-08-19T06:00:00Z",
    version: 1,
    has_contact: true,
    has_email: true,
    masked_target_display: "***",
    history_warning: null,
    ...overrides,
  };
}

const defaultItems: TodayItem[] = [
  createItem({ task_id: "preview-task-a" }),
  createItem({
    task_id: "preview-task-b",
    target_id: "preview-target-b",
    campaign_id: "campaign-tech",
    campaign_name: "科技达人拓展",
    member_id: "preview-member-b",
    influencer_id: "preview-influencer-b",
    preferred_platform_account: {
      id: "preview-account-b",
      platform: "xiaohongshu",
      account_name: "科技小王",
      account_handle: "keji-xiaowang",
      source_tags: ["科技"],
      followers_count: 68000,
    },
    assigned_operator_id: "operator-liu",
    kind: "FOLLOW_UP",
    channel: "XIAOHONGSHU_PRIVATE_MESSAGE",
    priority: "NORMAL",
    due_at: "2026-08-18T08:30:00Z",
    has_contact: true,
    has_email: false,
  }),
  createItem({
    task_id: "preview-task-c",
    target_id: "preview-target-c",
    member_id: "preview-member-c",
    influencer_id: "preview-influencer-c",
    preferred_platform_account: {
      id: "preview-account-c",
      platform: "douyin",
      account_name: "小岛日记",
      account_handle: null,
      source_tags: ["生活方式"],
      followers_count: 42000,
    },
    assigned_operator_id: "operator-unresolved",
    channel: "WECHAT",
    priority: "NORMAL",
    due_at: "2026-08-22T02:00:00Z",
    has_contact: false,
    has_email: false,
  }),
];

const historyItems = defaultItems.map((item, index) =>
  index === 0
    ? {
        ...item,
        history_warning: {
          channel: "EMAIL" as const,
          last_sent_at: "2026-08-18T01:00:00Z",
        },
      }
    : item,
);

function page(
  items: TodayItem[],
  next_cursor = basePage.next_cursor,
): TodayPage {
  return { ...basePage, items, next_cursor };
}

export const todayPreviewScenes: TodayPreviewScene[] = [
  {
    key: "default",
    label: "今日触达 · 默认列表",
    state: "ready",
    page: page(defaultItems),
    filters: { work_kind: "ALL" },
  },
  {
    key: "more-filters",
    label: "今日触达 · 更多筛选",
    state: "ready",
    page: page(defaultItems, null),
    filters: {
      work_kind: "ALL",
      followers_min: 10000,
      followers_max: 500000,
      contact_filter: "has_contact",
      priority: "HIGH",
    },
    moreFiltersOpen: true,
  },
  {
    key: "history",
    label: "今日触达 · 历史触达提示",
    state: "ready",
    page: page(historyItems, null),
    filters: { work_kind: "ALL" },
  },
  {
    key: "filtered-empty",
    label: "今日触达 · 筛选后无结果",
    state: "empty",
    page: page([], null),
    filters: { work_kind: "FOLLOW_UP", track: "科技", priority: "HIGH" },
  },
  {
    key: "empty",
    label: "今日触达 · 空状态",
    state: "empty",
    page: page([], null),
    filters: { work_kind: "ALL" },
  },
  {
    key: "loading",
    label: "今日触达 · 加载状态",
    state: "loading",
    page: page([], null),
    filters: { work_kind: "ALL" },
  },
  {
    key: "error",
    label: "今日触达 · 加载失败",
    state: "error",
    page: page([], null),
    filters: { work_kind: "ALL" },
  },
];

export const todayPreviewSceneByKey = new Map(
  todayPreviewScenes.map((scene) => [scene.key, scene]),
);
