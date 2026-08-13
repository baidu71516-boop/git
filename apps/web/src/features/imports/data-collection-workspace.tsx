"use client";

import { Tabs } from "antd";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo } from "react";

import { ImportWorkspace } from "@/components/import-workspace";

import { BulkImportWorkspace } from "./bulk-import-workspace";

export type ImportWorkspaceRole =
  "super_admin" | "manager" | "operator" | "viewer";

type WorkspaceMode = "legacy" | "bulk";

function currentMode(searchParams: URLSearchParams): WorkspaceMode {
  return searchParams.get("workspace") === "bulk" ? "bulk" : "legacy";
}

export function DataCollectionWorkspace({
  role,
}: {
  role: ImportWorkspaceRole;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const searchString = searchParams.toString();
  const mode = useMemo(
    () => currentMode(new URLSearchParams(searchString)),
    [searchString],
  );
  const bulkJobId = searchParams.get("bulk_job_id");

  function navigate(next: URLSearchParams) {
    const query = next.toString();
    router.replace(query ? `${pathname}?${query}` : pathname);
  }

  function selectMode(nextMode: string) {
    const next = new URLSearchParams(searchString);
    if (nextMode === "bulk") next.set("workspace", "bulk");
    else next.delete("workspace");
    navigate(next);
  }

  function selectBulkJob(jobId: string) {
    const next = new URLSearchParams(searchString);
    next.set("workspace", "bulk");
    next.set("bulk_job_id", jobId);
    router.push(`${pathname}?${next.toString()}`);
  }

  function clearBulkJob() {
    const next = new URLSearchParams(searchString);
    next.set("workspace", "bulk");
    next.delete("bulk_job_id");
    navigate(next);
  }

  return (
    <section className="data-collection-workspace" aria-label="数据采集工作区">
      <Tabs
        activeKey={mode}
        destroyOnHidden={false}
        onChange={selectMode}
        items={[
          {
            key: "legacy",
            label: "单文件导入",
            children: <ImportWorkspace role={role} />,
          },
          {
            key: "bulk",
            label: "批量文件处理",
            children: (
              <BulkImportWorkspace
                key={bulkJobId ?? "new"}
                role={role}
                jobId={bulkJobId}
                onSelectJob={selectBulkJob}
                onClearJob={clearBulkJob}
              />
            ),
          },
        ]}
      />
    </section>
  );
}
