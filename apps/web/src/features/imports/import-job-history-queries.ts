import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  cancelBulkImport,
  confirmBulkImport,
  getImportJob,
  listImportJobs,
} from "./api";
import type { ImportJobPublic } from "./types";
import { isActiveJobStatus, retryBulkRead } from "./queries";

const IMPORT_JOB_HISTORY_POLL_INTERVAL_MS = 1_200;

export const importJobHistoryQueryKeys = {
  all: ["import-jobs", "history"] as const,
  list: (offset: number, limit: number) =>
    ["import-jobs", "history", "list", offset, limit] as const,
  detail: (importJobId: string) =>
    ["import-jobs", "history", "detail", importJobId] as const,
};

export function useImportJobHistory(offset: number, limit: number) {
  return useQuery({
    queryKey: importJobHistoryQueryKeys.list(offset, limit),
    queryFn: () => listImportJobs(offset, limit),
    retry: retryBulkRead,
  });
}

export function useImportJobHistoryDetail(importJobId: string, enabled = true) {
  return useQuery({
    queryKey: importJobHistoryQueryKeys.detail(importJobId),
    queryFn: () => getImportJob(importJobId),
    enabled: enabled && importJobId.length > 0,
    retry: retryBulkRead,
    refetchInterval: (query) =>
      isActiveJobStatus(query.state.data?.status ?? "draft")
        ? IMPORT_JOB_HISTORY_POLL_INTERVAL_MS
        : false,
  });
}

async function refreshImportJobHistory(
  queryClient: ReturnType<typeof useQueryClient>,
  importJobId: string,
) {
  await Promise.all([
    queryClient.invalidateQueries({
      queryKey: importJobHistoryQueryKeys.detail(importJobId),
    }),
    queryClient.invalidateQueries({ queryKey: importJobHistoryQueryKeys.all }),
  ]);
}

export function useConfirmImportJobHistoryMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: confirmBulkImport,
    retry: false,
    onSuccess: async (dispatch) => {
      queryClient.setQueryData<ImportJobPublic>(
        importJobHistoryQueryKeys.detail(dispatch.import_job_id),
        (job) =>
          job
            ? {
                ...job,
                status: dispatch.status,
                preview_revision: dispatch.preview_revision,
              }
            : job,
      );
      await refreshImportJobHistory(queryClient, dispatch.import_job_id);
    },
  });
}

export function useCancelImportJobHistoryMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: cancelBulkImport,
    retry: false,
    onSuccess: async (job) => {
      queryClient.setQueryData(importJobHistoryQueryKeys.detail(job.id), job);
      await refreshImportJobHistory(queryClient, job.id);
    },
  });
}
