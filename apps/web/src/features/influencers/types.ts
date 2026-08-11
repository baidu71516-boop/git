export type OwnerSummary = {
  id: string;
  name: string;
  status: "active" | "disabled";
};

export type PlatformAccountSummary = {
  id: string;
  platform: string;
  platform_account_id: string | null;
  account_name: string;
  account_handle: string | null;
  profile_url: string | null;
  source: string;
  is_active: boolean;
  source_tags: string[];
};

export type CurrentMetricsSummary = {
  platform_account_id: string;
  source: string;
  source_updated_at: string | null;
  followers_count: number | null;
};

export type CurrentContactSummary = {
  id: string;
  type: string;
  display_value: string;
  source: string;
  validation_status: string;
  possible_duplicate_contact: boolean;
};

export type InfluencerListItem = {
  id: string;
  display_name: string;
  status: "active";
  crm_stage: string;
  owner: OwnerSummary | null;
  platform_accounts: PlatformAccountSummary[];
  current_metrics: CurrentMetricsSummary[];
  current_contacts: CurrentContactSummary[];
  possible_duplicate_contact: boolean;
  created_at: string;
  updated_at: string;
};

export type InfluencerListPage = {
  items: InfluencerListItem[];
  page: number;
  page_size: number;
  total: number;
};

export type InfluencerFilterOptions = {
  owners: OwnerSummary[];
  tags: string[];
  crm_stages: string[];
};

export type InfluencerListQueryParams = {
  q?: string;
  tag?: string;
  followers_min?: string;
  followers_max?: string;
  owner_operator_id?: string;
  crm_stage?: string;
  page?: string;
  page_size?: string;
};
