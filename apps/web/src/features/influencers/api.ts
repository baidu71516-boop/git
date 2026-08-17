import { ApiClientError, apiRequest } from "@/lib/api/client";

import type {
  InfluencerDetail,
  InfluencerFilterOptions,
  InfluencerListPage,
  InfluencerListQueryParams,
  MetricSnapshotPage,
} from "./types";

export const INFLUENCER_QUERY_PARAMETERS = [
  "q",
  "tag",
  "followers_min",
  "followers_max",
  "owner_operator_id",
  "crm_stage",
  "contact_filter",
  "notes_7d_filter",
  "notes_60d_filter",
  "freshness_status",
  "requires_refresh",
  "last_huitun_observed_before",
  "last_huitun_observed_after",
  "page",
  "page_size",
] as const satisfies readonly (keyof InfluencerListQueryParams)[];

export function normalizeInfluencerListQuery(
  query: InfluencerListQueryParams,
): InfluencerListQueryParams {
  const normalized: InfluencerListQueryParams = {};
  for (const name of INFLUENCER_QUERY_PARAMETERS) {
    const rawValue = query[name];
    if (rawValue === undefined || rawValue === "") continue;
    const value = name === "q" || name === "tag" ? rawValue.trim() : rawValue;
    if (value !== "") Object.assign(normalized, { [name]: value });
  }
  return normalized;
}

export function buildInfluencerListPath(
  query: InfluencerListQueryParams,
): string {
  const search = new URLSearchParams();
  const normalized = normalizeInfluencerListQuery(query);
  for (const name of INFLUENCER_QUERY_PARAMETERS) {
    const value = normalized[name];
    if (value !== undefined) search.set(name, value);
  }
  const queryString = search.toString();
  return queryString ? `/influencers?${queryString}` : "/influencers";
}

export function influencerListQueryKey(query: InfluencerListQueryParams) {
  return ["influencers", "list", normalizeInfluencerListQuery(query)] as const;
}

export async function fetchInfluencerList(
  query: InfluencerListQueryParams,
): Promise<InfluencerListPage> {
  const response = await apiRequest<InfluencerListPage>(
    buildInfluencerListPath(query),
  );
  if (response.data === null) {
    throw new ApiClientError("达人列表响应缺少数据", 200, "INVALID_RESPONSE");
  }
  return response.data;
}

export async function fetchInfluencerFilterOptions(): Promise<InfluencerFilterOptions> {
  const response = await apiRequest<InfluencerFilterOptions>(
    "/influencers/filter-options",
  );
  if (response.data === null) {
    throw new ApiClientError("筛选选项响应缺少数据", 200, "INVALID_RESPONSE");
  }
  return response.data;
}

export async function fetchInfluencerDetail(
  influencerId: string,
): Promise<InfluencerDetail> {
  const response = await apiRequest<InfluencerDetail>(
    `/influencers/${encodeURIComponent(influencerId)}`,
  );
  if (response.data === null) {
    throw new ApiClientError("达人详情响应缺少数据", 200, "INVALID_RESPONSE");
  }
  return response.data;
}

export async function fetchMetricSnapshots(
  influencerId: string,
  page: number,
  pageSize: number,
): Promise<MetricSnapshotPage> {
  const search = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  const response = await apiRequest<MetricSnapshotPage>(
    `/influencers/${encodeURIComponent(influencerId)}/metric-snapshots?${search.toString()}`,
  );
  if (response.data === null) {
    throw new ApiClientError("指标历史响应缺少数据", 200, "INVALID_RESPONSE");
  }
  return response.data;
}
