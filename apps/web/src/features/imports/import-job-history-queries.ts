import { useQuery } from "@tanstack/react-query";

import { getImportJob, listImportJobs } from "./api";
import { retryBulkRead } from "./queries";

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
  });
}
