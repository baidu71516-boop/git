import type {
  CandidatePool,
  CandidatePoolRun,
  TargetingPolicyDefinition,
} from "./types";
import { ApiClientError } from "@/lib/api/client";
import { formatBulkDateTime } from "@/features/imports/formatters";

export type Tone = "default" | "success" | "warning" | "processing" | "danger";
const poolKinds: Record<string, string> = {
  POTENTIAL_SELLER: "潜在卖家",
  POTENTIAL_BUYER: "潜在买家",
};
const poolStatuses: Record<string, { label: string; tone: Tone }> = {
  ACTIVE: { label: "启用中", tone: "success" },
  ARCHIVED: { label: "已归档", tone: "default" },
};
const runStatuses: Record<string, { label: string; tone: Tone }> = {
  PENDING: { label: "等待生成", tone: "default" },
  RUNNING: { label: "正在生成", tone: "processing" },
  COMPLETED: { label: "已完成", tone: "success" },
  FAILED: { label: "生成失败", tone: "danger" },
};
const reasons: Record<string, string> = {
  ACTIVITY_MISSING: "活跃数据缺失",
  AMBIGUOUS_CLASSIFICATION: "分类信息不明确",
  CATEGORY_ALIGNED: "分类方向一致",
  CATEGORY_MISMATCH: "分类方向不匹配",
  CLASSIFICATION_STALE: "分类信息已过期",
  COLLECTION_CATEGORY_UNMAPPED: "采集分类未映射",
  COLLECTION_CONTEXT_MISSING: "采集上下文缺失",
  CONTACT_AVAILABLE: "联系方式可用",
  CONTACT_EVIDENCE_REDACTED: "联系方式依据已隐藏",
  CONTENT_ACTIVITY_INCOMPLETE: "公开作品检测结果不完整",
  CONTENT_ACTIVITY_MATCH: "公开作品断更时长符合条件",
  CONTENT_ACTIVITY_MISSING: "尚无公开作品检测记录",
  CONTENT_ACTIVITY_NO_PUBLIC_CONTENT: "当前无公开作品，无法判断断更时长",
  CONTENT_ACTIVITY_RECENT: "公开作品更新时间仍在条件范围内",
  CONTENT_ACTIVITY_STALE: "公开作品检测已过期",
  CONTENT_ACTIVITY_UNKNOWN: "公开作品活跃度未知",
  CONTENT_ACTIVITY_UNTRUSTED: "公开作品检测未获可信结论",
  CREATOR_CATEGORY_UNMAPPED: "账号分类未映射",
  CREATOR_CLASSIFICATION_MISSING: "账号分类缺失",
  EMAIL_AVAILABLE: "邮箱可用",
  FOLLOWERS_IN_RANGE: "粉丝数符合范围",
  FOLLOWERS_MISSING: "粉丝数缺失",
  FOLLOWERS_OUT_OF_RANGE: "粉丝数不在范围内",
  FRESHNESS_MATCH: "数据新鲜度符合",
  FRESHNESS_MISSING: "数据新鲜度缺失",
  FRESHNESS_NOT_MATCH: "数据新鲜度不符合",
  NO_COMPARISON_RULE: "缺少分类比较规则",
  NO_CURRENT_CONTACT: "缺少当前联系方式",
  NO_CURRENT_EMAIL: "缺少当前邮箱",
  NOTES_60D_IN_RANGE: "60 天笔记数符合范围",
  NOTES_60D_OUT_OF_RANGE: "60 天笔记数不在范围内",
  NOTES_7D_IN_RANGE: "7 天笔记数符合范围",
  NOTES_7D_OUT_OF_RANGE: "7 天笔记数不在范围内",
  PLATFORM_MATCH: "平台符合",
  PLATFORM_MISSING: "平台信息缺失",
  PLATFORM_NOT_MATCH: "平台不符合",
  SOURCE_MATCH: "数据来源符合",
  SOURCE_MISSING: "数据来源缺失",
  SOURCE_NOT_MATCH: "数据来源不符合",
  TRACK_MATCH: "标签符合",
  TRACK_MISSING: "标签缺失",
  TRACK_NOT_MATCH: "标签不符合",
};
export function candidatePoolKindLabel(kind: CandidatePool["kind"]) {
  return poolKinds[kind] ?? "未知类型";
}
export function candidatePoolStatus(status: CandidatePool["status"]) {
  return poolStatuses[status] ?? { label: "未知状态", tone: "default" as Tone };
}
export function candidateRunStatus(status: CandidatePoolRun["status"]) {
  return runStatuses[status] ?? { label: "未知状态", tone: "default" as Tone };
}
export function candidateResultPresentation(result: string) {
  return result === "MATCH"
    ? { label: "符合条件", tone: "success" as Tone }
    : result === "UNKNOWN"
      ? { label: "信息不足", tone: "warning" as Tone }
      : { label: "未知结果", tone: "default" as Tone };
}
export function candidateReasonLabel(code: string) {
  return reasons[code] ?? "未知判断原因";
}
export function candidateDateTime(value: string) {
  return formatBulkDateTime(value);
}
export function mutationErrorMessage(error: unknown, fallback: string) {
  if (!(error instanceof ApiClientError)) return fallback;
  const labels: Record<string, string> = {
    CANDIDATE_POOL_INACTIVE: "当前候选池已归档，无法生成新的候选结果。",
    TARGETING_POLICY_REQUIRED: "当前候选池没有可用规则，暂时无法生成候选结果。",
    TARGETING_POLICY_INVALID: "当前规则不可用，请联系管理员检查规则配置。",
    POLICY_KIND_MISMATCH: "当前规则与候选池类型不匹配，请联系管理员。",
    BUYER_TAXONOMY_UNREVIEWED:
      "当前买家分类规则尚未完成审核，暂时无法生成候选结果。",
    TARGETING_POLICY_MISSING: "当前规则不可用，候选结果生成失败。",
    TARGETING_MATERIALIZATION_FAILED:
      "候选结果生成失败，请稍后查看或联系管理员。",
    CAMPAIGN_CLOSED: "该拓客活动已关闭，请选择其他活动。",
    CANDIDATE_POOL_RUN_NOT_COMPLETED: "该候选结果尚未生成完成，请刷新后再试。",
    CANDIDATE_POOL_RUN_MEMBER_NOT_FOUND:
      "所选候选结果已发生变化，请重新选择后再提交。",
    CANDIDATE_POOL_RUN_MEMBER_AMBIGUOUS:
      "同一达人只能选择一个平台账号，请重新选择。",
    OPERATOR_REQUIRED: "请先选择当前操作人。",
    PERMISSION_DENIED: "没有执行此操作的权限。",
  };
  return error.code ? (labels[error.code] ?? fallback) : fallback;
}
export function candidateRunFailureMessage(code: string | null) {
  const labels: Record<string, string> = {
    TARGETING_POLICY_MISSING: "当前规则不可用，候选结果生成失败。",
    TARGETING_POLICY_INVALID: "当前规则无效，候选结果生成失败。",
    CANDIDATE_POOL_INACTIVE: "候选池已归档，候选结果生成失败。",
    TARGETING_MATERIALIZATION_FAILED:
      "候选结果生成失败，请稍后查看或联系管理员。",
  };
  return code
    ? (labels[code] ?? "候选结果生成失败，请稍后查看或联系管理员。")
    : "候选结果生成失败，请稍后查看或联系管理员。";
}
export function isAmbiguousMutation(error: unknown) {
  return (
    !(error instanceof ApiClientError) ||
    error.status === 408 ||
    error.status >= 500 ||
    error.code === "INVALID_RESPONSE"
  );
}
export function policyType(definition: TargetingPolicyDefinition) {
  return definition.schema_version === 1 &&
    (definition.policy_type === "SELLER_V1" ||
      definition.policy_type === "BUYER_V1")
    ? definition.policy_type
    : null;
}
export function rangeLabel(
  value?: { minimum?: number | null; maximum?: number | null } | null,
) {
  if (!value) return null;
  if (value.minimum != null && value.maximum != null)
    return `${value.minimum} - ${value.maximum}`;
  return value.minimum != null
    ? `不少于 ${value.minimum}`
    : value.maximum != null
      ? `不超过 ${value.maximum}`
      : null;
}
