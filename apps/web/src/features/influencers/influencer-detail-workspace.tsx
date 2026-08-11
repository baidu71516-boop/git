"use client";

import { Alert, Button, Card, Space, Spin } from "antd";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiClientError } from "@/lib/api/client";

import { InfluencerDetail } from "./components/influencer-detail";
import { MetricSnapshotList } from "./components/metric-snapshot-list";
import { useInfluencerDetail, useMetricSnapshots } from "./queries";

const SNAPSHOT_PAGE_SIZE = 50;

export function InfluencerDetailWorkspace({
  influencerId,
}: {
  influencerId: string;
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

  return (
    <section className="influencer-workspace influencer-detail-workspace">
      <Card variant="borderless" className="influencer-detail-actions">
        <Space wrap>
          <Button aria-label="返回上一页" onClick={() => router.back()}>
            返回上一页
          </Button>
          <Link href="/influencers">返回达人库</Link>
        </Space>
      </Card>

      {detailQuery.isPending ? (
        <Card variant="borderless" className="influencer-detail-card">
          <div className="influencer-list-state">
            <Spin size="large" />
            <span>正在加载达人详情</span>
          </div>
        </Card>
      ) : detailQuery.isError ? (
        <Alert
          type="error"
          showIcon
          message={
            detailQuery.error instanceof ApiClientError &&
            detailQuery.error.status === 404
              ? "达人不存在或不可见"
              : "达人详情加载失败"
          }
          description={
            detailQuery.error instanceof ApiClientError &&
            detailQuery.error.status === 404
              ? "该达人不存在，或当前不可见。"
              : "网络错误不会被当作达人不存在。"
          }
          action={
            detailQuery.error instanceof ApiClientError &&
            detailQuery.error.status === 404 ? null : (
              <Button onClick={() => void detailQuery.refetch()}>
                重试达人详情
              </Button>
            )
          }
        />
      ) : (
        <>
          <InfluencerDetail detail={detailQuery.data} />
          <Card variant="borderless" className="influencer-detail-card">
            <MetricSnapshotList
              accounts={detailQuery.data.platform_accounts}
              page={snapshotPage}
              pageSize={SNAPSHOT_PAGE_SIZE}
              query={snapshotQuery}
              onPageChange={setSnapshotPage}
            />
          </Card>
        </>
      )}
    </section>
  );
}
