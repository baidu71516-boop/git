import { Suspense } from "react";

import { AuthShell } from "@/components/auth-shell";

export default function CampaignsPage() {
  return (
    <Suspense
      fallback={
        <main className="auth-shell auth-loading">正在加载拓客活动</main>
      }
    >
      <AuthShell workspace="campaigns" />
    </Suspense>
  );
}
