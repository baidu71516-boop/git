"use client";

import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";

const noOperation = () => undefined;

type PreviewWorkspaceShellProps = {
  title: string;
  description?: string;
  children: ReactNode;
};

export function InfluencerPreviewShell({
  title,
  description,
  children,
}: PreviewWorkspaceShellProps) {
  return (
    <AppShell
      title={title}
      description={description}
      department="界面预览"
      operator="预览用户"
      effectiveRole="仅展示"
      onLogout={noOperation}
    >
      {children}
    </AppShell>
  );
}
