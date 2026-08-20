import { Suspense } from "react";

import { AuthShell } from "@/components/auth-shell";

export default async function CampaignDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <Suspense
      fallback={
        <main className="auth-shell auth-loading">正在加载拓客活动</main>
      }
    >
      <AuthShell workspace="campaigns" campaignId={id} />
    </Suspense>
  );
}
