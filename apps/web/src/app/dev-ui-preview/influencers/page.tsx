import { notFound } from "next/navigation";

import { InfluencerPreviewWorkspace } from "@/features/influencers/influencer-preview-workspace";

export default function InfluencerVisualPreviewPage() {
  if (process.env.NODE_ENV !== "development") {
    notFound();
  }

  return <InfluencerPreviewWorkspace />;
}
