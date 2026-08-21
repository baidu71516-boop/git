import { Suspense } from "react";

import { AuthShell } from "@/components/auth-shell";

export default async function CandidateRunPage({
  params,
}: {
  params: Promise<{ poolId: string; runId: string }>;
}) {
  const { poolId, runId } = await params;
  return (
    <Suspense
      fallback={
        <main className="auth-shell auth-loading">正在加载候选结果</main>
      }
    >
      <AuthShell
        workspace="candidate-pools"
        candidatePoolId={poolId}
        candidateRunId={runId}
      />
    </Suspense>
  );
}
