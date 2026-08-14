"use client";

import { InfluencerDetailView } from "./influencer-detail-view";

export function InfluencerDetailWorkspace({
  influencerId,
}: {
  influencerId: string;
}) {
  return <InfluencerDetailView influencerId={influencerId} variant="page" />;
}
