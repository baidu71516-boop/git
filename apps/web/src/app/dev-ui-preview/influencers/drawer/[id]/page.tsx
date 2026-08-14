import { notFound } from "next/navigation";

import { DevInfluencerDetailDrawerPreview } from "@/features/influencers/influencer-detail-drawer-preview";
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
    <DevInfluencerDetailDrawerPreview
      detail={detail}
      backHref="/dev-ui-preview/influencers/drawer"
    />
  );
}
