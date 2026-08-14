import type { BadgeProps } from "antd";

import { formatBulkDateTime } from "@/features/imports/formatters";
import { ApiClientError } from "@/lib/api/client";

import type {
  RefreshFreshnessStatus,
  RefreshPriorityReason,
  RefreshQueueItemStatus,
  RefreshQueueStatus,
} from "./types";

type StatusPresentation = {
  label: string;
  color: BadgeProps["status"];
};

const queueStatusPresentations: Record<RefreshQueueStatus, StatusPresentation> =
  {
    open: { label: "待导出", color: "warning" },
    exported: { label: "已导出", color: "processing" },
    completed: { label: "已完成", color: "success" },
    cancelled: { label: "已取消", color: "default" },
  };

const itemStatusPresentations: Record<
  RefreshQueueItemStatus,
  StatusPresentation
> = {
  pending: { label: "待回流", color: "warning" },
  fulfilled_changed: { label: "已回流 · 有变更", color: "success" },
  fulfilled_no_change: { label: "已回流 · 无变更", color: "success" },
  stale_return: { label: "回流数据已过期", color: "error" },
  unresolved: { label: "无法确认", color: "error" },
  cancelled: { label: "已取消", color: "default" },
};

const freshnessLabels: Record<RefreshFreshnessStatus, string> = {
  fresh: "新鲜",
  aging: "较旧",
  stale: "陈旧",
  very_stale: "严重陈旧",
  unknown: "未知",
};

const priorityLabels: Record<RefreshPriorityReason, string> = {
  FRESHNESS_UNKNOWN: "数据时效未知",
  VERY_STALE: "严重陈旧",
  STALE: "陈旧",
  AGING: "较旧",
  FOLLOWERS_MISSING: "缺少粉丝数",
};

const priorityFreshness: Record<RefreshPriorityReason, RefreshFreshnessStatus> =
  {
    FRESHNESS_UNKNOWN: "unknown",
    VERY_STALE: "very_stale",
    STALE: "stale",
    AGING: "aging",
    FOLLOWERS_MISSING: "fresh",
  };

export function formatRefreshDateTime(value: string | null): string {
  return formatBulkDateTime(value);
}

export function queueStatusPresentation(
  status: RefreshQueueStatus,
): StatusPresentation {
  return queueStatusPresentations[status];
}

export function itemStatusPresentation(
  status: RefreshQueueItemStatus,
): StatusPresentation {
  return itemStatusPresentations[status];
}

export function freshnessLabel(status: RefreshFreshnessStatus): string {
  return freshnessLabels[status];
}

export function priorityReasonLabel(reason: RefreshPriorityReason): string {
  return priorityLabels[reason];
}

export function snapshotFreshnessFromReasons(
  reasons: readonly RefreshPriorityReason[],
): RefreshFreshnessStatus {
  return reasons.length > 0 ? priorityFreshness[reasons[0]] : "unknown";
}

export function platformLabel(platform: string): string {
  return platform === "xiaohongshu" ? "小红书" : platform;
}

export function isAmbiguousMutationError(error: unknown): boolean {
  return !(error instanceof ApiClientError) || error.status >= 500;
}

export function mutationErrorMessage(error: unknown, fallback: string): string {
  if (!(error instanceof ApiClientError)) return fallback;
  const messages: Record<string, string> = {
    OPERATOR_REQUIRED: "请先选择当前操作人。",
    PERMISSION_DENIED: "当前账号没有执行此操作的权限。",
    REFRESH_QUEUE_NOT_EXPORTABLE: "当前状态不允许导出 CSV。",
    REFRESH_QUEUE_NOT_CANCELLABLE: "当前状态不允许取消名单。",
    REFRESH_QUEUE_NOT_FOUND: "数据更新名单不存在或当前账号无权访问。",
    VALIDATION_ERROR: "提交内容不符合要求，请检查后重试。",
  };
  return error.code ? (messages[error.code] ?? fallback) : fallback;
}

export function filenameFromContentDisposition(
  contentDisposition: string | null,
  fallback: string,
): string {
  if (!contentDisposition) return fallback;
  const encodedMatch = contentDisposition.match(
    /filename\*\s*=\s*UTF-8''([^;]+)/i,
  );
  const plainMatch = contentDisposition.match(
    /filename\s*=\s*(?:"([^"]+)"|([^;]+))/i,
  );
  const raw = encodedMatch?.[1] ?? plainMatch?.[1] ?? plainMatch?.[2];
  if (!raw) return fallback;
  try {
    const decoded = encodedMatch ? decodeURIComponent(raw.trim()) : raw.trim();
    const safe = decoded.replaceAll("\\", "/").split("/").at(-1)?.trim();
    return safe || fallback;
  } catch {
    return fallback;
  }
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
