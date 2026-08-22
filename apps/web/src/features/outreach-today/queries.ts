import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { ApiClientError } from "@/lib/api/client";

import {
  fetchTodayCampaigns,
  fetchTodayOperators,
  fetchTodayPage,
  fetchTodayTracks,
  todayQueryKey,
} from "./api";
import type { TodayFilters } from "./types";

function retryRead(failureCount: number, error: Error): boolean {
  if (error instanceof ApiClientError && error.status < 500) return false;
  return failureCount < 1;
}

export function useToday(filters: TodayFilters) {
  return useInfiniteQuery({
    queryKey: todayQueryKey(filters),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => fetchTodayPage(filters, pageParam),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    retry: retryRead,
  });
}

export function useTodayOperators() {
  return useQuery({
    queryKey: ["outreach-tasks", "today", "options", "operators"],
    queryFn: fetchTodayOperators,
    retry: retryRead,
  });
}

export function useTodayCampaigns() {
  return useQuery({
    queryKey: ["outreach-tasks", "today", "options", "campaigns"],
    queryFn: fetchTodayCampaigns,
    retry: retryRead,
  });
}

export function useTodayTracks() {
  return useQuery({
    queryKey: ["outreach-tasks", "today", "options", "tracks"],
    queryFn: fetchTodayTracks,
    retry: retryRead,
  });
}
