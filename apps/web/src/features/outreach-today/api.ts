import { ApiClientError, apiRequest } from "@/lib/api/client";

import type {
  CampaignOption,
  OperatorOption,
  TodayFilters,
  TodayPage,
} from "./types";

const TODAY_QUERY_PARAMETERS = [
  "work_kind",
  "channel",
  "campaign_id",
  "owner_operator_id",
  "track",
  "followers_min",
  "followers_max",
  "contact_filter",
  "priority",
  "cursor",
  "limit",
] as const;

export const TODAY_PAGE_LIMIT = 50;

export function normalizeTodayFilters(filters: TodayFilters): TodayFilters {
  const normalized: TodayFilters = { work_kind: filters.work_kind };
  for (const name of TODAY_QUERY_PARAMETERS) {
    if (name === "work_kind" || name === "cursor" || name === "limit") continue;
    const value = filters[name as keyof TodayFilters];
    if (value !== undefined && value !== "") {
      normalized[name as keyof TodayFilters] = value as never;
    }
  }
  return normalized;
}

export function buildTodayPath(
  filters: TodayFilters,
  cursor?: string | null,
): string {
  const search = new URLSearchParams();
  const normalized = normalizeTodayFilters(filters);
  search.set("work_kind", normalized.work_kind);
  for (const name of TODAY_QUERY_PARAMETERS) {
    if (name === "work_kind" || name === "cursor") continue;
    if (name === "limit") {
      search.set("limit", String(TODAY_PAGE_LIMIT));
      continue;
    }
    const value = normalized[name as keyof TodayFilters];
    if (value !== undefined && value !== "") search.set(name, String(value));
  }
  if (cursor) search.set("cursor", cursor);
  return `/outreach-tasks/today?${search.toString()}`;
}

export async function fetchTodayPage(
  filters: TodayFilters,
  cursor?: string | null,
): Promise<TodayPage> {
  const response = await apiRequest<TodayPage>(buildTodayPath(filters, cursor));
  if (response.data === null) {
    throw new ApiClientError("今日触达响应缺少数据", 200, "INVALID_RESPONSE");
  }
  return response.data;
}

export async function fetchTodayOperators(): Promise<OperatorOption[]> {
  const response = await apiRequest<OperatorOption[]>("/operators");
  if (response.data === null) {
    throw new ApiClientError("负责人选项响应缺少数据", 200, "INVALID_RESPONSE");
  }
  return response.data.filter((operator) => operator.status === "active");
}

export async function fetchTodayCampaigns(): Promise<CampaignOption[]> {
  const items: CampaignOption[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < 100; page += 1) {
    const params = new URLSearchParams({ limit: "100" });
    if (cursor) params.set("cursor", cursor);
    const response = await apiRequest<{
      items: CampaignOption[];
      next_cursor: string | null;
    }>(`/campaigns?${params.toString()}`);
    if (response.data === null) {
      throw new ApiClientError(
        "Campaign 选项响应缺少数据",
        200,
        "INVALID_RESPONSE",
      );
    }
    items.push(...response.data.items);
    cursor = response.data.next_cursor;
    if (!cursor) break;
  }
  return items;
}

export async function fetchTodayTracks(): Promise<string[]> {
  const response = await apiRequest<{ tags: string[] }>(
    "/influencers/filter-options",
  );
  if (response.data === null) {
    throw new ApiClientError("赛道选项响应缺少数据", 200, "INVALID_RESPONSE");
  }
  return response.data.tags;
}

export const todayQueryKey = (filters: TodayFilters) =>
  ["outreach-tasks", "today", normalizeTodayFilters(filters)] as const;
