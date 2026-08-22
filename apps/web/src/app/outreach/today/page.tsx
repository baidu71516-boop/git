import { Suspense } from "react";

import { AuthShell } from "@/components/auth-shell";

export default function TodayOutreachPage() {
  return (
    <Suspense
      fallback={
        <main className="auth-shell auth-loading">正在加载今日触达</main>
      }
    >
      <AuthShell workspace="outreach-today" />
    </Suspense>
  );
}
