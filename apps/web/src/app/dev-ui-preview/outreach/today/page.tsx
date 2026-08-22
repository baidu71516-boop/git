import { notFound } from "next/navigation";

import { TodayPreviewWorkspace } from "@/features/outreach-today/today-preview-workspace";

export default function TodayOutreachVisualPreviewPage() {
  if (process.env.NODE_ENV !== "development") {
    notFound();
  }

  return <TodayPreviewWorkspace />;
}
