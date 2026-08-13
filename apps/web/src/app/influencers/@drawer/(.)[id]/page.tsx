import { InfluencerDetailDrawer } from "@/features/influencers/influencer-detail-drawer";

export default async function InterceptedInfluencerDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <InfluencerDetailDrawer influencerId={id} />;
}
