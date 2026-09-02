import type {
  BuyerLeadTier,
  CandidatePoolOwner,
  CandidatePoolRole,
  CandidatePoolRun,
} from "@/features/candidate-pools/types";

export type BuyerProspectRuleStatus = "ACTIVE" | "DISABLED" | "ARCHIVED";
export type BuyerProspectRecentCollectionWindow = "7" | "30" | "60" | "90" | "ALL";
export type BuyerProspectOwnerFilter = "ANY" | "UNASSIGNED" | "OPERATOR";

export type BuyerProspectSourceCollectionJob = {
  id: string;
  name: string;
  industry: string;
  subdirection: string | null;
};

export type BuyerProspectOperator = {
  id: string;
  name: string;
};

export type BuyerProspectTaxonomyCategory = {
  id: string;
  label: string;
};

export type BuyerProspectRuleOptions = {
  taxonomy_category_ids: string[];
  taxonomy_categories: BuyerProspectTaxonomyCategory[];
  source_collection_jobs: BuyerProspectSourceCollectionJob[];
  operators: BuyerProspectOperator[];
};

export type BuyerProspectRule = {
  id: string;
  department_id: string;
  owner_operator_id: string;
  owner: CandidatePoolOwner;
  name: string;
  status: BuyerProspectRuleStatus;
  version: number;
  current_policy_id: string;
  current_policy_version: number;
  category_ids: string[];
  follower_min: number | null;
  follower_max: number | null;
  buyer_lead_tiers: BuyerLeadTier[];
  source_collection_job_id: string;
  recent_collection_window: BuyerProspectRecentCollectionWindow;
  prospect_owner_filter: BuyerProspectOwnerFilter;
  prospect_owner_operator_id: string | null;
  exclude_contacted: boolean;
  latest_run: CandidatePoolRun | null;
  created_at: string;
  updated_at: string;
};

export type BuyerProspectRulePage = {
  items: BuyerProspectRule[];
  next_cursor: string | null;
};

export type BuyerProspectRuleCreateRequest = {
  name: string;
  owner_operator_id?: string;
  category_ids: string[];
  follower_min: number | null;
  follower_max: number | null;
  buyer_lead_tiers: BuyerLeadTier[];
  source_collection_job_id: string;
  recent_collection_window: BuyerProspectRecentCollectionWindow;
  prospect_owner_filter: BuyerProspectOwnerFilter;
  prospect_owner_operator_id?: string;
  exclude_contacted: boolean;
};

export type BuyerProspectRuleUpdateRequest = Omit<
  BuyerProspectRuleCreateRequest,
  "owner_operator_id" | "prospect_owner_operator_id"
> & {
  expected_pool_version: number;
  owner_operator_id: string;
  prospect_owner_operator_id?: string;
};

export type BuyerProspectRuleLifecycleRequest = {
  expected_pool_version: number;
  status: BuyerProspectRuleStatus;
};

export type BuyerProspectRole = CandidatePoolRole;
