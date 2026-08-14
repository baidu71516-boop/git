import { notFound } from "next/navigation";

import { RefreshQueueListPreviewWorkspace } from "@/features/refresh-queues/refresh-queue-preview-workspace";

export default function RefreshQueueVisualPreviewPage() {
  if (process.env.NODE_ENV !== "development") {
    notFound();
  }
  return <RefreshQueueListPreviewWorkspace />;
}
