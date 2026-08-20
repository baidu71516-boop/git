import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { ApiClientError } from "@/lib/api/client";

import {
  createCampaign,
  fetchCampaign,
  fetchCampaignOperators,
  fetchCampaignPage,
  updateCampaign,
} from "./api";
import type {
  CampaignScope,
  CreateCampaignInput,
  UpdateCampaignInput,
} from "./types";

function retryRead(failureCount: number, error: Error): boolean {
  if (error instanceof ApiClientError && error.status < 500) return false;
  return failureCount < 1;
}

export const campaignQueryKeys = {
  all: ["campaigns"] as const,
  lists: (scope?: CampaignScope) =>
    ["campaigns", "list", scope?.departmentId] as const,
  detail: (campaignId: string, scope?: CampaignScope) =>
    ["campaigns", "detail", campaignId, scope?.departmentId] as const,
  operators: (scope?: CampaignScope) =>
    ["campaigns", "operators", scope?.departmentId] as const,
};

export function useCampaignList(scope?: CampaignScope) {
  return useInfiniteQuery({
    queryKey: campaignQueryKeys.lists(scope),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => fetchCampaignPage(pageParam, scope),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    retry: retryRead,
  });
}

export function useCampaignDetail(campaignId: string, scope?: CampaignScope) {
  return useQuery({
    queryKey: campaignQueryKeys.detail(campaignId, scope),
    queryFn: () => fetchCampaign(campaignId, scope),
    enabled: campaignId.length > 0,
    retry: retryRead,
  });
}

export function useCampaignOperators(scope?: CampaignScope, enabled = true) {
  return useQuery({
    queryKey: campaignQueryKeys.operators(scope),
    queryFn: () => fetchCampaignOperators(scope),
    enabled,
    retry: retryRead,
  });
}

export function useCreateCampaignMutation(scope?: CampaignScope) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      input,
      idempotencyKey,
    }: {
      input: CreateCampaignInput;
      idempotencyKey: string;
    }) => createCampaign(input, idempotencyKey, scope),
    retry: false,
    onSuccess: (campaign) => {
      queryClient.setQueryData(
        campaignQueryKeys.detail(campaign.id, scope),
        campaign,
      );
      void queryClient.invalidateQueries({
        queryKey: campaignQueryKeys.lists(scope),
      });
    },
  });
}

export function useUpdateCampaignMutation(scope?: CampaignScope) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      campaignId,
      input,
    }: {
      campaignId: string;
      input: UpdateCampaignInput;
    }) => updateCampaign(campaignId, input, scope),
    retry: false,
    onSuccess: (campaign) => {
      queryClient.setQueryData(
        campaignQueryKeys.detail(campaign.id, scope),
        campaign,
      );
      void queryClient.invalidateQueries({
        queryKey: campaignQueryKeys.lists(scope),
      });
    },
  });
}
