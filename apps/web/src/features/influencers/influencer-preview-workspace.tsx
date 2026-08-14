"use client";

import { Card, Space } from "antd";
import { useMemo } from "react";

import { AppShell } from "@/components/app-shell";
import { PageHeader } from "@/components/ui/page-header";

import { InfluencerFilterBar } from "./components/influencer-filter-bar";
import { InfluencerPagination } from "./components/influencer-pagination";
import { InfluencerTable } from "./components/influencer-table";
import {
  createInfluencerPreviewItems,
  influencerPreviewCrmStages,
  influencerPreviewOwners,
} from "./preview-fixtures";

const noOperation = () => undefined;

export function InfluencerPreviewWorkspace() {
  const items = useMemo(() => createInfluencerPreviewItems(), []);
  const tags = useMemo(
    () =>
      Array.from(
        new Set(
          items.flatMap((item) =>
            item.platform_accounts.flatMap((account) => account.source_tags),
          ),
        ),
      ),
    [items],
  );

  return (
    <AppShell
      title="达人库"
      department="界面预览"
      operator="虚构数据"
      role="仅供开发"
      onLogout={noOperation}
    >
      <section className="influencer-workspace" aria-label="达人库视觉预览">
        <div className="influencer-page-heading">
          <PageHeader
            title="达人库"
            description="公司共享达人资源 · 统一查看、筛选和管理 · 仅开发环境可见"
          />
        </div>

        <InfluencerFilterBar
          query={{}}
          options={{
            owners: influencerPreviewOwners,
            tags,
            crm_stages: influencerPreviewCrmStages,
          }}
          optionsLoading={false}
          optionsError={false}
          validationMessage={null}
          onRetryOptions={noOperation}
          onChange={noOperation}
          onClear={noOperation}
        />

        <Card variant="borderless" className="influencer-list-card">
          <Space orientation="vertical" size="middle" className="full-width">
            <InfluencerTable items={items} />
            <InfluencerPagination
              page={1}
              pageSize={50}
              total={items.length}
              onPageChange={noOperation}
            />
          </Space>
        </Card>
      </section>
    </AppShell>
  );
}
