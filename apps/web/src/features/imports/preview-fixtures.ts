import type {
  ChangeSummary,
  CollectionJobPublic,
  ImportJobFilePublic,
  ImportJobPublic,
  ImportJobStatus,
  ImportRowCategory,
  ImportRowPublic,
  ImportRowsPage,
  ScreeningEvaluation,
  ScreeningResult,
  UnifiedPreviewSummary,
} from "./types";
import type { RefreshQueueDetail } from "@/features/refresh-queues/types";

export type BulkPreviewScenarioKey =
  | "no_job"
  | "no_files"
  | "mixed_files"
  | "screening_rules_readonly"
  | "screening_rules_conflict"
  | "failed"
  | "preview_stale"
  | "preview_ready"
  | "confirm_queued"
  | "importing"
  | "completed"
  | "refresh_preview_ready"
  | "refresh_completed";

export type BulkPreviewScenario = {
  key: BulkPreviewScenarioKey;
  label: string;
  job: ImportJobPublic | null;
  collection: CollectionJobPublic | null;
  files: ImportJobFilePublic[];
  rows: ImportRowPublic[];
  refreshQueueDetail?: RefreshQueueDetail | null;
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
    source_tags_exact_any: ["美妆"],
  },
  screening_rules_revision: 2,
  created_at: "2026-08-13T01:00:00Z",
  updated_at: "2026-08-13T01:00:00Z",
};

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
    mapping_hash: "a".repeat(64),
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
    raw_rows: 51,
    warning_rows: 3,
    error_rows: 1,
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

const previewFiles = mixedFiles.filter((file) => file.status === "ready");
const previewFile = previewFiles[0];

if (!previewFile) {
  throw new Error("UI Preview fixture requires one ready file");
}

const previewSummary: UnifiedPreviewSummary = {
  schema_version: 1,
  context_hash: "b".repeat(64),
  batch_plan_hash: "c".repeat(64),
  manifest: [
    {
      import_job_file_id: previewFile.id,
      position: previewFile.position,
      stored_file_sha256: previewFile.sha256,
      mapping_hash: previewFile.mapping_hash,
      status: previewFile.status,
      included: true,
      source_acquired_at: previewFile.source_acquired_at,
      source_acquired_at_origin: previewFile.source_acquired_at_origin,
      source_acquired_at_confirmation_required:
        previewFile.source_acquired_at_confirmation_required,
    },
  ],
  screening_rule_snapshot: {
    rules: collection.screening_rules,
    rule_revision: collection.screening_rules_revision,
    follower_min: collection.follower_min,
    follower_max: collection.follower_max,
    rule_hash: "d".repeat(64),
  },
  file_count: 1,
  occurrence_count: 1,
  excluded_file_count: 0,
  raw_rows: 51,
  unique_rows: 50,
  internal_duplicate_rows: 1,
  existing_rows: 47,
  new_rows: 1,
  changed_rows: 46,
  no_change_rows: 1,
  created_rows: 1,
  updated_rows: 46,
  skipped_rows: 1,
  error_rows: 1,
  manual_review_rows: 1,
  warning_rows: 3,
  possible_duplicate_contact_rows: 1,
  screened_rows: 49,
  screening_match_rows: 46,
  screening_not_match_rows: 1,
  screening_unknown_rows: 2,
};

const emptyChangeSummary = (): ChangeSummary => ({
  effective_changes: [],
  ignored_changes: [],
  freshness_changes: [],
  historical_observations: [],
});

function screening(result: ScreeningResult): ScreeningEvaluation {
  const observed =
    result === "MATCH" ? 206_600 : result === "NOT_MATCH" ? 980_000 : null;
  return {
    result,
    rule_schema_version: 1,
    rule_revision: collection.screening_rules_revision,
    rule_hash: "d".repeat(64),
    evidence: [
      {
        rule: "followers",
        result,
        configured: {
          min: collection.follower_min,
          max: collection.follower_max,
        },
        observed,
        reason:
          result === "MATCH"
            ? "FOLLOWERS_MATCH"
            : result === "NOT_MATCH"
              ? "FOLLOWERS_NOT_MATCH"
              : "FOLLOWERS_MISSING_OR_INVALID",
      },
    ],
  };
}

function createRow(
  rowNumber: number,
  overrides: Partial<ImportRowPublic>,
): ImportRowPublic {
  return {
    id: `70000000-0000-0000-0000-${String(rowNumber).padStart(12, "0")}`,
    import_job_id: "40000000-0000-0000-0000-000000000001",
    import_job_file_id: previewFile.id,
    row_number: rowNumber,
    raw_data: {},
    normalized_data: { display_name: `美妆达人 ${rowNumber}` },
    matched_influencer_id: "80000000-0000-0000-0000-000000000001",
    matched_platform_account_id: "90000000-0000-0000-0000-000000000001",
    match_type: "platform_account_id",
    action: "update",
    merge_plan: {
      screening: screening("MATCH"),
      change_summary: {
        ...emptyChangeSummary(),
        effective_changes: [
          {
            scope: "current_metrics",
            field: "followers_count",
            before: 180_000 + rowNumber,
            incoming: 190_000 + rowNumber,
            after: 190_000 + rowNumber,
            effect: "apply",
            reason: "CURRENT_METRICS_APPLIED",
            added: [],
            removed: [],
          },
        ],
      },
    },
    warnings: [],
    errors: [],
    preview_revision: 1,
    plan_hash: rowNumber.toString(16).padStart(64, "0"),
    committed_action: null,
    committed_at: null,
    ...overrides,
  };
}

const changedRow = createRow(2, {
  normalized_data: { display_name: "夏日防晒小美" },
  merge_plan: {
    screening: screening("MATCH"),
    change_summary: {
      effective_changes: [
        {
          scope: "current_metrics",
          field: "followers_count",
          before: 182_000,
          incoming: 206_600,
          after: 206_600,
          effect: "apply",
          reason: "CURRENT_METRICS_APPLIED",
          added: [],
          removed: [],
        },
        {
          scope: "account",
          field: "source_tags",
          before: null,
          incoming: null,
          after: null,
          effect: "apply",
          reason: "ACCOUNT_FIELD_APPLIED",
          added: ["生活方式"],
          removed: ["泛美妆"],
        },
        {
          scope: "contact",
          contact_type: "email",
          operation: "create",
          validation_status: "valid",
          count: 1,
          possible_duplicate: true,
        },
      ],
      ignored_changes: [
        {
          scope: "source_state",
          field: "bio",
          before: "防晒与身体护理",
          incoming: "历史简介",
          after: "防晒与身体护理",
          effect: "ignore",
          reason: "SOURCE_VALUE_RETAINED",
          added: [],
          removed: [],
        },
      ],
      freshness_changes: [
        {
          scope: "freshness",
          field: "source_acquired_at",
          before: "2026-08-01T09:30:00+08:00",
          incoming: "2026-08-12T09:30:00+08:00",
          after: "2026-08-12T09:30:00+08:00",
          effect: "observe",
          reason: "OBSERVATION_ADVANCED",
          added: [],
          removed: [],
        },
      ],
      historical_observations: [
        {
          scope: "metric_snapshot",
          field: "metrics",
          before: null,
          incoming: { followers_count: 206_600 },
          after: null,
          effect: "history",
          reason: "METRIC_SNAPSHOT_CREATED",
          added: [],
          removed: [],
        },
      ],
    },
  },
  warnings: [
    {
      code: "POSSIBLE_DUPLICATE_CONTACT",
      message: "Contact may already belong to another account",
      field: "email",
    },
  ],
});

const manualReviewRow = createRow(3, {
  normalized_data: { display_name: "身份冲突达人" },
  matched_influencer_id: null,
  matched_platform_account_id: null,
  match_type: "none",
  action: "manual_review",
  merge_plan: {
    screening: screening("UNKNOWN"),
    change_summary: emptyChangeSummary(),
  },
  warnings: [
    {
      code: "BATCH_DATABASE_IDENTITY_CONFLICT",
      message: "raw identity conflict detail",
    },
  ],
});

const errorRow = createRow(4, {
  normalized_data: { display_name: "格式异常达人" },
  matched_influencer_id: null,
  matched_platform_account_id: null,
  match_type: "none",
  action: "error",
  merge_plan: { change_summary: emptyChangeSummary() },
  errors: [
    {
      code: "MISSING_PLATFORM_IDENTITY",
      message: "raw parser error detail",
      field: "profile_url",
    },
  ],
});

const createPreviewRow = createRow(5, {
  normalized_data: { display_name: "超大体量达人" },
  matched_influencer_id: null,
  matched_platform_account_id: null,
  match_type: "none",
  action: "create",
  merge_plan: {
    screening: screening("NOT_MATCH"),
    change_summary: {
      ...emptyChangeSummary(),
      effective_changes: [
        {
          scope: "account",
          field: "display_name",
          before: null,
          incoming: "超大体量达人",
          after: "超大体量达人",
          effect: "apply",
          reason: "ACCOUNT_CREATED",
          added: [],
          removed: [],
        },
      ],
    },
  },
});

const noChangeRow = createRow(6, {
  normalized_data: { display_name: "资料未变化达人" },
  action: "no_change",
  merge_plan: {
    screening: screening("UNKNOWN"),
    change_summary: {
      ...emptyChangeSummary(),
      freshness_changes: [
        {
          scope: "freshness",
          field: "source_acquired_at",
          before: "2026-08-11T09:30:00+08:00",
          incoming: "2026-08-12T09:30:00+08:00",
          after: "2026-08-12T09:30:00+08:00",
          effect: "observe",
          reason: "OBSERVATION_ADVANCED",
          added: [],
          removed: [],
        },
      ],
    },
  },
});

const duplicateRow = createRow(7, {
  normalized_data: { display_name: "批次重复达人" },
  matched_influencer_id: null,
  matched_platform_account_id: null,
  match_type: "none",
  action: "skip",
  merge_plan: {
    batch_duplicate: {
      owner_file_position: previewFile.position,
      owner_row_number: changedRow.row_number,
    },
    change_summary: emptyChangeSummary(),
  },
  warnings: [
    {
      code: "BATCH_DUPLICATE",
      message: "Row duplicates another included row in this import batch",
    },
  ],
});

const previewRows: ImportRowPublic[] = [
  changedRow,
  manualReviewRow,
  errorRow,
  createPreviewRow,
  noChangeRow,
  duplicateRow,
  ...Array.from({ length: 45 }, (_, index) => createRow(index + 8, {})),
];

const refreshQueueId = "a0000000-0000-0000-0000-000000000001";
const refreshPreviewSummary: UnifiedPreviewSummary = {
  ...previewSummary,
  refresh_return: {
    schema_version: 1,
    row_count: 51,
    queue_item_count: 50,
    matched_row_count: 44,
    outside_queue_row_count: 3,
    pending_row_count: 4,
    queue_items_with_return_count: 45,
    queue_items_without_return_count: 5,
    expected_fulfilled_changed_count: 20,
    expected_fulfilled_no_change_count: 18,
    expected_stale_return_count: 3,
    expected_unresolved_count: 3,
    unchanged_terminal_item_count: 1,
    conflict_item_count: 1,
    missing_queue_item_ids: [
      "a1000000-0000-0000-0000-000000000001",
      "a1000000-0000-0000-0000-000000000002",
      "a1000000-0000-0000-0000-000000000003",
      "a1000000-0000-0000-0000-000000000004",
      "a1000000-0000-0000-0000-000000000005",
    ],
  },
};

function withRefreshEvidence(
  row: ImportRowPublic,
  evidence: NonNullable<
    NonNullable<ImportRowPublic["merge_plan"]>["refresh_return"]
  >,
): ImportRowPublic {
  return {
    ...row,
    merge_plan: {
      ...row.merge_plan,
      refresh_return: evidence,
    },
  };
}

const refreshPreviewRows = previewRows.map((row, index) => {
  const locator = {
    import_job_file_id: row.import_job_file_id,
    file_position: 1,
    row_number: row.row_number,
    import_row_id: row.id,
  };
  if (index === 0) {
    return withRefreshEvidence(row, {
      locator,
      outcome: "expected_fulfillment",
      queue_item_id: "a1000000-0000-0000-0000-000000000010",
      expected_status: "stale_return",
      reason: "ACQUISITION_NOT_NEWER_THAN_BASELINE",
      is_last_return_claimant: true,
    });
  }
  if (index === 1) {
    return withRefreshEvidence(row, {
      locator,
      outcome: "expected_fulfillment",
      queue_item_id: "a1000000-0000-0000-0000-000000000011",
      expected_status: "unresolved",
      reason: "MULTIPLE_OWNER_ROWS",
      is_last_return_claimant: true,
    });
  }
  return withRefreshEvidence(row, {
    locator,
    outcome: "expected_fulfillment",
    queue_item_id: `a1000000-0000-0000-${String(index).padStart(12, "0")}`,
    expected_status:
      row.action === "no_change" ? "fulfilled_no_change" : "fulfilled_changed",
    reason:
      row.action === "no_change" ? "RELIABLE_NO_CHANGE" : "EFFECTIVE_CHANGES",
    is_last_return_claimant: true,
  });
});

const refreshQueueDetail: RefreshQueueDetail = {
  queue: {
    id: refreshQueueId,
    department_id: collection.department_id,
    created_by_operator_id: collection.owner_operator_id,
    status: "exported",
    as_of: "2026-08-01T01:00:00Z",
    requested_limit: 50,
    today_total_limit: 100,
    refresh_limit: 50,
    policy_version: 1,
    criteria_snapshot: {},
    created_at: "2026-08-13T00:30:00Z",
    updated_at: "2026-08-13T04:30:00Z",
    exported_at: "2026-08-13T01:00:00Z",
    completed_at: null,
    cancelled_at: null,
  },
  summary: {
    requested: 50,
    selected: 50,
    unique_influencers: 50,
    freshness_breakdown: { stale: 50 },
    priority_breakdown: { "3": 50 },
    status_breakdown: {
      fulfilled_changed: 20,
      fulfilled_no_change: 18,
      stale_return: 3,
      unresolved: 3,
      pending: 6,
    },
  },
};

const completedRefreshQueueDetail: RefreshQueueDetail = {
  ...refreshQueueDetail,
  queue: {
    ...refreshQueueDetail.queue,
    status: "completed",
    completed_at: "2026-08-13T04:30:00Z",
  },
  summary: {
    ...refreshQueueDetail.summary,
    status_breakdown: {
      fulfilled_changed: 26,
      fulfilled_no_change: 24,
    },
  },
};

function createJob(
  status: ImportJobStatus = "draft",
  overrides: Partial<ImportJobPublic> = {},
): ImportJobPublic {
  const previewRevision = status === "draft" ? 0 : 1;
  return {
    id: "40000000-0000-0000-0000-000000000001",
    collection_job_id: collection.id,
    refresh_queue_id: null,
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
    completed_at: null,
    error_code: status === "failed" ? "INTERNAL_ERROR" : null,
    error_message:
      status === "failed" ? "internal development preview fixture" : null,
    created_at: "2026-08-13T01:05:00Z",
    updated_at: "2026-08-13T03:00:00Z",
    ...overrides,
  };
}

function createUnifiedPreviewJob(
  status: ImportJobStatus,
  overrides: Partial<ImportJobPublic> = {},
): ImportJobPublic {
  const confirmed =
    status === "confirm_queued" ||
    status === "importing" ||
    status === "completed";
  const completed = status === "completed";
  return createJob(status, {
    preview_summary: previewSummary,
    total_rows: previewSummary.raw_rows,
    valid_rows: previewSummary.raw_rows - previewSummary.error_rows,
    warning_rows: previewSummary.warning_rows,
    error_rows: previewSummary.error_rows,
    created_rows: completed ? previewSummary.created_rows : 0,
    updated_rows: completed ? previewSummary.updated_rows : 0,
    no_change_rows: completed ? previewSummary.no_change_rows : 0,
    skipped_rows: completed ? previewSummary.skipped_rows : 0,
    manual_review_rows: completed ? previewSummary.manual_review_rows : 0,
    confirmed_revision: confirmed ? 1 : null,
    confirmed_at: confirmed ? "2026-08-13T04:00:00Z" : null,
    completed_at: completed ? "2026-08-13T04:30:00Z" : null,
    result: completed
      ? {
          import_job_id: "40000000-0000-0000-0000-000000000001",
          preview_revision: 1,
          created_rows: previewSummary.created_rows,
          updated_rows: previewSummary.updated_rows,
          no_change_rows: previewSummary.no_change_rows,
          skipped_rows: previewSummary.skipped_rows,
          error_rows: previewSummary.error_rows,
          manual_review_rows: previewSummary.manual_review_rows,
        }
      : null,
    ...overrides,
  });
}

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
      rows: [],
    },
    {
      key: "no_files",
      label: "任务无文件",
      job: createJob(),
      collection,
      files: [],
      rows: [],
    },
    {
      key: "mixed_files",
      label: "多文件处理中",
      job: createJob(),
      collection,
      files: mixedFiles,
      rows: [],
    },
    {
      key: "screening_rules_conflict",
      label: "筛选规则冲突",
      job: createJob(),
      collection,
      files: [],
      rows: [],
    },
    {
      key: "screening_rules_readonly",
      label: "筛选规则只读",
      job: createJob(),
      collection,
      files: [],
      rows: [],
    },
    ...terminalStatuses.map(({ key, label, status }) => ({
      key,
      label,
      job: createUnifiedPreviewJob(status),
      collection,
      files: previewFiles,
      rows: previewRows,
    })),
    {
      key: "refresh_preview_ready",
      label: "更新回流预览",
      job: createUnifiedPreviewJob("preview_ready", {
        refresh_queue_id: refreshQueueId,
        preview_summary: refreshPreviewSummary,
      }),
      collection,
      files: previewFiles,
      rows: refreshPreviewRows,
      refreshQueueDetail,
    },
    {
      key: "refresh_completed",
      label: "更新回流完成",
      job: createUnifiedPreviewJob("completed", {
        refresh_queue_id: refreshQueueId,
        preview_summary: refreshPreviewSummary,
      }),
      collection,
      files: previewFiles,
      rows: refreshPreviewRows,
      refreshQueueDetail: completedRefreshQueueDetail,
    },
  ];
}

function isBatchDuplicate(row: ImportRowPublic): boolean {
  const duplicate = row.merge_plan?.batch_duplicate;
  return typeof duplicate === "object" && duplicate !== null;
}

function rowMatchesCategory(
  row: ImportRowPublic,
  category: ImportRowCategory,
): boolean {
  if (category === "all") return true;
  if (category === "attention") {
    return (
      row.action === "error" ||
      row.action === "manual_review" ||
      row.warnings.length > 0
    );
  }
  if (category === "error") return row.action === "error";
  if (category === "manual_review") return row.action === "manual_review";
  if (category === "warning") return row.warnings.length > 0;
  if (category === "changed") return row.action === "update";
  if (category === "new") return row.action === "create";
  if (category === "no_change") return row.action === "no_change";
  return isBatchDuplicate(row);
}

export function getBulkPreviewRowsPage(
  scenario: BulkPreviewScenario,
  category: ImportRowCategory,
  offset: number,
  limit: number,
): ImportRowsPage | null {
  if (!scenario.job?.preview_summary) return null;
  const safeOffset = Math.max(0, Math.trunc(offset));
  const safeLimit = Math.min(200, Math.max(1, Math.trunc(limit)));
  const matching = scenario.rows.filter((row) =>
    rowMatchesCategory(row, category),
  );
  return {
    items: matching.slice(safeOffset, safeOffset + safeLimit),
    total: matching.length,
    offset: safeOffset,
    limit: safeLimit,
  };
}
