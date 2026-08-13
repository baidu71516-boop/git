"use client";

import { Alert, Button, Drawer, Space, Tag, Typography } from "antd";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { AppLoading } from "@/components/ui/app-loading";
import { ApiClientError } from "@/lib/api/client";

import { InfluencerDetail } from "./components/influencer-detail";
import { MetricSnapshotList } from "./components/metric-snapshot-list";
import { crmStageDisplay, platformLabel } from "./formatters";
import { useInfluencerDetail, useMetricSnapshots } from "./queries";
import type { InfluencerDetail as InfluencerDetailData } from "./types";

const { Text } = Typography;
const SNAPSHOT_PAGE_SIZE = 50;

type DetailVariant = "page" | "drawer";

function drawerSubtitle(detail: InfluencerDetailData): string | null {
  const account =
    detail.platform_accounts.find((candidate) => candidate.is_active) ??
    detail.platform_accounts[0];
  if (!account) return null;
  return `${platformLabel(account.platform)} · ${account.account_name}`;
}

function DrawerTitle({ detail }: { detail: InfluencerDetailData | undefined }) {
  if (!detail) return <span>达人详情</span>;
  const subtitle = drawerSubtitle(detail);
  return (
    <div className="influencer-drawer-heading">
      <strong>{detail.display_name}</strong>
      {subtitle ? <Text type="secondary">{subtitle}</Text> : null}
    </div>
  );
}

export function InfluencerDetailView({
  influencerId,
  variant,
}: {
  influencerId: string;
  variant: DetailVariant;
}) {
  const router = useRouter();
  const [snapshotPage, setSnapshotPage] = useState(1);
  const detailQuery = useInfluencerDetail(influencerId);
  const snapshotQuery = useMetricSnapshots(
    influencerId,
    snapshotPage,
    SNAPSHOT_PAGE_SIZE,
    detailQuery.isSuccess,
  );
  const isDrawer = variant === "drawer";

  function closeDrawer() {
    router.back();
  }

  const detailState = detailQuery.isPending ? (
    <div className="influencer-list-state">
      <AppLoading label="正在加载达人详情" />
    </div>
  ) : detailQuery.isError ? (
    <Alert
      type="error"
      showIcon
      title={
        detailQuery.error instanceof ApiClientError &&
        detailQuery.error.status === 404
          ? "达人不存在或不可见"
          : "达人资料加载失败"
      }
      description={
        detailQuery.error instanceof ApiClientError &&
        detailQuery.error.status === 404
          ? "该达人不存在，或当前账号无权查看。"
          : "请检查网络连接后重新加载。"
      }
      action={
        <Space wrap>
          <Button onClick={() => void detailQuery.refetch()}>重新加载</Button>
          {isDrawer ? <Button onClick={closeDrawer}>关闭</Button> : null}
        </Space>
      }
    />
  ) : (
    <>
      <InfluencerDetail detail={detailQuery.data} showTitle={!isDrawer} />
      <MetricSnapshotList
        accounts={detailQuery.data.platform_accounts}
        page={snapshotPage}
        pageSize={SNAPSHOT_PAGE_SIZE}
        query={snapshotQuery}
        onPageChange={setSnapshotPage}
      />
    </>
  );

  if (isDrawer) {
    const stage = detailQuery.data
      ? crmStageDisplay(detailQuery.data.crm_stage)
      : null;
    return (
      <Drawer
        rootClassName="influencer-detail-drawer"
        open
        size="default"
        width={740}
        title={<DrawerTitle detail={detailQuery.data} />}
        extra={stage ? <Tag color={stage.color}>{stage.label}</Tag> : null}
        closable={{ "aria-label": "关闭达人详情", placement: "end" }}
        keyboard
        mask={{ closable: true }}
        onClose={closeDrawer}
      >
        <div className="influencer-drawer-content influencer-detail-layout">
          {detailState}
        </div>
      </Drawer>
    );
  }

  return (
    <section className="influencer-workspace influencer-detail-workspace">
      <div className="influencer-detail-workspace-content">
        <Space className="influencer-detail-actions" size="middle">
          <Button aria-label="返回上一页" onClick={() => router.back()}>
            返回上一页
          </Button>
          <Link href="/influencers">返回达人库</Link>
        </Space>
        {detailState}
      </div>
    </section>
  );
}
