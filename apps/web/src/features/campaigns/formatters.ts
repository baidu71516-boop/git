import type { Campaign, CampaignOwner } from "./types";

import { formatBulkDateTime } from "@/features/imports/formatters";
import { ApiClientError } from "@/lib/api/client";

export type CampaignStatusPresentation = {
  label: string;
  tone: "default" | "success" | "warning" | "processing";
};

const statusPresentations: Record<string, CampaignStatusPresentation> = {
  DRAFT: { label: "草稿", tone: "default" },
  ACTIVE: { label: "进行中", tone: "processing" },
  PAUSED: { label: "已暂停", tone: "warning" },
  CLOSED: { label: "已关闭", tone: "default" },
};

export function campaignStatusPresentation(
  status: Campaign["status"],
): CampaignStatusPresentation {
  return statusPresentations[status] ?? { label: "未知状态", tone: "default" };
}

export function formatCampaignDateTime(value: string): string {
  return formatBulkDateTime(value);
}

export function isDisabledCampaignOwner(owner: CampaignOwner): boolean {
  return owner.status === "disabled";
}

export function campaignMutationErrorMessage(
  error: unknown,
  fallback: string,
): string {
  if (!(error instanceof ApiClientError)) return fallback;
  const messages: Record<string, string> = {
    OPERATOR_REQUIRED: "请先选择当前操作人。",
    PERMISSION_DENIED: "没有执行此操作的权限。",
    IDEMPOTENCY_KEY_REUSED: "本次创建请求标识已被使用，请重新发起创建。",
    VALIDATION_ERROR: "提交内容有误，请检查后重试。",
    CAMPAIGN_CLOSED: "该拓客活动已关闭，无法继续编辑。",
  };
  if (error.code && messages[error.code]) return messages[error.code];
  if (error.status === 403) return "没有执行此操作的权限。";
  return fallback;
}

export function isAmbiguousCampaignMutationError(error: unknown): boolean {
  if (!(error instanceof ApiClientError)) return true;
  return (
    error.code === "INVALID_RESPONSE" ||
    error.status === 408 ||
    error.status >= 500
  );
}
