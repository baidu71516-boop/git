export type CollectionJobStatus =
  "draft" | "active" | "completed" | "cancelled";

export type ImportSourceType = "manual_huitun_export" | "generic_csv";

export type ImportJobStatus =
  | "draft"
  | "uploaded"
  | "parsing"
  | "mapping_required"
  | "previewing"
  | "preview_ready"
  | "preview_stale"
  | "confirm_queued"
  | "importing"
  | "completed"
  | "failed"
  | "cancelled";

export type ImportJobFileStatus =
  "uploaded" | "parsing" | "mapping_required" | "ready" | "failed" | "excluded";

export type SourceAcquiredAtOrigin =
  "server_default" | "user_confirmed" | "legacy_unknown";

export type StoredFileType = "csv" | "xlsx";

export type JsonPrimitive = string | number | boolean | null;

export type JsonValue =
  JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export type ImportRowAction =
  "create" | "update" | "no_change" | "skip" | "error" | "manual_review";

export type ImportRowCategory =
  | "all"
  | "attention"
  | "error"
  | "manual_review"
  | "warning"
  | "changed"
  | "new"
  | "no_change"
  | "duplicate";

export type RefreshReturnReason =
  | "ROW_NOT_OWNER_EFFECTIVE"
  | "ROW_NOT_UNIQUELY_MATCHED"
  | "ROW_ACTION_STAYS_PENDING"
  | "QUEUE_ITEM_NOT_FOUND"
  | "QUEUE_ITEM_TERMINAL"
  | "MISSING_RETURN"
  | "MULTIPLE_OWNER_ROWS"
  | "ACTION_CHANGE_SUMMARY_MISMATCH"
  | "ACTION_NOT_FULFILLABLE"
  | "ACQUISITION_CONFIRMATION_REQUIRED"
  | "ACQUISITION_MISSING"
  | "ACQUISITION_NOT_NEWER_THAN_BASELINE"
  | "NO_CHANGE_BASELINE_MISSING"
  | "EFFECTIVE_CHANGES"
  | "RELIABLE_NO_CHANGE";

export type RefreshReturnRowOutcome =
  "pending" | "outside_queue" | "expected_fulfillment" | "terminal_item";

export type RefreshReturnExpectedStatus =
  | "pending"
  | "fulfilled_changed"
  | "fulfilled_no_change"
  | "stale_return"
  | "unresolved"
  | "cancelled";

export type RefreshReturnRowEvidence = {
  locator: {
    import_row_id: string;
    import_job_file_id: string;
    file_position: number;
    row_number: number;
  };
  outcome: RefreshReturnRowOutcome;
  queue_item_id: string | null;
  expected_status: RefreshReturnExpectedStatus | null;
  reason: RefreshReturnReason;
  is_last_return_claimant: boolean;
};

export type RefreshReturnPreviewSummary = {
  schema_version: 1;
  row_count: number;
  queue_item_count: number;
  matched_row_count: number;
  outside_queue_row_count: number;
  pending_row_count: number;
  queue_items_with_return_count: number;
  queue_items_without_return_count: number;
  expected_fulfilled_changed_count: number;
  expected_fulfilled_no_change_count: number;
  expected_stale_return_count: number;
  expected_unresolved_count: number;
  unchanged_terminal_item_count: number;
  conflict_item_count: number;
};

export type RefreshReturnPreviewSummaryDocument =
  RefreshReturnPreviewSummary & {
    missing_queue_item_ids: string[];
  };

export type RefreshReturnConfirmResult = RefreshReturnPreviewSummary & {
  claimed_item_count: number;
  queue_completed: boolean;
};

export type ImportMatchType =
  | "platform_account_id"
  | "external_source_id"
  | "normalized_profile_url"
  | "none";

export type ScreeningResult = "MATCH" | "NOT_MATCH" | "UNKNOWN";

export type ScreeningRuleEvidence = {
  rule: "platforms" | "source_tags_exact_any" | "followers";
  result: ScreeningResult;
  configured: JsonValue;
  observed: JsonValue;
  reason: string;
};

export type ScreeningEvaluation = {
  result: ScreeningResult;
  rule_schema_version: 1;
  rule_revision: number;
  rule_hash: string;
  evidence: ScreeningRuleEvidence[];
};

export type ChangeScope =
  | "account"
  | "source_state"
  | "source_identity"
  | "current_metrics"
  | "metric_snapshot"
  | "freshness";

export type ChangeEffect = "apply" | "ignore" | "observe" | "history";

export type ContactType = "email" | "wechat" | "phone" | "other";

export type ContactOperation =
  "create" | "deactivate" | "mark_possible_duplicate" | "observe";

export type ContactValidationStatus = "valid" | "invalid" | "unverified";

export type FieldChange = {
  scope: ChangeScope;
  field: string;
  before: JsonValue;
  incoming: JsonValue;
  after: JsonValue;
  effect: ChangeEffect;
  reason: string;
  added: string[];
  removed: string[];
};

export type ContactChange = {
  scope: "contact";
  contact_type: ContactType;
  operation: ContactOperation;
  validation_status: ContactValidationStatus | null;
  count: number;
  possible_duplicate: boolean;
};

export type ChangeItem = FieldChange | ContactChange;

export type ChangeSummary = {
  effective_changes: ChangeItem[];
  ignored_changes: ChangeItem[];
  freshness_changes: ChangeItem[];
  historical_observations: ChangeItem[];
};

export type ScreeningRulesV1 = {
  schema_version: 1;
  platforms: "xiaohongshu"[];
  source_tags_exact_any: string[];
};

export type ScreeningRuleSnapshot = {
  rules: ScreeningRulesV1;
  rule_revision: number;
  follower_min: number | null;
  follower_max: number | null;
  rule_hash: string;
};

export type PreviewFileManifestEntry = {
  import_job_file_id: string;
  position: number;
  stored_file_sha256: string;
  mapping_hash: string | null;
  status: ImportJobFileStatus;
  included: boolean;
  source_acquired_at: string | null;
  source_acquired_at_origin: SourceAcquiredAtOrigin;
  source_acquired_at_confirmation_required: boolean;
};

export type UnifiedPreviewSummary = {
  schema_version: 1;
  context_hash: string;
  batch_plan_hash: string;
  manifest: PreviewFileManifestEntry[];
  screening_rule_snapshot: ScreeningRuleSnapshot;
  file_count: number;
  occurrence_count: number;
  excluded_file_count: number;
  raw_rows: number;
  unique_rows: number;
  internal_duplicate_rows: number;
  existing_rows: number;
  new_rows: number;
  changed_rows: number;
  no_change_rows: number;
  created_rows: number;
  updated_rows: number;
  skipped_rows: number;
  error_rows: number;
  manual_review_rows: number;
  warning_rows: number;
  possible_duplicate_contact_rows: number;
  screened_rows: number;
  screening_match_rows: number;
  screening_not_match_rows: number;
  screening_unknown_rows: number;
  refresh_return?: RefreshReturnPreviewSummaryDocument;
};

export type ImportConfirmResult = {
  import_job_id: string;
  preview_revision: number;
  created_rows: number;
  updated_rows: number;
  no_change_rows: number;
  skipped_rows: number;
  error_rows: number;
  manual_review_rows: number;
  refresh_return?: RefreshReturnConfirmResult;
};

export type PreviewSummary = UnifiedPreviewSummary;

export type ConfirmResult = ImportConfirmResult;

export type CollectionJobCreateInput = {
  name: string;
  industry: string;
  subdirection?: string | null;
  purpose: string;
  target_action: string;
  follower_min?: number | null;
  follower_max?: number | null;
  target_count: number;
  source_type?: ImportSourceType;
  notes?: string | null;
};

export type CollectionJobScreeningRulesUpdatePayload = {
  screening_rules: ScreeningRulesV1;
  follower_min: number | null;
  follower_max: number | null;
  expected_revision: number;
};

export type UpdateCollectionJobScreeningRulesInput = {
  collectionJobId: string;
  importJobId: string;
  payload: CollectionJobScreeningRulesUpdatePayload;
};

export type CollectionJobPublic = {
  id: string;
  name: string;
  industry: string;
  subdirection: string | null;
  purpose: string;
  target_action: string;
  follower_min: number | null;
  follower_max: number | null;
  target_count: number;
  department_id: string;
  owner_operator_id: string;
  source_type: ImportSourceType;
  status: CollectionJobStatus;
  notes: string | null;
  screening_rules: ScreeningRulesV1;
  screening_rules_revision: number;
  created_at: string;
  updated_at: string;
};

export type ImportJobPublic = {
  id: string;
  collection_job_id: string;
  refresh_queue_id: string | null;
  department_id: string;
  operator_id: string;
  stored_file_id: string | null;
  original_filename: string | null;
  mime_type: string | null;
  file_size: number | null;
  sha256: string | null;
  source_type: ImportSourceType;
  status: ImportJobStatus;
  detected_fields: string[] | null;
  field_mapping: Record<string, string> | null;
  mapping_hash: string | null;
  preview_revision: number;
  preview_summary: UnifiedPreviewSummary | null;
  result: ImportConfirmResult | null;
  total_rows: number;
  valid_rows: number;
  warning_rows: number;
  error_rows: number;
  created_rows: number;
  updated_rows: number;
  no_change_rows: number;
  skipped_rows: number;
  manual_review_rows: number;
  confirmed_revision: number | null;
  confirmed_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type ImportJobFilePublic = {
  id: string;
  import_job_id: string;
  stored_file_id: string;
  position: number;
  original_filename: string;
  declared_mime: string | null;
  status: ImportJobFileStatus;
  source_acquired_at: string | null;
  source_acquired_at_origin: SourceAcquiredAtOrigin;
  source_acquired_at_confirmation_required: boolean;
  detected_type: StoredFileType;
  detected_mime: string;
  file_size: number;
  sha256: string;
  detected_fields: string[] | null;
  field_mapping: Record<string, string> | null;
  mapping_hash: string | null;
  raw_rows: number;
  warning_rows: number;
  error_rows: number;
  error_code: string | null;
  error_message: string | null;
  parse_task_id: string | null;
  parse_attempts: number;
  parse_started_at: string | null;
  parse_completed_at: string | null;
  excluded_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ImportJobFileUploadResult = {
  file: ImportJobFilePublic;
  idempotent: boolean;
};

export type ImportRowIssue = Record<string, JsonValue>;

export type ImportRowMergePlan = {
  screening?: ScreeningEvaluation;
  change_summary?: ChangeSummary;
  preview_context_hash?: string;
  refresh_return?: RefreshReturnRowEvidence;
  [key: string]: unknown;
};

export type ImportRowPublic = {
  id: string;
  import_job_id: string;
  import_job_file_id: string;
  row_number: number;
  raw_data: Record<string, JsonValue>;
  normalized_data: Record<string, JsonValue> | null;
  matched_influencer_id: string | null;
  matched_platform_account_id: string | null;
  match_type: ImportMatchType;
  action: ImportRowAction;
  merge_plan: ImportRowMergePlan | null;
  warnings: ImportRowIssue[];
  errors: ImportRowIssue[];
  preview_revision: number;
  plan_hash: string;
  committed_action: ImportRowAction | null;
  committed_at: string | null;
};

export type ImportRowsPage = {
  items: ImportRowPublic[];
  total: number;
  offset: number;
  limit: number;
};

export type ImportDispatchResult = {
  import_job_id: string;
  status: ImportJobStatus;
  preview_revision: number;
  task_id: string | null;
  idempotent: boolean;
};

export type CreateBulkImportJobInput = {
  collection_job_id: string;
  refresh_queue_id?: string | null;
};

export type UploadBulkImportFileInput = {
  importJobId: string;
  clientFileId: string;
  file: File;
  sourceAcquiredAt?: string;
};

export type UpdateBulkImportFileSourceAcquiredAtInput = {
  importJobId: string;
  importJobFileId: string;
  sourceAcquiredAt: string;
};

export type UpdateBulkImportFileMappingInput = {
  importJobId: string;
  importJobFileId: string;
  mapping: Record<string, string>;
};

export type BulkImportFileIdentity = {
  importJobId: string;
  importJobFileId: string;
};

export type RequestBulkPreviewInput = {
  importJobId: string;
  rebuild: boolean;
};

export type ListBulkImportRowsInput = {
  importJobId: string;
  category: ImportRowCategory;
  offset: number;
  limit: number;
};

export type BulkImportRowsQueryInput = ListBulkImportRowsInput & {
  previewRevision: number;
};

export type ConfirmBulkImportInput = {
  importJobId: string;
  previewRevision: number;
};
