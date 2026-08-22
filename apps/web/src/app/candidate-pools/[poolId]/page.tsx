import { Suspense } from "react";

import { AuthShell } from "@/components/auth-shell";

export default async function CandidatePoolPage({
  params,
}: {
  params: Promise<{ poolId: string }>;
}) {
  const { poolId } = await params;
  return (
    <Suspense
      fallback={<main className="auth-shell auth-loading">正在加载候选池</main>}
    >
      <AuthShell workspace="candidate-pools" candidatePoolId={poolId} />
    </Suspense>
  );
}
