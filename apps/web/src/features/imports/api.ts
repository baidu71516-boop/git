import { ApiClientError, apiRequest } from "@/lib/api/client";

import type {
  BulkImportFileIdentity,
  CollectionJobCreateInput,
  CollectionJobPublic,
  ConfirmBulkImportInput,
  CreateBulkImportJobInput,
  ImportDispatchResult,
  ImportJobFilePublic,
  ImportJobFileUploadResult,
  ImportJobPublic,
  ImportRowsPage,
  ListBulkImportRowsInput,
  RequestBulkPreviewInput,
  UpdateBulkImportFileMappingInput,
  UpdateBulkImportFileSourceAcquiredAtInput,
  UpdateCollectionJobScreeningRulesInput,
  UploadBulkImportFileInput,
} from "./types";

function encoded(value: string): string {
  return encodeURIComponent(value);
}

export const bulkImportApiPaths = {
  collectionJobs: "/collection-jobs",
  collectionJob: (collectionJobId: string) =>
    `/collection-jobs/${encoded(collectionJobId)}`,
  collectionJobScreeningRules: (collectionJobId: string) =>
    `/collection-jobs/${encoded(collectionJobId)}/screening-rules`,
  createBulkJob: "/import-jobs/bulk",
  importJob: (importJobId: string) => `/import-jobs/${encoded(importJobId)}`,
  files: (importJobId: string) => `/import-jobs/${encoded(importJobId)}/files`,
  file: (importJobId: string, importJobFileId: string) =>
    `/import-jobs/${encoded(importJobId)}/files/${encoded(importJobFileId)}`,
  fileMapping: (importJobId: string, importJobFileId: string) =>
    `/import-jobs/${encoded(importJobId)}/files/${encoded(importJobFileId)}/mapping`,
  fileRetry: (importJobId: string, importJobFileId: string) =>
    `/import-jobs/${encoded(importJobId)}/files/${encoded(importJobFileId)}/retry`,
  fileExclude: (importJobId: string, importJobFileId: string) =>
    `/import-jobs/${encoded(importJobId)}/files/${encoded(importJobFileId)}/exclude`,
  preview: (importJobId: string) =>
    `/import-jobs/${encoded(importJobId)}/preview`,
  rows: (importJobId: string) => `/import-jobs/${encoded(importJobId)}/rows`,
  confirm: (importJobId: string) =>
    `/import-jobs/${encoded(importJobId)}/confirm`,
  retry: (importJobId: string) => `/import-jobs/${encoded(importJobId)}/retry`,
} as const;

function requireData<T>(data: T | null, description: string): T {
  if (data === null) {
    throw new ApiClientError(
      `${description}响应缺少数据`,
      200,
      "INVALID_RESPONSE",
    );
  }
  return data;
}

function jsonBody(value: object): string {
  return JSON.stringify(value);
}

export async function listCollectionJobs(): Promise<CollectionJobPublic[]> {
  const response = await apiRequest<CollectionJobPublic[]>(
    bulkImportApiPaths.collectionJobs,
  );
  return requireData(response.data, "采集任务列表");
}

export async function getCollectionJob(
  collectionJobId: string,
): Promise<CollectionJobPublic> {
  const response = await apiRequest<CollectionJobPublic>(
    bulkImportApiPaths.collectionJob(collectionJobId),
  );
  return requireData(response.data, "采集任务");
}

export async function createCollectionJob(
  payload: CollectionJobCreateInput,
): Promise<CollectionJobPublic> {
  const response = await apiRequest<CollectionJobPublic>(
    bulkImportApiPaths.collectionJobs,
    { method: "POST", body: jsonBody(payload) },
  );
  return requireData(response.data, "采集任务创建");
}

export async function updateCollectionJobScreeningRules({
  collectionJobId,
  payload,
}: UpdateCollectionJobScreeningRulesInput): Promise<CollectionJobPublic> {
  const response = await apiRequest<CollectionJobPublic>(
    bulkImportApiPaths.collectionJobScreeningRules(collectionJobId),
    { method: "PUT", body: jsonBody(payload) },
  );
  return requireData(response.data, "筛选规则更新");
}

export async function createBulkImportJob(
  payload: CreateBulkImportJobInput,
): Promise<ImportJobPublic> {
  const response = await apiRequest<ImportJobPublic>(
    bulkImportApiPaths.createBulkJob,
    { method: "POST", body: jsonBody(payload) },
  );
  return requireData(response.data, "批量文件处理任务创建");
}

export async function getImportJob(
  importJobId: string,
): Promise<ImportJobPublic> {
  const response = await apiRequest<ImportJobPublic>(
    bulkImportApiPaths.importJob(importJobId),
  );
  return requireData(response.data, "批量文件处理任务");
}

export async function listBulkImportFiles(
  importJobId: string,
): Promise<ImportJobFilePublic[]> {
  const response = await apiRequest<ImportJobFilePublic[]>(
    bulkImportApiPaths.files(importJobId),
  );
  return requireData(response.data, "批量文件列表");
}

export async function listBulkImportRows({
  importJobId,
  category,
  offset,
  limit,
}: ListBulkImportRowsInput): Promise<ImportRowsPage> {
  const query = new URLSearchParams({
    category,
    offset: String(offset),
    limit: String(limit),
  });
  const response = await apiRequest<ImportRowsPage>(
    `${bulkImportApiPaths.rows(importJobId)}?${query.toString()}`,
  );
  return requireData(response.data, "数据预览列表");
}

export async function uploadBulkImportFile({
  importJobId,
  clientFileId,
  file,
  sourceAcquiredAt,
}: UploadBulkImportFileInput): Promise<ImportJobFileUploadResult> {
  const form = new FormData();
  form.append("client_file_id", clientFileId);
  form.append("file", file);
  if (sourceAcquiredAt !== undefined) {
    form.append("source_acquired_at", sourceAcquiredAt);
  }
  const response = await apiRequest<ImportJobFileUploadResult>(
    bulkImportApiPaths.files(importJobId),
    { method: "POST", body: form },
  );
  return requireData(response.data, "文件上传");
}

export async function updateBulkImportFileSourceAcquiredAt({
  importJobId,
  importJobFileId,
  sourceAcquiredAt,
}: UpdateBulkImportFileSourceAcquiredAtInput): Promise<ImportJobFilePublic> {
  const response = await apiRequest<ImportJobFilePublic>(
    bulkImportApiPaths.file(importJobId, importJobFileId),
    {
      method: "PATCH",
      body: jsonBody({ source_acquired_at: sourceAcquiredAt }),
    },
  );
  return requireData(response.data, "数据取得时间更新");
}

export async function updateBulkImportFileMapping({
  importJobId,
  importJobFileId,
  mapping,
}: UpdateBulkImportFileMappingInput): Promise<ImportJobFilePublic> {
  const response = await apiRequest<ImportJobFilePublic>(
    bulkImportApiPaths.fileMapping(importJobId, importJobFileId),
    { method: "PUT", body: jsonBody({ mapping }) },
  );
  return requireData(response.data, "文件字段映射更新");
}

export async function retryBulkImportFile({
  importJobId,
  importJobFileId,
}: BulkImportFileIdentity): Promise<ImportJobFilePublic> {
  const response = await apiRequest<ImportJobFilePublic>(
    bulkImportApiPaths.fileRetry(importJobId, importJobFileId),
    { method: "POST" },
  );
  return requireData(response.data, "文件重试");
}

export async function excludeBulkImportFile({
  importJobId,
  importJobFileId,
}: BulkImportFileIdentity): Promise<ImportJobFilePublic> {
  const response = await apiRequest<ImportJobFilePublic>(
    bulkImportApiPaths.fileExclude(importJobId, importJobFileId),
    { method: "POST" },
  );
  return requireData(response.data, "文件排除");
}

export async function requestBulkPreview({
  importJobId,
  rebuild,
}: RequestBulkPreviewInput): Promise<ImportDispatchResult> {
  const response = await apiRequest<ImportDispatchResult>(
    bulkImportApiPaths.preview(importJobId),
    { method: "POST", body: jsonBody({ rebuild }) },
  );
  return requireData(response.data, "数据预览请求");
}

export async function confirmBulkImport({
  importJobId,
  previewRevision,
}: ConfirmBulkImportInput): Promise<ImportDispatchResult> {
  const response = await apiRequest<ImportDispatchResult>(
    bulkImportApiPaths.confirm(importJobId),
    {
      method: "POST",
      body: jsonBody({ preview_revision: previewRevision }),
    },
  );
  return requireData(response.data, "确认导入");
}

export async function retryBulkImportJob(
  importJobId: string,
): Promise<ImportDispatchResult> {
  const response = await apiRequest<ImportDispatchResult>(
    bulkImportApiPaths.retry(importJobId),
    { method: "POST" },
  );
  return requireData(response.data, "批量文件处理任务重试");
}
