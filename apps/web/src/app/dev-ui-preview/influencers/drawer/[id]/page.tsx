import { notFound } from "next/navigation";

import { InfluencerPreviewWorkspace } from "@/features/influencers/influencer-preview-workspace";
import { getInfluencerPreviewDetailById } from "@/features/influencers/preview-fixtures";

export default async function DevInfluencerDetailDrawerPreviewPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  if (process.env.NODE_ENV !== "development") {
    notFound();
  }

  const { id } = await params;
  const detail = getInfluencerPreviewDetailById(id);

  if (!detail) {
    notFound();
  }

  return (
    <InfluencerPreviewWorkspace
      openInfluencerId={id}
      closeDrawerHref="/dev-ui-preview/influencers"
    />
  );
}
