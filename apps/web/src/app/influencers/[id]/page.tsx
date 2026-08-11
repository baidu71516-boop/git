import { Suspense } from "react";

import { AuthShell } from "@/components/auth-shell";

export default async function InfluencerDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <Suspense
      fallback={
        <main className="auth-shell auth-loading">正在加载达人详情</main>
      }
    >
      <AuthShell workspace="influencers" influencerId={id} />
    </Suspense>
  );
}
