import { ApiClientError, apiRequest } from "@/lib/api/client";

import type {
  InfluencerFilterOptions,
  InfluencerListPage,
  InfluencerListQueryParams,
} from "./types";

export const INFLUENCER_QUERY_PARAMETERS = [
  "q",
  "tag",
  "followers_min",
  "followers_max",
  "owner_operator_id",
  "crm_stage",
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
    if (value !== "") normalized[name] = value;
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
