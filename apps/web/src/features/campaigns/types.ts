export type CampaignRole = "super_admin" | "manager" | "operator" | "viewer";

export type CampaignOwner = {
  id: string;
  name: string;
  status: "active" | "disabled";
};

export type Campaign = {
  id: string;
  department_id: string;
  owner_operator_id: string;
  owner: CampaignOwner;
  created_by_operator_id: string;
  name: string;
  status: "DRAFT" | "ACTIVE" | "PAUSED" | "CLOSED" | string;
  review_mode: string;
  review_count: number | null;
  duplicate_history_policy: string;
  duplicate_window_days: number | null;
  version: number;
  created_at: string;
  updated_at: string;
};

export type CampaignPage = {
  items: Campaign[];
  next_cursor: string | null;
};

export type CampaignOperator = {
  id: string;
  department_id: string;
  name: string;
  role: CampaignRole;
  status: "active" | "disabled";
};

export type CampaignScope = {
  departmentId?: string;
};

export type CreateCampaignInput = {
  name: string;
  owner_operator_id?: string;
};

export type CampaignEditableValues = {
  name: string;
  owner_operator_id: string;
};

export type UpdateCampaignInput = CampaignEditableValues & {
  review_mode: string;
  review_count: number | null;
  duplicate_history_policy: string;
  duplicate_window_days: number | null;
  expected_version: number;
};

export type CampaignMemberInfluencer = {
  id: string;
  display_name: string;
  status: "active" | "disabled";
};

export type CampaignMemberPlatformAccount = {
  id: string;
  platform: string;
  platform_account_id: string | null;
  account_name: string;
  account_handle: string | null;
  is_active: boolean;
};

export type CampaignMember = {
  id: string;
  campaign_id: string;
  influencer_id: string;
  preferred_platform_account_id: string;
  version: number;
  created_at: string;
  influencer: CampaignMemberInfluencer;
  preferred_platform_account: CampaignMemberPlatformAccount;
};

export type CampaignMemberPage = {
  items: CampaignMember[];
  next_cursor: string | null;
};

export type CampaignMemberAddItem = {
  influencer_id: string;
  preferred_platform_account_id: string;
};

export type CampaignMemberBulkAddResult = {
  campaign_id: string;
  requested_count: number;
  added_count: number;
  restored_count: number;
  already_active_count: number;
};
