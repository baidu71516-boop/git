"use client";

import {
  DownloadOutlined,
  PlusOutlined,
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
import type { ReactNode } from "react";
import { useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { PageHeader } from "@/components/ui/page-header";

import { CreateRefreshQueueModal } from "./components/create-refresh-queue-modal";
import { RefreshQueueItemTable } from "./components/refresh-queue-item-table";
import { RefreshQueueStatusBadge } from "./components/refresh-queue-status";
import { RefreshQueueSummary } from "./components/refresh-queue-summary";
import { downloadBlob, formatRefreshDateTime } from "./formatters";
import {
  createRefreshQueuePreviewItems,
  getRefreshQueuePreviewDetail,
  refreshQueuePreviewQueues,
} from "./preview-fixtures";
import { RefreshQueueListTable } from "./refresh-queue-list";
import { RefreshQueueReturnButton } from "./refresh-queue-detail";
import type { RefreshQueue, RefreshQueueStatus } from "./types";

const { Text, Title } = Typography;
const noOperation = () => undefined;

function PreviewShell({ children }: { children: ReactNode }) {
  return (
    <AppShell
      title="数据更新 · 界面预览"
      department="界面预览"
      operator="预览操作人"
      role="仅展示"
      onLogout={noOperation}
    >
      {children}
    </AppShell>
  );
}

const previewListItems: RefreshQueue[] = Array.from(
  { length: 54 },
  (_, index) => {
    const source =
      refreshQueuePreviewQueues[index % refreshQueuePreviewQueues.length];
    return {
      ...source,
      id: index < 4 ? source.id : `${source.status}-${index + 1}`,
      created_at: new Date(
        new Date(source.created_at).getTime() - index * 3_600_000,
      ).toISOString(),
    };
  },
);

export function RefreshQueueListPreviewWorkspace() {
  const [page, setPage] = useState(1);
  const [createOpen, setCreateOpen] = useState(false);
  const [created, setCreated] = useState(false);
  const pageSize = 50;
  const items = previewListItems.slice((page - 1) * pageSize, page * pageSize);

  async function previewCreate() {
    return getRefreshQueuePreviewDetail("open");
  }

  return (
    <PreviewShell>
      <section className="refresh-queue-workspace" aria-label="数据更新预览">
        <Alert
          type="info"
          showIcon
          title="开发预览状态"
          description="仅开发环境可见，使用内存 fixture，不访问 Auth、API 或数据库。"
        />
        {created ? (
          <Alert
            type="success"
            showIcon
            title="已完成本地创建交互预览"
            closable
            onClose={() => setCreated(false)}
          />
        ) : null}
        <PageHeader
          title="数据更新"
          description="集中管理需要重新采集的达人数据名单。"
          extra={
            <Button
              type="primary"
              icon={<PlusOutlined aria-hidden="true" />}
              onClick={() => setCreateOpen(true)}
            >
              创建更新名单
            </Button>
          }
        />
        <Card variant="borderless" className="refresh-queue-list-card">
          <RefreshQueueListTable
            items={items}
            detailHref={(queue) =>
              `/dev-ui-preview/refresh-queues/${queue.status}`
            }
          />
          <div className="refresh-queue-pagination">
            <Pagination
              current={page}
              pageSize={pageSize}
              total={previewListItems.length}
              showTotal={(total) => `共 ${total} 个名单`}
              onChange={setPage}
            />
          </div>
        </Card>
        <CreateRefreshQueueModal
          open={createOpen}
          role="super_admin"
          departments={[
            { id: "preview-department", name: "预览部门" },
            { id: "preview-department-2", name: "跨部门预览" },
          ]}
          currentDepartmentId="preview-department"
          create={previewCreate}
          onClose={() => setCreateOpen(false)}
          onCreated={() => {
            setCreateOpen(false);
            setCreated(true);
          }}
        />
      </section>
    </PreviewShell>
  );
}

export function RefreshQueueDetailPreviewWorkspace({
  initialStatus,
}: {
  initialStatus: RefreshQueueStatus;
}) {
  const [page, setPage] = useState(1);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelled, setCancelled] = useState(false);
  const [exported, setExported] = useState(false);
  const pageSize = 50;
  const status = cancelled ? "cancelled" : initialStatus;
  const detail = useMemo(() => getRefreshQueuePreviewDetail(status), [status]);
  const items = useMemo(() => createRefreshQueuePreviewItems(status), [status]);
  const visibleItems = items.slice((page - 1) * pageSize, page * pageSize);
  const canAct = status === "open" || status === "exported";
  const statusBreakdown = detail.summary.status_breakdown;
  const unfinishedCount =
    (statusBreakdown.pending ?? 0) +
    (statusBreakdown.stale_return ?? 0) +
    (statusBreakdown.unresolved ?? 0);
  const hasPriorReturn =
    (statusBreakdown.fulfilled_changed ?? 0) +
      (statusBreakdown.fulfilled_no_change ?? 0) +
      (statusBreakdown.stale_return ?? 0) +
      (statusBreakdown.unresolved ?? 0) >
    0;
  const canProcessReturn = status === "exported" && unfinishedCount > 0;

  function previewExport() {
    downloadBlob(
      new Blob(["account_name,platform\n山野生活研究所,xiaohongshu\n"], {
        type: "text/csv",
      }),
      `refresh-queue-${detail.queue.id}.csv`,
    );
    setExported(true);
  }

  return (
    <PreviewShell>
      <section
        className="refresh-queue-workspace"
        aria-label="数据更新名单预览"
      >
        <Alert
          type="info"
          showIcon
          title="开发预览状态"
          description="仅开发环境可见，所有状态和交互均来自本地内存 fixture。"
        />
        <div className="refresh-queue-preview-state-links">
          <Text type="secondary">查看状态</Text>
          <Space wrap>
            {refreshQueuePreviewQueues.map((queue) => (
              <Link
                key={queue.status}
                href={`/dev-ui-preview/refresh-queues/${queue.status}`}
              >
                <Button
                  type={initialStatus === queue.status ? "primary" : "default"}
                >
                  <RefreshQueueStatusBadge status={queue.status} />
                </Button>
              </Link>
            ))}
          </Space>
        </div>
        {exported ? (
          <Alert type="success" showIcon title="已触发本地 CSV 下载预览" />
        ) : null}
        <PageHeader
          title="数据更新名单"
          breadcrumb={[
            {
              title: (
                <Link href="/dev-ui-preview/refresh-queues">数据更新</Link>
              ),
            },
            { title: "名单详情" },
          ]}
          extra={
            canAct ? (
              <Space wrap>
                {canProcessReturn ? (
                  <RefreshQueueReturnButton
                    queueId={detail.queue.id}
                    hasPriorReturn={hasPriorReturn}
                    emphasize={canProcessReturn}
                  />
                ) : null}
                <Button
                  type={canProcessReturn ? "default" : "primary"}
                  icon={<DownloadOutlined aria-hidden="true" />}
                  onClick={previewExport}
                >
                  {status === "exported" ? "重新导出 CSV" : "导出 CSV"}
                </Button>
                <Button
                  danger
                  icon={<StopOutlined aria-hidden="true" />}
                  onClick={() => setCancelOpen(true)}
                >
                  取消名单
                </Button>
              </Space>
            ) : null
          }
        />
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
            <Text type="secondary">共 {items.length} 条</Text>
          </div>
          <RefreshQueueItemTable items={visibleItems} />
          <div className="refresh-queue-pagination">
            <Pagination
              current={page}
              pageSize={pageSize}
              total={items.length}
              showTotal={(total) => `共 ${total} 条`}
              onChange={setPage}
            />
          </div>
        </Card>
        <Modal
          title="取消这个更新名单？"
          open={cancelOpen}
          okText="确认取消"
          cancelText="返回"
          okButtonProps={{ danger: true }}
          onOk={() => {
            setCancelled(true);
            setCancelOpen(false);
          }}
          onCancel={() => setCancelOpen(false)}
        >
          <Text>
            尚未完成的条目将标记为已取消，已经完成的回流结果不会被撤销。
          </Text>
        </Modal>
      </section>
    </PreviewShell>
  );
}
