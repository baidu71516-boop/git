import { Suspense } from "react";

import { AuthShell } from "@/components/auth-shell";

export default function BuyerProspectsPage() {
  return (
    <Suspense
      fallback={<main className="auth-shell auth-loading">正在加载潜在客户</main>}
    >
      <AuthShell workspace="buyer-prospects" />
    </Suspense>
  );
}
