"use client";

import { notFound } from "next/navigation";

import { CampaignPreviewWorkspace } from "@/features/campaigns/campaign-preview-workspace";

export default function CampaignVisualPreviewPage() {
  if (process.env.NODE_ENV !== "development") {
    notFound();
  }
  return <CampaignPreviewWorkspace />;
}
