import { notFound } from "next/navigation";

import { BulkImportPreviewWorkspace } from "@/features/imports/bulk-import-preview-workspace";

export default function DataCollectionVisualPreviewPage() {
  if (process.env.NODE_ENV !== "development") {
    notFound();
  }

  return <BulkImportPreviewWorkspace />;
}
