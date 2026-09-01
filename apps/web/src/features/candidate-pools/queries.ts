import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { ApiClientError } from "@/lib/api/client";
import {
  CANDIDATE_MEMBER_PAGE_LIMIT,
  addCandidatesToCampaign,
  createCandidatePool,
  createCandidateRun,
  fetchCandidateMembers,
  fetchCandidatePolicies,
  fetchCandidatePool,
  fetchCandidatePoolPage,
  fetchCandidateRun,
  fetchCandidateRunPage,
} from "./api";
import type {
  CandidateCampaignSelection,
  CandidateMember,
  CandidateMemberPageSize,
  CandidatePoolCreateRequest,
  CandidatePoolRunRequest,
  BuyerLeadTier,
} from "./types";

export const BUYER_LEAD_TIERS: readonly BuyerLeadTier[] = [
  "HIGH",
  "CHANGED",
  "RELATED",
  "SAME_CATEGORY",
  "UNKNOWN",
];

function retryRead(failures: number, error: Error) {
  return (
    !(error instanceof ApiClientError && error.status < 500) && failures < 1
  );
}
export const candidatePoolQueryKeys = {
  lists: ["candidate-pools", "list"] as const,
  detail: (poolId: string) => ["candidate-pools", "detail", poolId] as const,
  policies: (poolId: string) =>
    ["candidate-pools", "policies", poolId] as const,
  runs: (poolId: string) => ["candidate-pools", "runs", poolId] as const,
  run: (poolId: string, runId: string) =>
    ["candidate-pools", "run", poolId, runId] as const,
  members: (
    poolId: string,
    runId: string,
    result?: string,
    pageSize: CandidateMemberPageSize = CANDIDATE_MEMBER_PAGE_LIMIT,
    buyerLeadTier?: BuyerLeadTier,
  ) =>
    buyerLeadTier
      ? ([
          "candidate-pools",
          "members",
          poolId,
          runId,
          result,
          pageSize,
          buyerLeadTier,
        ] as const)
      : ([
          "candidate-pools",
          "members",
          poolId,
          runId,
          result,
          pageSize,
        ] as const),
  buyerSummary: (poolId: string, runId: string) =>
    ["candidate-pools", "buyer-summary", poolId, runId] as const,
};
export function useCandidatePoolList() {
  return useInfiniteQuery({
    queryKey: candidatePoolQueryKeys.lists,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => fetchCandidatePoolPage(pageParam),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    retry: retryRead,
  });
}
export function useCandidatePool(poolId: string, enabled = true) {
  return useQuery({
    queryKey: candidatePoolQueryKeys.detail(poolId),
    queryFn: () => fetchCandidatePool(poolId),
    enabled: enabled && Boolean(poolId),
    retry: retryRead,
  });
}
export function useCandidatePolicies(poolId: string, enabled: boolean) {
  return useQuery({
    queryKey: candidatePoolQueryKeys.policies(poolId),
    queryFn: () => fetchCandidatePolicies(poolId),
    enabled: enabled && Boolean(poolId),
    retry: retryRead,
  });
}
export function useCandidateRuns(poolId: string, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: candidatePoolQueryKeys.runs(poolId),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => fetchCandidateRunPage(poolId, pageParam),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: enabled && Boolean(poolId),
    retry: retryRead,
  });
}
export function useCandidateRun(
  poolId: string,
  runId: string,
  polling: boolean,
) {
  return useQuery({
    queryKey: candidatePoolQueryKeys.run(poolId, runId),
    queryFn: () => fetchCandidateRun(poolId, runId),
    enabled: Boolean(poolId && runId),
    retry: retryRead,
    refetchInterval: (query) =>
      polling &&
      typeof document !== "undefined" &&
      !document.hidden &&
      ["PENDING", "RUNNING"].includes(query.state.data?.status ?? "")
        ? 3000
        : false,
  });
}
export function useCandidateMembers(
  poolId: string,
  runId: string,
  result: "MATCH" | "UNKNOWN" | undefined,
  enabled: boolean,
  pageSize: CandidateMemberPageSize = CANDIDATE_MEMBER_PAGE_LIMIT,
  buyerLeadTier?: BuyerLeadTier,
) {
  return useInfiniteQuery({
    queryKey: candidatePoolQueryKeys.members(
      poolId,
      runId,
      result,
      pageSize,
      buyerLeadTier,
    ),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      fetchCandidateMembers(
        poolId,
        runId,
        pageParam,
        result,
        pageSize,
        buyerLeadTier,
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: enabled && Boolean(poolId && runId),
    retry: retryRead,
  });
}

async function fetchAllBuyerTierMembers(
  poolId: string,
  runId: string,
  tier: BuyerLeadTier,
): Promise<CandidateMember[]> {
  const items: CandidateMember[] = [];
  let cursor: string | null = null;
  do {
    const page = await fetchCandidateMembers(
      poolId,
      runId,
      cursor,
      undefined,
      200,
      tier,
    );
    items.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor);
  return items;
}

export function useBuyerLeadTierSummary(
  poolId: string,
  runId: string,
  enabled: boolean,
) {
  return useQuery({
    queryKey: candidatePoolQueryKeys.buyerSummary(poolId, runId),
    queryFn: async () => {
      const entries = await Promise.all(
        BUYER_LEAD_TIERS.map(
          async (tier) =>
            [
              tier,
              await fetchAllBuyerTierMembers(poolId, runId, tier),
            ] as const,
        ),
      );
      return Object.fromEntries(entries) as Record<
        BuyerLeadTier,
        CandidateMember[]
      >;
    },
    enabled: enabled && Boolean(poolId && runId),
    retry: retryRead,
  });
}
export function useCreateCandidateRunMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      poolId,
      idempotencyKey,
      payload,
    }: {
      poolId: string;
      idempotencyKey: string;
      payload?: CandidatePoolRunRequest;
    }) => createCandidateRun(poolId, idempotencyKey, payload),
    retry: false,
    onSuccess: (run, variables) => {
      client.setQueryData(
        candidatePoolQueryKeys.run(variables.poolId, run.id),
        run,
      );
      void client.invalidateQueries({
        queryKey: candidatePoolQueryKeys.runs(variables.poolId),
      });
      void client.invalidateQueries({
        queryKey: candidatePoolQueryKeys.policies(variables.poolId),
      });
      void client.invalidateQueries({
        queryKey: candidatePoolQueryKeys.detail(variables.poolId),
      });
    },
  });
}
export function useCreateCandidatePoolMutation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      payload,
      idempotencyKey,
    }: {
      payload: CandidatePoolCreateRequest;
      idempotencyKey: string;
    }) => createCandidatePool(payload, idempotencyKey),
    retry: false,
    onSuccess: (pool) => {
      client.setQueryData(candidatePoolQueryKeys.detail(pool.id), pool);
      void client.invalidateQueries({ queryKey: candidatePoolQueryKeys.lists });
    },
  });
}
export function useAddCandidatesToCampaignMutation() {
  return useMutation({
    mutationFn: ({
      campaignId,
      idempotencyKey,
      ...selectionInput
    }: {
      campaignId: string;
      idempotencyKey: string;
    } & (
      | { selection: CandidateCampaignSelection }
      | { runId: string; memberIds: string[] }
    )) =>
      "selection" in selectionInput
        ? addCandidatesToCampaign(
            campaignId,
            selectionInput.selection,
            idempotencyKey,
          )
        : addCandidatesToCampaign(
            campaignId,
            selectionInput.runId,
            selectionInput.memberIds,
            idempotencyKey,
          ),
    retry: false,
  });
}
