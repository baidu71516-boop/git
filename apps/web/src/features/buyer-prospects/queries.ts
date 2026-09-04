import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { ApiClientError } from "@/lib/api/client";

import {
  createBuyerProspectRule,
  fetchBuyerProspectRule,
  fetchBuyerProspectRuleOptions,
  fetchBuyerProspectRulePage,
  runBuyerProspectRule,
  setBuyerProspectRuleLifecycle,
  updateBuyerProspectRule,
} from "./api";
import type {
  BuyerProspectRuleCreateRequest,
  BuyerProspectRuleLifecycleRequest,
  BuyerProspectRuleUpdateRequest,
} from "./types";

function retryRead(failures: number, error: Error) {
  return (
    !(error instanceof ApiClientError && error.status < 500) && failures < 1
  );
}

export const buyerProspectQueryKeys = {
  list: ["buyer-prospects", "list"] as const,
  detail: (ruleId: string) => ["buyer-prospects", "detail", ruleId] as const,
  options: ["buyer-prospects", "options"] as const,
};

export function useBuyerProspectRuleList(includeArchived = false) {
  return useInfiniteQuery({
    queryKey: [...buyerProspectQueryKeys.list, includeArchived] as const,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      fetchBuyerProspectRulePage(pageParam, includeArchived),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    retry: retryRead,
  });
}

export function useBuyerProspectRuleOptions(enabled = true) {
  return useQuery({
    queryKey: buyerProspectQueryKeys.options,
    queryFn: fetchBuyerProspectRuleOptions,
    enabled,
    retry: retryRead,
  });
}

export function useBuyerProspectRule(ruleId: string) {
  return useQuery({
    queryKey: buyerProspectQueryKeys.detail(ruleId),
    queryFn: () => fetchBuyerProspectRule(ruleId),
    enabled: Boolean(ruleId),
    retry: retryRead,
  });
}

export function useCreateBuyerProspectRuleMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      payload,
      idempotencyKey,
    }: {
      payload: BuyerProspectRuleCreateRequest;
      idempotencyKey: string;
    }) => createBuyerProspectRule(payload, idempotencyKey),
    retry: false,
    onSuccess: (rule) => {
      client.setQueryData(buyerProspectQueryKeys.detail(rule.id), rule);
      void client.invalidateQueries({ queryKey: buyerProspectQueryKeys.list });
    },
  });
}

export function useUpdateBuyerProspectRuleMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      ruleId,
      payload,
    }: {
      ruleId: string;
      payload: BuyerProspectRuleUpdateRequest;
    }) => updateBuyerProspectRule(ruleId, payload),
    retry: false,
    onSuccess: (rule) => {
      client.setQueryData(buyerProspectQueryKeys.detail(rule.id), rule);
      void client.invalidateQueries({ queryKey: buyerProspectQueryKeys.list });
    },
  });
}

export function useBuyerProspectRuleLifecycleMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      ruleId,
      payload,
    }: {
      ruleId: string;
      payload: BuyerProspectRuleLifecycleRequest;
    }) => setBuyerProspectRuleLifecycle(ruleId, payload),
    retry: false,
    onSuccess: async (rule) => {
      client.setQueryData(buyerProspectQueryKeys.detail(rule.id), rule);
      await client.invalidateQueries({ queryKey: buyerProspectQueryKeys.list });
    },
  });
}

export function useRunBuyerProspectRuleMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      ruleId,
      idempotencyKey,
    }: {
      ruleId: string;
      idempotencyKey: string;
    }) => runBuyerProspectRule(ruleId, idempotencyKey),
    retry: false,
    onSuccess: (_run, variables) => {
      void client.invalidateQueries({
        queryKey: buyerProspectQueryKeys.detail(variables.ruleId),
      });
      void client.invalidateQueries({ queryKey: buyerProspectQueryKeys.list });
    },
  });
}
