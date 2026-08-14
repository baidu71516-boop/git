"use client";

import {
  DownloadOutlined,
  ImportOutlined,
  StopOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Modal,
  Pagination,
  Space,
  Typography,
} from "antd";
import Link from "next/link";
import { useState } from "react";

import { AppEmpty } from "@/components/ui/app-empty";
import { AppLoading } from "@/components/ui/app-loading";
import { PageHeader } from "@/components/ui/page-header";

import { RefreshQueueItemTable } from "./components/refresh-queue-item-table";
import { RefreshQueueStatusBadge } from "./components/refresh-queue-status";
import { RefreshQueueSummary } from "./components/refresh-queue-summary";
import {
  downloadBlob,
  filenameFromContentDisposition,
  formatRefreshDateTime,
  mutationErrorMessage,
} from "./formatters";
import {
  useCancelRefreshQueueMutation,
  useExportRefreshQueueMutation,
  useRefreshQueueDetail,
  useRefreshQueueItems,
} from "./queries";
import type { RefreshQueueRole } from "./types";

const { Text, Title } = Typography;

export function RefreshQueueReturnButton({
  queueId,
  hasPriorReturn,
}: {
  queueId: string;
  hasPriorReturn: boolean;
}) {
  return (
    <Button
      href={`/?workspace=bulk&refresh_queue_id=${encodeURIComponent(queueId)}`}
      icon={<ImportOutlined aria-hidden="true" />}
    >
      {hasPriorReturn ? "继续处理回流数据" : "处理回流数据"}
    </Button>
  );
}

export function RefreshQueueDetail({
  queueId,
  role,
}: {
  queueId: string;
  role: RefreshQueueRole;
}) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const detailQuery = useRefreshQueueDetail(queueId);
  const itemsQuery = useRefreshQueueItems(
    queueId,
    (page - 1) * pageSize,
    pageSize,
    detailQuery.isSuccess,
  );
  const exportMutation = useExportRefreshQueueMutation();
  const cancelMutation = useCancelRefreshQueueMutation();
  const queue = detailQuery.data?.queue;
  const canMutate = role !== "viewer";
  const canExport =
    canMutate && (queue?.status === "open" || queue?.status === "exported");
  const canCancel = canExport;
  const statusBreakdown = detailQuery.data?.summary.status_breakdown;
  const unfinishedCount =
    (statusBreakdown?.pending ?? 0) +
    (statusBreakdown?.stale_return ?? 0) +
    (statusBreakdown?.unresolved ?? 0);
  const hasPriorReturn =
    (statusBreakdown?.fulfilled_changed ?? 0) +
      (statusBreakdown?.fulfilled_no_change ?? 0) +
      (statusBreakdown?.stale_return ?? 0) +
      (statusBreakdown?.unresolved ?? 0) >
    0;
  const canProcessReturn =
    canMutate && queue?.status === "exported" && unfinishedCount > 0;

  async function handleExport() {
    setActionError(null);
    try {
      const result = await exportMutation.mutateAsync(queueId);
      const filename = filenameFromContentDisposition(
        result.contentDisposition,
        `refresh-queue-${queueId}.csv`,
      );
      downloadBlob(result.blob, filename);
    } catch (caught) {
      setActionError(
        mutationErrorMessage(caught, "CSV 导出失败，请稍后重试。"),
      );
    }
  }

  async function handleCancel() {
    setActionError(null);
    try {
      await cancelMutation.mutateAsync(queueId);
      setCancelOpen(false);
    } catch (caught) {
      setActionError(
        mutationErrorMessage(caught, "取消更新名单失败，请稍后重试。"),
      );
      setCancelOpen(false);
    }
  }

  if (detailQuery.isPending) {
    return <AppLoading label="正在加载数据更新名单" />;
  }

  if (detailQuery.isError || !detailQuery.data) {
    return (
      <Alert
        type="error"
        showIcon
        title="数据更新名单加载失败"
        description="名单不存在、无权访问或网络连接异常。"
        action={
          <Space>
            <Button href="/refresh-queues">返回列表</Button>
            <Button onClick={() => void detailQuery.refetch()}>重新加载</Button>
          </Space>
        }
      />
    );
  }

  const detail = detailQuery.data;

  return (
    <section className="refresh-queue-workspace" aria-label="数据更新名单">
      <PageHeader
        title="数据更新名单"
        breadcrumb={[
          { title: <Link href="/refresh-queues">数据更新</Link> },
          { title: "名单详情" },
        ]}
        extra={
          canExport || canCancel || canProcessReturn ? (
            <Space wrap>
              {canProcessReturn ? (
                <RefreshQueueReturnButton
                  queueId={queueId}
                  hasPriorReturn={hasPriorReturn}
                />
              ) : null}
              {canExport ? (
                <Button
                  type="primary"
                  icon={<DownloadOutlined aria-hidden="true" />}
                  loading={exportMutation.isPending}
                  onClick={() => void handleExport()}
                >
                  {queue?.status === "exported" ? "重新导出 CSV" : "导出 CSV"}
                </Button>
              ) : null}
              {canCancel ? (
                <Button
                  danger
                  icon={<StopOutlined aria-hidden="true" />}
                  onClick={() => setCancelOpen(true)}
                >
                  取消名单
                </Button>
              ) : null}
            </Space>
          ) : null
        }
      />

      {actionError ? (
        <Alert
          type="error"
          showIcon
          title={actionError}
          closable
          onClose={() => setActionError(null)}
        />
      ) : null}

      <Card variant="borderless" className="refresh-queue-detail-card">
        <Descriptions column={{ xs: 1, sm: 3 }} size="small">
          <Descriptions.Item label="状态">
            <RefreshQueueStatusBadge status={detail.queue.status} />
          </Descriptions.Item>
          <Descriptions.Item label="创建时间">
            {formatRefreshDateTime(detail.queue.created_at)}
          </Descriptions.Item>
          <Descriptions.Item label="名单基准时间">
            {formatRefreshDateTime(detail.queue.as_of)}
          </Descriptions.Item>
        </Descriptions>
        <RefreshQueueSummary summary={detail.summary} />
      </Card>

      <Card variant="borderless" className="refresh-queue-items-card">
        <div className="refresh-queue-section-heading">
          <div>
            <Title level={4}>名单条目</Title>
            <Text type="secondary">按名单创建时冻结的数据展示。</Text>
          </div>
          {itemsQuery.data ? (
            <Text type="secondary">共 {itemsQuery.data.total} 条</Text>
          ) : null}
        </div>
        {itemsQuery.isPending ? (
          <div className="refresh-queue-state">
            <AppLoading label="正在加载名单条目" />
          </div>
        ) : itemsQuery.isError ? (
          <Alert
            type="error"
            showIcon
            title="名单条目加载失败"
            action={
              <Button onClick={() => void itemsQuery.refetch()}>
                重新加载
              </Button>
            }
          />
        ) : itemsQuery.data?.items.length === 0 ? (
          <AppEmpty description="这个更新名单没有条目。" />
        ) : itemsQuery.data ? (
          <>
            <RefreshQueueItemTable items={itemsQuery.data.items} />
            <div className="refresh-queue-pagination">
              <Pagination
                current={page}
                pageSize={pageSize}
                total={itemsQuery.data.total}
                showSizeChanger
                pageSizeOptions={[20, 50, 100, 200]}
                showTotal={(total) => `共 ${total} 条`}
                onChange={(nextPage, nextPageSize) => {
                  setPage(nextPageSize === pageSize ? nextPage : 1);
                  setPageSize(nextPageSize);
                }}
              />
            </div>
          </>
        ) : null}
      </Card>

      <Modal
        title="取消这个更新名单？"
        open={cancelOpen}
        okText="确认取消"
        cancelText="返回"
        okButtonProps={{ danger: true }}
        confirmLoading={cancelMutation.isPending}
        onOk={() => void handleCancel()}
        onCancel={() => setCancelOpen(false)}
      >
        <Text>
          尚未完成的条目将标记为已取消，已经完成的回流结果不会被撤销。
        </Text>
      </Modal>
    </section>
  );
}
