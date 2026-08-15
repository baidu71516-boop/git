import { ApiClientError, apiDownload, apiRequest } from "@/lib/api/client";

import type {
  RefreshQueueCreateInput,
  RefreshQueueDetail,
  RefreshQueueItemPage,
  RefreshQueuePage,
} from "./types";

function encoded(value: string): string {
  return encodeURIComponent(value);
}

export const refreshQueueApiPaths = {
  queues: "/refresh-queues",
  queue: (queueId: string) => `/refresh-queues/${encoded(queueId)}`,
  items: (queueId: string) => `/refresh-queues/${encoded(queueId)}/items`,
  export: (queueId: string) => `/refresh-queues/${encoded(queueId)}/export`,
  cancel: (queueId: string) => `/refresh-queues/${encoded(queueId)}/cancel`,
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

function pagination(offset: number, limit: number): string {
  return new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  }).toString();
}

export async function listRefreshQueues(
  offset: number,
  limit: number,
): Promise<RefreshQueuePage> {
  const response = await apiRequest<RefreshQueuePage>(
    `${refreshQueueApiPaths.queues}?${pagination(offset, limit)}`,
  );
  return requireData(response.data, "数据更新列表");
}

export async function createRefreshQueue(
  input: RefreshQueueCreateInput,
): Promise<RefreshQueueDetail> {
  const response = await apiRequest<RefreshQueueDetail>(
    refreshQueueApiPaths.queues,
    { method: "POST", body: JSON.stringify(input) },
  );
  return requireData(response.data, "数据更新名单创建");
}

export async function getRefreshQueue(
  queueId: string,
): Promise<RefreshQueueDetail> {
  const response = await apiRequest<RefreshQueueDetail>(
    refreshQueueApiPaths.queue(queueId),
  );
  return requireData(response.data, "数据更新名单详情");
}

export async function listRefreshQueueItems(
  queueId: string,
  offset: number,
  limit: number,
): Promise<RefreshQueueItemPage> {
  const response = await apiRequest<RefreshQueueItemPage>(
    `${refreshQueueApiPaths.items(queueId)}?${pagination(offset, limit)}`,
  );
  return requireData(response.data, "数据更新名单条目");
}

export async function exportRefreshQueue(queueId: string) {
  return apiDownload(refreshQueueApiPaths.export(queueId), { method: "POST" });
}

export async function cancelRefreshQueue(
  queueId: string,
): Promise<RefreshQueueDetail> {
  const response = await apiRequest<RefreshQueueDetail>(
    refreshQueueApiPaths.cancel(queueId),
    { method: "POST" },
  );
  return requireData(response.data, "数据更新名单取消");
}
