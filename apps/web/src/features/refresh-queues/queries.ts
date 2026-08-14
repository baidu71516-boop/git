import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiClientError } from "@/lib/api/client";

import {
  cancelRefreshQueue,
  createRefreshQueue,
  exportRefreshQueue,
  getRefreshQueue,
  listRefreshQueueItems,
  listRefreshQueues,
} from "./api";

function retryRead(failureCount: number, error: Error): boolean {
  if (error instanceof ApiClientError && error.status < 500) return false;
  return failureCount < 1;
}

export const refreshQueueQueryKeys = {
  all: ["refresh-queues"] as const,
  lists: () => ["refresh-queues", "list"] as const,
  list: (offset: number, limit: number) =>
    ["refresh-queues", "list", offset, limit] as const,
  detail: (queueId: string) => ["refresh-queues", "detail", queueId] as const,
  items: (queueId: string, offset: number, limit: number) =>
    ["refresh-queues", "items", queueId, offset, limit] as const,
};

export function useRefreshQueueList(offset: number, limit: number) {
  return useQuery({
    queryKey: refreshQueueQueryKeys.list(offset, limit),
    queryFn: () => listRefreshQueues(offset, limit),
    retry: retryRead,
  });
}

export function useRefreshQueueDetail(queueId: string) {
  return useQuery({
    queryKey: refreshQueueQueryKeys.detail(queueId),
    queryFn: () => getRefreshQueue(queueId),
    enabled: queueId.length > 0,
    retry: retryRead,
  });
}

export function useRefreshQueueItems(
  queueId: string,
  offset: number,
  limit: number,
  enabled = true,
) {
  return useQuery({
    queryKey: refreshQueueQueryKeys.items(queueId, offset, limit),
    queryFn: () => listRefreshQueueItems(queueId, offset, limit),
    enabled: enabled && queueId.length > 0,
    retry: retryRead,
  });
}

export function useCreateRefreshQueueMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createRefreshQueue,
    retry: false,
    onSuccess: (detail) => {
      queryClient.setQueryData(
        refreshQueueQueryKeys.detail(detail.queue.id),
        detail,
      );
      void queryClient.invalidateQueries({
        queryKey: refreshQueueQueryKeys.lists(),
      });
    },
  });
}

export function useExportRefreshQueueMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: exportRefreshQueue,
    retry: false,
    onSuccess: (_result, queueId) => {
      void queryClient.invalidateQueries({
        queryKey: refreshQueueQueryKeys.detail(queueId),
      });
      void queryClient.invalidateQueries({
        queryKey: refreshQueueQueryKeys.lists(),
      });
    },
  });
}

export function useCancelRefreshQueueMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: cancelRefreshQueue,
    retry: false,
    onSuccess: (detail) => {
      queryClient.setQueryData(
        refreshQueueQueryKeys.detail(detail.queue.id),
        detail,
      );
      void queryClient.invalidateQueries({
        queryKey: ["refresh-queues", "items", detail.queue.id],
      });
      void queryClient.invalidateQueries({
        queryKey: refreshQueueQueryKeys.lists(),
      });
    },
  });
}
