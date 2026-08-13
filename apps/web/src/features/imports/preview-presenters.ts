import type {
  ImportRowAction,
  ImportRowCategory,
  ImportRowPublic,
  ScreeningResult,
} from "./types";

export type PreviewPresentationTone =
  "default" | "success" | "warning" | "danger" | "processing";

export type PreviewStatusPresentation = {
  label: string;
  tone: PreviewPresentationTone;
};

export type PreviewIssuePresentation = {
  code: string | null;
  message: string;
  field: string | null;
  severity: "warning" | "error";
};

export type PreviewChangeItemPresentation = {
  kind: "field" | "contact";
  label: string;
  before: string | null;
  incoming: string | null;
  after: string | null;
  added: string[];
  removed: string[];
  message: string | null;
};

export type PreviewChangeBucketKey =
  | "effective_changes"
  | "ignored_changes"
  | "freshness_changes"
  | "historical_observations";

export type PreviewChangeBucketPresentation = {
  key: PreviewChangeBucketKey;
  label: string;
  items: PreviewChangeItemPresentation[];
};

export type PreviewScreeningEvidencePresentation = {
  label: string;
  result: PreviewStatusPresentation;
  configured: string;
  observed: string;
  message: string;
};

export type PreviewScreeningPresentation = PreviewStatusPresentation & {
  result: ScreeningResult;
  evidence: PreviewScreeningEvidencePresentation[];
};

export type PreviewBatchDuplicatePresentation = {
  ownerFilePosition: number | null;
  ownerRowNumber: number;
};

export type PreviewRowPresentation = {
  displayName: string | null;
  action: PreviewStatusPresentation;
  screening: PreviewScreeningPresentation | null;
  batchDuplicate: PreviewBatchDuplicatePresentation | null;
  isBatchDuplicate: boolean;
  changeSummary: PreviewChangeBucketPresentation[];
  changeCount: number;
  hasChanges: boolean;
  issues: PreviewIssuePresentation[];
  issueCount: number;
};

export type PreviewRowPresentationInput = Pick<
  ImportRowPublic,
  "action" | "normalized_data" | "merge_plan" | "warnings" | "errors"
>;

const actionPresentations: Readonly<
  Record<ImportRowAction, PreviewStatusPresentation>
> = {
  create: { label: "新增", tone: "success" },
  update: { label: "更新", tone: "processing" },
  no_change: { label: "无需变更", tone: "default" },
  skip: { label: "跳过", tone: "default" },
  error: { label: "错误", tone: "danger" },
  manual_review: { label: "需人工处理", tone: "warning" },
};

const screeningPresentations: Readonly<
  Record<ScreeningResult, PreviewStatusPresentation>
> = {
  MATCH: { label: "符合条件", tone: "success" },
  NOT_MATCH: { label: "不符合条件", tone: "default" },
  UNKNOWN: { label: "信息不足", tone: "warning" },
};

const categoryLabels: Readonly<Record<ImportRowCategory, string>> = {
  all: "全部",
  attention: "需关注",
  error: "错误",
  manual_review: "人工处理",
  warning: "有警告",
  changed: "有变更",
  new: "新增",
  no_change: "无需变更",
  duplicate: "重复",
};

const changeBuckets: ReadonlyArray<{
  key: PreviewChangeBucketKey;
  label: string;
}> = [
  { key: "effective_changes", label: "本次会更新" },
  { key: "ignored_changes", label: "不会更新" },
  { key: "freshness_changes", label: "数据更新时间变化" },
  { key: "historical_observations", label: "历史观察" },
];

const fieldLabels: Readonly<Record<string, string>> = {
  display_name: "达人名称",
  nickname: "达人名称",
  account_name: "达人名称",
  account_handle: "小红书号",
  profile_url: "达人主页",
  normalized_profile_url: "达人主页",
  external_account_id: "来源账号",
  huitun_score: "灰豚指数",
  gender: "性别",
  region_raw: "地域",
  bio: "简介",
  verification_info: "认证信息",
  email: "联系邮箱",
  phone: "联系电话",
  mobile: "联系电话",
  wechat: "微信联系方式",
  source_updated_at: "来源更新时间",
  source_acquired_at: "数据取得时间",
  is_brand_partner: "品牌合作人",
  mcn_name: "签约 MCN",
  creator_tags: "达人标签",
  source_tags: "达人标签",
  creator_level: "认证类型",
  followers_count: "粉丝数",
  notes_count: "笔记总数",
  likes_collects_total: "赞藏总数",
  commercial_notes_count: "商业笔记总数",
  notes_60d: "近 60 天笔记数",
  viral_rate_60d: "近 60 天爆文率",
  avg_likes_60d: "近 60 天平均点赞",
  avg_collects_60d: "近 60 天平均收藏",
  avg_comments_60d: "近 60 天平均评论",
  avg_shares_60d: "近 60 天平均分享",
  active_fans_raw: "活跃粉丝占比",
  suspicious_fans_raw: "水粉占比",
  fan_gender_raw: "粉丝性别",
  fan_region_raw: "粉丝地域",
  fan_age_raw: "粉丝年龄",
  fan_active_time_raw: "粉丝活跃时间",
  fan_interests_raw: "粉丝关注焦点",
  image_note_price: "图文笔记报价",
  image_cpe: "图文 CPE",
  image_cpm: "图文 CPM",
  video_note_price: "视频笔记报价",
  video_cpe: "视频 CPE",
  video_cpm: "视频 CPM",
  metrics: "指标快照",
  source_data_hash: "来源数据",
  metrics_hash: "指标数据",
  min: "最低",
  max: "最高",
};

const evidenceLabels: Readonly<Record<string, string>> = {
  platforms: "平台",
  source_tags_exact_any: "达人标签",
  followers: "粉丝数",
};

const contactTypeLabels: Readonly<Record<string, string>> = {
  email: "联系邮箱",
  wechat: "微信联系方式",
  phone: "联系电话",
  other: "其他联系方式",
};

const contactOperationLabels: Readonly<Record<string, string>> = {
  create: "新增",
  deactivate: "停用",
  mark_possible_duplicate: "标记疑似重复",
  observe: "记录观察",
};

const safeIssueMessages: Readonly<Record<string, string>> = {
  MISSING_REQUIRED_FIELD: "缺少必填字段。",
  INVALID_PROFILE_URL: "达人主页地址格式无效。",
  IDENTITY_CONFLICT: "账号标识与达人主页信息不一致。",
  MISSING_PLATFORM_IDENTITY: "缺少可识别的达人账号信息。",
  INVALID_SOURCE_TIME: "来源更新时间无法识别，将按保守规则处理。",
  INVALID_BOOLEAN: "字段值格式无法识别。",
  INVALID_EMAIL: "联系邮箱格式无效，不会作为正式联系方式保存。",
  INVALID_INTEGER: "整数指标格式无法识别。",
  INVALID_DECIMAL: "数值指标格式无法识别。",
  INVALID_PERCENT: "百分比指标格式无法识别。",
  INVALID_COMPOSITE_VALUE: "组合指标格式无法完全识别。",
  DUPLICATE_IDENTITY_IN_FILE: "文件内存在重复达人账号，本行将跳过。",
  BATCH_DUPLICATE: "本批次存在重复达人账号，本行将跳过。",
  BATCH_MERGE_PAYLOAD_CONFLICT:
    "同批次达人数据存在无法自动合并的差异，需要人工处理。",
  BATCH_DATABASE_IDENTITY_CONFLICT:
    "同批次达人数据对应到多个现有账号，需要人工处理。",
  DATABASE_IDENTITY_CONFLICT: "现有达人账号信息存在冲突，需要人工处理。",
  POSSIBLE_DUPLICATE_CONTACT: "联系方式可能与其他达人重复，请关注。",
  ACCOUNT_HANDLE_CHANGED: "来源数据包含更新后的小红书号。",
  SAME_TIMESTAMP_CONFLICT: "同一更新时间的数据存在差异，已保留现有值。",
  SAME_TIMESTAMP_METRIC_CONFLICT: "同一更新时间的指标存在差异，已保留现有值。",
  STALE_SOURCE_VALUE_IGNORED: "较早的来源数据不会覆盖现有值。",
  STALE_METRICS_CURRENT_IGNORED: "较早的指标不会覆盖当前指标。",
  SOURCE_TIME_MISSING_FILL_ONLY: "缺少来源更新时间，仅会补充空缺信息。",
  METRIC_SOURCE_TIME_MISSING: "缺少来源更新时间，指标不会覆盖当前值。",
  CONTACT_RETAINED_AS_HISTORY: "较早的联系方式只会保留为历史观察。",
  CELL_TOO_LARGE: "单元格内容过长，已按安全限制处理。",
  FORMULA_IGNORED: "公式内容未作为数据导入。",
  INVALID_ROW_WIDTH: "该行列数与文件表头不一致。",
  WARNINGS_TRUNCATED: "该行还有更多提醒未显示。",
};

const safeIssueCodes = new Set(Object.keys(safeIssueMessages));
const sensitiveFieldPattern =
  /email|phone|mobile|contact|normalized.?value|password|secret|token|credential|cookie|session|api.?key|邮箱|手机|电话|联系方式|密钥|令牌/i;
const hiddenFieldPattern =
  /hash|token|secret|password|credential|cookie|session|api.?key/i;
const emailPattern = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi;
const phoneCandidatePattern = /\+?\d[\d\s().-]{5,}\d/g;
const decimalPattern = /^-?\d+\.\d+$/;
const isoDateTimePattern =
  /^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?$/;
const safeCodePattern = /^[A-Z][A-Z0-9_]{0,79}$/;
const numberFormatter = new Intl.NumberFormat("zh-CN", {
  notation: "compact",
  maximumFractionDigits: 2,
});

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

function screeningResult(value: unknown): ScreeningResult | null {
  return value === "MATCH" || value === "NOT_MATCH" || value === "UNKNOWN"
    ? value
    : null;
}

function truncate(value: string, maximum = 240): string {
  return value.length <= maximum ? value : `${value.slice(0, maximum)}…`;
}

/** Redacts Contact-like content even when it was embedded in a public field. */
function redactEmbeddedContacts(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "—";
  if (decimalPattern.test(trimmed) || isoDateTimePattern.test(trimmed)) {
    return truncate(trimmed);
  }
  const withoutEmails = trimmed.replace(emailPattern, "***");
  return truncate(
    withoutEmails.replace(phoneCandidatePattern, (candidate) => {
      const digitCount = candidate.replace(/\D/g, "").length;
      return digitCount >= 7 && digitCount <= 15 ? "***" : candidate;
    }),
  );
}

function safeFieldLabel(value: unknown): string {
  if (typeof value !== "string") return "其他字段";
  return Object.hasOwn(fieldLabels, value) ? fieldLabels[value] : "其他字段";
}

function formatObject(value: Record<string, unknown>, depth: number): string {
  const entries = Object.entries(value).slice(0, 12);
  if (entries.length === 0) return "—";
  const result = entries.map(([key, item]) => {
    const label = safeFieldLabel(key);
    if (sensitiveFieldPattern.test(key)) return `${label}：***`;
    return `${label}：${formatPreviewValue(item, depth + 1)}`;
  });
  if (Object.keys(value).length > entries.length) result.push("…");
  return truncate(result.join("；"));
}

/** Formats JSON-safe Preview values without ever returning raw objects. */
export function formatPreviewValue(value: unknown, depth = 0): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") {
    if (value === "xiaohongshu") return "小红书";
    if (value === "[REDACTED]" || value === "***") return "***";
    return redactEmbeddedContacts(value);
  }
  if (typeof value === "number") {
    return Number.isFinite(value) ? numberFormatter.format(value) : "—";
  }
  if (typeof value === "boolean") return value ? "是" : "否";
  if (depth >= 2) {
    if (Array.isArray(value)) return `${value.length} 项`;
    return isRecord(value) ? `${Object.keys(value).length} 项` : "—";
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return "—";
    const items = value
      .slice(0, 12)
      .map((item) => formatPreviewValue(item, depth + 1));
    if (value.length > items.length) items.push("…");
    return truncate(items.join("、"));
  }
  if (isRecord(value)) return formatObject(value, depth);
  return "—";
}

export function getPreviewActionPresentation(
  action: unknown,
): PreviewStatusPresentation {
  return typeof action === "string" &&
    Object.hasOwn(actionPresentations, action)
    ? actionPresentations[action as ImportRowAction]
    : { label: "未知", tone: "default" };
}

export function getScreeningPresentation(
  result: unknown,
): PreviewStatusPresentation | null {
  const parsed = screeningResult(result);
  return parsed ? screeningPresentations[parsed] : null;
}

export function getPreviewCategoryLabel(category: unknown): string {
  return typeof category === "string" && Object.hasOwn(categoryLabels, category)
    ? categoryLabels[category as ImportRowCategory]
    : "全部";
}

function evidenceMessage(result: ScreeningResult): string {
  if (result === "MATCH") return "该项符合当前筛选规则。";
  if (result === "NOT_MATCH") return "该项不符合当前筛选规则。";
  return "该项信息不足，无法确定是否符合规则。";
}

export function presentScreening(
  value: unknown,
): PreviewScreeningPresentation | null {
  if (!isRecord(value)) return null;
  const result = screeningResult(value.result);
  if (!result) return null;
  const status = screeningPresentations[result];
  const evidence = Array.isArray(value.evidence)
    ? value.evidence.flatMap<PreviewScreeningEvidencePresentation>((item) => {
        if (!isRecord(item) || typeof item.rule !== "string") return [];
        const rule = item.rule;
        if (!Object.hasOwn(evidenceLabels, rule)) return [];
        const itemResult = screeningResult(item.result);
        if (!itemResult) return [];
        return [
          {
            label: evidenceLabels[rule],
            result: screeningPresentations[itemResult],
            configured: formatPreviewValue(item.configured),
            observed: formatPreviewValue(item.observed),
            message: evidenceMessage(itemResult),
          },
        ];
      })
    : [];
  return { result, ...status, evidence };
}

export function presentBatchDuplicate(
  value: unknown,
): PreviewBatchDuplicatePresentation | null {
  if (!isRecord(value) || !isPositiveInteger(value.owner_row_number)) {
    return null;
  }
  return {
    ownerFilePosition: isPositiveInteger(value.owner_file_position)
      ? value.owner_file_position
      : null,
    ownerRowNumber: value.owner_row_number,
  };
}

function safeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === "string")
    .slice(0, 50)
    .map(redactEmbeddedContacts);
}

function presentContactChange(
  value: Record<string, unknown>,
): PreviewChangeItemPresentation | null {
  const count = value.count;
  if (!isPositiveInteger(count)) return null;
  const contactType =
    typeof value.contact_type === "string" ? value.contact_type : "other";
  const operation =
    typeof value.operation === "string" ? value.operation : "unknown";
  const label = Object.hasOwn(contactTypeLabels, contactType)
    ? contactTypeLabels[contactType]
    : contactTypeLabels.other;
  const operationLabel = Object.hasOwn(contactOperationLabels, operation)
    ? contactOperationLabels[operation]
    : "处理";
  const duplicateNote =
    value.possible_duplicate === true ? "，其中包含疑似重复联系方式" : "";
  return {
    kind: "contact",
    label,
    before: null,
    incoming: null,
    after: null,
    added: [],
    removed: [],
    message: `${operationLabel} ${numberFormatter.format(count)} 条${duplicateNote}`,
  };
}

function presentFieldChange(
  value: Record<string, unknown>,
): PreviewChangeItemPresentation | null {
  if (typeof value.field !== "string" || value.field.length === 0) return null;
  const hidden =
    sensitiveFieldPattern.test(value.field) ||
    hiddenFieldPattern.test(value.field);
  const added = hidden ? [] : safeStringList(value.added);
  const removed = hidden ? [] : safeStringList(value.removed);
  return {
    kind: "field",
    label: safeFieldLabel(value.field),
    before: hidden ? null : formatPreviewValue(value.before),
    incoming: hidden ? null : formatPreviewValue(value.incoming),
    after: hidden ? null : formatPreviewValue(value.after),
    added,
    removed,
    message: hidden ? "内容已发生变化，敏感或技术值不予显示。" : null,
  };
}

function presentChangeItem(
  value: unknown,
): PreviewChangeItemPresentation | null {
  if (!isRecord(value)) return null;
  return value.scope === "contact"
    ? presentContactChange(value)
    : presentFieldChange(value);
}

export function presentChangeSummary(
  value: unknown,
): PreviewChangeBucketPresentation[] {
  const summary = isRecord(value) ? value : {};
  return changeBuckets.map(({ key, label }) => ({
    key,
    label,
    items: Array.isArray(summary[key])
      ? summary[key].flatMap<PreviewChangeItemPresentation>((item) => {
          const presented = presentChangeItem(item);
          return presented ? [presented] : [];
        })
      : [],
  }));
}

function issueField(value: unknown): string | null {
  if (typeof value !== "string" || value.length === 0) return null;
  return Object.hasOwn(fieldLabels, value) ? fieldLabels[value] : null;
}

function presentIssue(
  value: unknown,
  severity: "warning" | "error",
): PreviewIssuePresentation | null {
  if (!isRecord(value)) return null;
  // Read only the public Issue contract. Raw messages are intentionally not
  // forwarded: an unknown worker/database error must not become employee copy.
  const rawCode = typeof value.code === "string" ? value.code : null;
  const code =
    rawCode && safeCodePattern.test(rawCode) && safeIssueCodes.has(rawCode)
      ? rawCode
      : null;
  return {
    code,
    message:
      (code ? safeIssueMessages[code] : null) ??
      (severity === "error"
        ? "该行数据无法处理，请检查源文件。"
        : "该行存在需要关注的问题。"),
    field: issueField(value.field),
    severity,
  };
}

export function presentPreviewIssues(
  warnings: unknown,
  errors: unknown,
): PreviewIssuePresentation[] {
  const warningItems = Array.isArray(warnings) ? warnings : [];
  const errorItems = Array.isArray(errors) ? errors : [];
  return [
    ...errorItems.flatMap<PreviewIssuePresentation>((item) => {
      const presented = presentIssue(item, "error");
      return presented ? [presented] : [];
    }),
    ...warningItems.flatMap<PreviewIssuePresentation>((item) => {
      const presented = presentIssue(item, "warning");
      return presented ? [presented] : [];
    }),
  ];
}

export function presentPreviewRow(
  row: PreviewRowPresentationInput,
): PreviewRowPresentation {
  const normalizedData = isRecord(row.normalized_data)
    ? row.normalized_data
    : null;
  const rawDisplayName = normalizedData?.display_name;
  const displayName =
    typeof rawDisplayName === "string"
      ? redactEmbeddedContacts(rawDisplayName)
      : null;
  const mergePlan = isRecord(row.merge_plan) ? row.merge_plan : {};
  const screening = presentScreening(mergePlan.screening);
  const batchDuplicate = presentBatchDuplicate(mergePlan.batch_duplicate);
  const changeSummary = presentChangeSummary(mergePlan.change_summary);
  const effective = changeSummary.find(
    (bucket) => bucket.key === "effective_changes",
  );
  const changeCount = effective?.items.length ?? 0;
  const issues = presentPreviewIssues(row.warnings, row.errors);
  return {
    displayName,
    action: getPreviewActionPresentation(row.action),
    screening,
    batchDuplicate,
    isBatchDuplicate: batchDuplicate !== null,
    changeSummary,
    changeCount,
    hasChanges: changeCount > 0,
    issues,
    issueCount: issues.length,
  };
}
