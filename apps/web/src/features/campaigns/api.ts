import { ApiClientError, apiRequest } from "@/lib/api/client";

import type {
  Campaign,
  CampaignMember,
  CampaignMemberAddItem,
  CampaignMemberBulkAddResult,
  CampaignMemberPage,
  CampaignEditableValues,
  CampaignOperator,
  CampaignPage,
  CampaignScope,
  CreateCampaignInput,
  UpdateCampaignInput,
} from "./types";

export const CAMPAIGN_PAGE_LIMIT = 50;
export const CAMPAIGN_MEMBER_PAGE_LIMIT = 50;

function requireData<T>(data: T | null, description: string): T {
  if (data === null) {
    throw new ApiClientError(
      `${description}响应缺少数据`,
      200,
      "INVALID_RESPONSE",
    );
  }
  return data;
}

function scopeHeaders(scope?: CampaignScope): HeadersInit | undefined {
  return scope?.departmentId
    ? { "X-Department-ID": scope.departmentId }
    : undefined;
}

function encoded(value: string): string {
  return encodeURIComponent(value);
}

export function campaignListPath(cursor?: string | null): string {
  const params = new URLSearchParams({ limit: String(CAMPAIGN_PAGE_LIMIT) });
  if (cursor) params.set("cursor", cursor);
  return `/campaigns?${params.toString()}`;
}

export function campaignDetailPath(campaignId: string): string {
  return `/campaigns/${encoded(campaignId)}`;
}

export function campaignMemberListPath(
  campaignId: string,
  cursor?: string | null,
): string {
  const params = new URLSearchParams({
    limit: String(CAMPAIGN_MEMBER_PAGE_LIMIT),
  });
  if (cursor) params.set("cursor", cursor);
  return `${campaignDetailPath(campaignId)}/members?${params.toString()}`;
}

export function campaignMemberBulkAddPath(campaignId: string): string {
  return `${campaignDetailPath(campaignId)}/members/bulk-add`;
}

export function campaignMemberRemovePath(
  campaignId: string,
  memberId: string,
): string {
  return `${campaignDetailPath(campaignId)}/members/${encoded(memberId)}/remove`;
}

export async function fetchCampaignPage(
  cursor?: string | null,
  scope?: CampaignScope,
): Promise<CampaignPage> {
  const response = await apiRequest<CampaignPage>(campaignListPath(cursor), {
    headers: scopeHeaders(scope),
  });
  return requireData(response.data, "拓客活动列表");
}

export async function fetchCampaign(
  campaignId: string,
  scope?: CampaignScope,
): Promise<Campaign> {
  const response = await apiRequest<Campaign>(campaignDetailPath(campaignId), {
    headers: scopeHeaders(scope),
  });
  return requireData(response.data, "拓客活动");
}

export async function fetchCampaignMemberPage(
  campaignId: string,
  cursor?: string | null,
  scope?: CampaignScope,
): Promise<CampaignMemberPage> {
  const response = await apiRequest<CampaignMemberPage>(
    campaignMemberListPath(campaignId, cursor),
    { headers: scopeHeaders(scope) },
  );
  return requireData(response.data, "活动达人列表");
}

export async function bulkAddCampaignMembers(
  campaignId: string,
  members: CampaignMemberAddItem[],
  idempotencyKey: string,
  scope?: CampaignScope,
): Promise<CampaignMemberBulkAddResult> {
  const response = await apiRequest<CampaignMemberBulkAddResult>(
    campaignMemberBulkAddPath(campaignId),
    {
      method: "POST",
      headers: { ...scopeHeaders(scope), "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ members }),
    },
  );
  return requireData(response.data, "添加活动达人");
}

export async function removeCampaignMember(
  campaignId: string,
  member: Pick<CampaignMember, "id" | "version">,
  scope?: CampaignScope,
): Promise<CampaignMember> {
  const response = await apiRequest<CampaignMember>(
    campaignMemberRemovePath(campaignId, member.id),
    {
      method: "POST",
      headers: scopeHeaders(scope),
      body: JSON.stringify({ expected_version: member.version }),
    },
  );
  return requireData(response.data, "移出活动达人");
}

export async function fetchCampaignOperators(
  scope?: CampaignScope,
): Promise<CampaignOperator[]> {
  const response = await apiRequest<CampaignOperator[]>("/operators", {
    headers: scopeHeaders(scope),
  });
  return requireData(response.data, "负责人选项").filter(
    (operator) => operator.status === "active",
  );
}

export async function createCampaign(
  input: CreateCampaignInput,
  idempotencyKey: string,
  scope?: CampaignScope,
): Promise<Campaign> {
  const response = await apiRequest<Campaign>("/campaigns", {
    method: "POST",
    headers: { ...scopeHeaders(scope), "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(input),
  });
  return requireData(response.data, "拓客活动创建");
}

export function buildCampaignUpdateInput(
  baseline: Campaign,
  values: CampaignEditableValues,
): UpdateCampaignInput {
  return {
    name: values.name,
    owner_operator_id: values.owner_operator_id,
    review_mode: baseline.review_mode,
    review_count: baseline.review_count,
    duplicate_history_policy: baseline.duplicate_history_policy,
    duplicate_window_days: baseline.duplicate_window_days,
    expected_version: baseline.version,
  };
}

export async function updateCampaign(
  campaignId: string,
  input: UpdateCampaignInput,
  scope?: CampaignScope,
): Promise<Campaign> {
  const response = await apiRequest<Campaign>(campaignDetailPath(campaignId), {
    method: "PUT",
    headers: scopeHeaders(scope),
    body: JSON.stringify(input),
  });
  return requireData(response.data, "拓客活动更新");
}
