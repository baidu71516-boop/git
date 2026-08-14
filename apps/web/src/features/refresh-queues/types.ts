export type RefreshQueueStatus =
  "open" | "exported" | "completed" | "cancelled";

export type RefreshQueueItemStatus =
  | "pending"
  | "fulfilled_changed"
  | "fulfilled_no_change"
  | "stale_return"
  | "unresolved"
  | "cancelled";

export type RefreshPriorityReason =
  "FRESHNESS_UNKNOWN" | "VERY_STALE" | "STALE" | "AGING" | "FOLLOWERS_MISSING";

export type RefreshFreshnessStatus =
  "fresh" | "aging" | "stale" | "very_stale" | "unknown";

export type RefreshPlatform = "xiaohongshu";

export type IdentitySnapshot = {
  schema_version: 1;
  platform: RefreshPlatform;
  account_name: string | null;
  platform_account_id: string | null;
  account_handle: string | null;
  profile_url: string | null;
  external_source_id: string | null;
  followers_count: number | null;
};

export type RefreshQueue = {
  id: string;
  department_id: string;
  created_by_operator_id: string;
  status: RefreshQueueStatus;
  as_of: string;
  requested_limit: number;
  today_total_limit: number;
  refresh_limit: number;
  policy_version: number;
  criteria_snapshot: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  exported_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
};

export type RefreshQueueItem = {
  id: string;
  department_id: string;
  queue_id: string;
  influencer_id: string;
  platform_account_id: string;
  source: "huitun";
  priority_tier: 1 | 2 | 3 | 4 | 5;
  priority_reasons: RefreshPriorityReason[];
  identity_snapshot: IdentitySnapshot;
  baseline_last_observed_at: string | null;
  baseline_source_updated_at: string | null;
  status: RefreshQueueItemStatus;
  fulfilled_import_job_id: string | null;
  fulfilled_import_row_id: string | null;
  fulfilled_at: string | null;
  last_return_import_job_id: string | null;
  last_return_import_row_id: string | null;
  created_at: string;
  updated_at: string;
};

export type RefreshQueueSummary = {
  requested: number;
  selected: number;
  unique_influencers: number;
  freshness_breakdown: Partial<Record<RefreshFreshnessStatus, number>>;
  priority_breakdown: Partial<Record<"1" | "2" | "3" | "4" | "5", number>>;
  status_breakdown: Partial<Record<RefreshQueueItemStatus, number>>;
};

export type RefreshQueueDetail = {
  queue: RefreshQueue;
  summary: RefreshQueueSummary;
};

export type RefreshQueuePage = {
  items: RefreshQueue[];
  total: number;
  offset: number;
  limit: number;
};

export type RefreshQueueItemPage = {
  items: RefreshQueueItem[];
  total: number;
  offset: number;
  limit: number;
};

export type RefreshQueueCreateInput = {
  requested_limit: number;
  refresh_limit: number;
  today_total_limit: number;
  department_id?: string;
};

export type DepartmentOption = {
  id: string;
  name: string;
};

export type RefreshQueueRole =
  "super_admin" | "manager" | "operator" | "viewer";
