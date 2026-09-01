"use client";

import { Card, Space } from "antd";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { PageHeader } from "@/components/ui/page-header";

import { InfluencerFilterBar } from "./components/influencer-filter-bar";
import { InfluencerPagination } from "./components/influencer-pagination";
import { InfluencerTable } from "./components/influencer-table";
import {
  createInfluencerPreviewItems,
  createInfluencerPreviewDetailItems,
  influencerPreviewCrmStages,
  influencerPreviewOwners,
} from "./preview-fixtures";
import { DevInfluencerDetailDrawer } from "./influencer-detail-drawer-preview";

const noOperation = () => undefined;

export function InfluencerPreviewWorkspace({
  openInfluencerId,
  closeDrawerHref = "/dev-ui-preview/influencers",
}: {
  openInfluencerId?: string;
  closeDrawerHref?: string;
}) {
  const router = useRouter();
  const items = useMemo(() => createInfluencerPreviewItems(), []);
  const details = useMemo(() => createInfluencerPreviewDetailItems(), []);
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
  const detailById = useMemo(
    () => new Map(details.map((detail) => [detail.id, detail])),
    [details],
  );
  const [openId, setOpenId] = useState<string | null>(openInfluencerId ?? null);
  const isLauncher = openInfluencerId !== undefined;
  const openDetail = openId ? detailById.get(openId) : null;

  const openDrawerFromList = (id: string) => {
    setOpenId(id);
  };

  const closeDrawer = () => {
    setOpenId(null);
    if (isLauncher) {
      void router.push(closeDrawerHref);
    }
  };

  return (
    <AppShell
      title="达人库"
      department="界面预览"
      operator="虚构数据"
      effectiveRole="仅供开发"
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
            <InfluencerTable
              items={items}
              detailHrefPrefix="/dev-ui-preview/influencers/drawer"
              onOpenDetail={openDrawerFromList}
            />
            <InfluencerPagination
              page={1}
              pageSize={50}
              total={items.length}
              onPageChange={noOperation}
            />
          </Space>
        </Card>
        {openDetail ? (
          <DevInfluencerDetailDrawer
            detail={openDetail}
            open
            onClose={closeDrawer}
          />
        ) : null}
      </section>
    </AppShell>
  );
}
