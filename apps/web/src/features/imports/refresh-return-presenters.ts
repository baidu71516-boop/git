import type {
  RefreshReturnExpectedStatus,
  RefreshReturnReason,
  RefreshReturnRowEvidence,
} from "./types";

export type RefreshReturnPresentation = {
  label: string;
  tone: "default" | "success" | "warning" | "danger" | "processing";
  explanation: string | null;
};

const reasonLabels: Record<RefreshReturnReason, string> = {
  ROW_NOT_OWNER_EFFECTIVE: "非本次有效数据行",
  ROW_NOT_UNIQUELY_MATCHED: "未唯一匹配账号",
  ROW_ACTION_STAYS_PENDING: "本行不会推进回流状态",
  QUEUE_ITEM_NOT_FOUND: "未找到对应名单条目",
  QUEUE_ITEM_TERMINAL: "名单条目已有最终结果",
  MISSING_RETURN: "缺少回流数据",
  MULTIPLE_OWNER_ROWS: "多行同时指向同一名单条目",
  ACTION_CHANGE_SUMMARY_MISMATCH: "处理结果与变更摘要不一致",
  ACTION_NOT_FULFILLABLE: "当前处理结果无法核销回流",
  ACQUISITION_CONFIRMATION_REQUIRED: "数据取得时间待确认",
  ACQUISITION_MISSING: "缺少数据取得时间",
  ACQUISITION_NOT_NEWER_THAN_BASELINE: "回流数据时间不晚于名单基准",
  NO_CHANGE_BASELINE_MISSING: "缺少可比较的历史基准",
  EFFECTIVE_CHANGES: "检测到有效变更",
  RELIABLE_NO_CHANGE: "已确认无有效变更",
};

const expectedStatusPresentations: Record<
  RefreshReturnExpectedStatus,
  RefreshReturnPresentation
> = {
  pending: { label: "尚未形成回流结果", tone: "default", explanation: null },
  fulfilled_changed: {
    label: "预计有变更",
    tone: "success",
    explanation: null,
  },
  fulfilled_no_change: {
    label: "预计无变更",
    tone: "success",
    explanation: null,
  },
  stale_return: {
    label: "回流数据已过期",
    tone: "danger",
    explanation:
      "本次数据可以继续参与正常导入，但不能完成对应更新名单条目。需要以后重新取得更新的数据再回流。",
  },
  unresolved: {
    label: "需要进一步确认",
    tone: "warning",
    explanation:
      "本次导入可以继续，但该名单条目不会完成。需要后续提交更可靠的新回流数据。",
  },
  cancelled: {
    label: "已有最终回流结果",
    tone: "default",
    explanation: null,
  },
};

export function refreshReturnReasonLabel(reason: RefreshReturnReason): string {
  return reasonLabels[reason] ?? "需要进一步确认";
}

export function presentRefreshReturnEvidence(
  evidence: RefreshReturnRowEvidence | null | undefined,
): RefreshReturnPresentation | null {
  if (!evidence) return null;
  if (evidence.outcome === "pending") {
    return {
      label: "尚未形成回流结果",
      tone: "default",
      explanation: null,
    };
  }
  if (evidence.outcome === "outside_queue") {
    return {
      label: "不属于当前更新名单",
      tone: "default",
      explanation: null,
    };
  }
  if (evidence.outcome === "terminal_item") {
    return {
      label: "已有最终回流结果",
      tone: "default",
      explanation: null,
    };
  }
  if (evidence.expected_status) {
    return expectedStatusPresentations[evidence.expected_status];
  }
  return {
    label: "需要进一步确认",
    tone: "warning",
    explanation: null,
  };
}
