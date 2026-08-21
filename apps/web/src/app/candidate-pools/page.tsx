import { Suspense } from "react";

import { AuthShell } from "@/components/auth-shell";

export default function CandidatePoolsPage() {
  return (
    <Suspense
      fallback={<main className="auth-shell auth-loading">正在加载候选池</main>}
    >
      <AuthShell workspace="candidate-pools" />
    </Suspense>
  );
}
