import { useQuery } from "@tanstack/react-query";

import { ApiClientError } from "@/lib/api/client";

import {
  fetchInfluencerFilterOptions,
  fetchInfluencerList,
  influencerListQueryKey,
} from "./api";
import type { InfluencerListQueryParams } from "./types";

function retryRead(failureCount: number, error: Error): boolean {
  if (error instanceof ApiClientError && error.status < 500) return false;
  return failureCount < 1;
}

export function useInfluencerList(
  query: InfluencerListQueryParams,
  enabled = true,
) {
  return useQuery({
    queryKey: influencerListQueryKey(query),
    queryFn: () => fetchInfluencerList(query),
    enabled,
    retry: retryRead,
  });
}

export function useInfluencerFilterOptions() {
  return useQuery({
    queryKey: ["influencers", "filter-options"],
    queryFn: fetchInfluencerFilterOptions,
    retry: retryRead,
  });
}
