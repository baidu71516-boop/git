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

export type ScreeningRulesV1 = {
  schema_version: 1;
  platforms: "xiaohongshu"[];
  source_tags_exact_any: string[];
};

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
  preview_summary: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
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

export type ImportDispatchResult = {
  import_job_id: string;
  status: ImportJobStatus;
  preview_revision: number;
  task_id: string | null;
  idempotent: boolean;
};

export type CreateBulkImportJobInput = {
  collection_job_id: string;
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
