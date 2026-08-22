export type TodayWorkKind = "ALL" | "FIRST_TOUCH" | "FOLLOW_UP";
export type TodayChannel =
  | "EMAIL"
  | "XIAOHONGSHU_PRIVATE_MESSAGE"
  | "DOUYIN_PRIVATE_MESSAGE"
  | "WECHAT"
  | "MANUAL";
export type TodayPriority = "HIGH" | "NORMAL";
export type TodayContactFilter = "has_contact" | "has_email" | "no_contact";

export type TodayFilters = {
  work_kind: TodayWorkKind;
  channel?: TodayChannel;
  campaign_id?: string;
  owner_operator_id?: string;
  track?: string;
  followers_min?: number;
  followers_max?: number;
  contact_filter?: TodayContactFilter;
  priority?: TodayPriority;
};

export type TodayPreferredAccount = {
  id: string;
  platform: string;
  account_name: string;
  account_handle: string | null;
  source_tags: string[];
  followers_count: number | null;
};

export type TodayHistoryWarning = {
  channel: TodayChannel;
  last_sent_at: string;
};

export type TodayItem = {
  task_id: string;
  target_id: string;
  campaign_id: string;
  campaign_name: string;
  member_id: string;
  influencer_id: string;
  crm_stage: string;
  preferred_platform_account: TodayPreferredAccount;
  assigned_operator_id: string | null;
  kind: "FIRST_TOUCH" | "FOLLOW_UP";
  state: "REVIEW_REQUIRED" | "READY" | "SENT" | "STOPPED" | "FAILED";
  channel: TodayChannel;
  priority: TodayPriority;
  due_at: string;
  version: number;
  has_contact: boolean;
  has_email: boolean;
  masked_target_display: string;
  history_warning: TodayHistoryWarning | null;
};

export type TodayPage = {
  business_date: string;
  timezone: "Asia/Shanghai";
  as_of: string;
  items: TodayItem[];
  next_cursor: string | null;
};

export type OperatorOption = {
  id: string;
  name: string;
  status: "active" | "disabled";
};

export type CampaignOption = {
  id: string;
  name: string;
  status: string;
};

export type TodayOptions = {
  operators: OperatorOption[];
  campaigns: CampaignOption[];
  tracks: string[];
};
