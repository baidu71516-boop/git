import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { ApiClientError } from "@/lib/api/client";
import {
  addCandidatesToCampaign,
  createCandidateRun,
  fetchCandidateMembers,
  fetchCandidatePolicies,
  fetchCandidatePool,
  fetchCandidatePoolPage,
  fetchCandidateRun,
  fetchCandidateRunPage,
} from "./api";

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
  members: (poolId: string, runId: string, result?: string) =>
    ["candidate-pools", "members", poolId, runId, result] as const,
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
export function useCandidatePool(poolId: string) {
  return useQuery({
    queryKey: candidatePoolQueryKeys.detail(poolId),
    queryFn: () => fetchCandidatePool(poolId),
    enabled: Boolean(poolId),
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
) {
  return useInfiniteQuery({
    queryKey: candidatePoolQueryKeys.members(poolId, runId, result),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      fetchCandidateMembers(poolId, runId, pageParam, result),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
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
    }: {
      poolId: string;
      idempotencyKey: string;
    }) => createCandidateRun(poolId, idempotencyKey),
    retry: false,
    onSuccess: (run, variables) => {
      client.setQueryData(
        candidatePoolQueryKeys.run(variables.poolId, run.id),
        run,
      );
      void client.invalidateQueries({
        queryKey: candidatePoolQueryKeys.runs(variables.poolId),
      });
    },
  });
}
export function useAddCandidatesToCampaignMutation() {
  return useMutation({
    mutationFn: ({
      campaignId,
      runId,
      memberIds,
      idempotencyKey,
    }: {
      campaignId: string;
      runId: string;
      memberIds: string[];
      idempotencyKey: string;
    }) => addCandidatesToCampaign(campaignId, runId, memberIds, idempotencyKey),
    retry: false,
  });
}
