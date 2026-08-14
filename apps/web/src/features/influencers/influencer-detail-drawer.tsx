"use client";

import { InfluencerDetailView } from "./influencer-detail-view";

export function InfluencerDetailDrawer({
  influencerId,
}: {
  influencerId: string;
}) {
  return <InfluencerDetailView influencerId={influencerId} variant="drawer" />;
}
