import { Suspense } from "react";

import { AuthShell } from "@/components/auth-shell";

export default function InfluencersPage() {
  return (
    <Suspense
      fallback={<main className="auth-shell auth-loading">正在加载达人库</main>}
    >
      <AuthShell workspace="influencers" />
    </Suspense>
  );
}
