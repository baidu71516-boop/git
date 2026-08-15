import { notFound } from "next/navigation";

import { RefreshQueueDetailPreviewWorkspace } from "@/features/refresh-queues/refresh-queue-preview-workspace";
import type { RefreshQueueStatus } from "@/features/refresh-queues/types";

const statuses = new Set<RefreshQueueStatus>([
  "open",
  "exported",
  "completed",
  "cancelled",
]);

export default async function RefreshQueueDetailVisualPreviewPage({
  params,
}: {
  params: Promise<{ status: string }>;
}) {
  if (process.env.NODE_ENV !== "development") {
    notFound();
  }
  const { status } = await params;
  if (!statuses.has(status as RefreshQueueStatus)) {
    notFound();
  }
  return (
    <RefreshQueueDetailPreviewWorkspace
      initialStatus={status as RefreshQueueStatus}
    />
  );
}
