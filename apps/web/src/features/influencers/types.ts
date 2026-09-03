export type OwnerSummary = {
  id: string;
  name: string;
  status: "active" | "disabled";
};

export type FreshnessStatus =
  "unknown" | "fresh" | "aging" | "stale" | "very_stale";

export type ContentActivityReadStatus =
  "not_checked" | "current" | "last_known" | "stale" | "unknown";

export type ContentActivityObservationStatus =
  | "COMPLETE"
  | "IDENTITY_UNRESOLVED"
  | "ACCESS_RESTRICTED"
  | "PROVIDER_AUTH_ERROR"
  | "PROVIDER_RATE_LIMITED"
  | "PROVIDER_ERROR"
  | "RESULT_INCOMPLETE"
  | "RESULT_UNTRUSTED"
  | "UNKNOWN";

export type ContentActivityCoverageStatus =
  | "FULL_CURRENT_PUBLIC_SET"
  | "LATEST_BOUND_PROVEN"
  | "LOOKBACK_BOUNDED"
  | "INCOMPLETE"
  | "UNKNOWN";

export type ContentActivityResult =
  | "PUBLICATION_FOUND"
  | "NO_PUBLIC_CONTENT"
  | "AT_LEAST_LOOKBACK_INACTIVE"
  | "UNDETERMINED";

export type ContentActivityFilter =
  | "active_within_7d"
  | "inactive_30d"
  | "inactive_60d"
  | "inactive_90d"
  | "inactive_180d";

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
  last_huitun_observed_at: string | null;
  last_huitun_imported_at: string | null;
  freshness_status: FreshnessStatus | null;
  freshness_age_days: number | null;
  requires_refresh: boolean;
  content_activity_state?: ContentActivityReadStatus;
  content_activity_trusted_observed_at?: string | null;
  content_activity_trusted_observation_status?: ContentActivityObservationStatus | null;
  content_activity_trusted_coverage_status?: ContentActivityCoverageStatus | null;
  content_activity_trusted_result?: ContentActivityResult | null;
  content_activity_last_publication_at?: string | null;
  content_activity_inactive_days?: number | null;
  content_activity_latest_attempt_observed_at?: string | null;
  content_activity_latest_attempt_observation_status?: ContentActivityObservationStatus | null;
  content_activity_latest_attempt_coverage_status?: ContentActivityCoverageStatus | null;
  content_activity_latest_attempt_result?: ContentActivityResult | null;
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
  avatar_url?: string | null;
  status: "active";
  crm_stage: string;
  owner: OwnerSummary | null;
  platform_accounts: PlatformAccountSummary[];
  current_metrics: CurrentMetricsSummary[];
  current_contacts: CurrentContactSummary[];
  possible_duplicate_contact: boolean;
  freshness_status: FreshnessStatus;
  requires_refresh: boolean;
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
  contact_filter?: "has_contact" | "has_email" | "no_contact";
  notes_7d_filter?: "zero" | "one_to_two" | "three_plus" | "missing";
  notes_60d_filter?:
    "zero" | "one_to_two" | "three_to_nine" | "ten_or_more" | "missing";
  content_activity_filter?: ContentActivityFilter;
  freshness_status?: FreshnessStatus;
  requires_refresh?: "true" | "false";
  last_huitun_observed_before?: string;
  last_huitun_observed_after?: string;
  page?: string;
  page_size?: string;
};

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue =
  JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type MetricsDocument = Record<string, JsonValue>;

export type PlatformAccountDetail = PlatformAccountSummary & {
  bio: string | null;
  gender: string | null;
  region_raw: string | null;
  verification_info: string | null;
  mcn_name: string | null;
  creator_level: string | null;
  is_brand_partner: boolean | null;
};

export type InfluencerContactDetail = {
  id: string;
  platform_account_id: string | null;
  type: string;
  display_value: string;
  source: string;
  validation_status: string;
  is_current: boolean;
  possible_duplicate_contact: boolean;
  first_seen_at: string;
  last_seen_at: string;
  source_updated_at: string | null;
  first_import_job_id: string | null;
  first_import_row_id: string | null;
  last_import_job_id: string | null;
  last_import_row_id: string | null;
};

export type SourceStateDetail = {
  platform_account_id: string;
  source: string;
  source_updated_at: string | null;
  state_version: number;
  creator_tags: string[];
  creator_classification_tags: string[];
  last_import_job_id: string;
  last_import_row_id: string;
};

export type SourceIdentityDetail = {
  id: string;
  platform_account_id: string;
  platform: string;
  source: string;
  external_account_id: string;
  first_import_job_id: string;
  first_import_row_id: string;
  last_import_job_id: string;
  last_import_row_id: string;
};

export type CurrentMetricsDetail = {
  platform_account_id: string;
  source: string;
  source_updated_at: string | null;
  metrics: MetricsDocument;
  last_import_job_id: string;
  last_import_row_id: string;
};

export type InfluencerDetail = {
  id: string;
  display_name: string;
  avatar_url?: string | null;
  status: "active";
  crm_stage: string;
  freshness_status: FreshnessStatus;
  requires_refresh: boolean;
  owner: OwnerSummary | null;
  created_at: string;
  updated_at: string;
  platform_accounts: PlatformAccountDetail[];
  contacts: InfluencerContactDetail[];
  source_states: SourceStateDetail[];
  source_identities: SourceIdentityDetail[];
  current_metrics: CurrentMetricsDetail[];
};

export type MetricSnapshotItem = {
  id: string;
  platform_account_id: string;
  source: string;
  source_updated_at: string | null;
  captured_at: string;
  metrics: MetricsDocument;
  import_job_id: string;
  import_row_id: string;
};

export type MetricSnapshotPage = {
  items: MetricSnapshotItem[];
  page: number;
  page_size: number;
  total: number;
};
