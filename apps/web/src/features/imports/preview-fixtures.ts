import type {
  CollectionJobPublic,
  ImportJobFilePublic,
  ImportJobPublic,
  ImportJobStatus,
} from "./types";

export type BulkPreviewScenarioKey =
  | "no_job"
  | "no_files"
  | "mixed_files"
  | "failed"
  | "preview_stale"
  | "preview_ready"
  | "confirm_queued"
  | "importing"
  | "completed";

export type BulkPreviewScenario = {
  key: BulkPreviewScenarioKey;
  label: string;
  job: ImportJobPublic | null;
  collection: CollectionJobPublic | null;
  files: ImportJobFilePublic[];
};

const collection: CollectionJobPublic = {
  id: "10000000-0000-0000-0000-000000000001",
  name: "夏季达人合作名单采集",
  industry: "美妆个护",
  subdirection: "防晒与身体护理",
  purpose: "补充品牌商务触达名单",
  target_action: "商务邮件触达",
  follower_min: 10_000,
  follower_max: 800_000,
  target_count: 500,
  department_id: "20000000-0000-0000-0000-000000000001",
  owner_operator_id: "30000000-0000-0000-0000-000000000001",
  source_type: "manual_huitun_export",
  status: "active",
  notes: null,
  screening_rules: {
    schema_version: 1,
    platforms: ["xiaohongshu"],
    source_tags_exact_any: [],
  },
  screening_rules_revision: 1,
  created_at: "2026-08-13T01:00:00Z",
  updated_at: "2026-08-13T01:00:00Z",
};

function createJob(status: ImportJobStatus = "draft"): ImportJobPublic {
  const previewRevision = status === "draft" ? 0 : 1;
  return {
    id: "40000000-0000-0000-0000-000000000001",
    collection_job_id: collection.id,
    department_id: collection.department_id,
    operator_id: collection.owner_operator_id,
    stored_file_id: null,
    original_filename: null,
    mime_type: null,
    file_size: null,
    sha256: null,
    source_type: "manual_huitun_export",
    status,
    detected_fields: null,
    field_mapping: null,
    mapping_hash: null,
    preview_revision: previewRevision,
    preview_summary: null,
    result: null,
    total_rows: 0,
    valid_rows: 0,
    warning_rows: 0,
    error_rows: 0,
    created_rows: 0,
    updated_rows: 0,
    no_change_rows: 0,
    skipped_rows: 0,
    manual_review_rows: 0,
    confirmed_revision: null,
    confirmed_at: null,
    completed_at: status === "completed" ? "2026-08-13T04:30:00Z" : null,
    error_code: status === "failed" ? "INTERNAL_ERROR" : null,
    error_message: status === "failed" ? "internal preview fixture" : null,
    created_at: "2026-08-13T01:05:00Z",
    updated_at: "2026-08-13T03:00:00Z",
  };
}

function createFile(
  position: number,
  overrides: Partial<ImportJobFilePublic>,
): ImportJobFilePublic {
  return {
    id: `50000000-0000-0000-0000-${String(position).padStart(12, "0")}`,
    import_job_id: "40000000-0000-0000-0000-000000000001",
    stored_file_id: `60000000-0000-0000-0000-${String(position).padStart(12, "0")}`,
    position,
    original_filename: `达人数据_${position}.csv`,
    declared_mime: "text/csv",
    status: "ready",
    source_acquired_at: "2026-08-12T09:30:00+08:00",
    source_acquired_at_origin: "user_confirmed",
    source_acquired_at_confirmation_required: false,
    detected_type: "csv",
    detected_mime: "text/csv",
    file_size: 384_000 + position * 8_192,
    sha256: String(position).repeat(64).slice(0, 64),
    detected_fields: ["达人名称", "达人官方地址", "粉丝数"],
    field_mapping: {
      达人名称: "nickname",
      达人官方地址: "profile_url",
      粉丝数: "followers_count",
    },
    mapping_hash: "preview-mapping-hash",
    raw_rows: 128 + position * 17,
    warning_rows: 0,
    error_rows: 0,
    error_code: null,
    error_message: null,
    parse_task_id: null,
    parse_attempts: 1,
    parse_started_at: "2026-08-13T01:10:00Z",
    parse_completed_at: "2026-08-13T01:11:00Z",
    excluded_at: null,
    created_at: "2026-08-13T01:08:00Z",
    updated_at: "2026-08-13T01:11:00Z",
    ...overrides,
  };
}

const mixedFiles: ImportJobFilePublic[] = [
  createFile(1, {
    original_filename: "灰豚_美妆达人_第一批.csv",
    status: "uploaded",
    raw_rows: 0,
    detected_fields: null,
    field_mapping: null,
    mapping_hash: null,
    parse_attempts: 0,
    parse_started_at: null,
    parse_completed_at: null,
  }),
  createFile(2, {
    original_filename: "灰豚_防晒达人_第二批.xlsx",
    declared_mime:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    detected_type: "xlsx",
    detected_mime:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    status: "parsing",
    raw_rows: 0,
    detected_fields: null,
    field_mapping: null,
    mapping_hash: null,
    parse_completed_at: null,
  }),
  createFile(3, {
    original_filename: "补充名单_字段待确认.csv",
    status: "mapping_required",
    field_mapping: null,
    mapping_hash: null,
    source_acquired_at_confirmation_required: true,
    source_acquired_at_origin: "server_default",
  }),
  createFile(4, {
    original_filename: "达人合作名单_已就绪.csv",
    status: "ready",
    raw_rows: 0,
    warning_rows: 2,
  }),
  createFile(5, {
    original_filename: "历史导出_格式异常.csv",
    status: "failed",
    error_code: "INVALID_HEADER",
    error_message: "preview-only raw worker error",
    detected_fields: null,
    field_mapping: null,
    mapping_hash: null,
    raw_rows: 0,
    parse_completed_at: null,
  }),
  createFile(6, {
    original_filename: "重复文件_已排除.csv",
    status: "excluded",
    excluded_at: "2026-08-13T02:00:00Z",
  }),
];

const terminalStatuses: ReadonlyArray<{
  key: Exclude<BulkPreviewScenarioKey, "no_job" | "no_files" | "mixed_files">;
  label: string;
  status: ImportJobStatus;
}> = [
  { key: "failed", label: "任务失败", status: "failed" },
  {
    key: "preview_stale",
    label: "数据预览需重建",
    status: "preview_stale",
  },
  {
    key: "preview_ready",
    label: "数据预览已生成",
    status: "preview_ready",
  },
  {
    key: "confirm_queued",
    label: "确认任务排队中",
    status: "confirm_queued",
  },
  { key: "importing", label: "导入中", status: "importing" },
  { key: "completed", label: "导入完成", status: "completed" },
];

export function createBulkPreviewScenarios(): BulkPreviewScenario[] {
  return [
    {
      key: "no_job",
      label: "无批量任务",
      job: null,
      collection: null,
      files: [],
    },
    {
      key: "no_files",
      label: "任务无文件",
      job: createJob(),
      collection,
      files: [],
    },
    {
      key: "mixed_files",
      label: "多文件处理中",
      job: createJob(),
      collection,
      files: mixedFiles,
    },
    ...terminalStatuses.map(({ key, label, status }) => ({
      key,
      label,
      job: createJob(status),
      collection,
      files: mixedFiles.filter((file) => file.status === "ready"),
    })),
  ];
}
