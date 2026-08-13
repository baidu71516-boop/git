import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiClientError } from "@/lib/api/client";

import {
  createBulkImportJob,
  createCollectionJob,
  excludeBulkImportFile,
  getCollectionJob,
  getImportJob,
  listBulkImportFiles,
  listCollectionJobs,
  requestBulkPreview,
  retryBulkImportFile,
  retryBulkImportJob,
  updateBulkImportFileMapping,
  updateBulkImportFileSourceAcquiredAt,
  uploadBulkImportFile,
} from "./api";
import type { ImportJobFilePublic, ImportJobStatus } from "./types";

const BULK_POLL_INTERVAL_MS = 1_200;

export const bulkImportQueryKeys = {
  all: ["imports", "bulk"] as const,
  collections: () => ["imports", "bulk", "collections"] as const,
  collection: (collectionJobId: string) =>
    ["imports", "bulk", "collection", collectionJobId] as const,
  job: (importJobId: string) =>
    ["imports", "bulk", "job", importJobId] as const,
  files: (importJobId: string) =>
    ["imports", "bulk", "files", importJobId] as const,
};

export function retryBulkRead(failureCount: number, error: Error): boolean {
  if (error instanceof ApiClientError && error.status < 500) return false;
  return failureCount < 1;
}

export function isActiveJobStatus(status: ImportJobStatus): boolean {
  return (
    status === "previewing" ||
    status === "confirm_queued" ||
    status === "importing"
  );
}

export function shouldPollFiles(
  files: readonly ImportJobFilePublic[] | undefined,
): boolean {
  return (
    files?.some(
      (file) => file.status === "uploaded" || file.status === "parsing",
    ) ?? false
  );
}

function upsertFile(
  files: ImportJobFilePublic[] | undefined,
  file: ImportJobFilePublic,
): ImportJobFilePublic[] {
  if (!files) return [file];
  const existingIndex = files.findIndex((item) => item.id === file.id);
  if (existingIndex < 0) {
    return [...files, file].sort(
      (left, right) => left.position - right.position,
    );
  }
  return files.map((item) => (item.id === file.id ? file : item));
}

export function useCollectionJobs(enabled = true) {
  return useQuery({
    queryKey: bulkImportQueryKeys.collections(),
    queryFn: listCollectionJobs,
    enabled,
    retry: retryBulkRead,
  });
}

export function useCollectionJob(collectionJobId: string, enabled = true) {
  return useQuery({
    queryKey: bulkImportQueryKeys.collection(collectionJobId),
    queryFn: () => getCollectionJob(collectionJobId),
    enabled: enabled && collectionJobId.length > 0,
    retry: retryBulkRead,
  });
}

export function useBulkImportJob(importJobId: string, enabled = true) {
  return useQuery({
    queryKey: bulkImportQueryKeys.job(importJobId),
    queryFn: () => getImportJob(importJobId),
    enabled: enabled && importJobId.length > 0,
    retry: retryBulkRead,
    refetchInterval: (query) =>
      query.state.data && isActiveJobStatus(query.state.data.status)
        ? BULK_POLL_INTERVAL_MS
        : false,
  });
}

export function useBulkImportFiles(importJobId: string, enabled = true) {
  return useQuery({
    queryKey: bulkImportQueryKeys.files(importJobId),
    queryFn: () => listBulkImportFiles(importJobId),
    enabled: enabled && importJobId.length > 0,
    retry: retryBulkRead,
    refetchInterval: (query) =>
      shouldPollFiles(query.state.data) ? BULK_POLL_INTERVAL_MS : false,
  });
}

export function useCreateCollectionJobMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createCollectionJob,
    retry: false,
    onSuccess: (collection) => {
      queryClient.setQueryData(
        bulkImportQueryKeys.collection(collection.id),
        collection,
      );
      void queryClient.invalidateQueries({
        queryKey: bulkImportQueryKeys.collections(),
      });
    },
  });
}

export function useCreateBulkImportJobMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createBulkImportJob,
    retry: false,
    onSuccess: (job) => {
      queryClient.setQueryData(bulkImportQueryKeys.job(job.id), job);
      queryClient.setQueryData(bulkImportQueryKeys.files(job.id), []);
    },
  });
}

export function useUploadBulkImportFileMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: uploadBulkImportFile,
    retry: false,
    onSuccess: ({ file }) => {
      queryClient.setQueryData<ImportJobFilePublic[]>(
        bulkImportQueryKeys.files(file.import_job_id),
        (files) => upsertFile(files, file),
      );
      void queryClient.invalidateQueries({
        queryKey: bulkImportQueryKeys.files(file.import_job_id),
      });
    },
  });
}

export function useUpdateBulkImportFileSourceAcquiredAtMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: updateBulkImportFileSourceAcquiredAt,
    retry: false,
    onSuccess: (file) => {
      queryClient.setQueryData<ImportJobFilePublic[]>(
        bulkImportQueryKeys.files(file.import_job_id),
        (files) => upsertFile(files, file),
      );
    },
  });
}

export function useUpdateBulkImportFileMappingMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: updateBulkImportFileMapping,
    retry: false,
    onSuccess: (file) => {
      queryClient.setQueryData<ImportJobFilePublic[]>(
        bulkImportQueryKeys.files(file.import_job_id),
        (files) => upsertFile(files, file),
      );
      void queryClient.invalidateQueries({
        queryKey: bulkImportQueryKeys.files(file.import_job_id),
      });
    },
  });
}

export function useRetryBulkImportFileMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: retryBulkImportFile,
    retry: false,
    onSuccess: (file) => {
      queryClient.setQueryData<ImportJobFilePublic[]>(
        bulkImportQueryKeys.files(file.import_job_id),
        (files) => upsertFile(files, file),
      );
      void queryClient.invalidateQueries({
        queryKey: bulkImportQueryKeys.files(file.import_job_id),
      });
    },
  });
}

export function useExcludeBulkImportFileMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: excludeBulkImportFile,
    retry: false,
    onSuccess: (file) => {
      queryClient.setQueryData<ImportJobFilePublic[]>(
        bulkImportQueryKeys.files(file.import_job_id),
        (files) => upsertFile(files, file),
      );
      void queryClient.invalidateQueries({
        queryKey: bulkImportQueryKeys.files(file.import_job_id),
      });
    },
  });
}

export function useRequestBulkPreviewMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: requestBulkPreview,
    retry: false,
    onSuccess: (dispatch) => {
      void queryClient.invalidateQueries({
        queryKey: bulkImportQueryKeys.job(dispatch.import_job_id),
      });
    },
  });
}

export function useRetryBulkImportJobMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: retryBulkImportJob,
    retry: false,
    onSuccess: (dispatch) => {
      void queryClient.invalidateQueries({
        queryKey: bulkImportQueryKeys.job(dispatch.import_job_id),
      });
    },
  });
}
