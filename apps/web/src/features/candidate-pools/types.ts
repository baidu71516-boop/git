export type CandidatePoolRole =
  "super_admin" | "manager" | "operator" | "viewer";

export type CandidatePoolOwner = {
  id: string;
  name: string;
  status: "active" | "disabled" | string;
};

export type CandidatePool = {
  id: string;
  department_id: string;
  owner_operator_id: string;
  owner: CandidatePoolOwner;
  name: string;
  kind: string;
  source_collection_job_id: string | null;
  status: string;
  current_policy_id: string | null;
  version: number;
  created_at: string;
  updated_at: string;
};

export type CandidatePoolPage = {
  items: CandidatePool[];
  next_cursor: string | null;
};

export type SellerTargetingPolicy = {
  schema_version: 1;
  policy_type: "SELLER_V1";
  contact_availability?: "has_contact" | "has_email" | "no_contact" | null;
  followers?: { minimum?: number | null; maximum?: number | null } | null;
  tags_exact_any?: string[];
  notes_7d?: { minimum?: number | null; maximum?: number | null } | null;
  notes_60d?: { minimum?: number | null; maximum?: number | null } | null;
  freshness?: {
    allowed_statuses: Array<"fresh" | "aging" | "stale" | "very_stale">;
  } | null;
  platforms?: string[];
  sources?: string[];
};

type ExistingSellerTargetingPolicy = {
  schema_version: 1;
  policy_type: "SELLER_V1";
  contact_availability?: { types?: string[] } | null;
  followers?: { minimum?: number | null; maximum?: number | null } | null;
  tags_exact_any?: string[];
  notes_7d?: { minimum?: number | null; maximum?: number | null } | null;
  notes_60d?: { minimum?: number | null; maximum?: number | null } | null;
  freshness?: { allowed_statuses?: string[] } | null;
  content_activity?: {
    schema_version?: number;
    minimum_inactive_days?: number;
  } | null;
  long_inactivity?: {
    schema_version?: number;
    minimum_inactive_days?: 30 | 60 | 90 | 180;
  } | null;
  platforms?: string[];
  sources?: string[];
};

export type TargetingPolicyDefinition =
  | SellerTargetingPolicy
  | ExistingSellerTargetingPolicy
  | {
      schema_version: 1;
      policy_type: "BUYER_V1";
      taxonomy: {
        taxonomy_version: string;
        reviewed: boolean;
        categories?: string[];
        aliases?: Array<{ label: string; category_id: string }>;
        parent_child?: Array<{
          left_category_id: string;
          right_category_id: string;
        }>;
        compatible?: Array<{
          left_category_id: string;
          right_category_id: string;
        }>;
        incompatible?: Array<{
          left_category_id: string;
          right_category_id: string;
        }>;
      };
      freshness?: { allowed_statuses?: string[] } | null;
    }
  | { schema_version: number; policy_type: string; [key: string]: unknown };

export function isSellerTargetingPolicy(
  definition: TargetingPolicyDefinition,
): definition is SellerTargetingPolicy | ExistingSellerTargetingPolicy {
  return (
    definition.schema_version === 1 && definition.policy_type === "SELLER_V1"
  );
}

export function isAuthorableSellerTargetingPolicy(
  definition: TargetingPolicyDefinition,
): definition is SellerTargetingPolicy {
  const existingSeller = definition as ExistingSellerTargetingPolicy;
  return (
    isSellerTargetingPolicy(definition) &&
    (definition.contact_availability == null ||
      typeof definition.contact_availability === "string") &&
    existingSeller.content_activity == null &&
    existingSeller.long_inactivity == null
  );
}

export type TargetingPolicy = {
  id: string;
  pool_id: string;
  version: number;
  schema_version: number;
  definition: TargetingPolicyDefinition;
  canonical_hash: string;
  created_by_operator_id: string;
  created_at: string;
  updated_at: string;
};

export type CandidatePoolRun = {
  id: string;
  pool_id: string;
  policy_id: string;
  as_of: string;
  input_watermark: Record<string, unknown> | null;
  status: string;
  match_count: number;
  unknown_count: number;
  not_match_count: number;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  idempotent_replay: boolean;
};

export type CandidatePoolRunPage = {
  items: CandidatePoolRun[];
  next_cursor: string | null;
};

export type CandidatePoolCreateRequest = {
  name: string;
  kind: "POTENTIAL_SELLER";
  policy: SellerTargetingPolicy;
};

export type CandidatePoolRunRequest =
  | Record<string, never>
  | { policy_id: string }
  | {
      base_policy_id: string;
      expected_pool_version: number;
      policy: SellerTargetingPolicy;
    };

export type CandidateMember = {
  id: string;
  run_id: string;
  influencer_id: string;
  platform_account_id: string;
  result: "MATCH" | "UNKNOWN" | string;
  reason_codes: string[];
  redacted_evidence: Record<string, unknown>;
  evidence_hash: string;
  created_at: string;
  updated_at: string;
  influencer: {
    id: string;
    display_name: string;
    status: "active" | "disabled" | string;
  };
  platform_account: {
    id: string;
    platform: string;
    platform_account_id: string | null;
    account_name: string;
    account_handle: string | null;
    is_active: boolean;
  };
};

export type CandidateMemberPage = {
  items: CandidateMember[];
  next_cursor: string | null;
};

export type CandidateMemberPageSize = 20 | 50 | 100 | 200;

/** Existing explicit selection shape. Keep this request body unchanged. */
export type CandidateCampaignExplicitSelection = {
  run_id: string;
  member_ids: string[];
  selection_mode?: never;
  excluded_member_ids?: never;
};

/** Server-resolved immutable MATCH set, minus the selected exclusions. */
export type CandidateCampaignAllMatchSelection = {
  run_id: string;
  selection_mode: "ALL_MATCH";
  excluded_member_ids: string[];
  member_ids?: never;
};

export type CandidateCampaignSelection =
  CandidateCampaignExplicitSelection | CandidateCampaignAllMatchSelection;

export type CandidateCampaignAddResult = {
  campaign_id: string;
  added_count: number;
  restored_count: number;
  already_active_count: number;
};
