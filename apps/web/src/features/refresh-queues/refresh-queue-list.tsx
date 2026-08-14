"use client";

import { PlusOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Pagination, Space, Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { AppEmpty } from "@/components/ui/app-empty";
import { AppLoading } from "@/components/ui/app-loading";
import { PageHeader } from "@/components/ui/page-header";

import { CreateRefreshQueueModal } from "./components/create-refresh-queue-modal";
import { RefreshQueueStatusBadge } from "./components/refresh-queue-status";
import { formatRefreshDateTime } from "./formatters";
import { useRefreshQueueList } from "./queries";
import type { DepartmentOption, RefreshQueue, RefreshQueueRole } from "./types";

const columns: ColumnsType<RefreshQueue> = [
  {
    title: "创建时间",
    dataIndex: "created_at",
    width: 170,
    render: (value: string) => formatRefreshDateTime(value),
  },
  {
    title: "状态",
    dataIndex: "status",
    width: 130,
    render: (status: RefreshQueue["status"]) => (
      <RefreshQueueStatusBadge status={status} />
    ),
  },
  {
    title: "目标数量",
    dataIndex: "requested_limit",
    width: 120,
  },
  {
    title: "本次更新上限",
    dataIndex: "refresh_limit",
    width: 140,
  },
  {
    title: "今日计划上限",
    dataIndex: "today_total_limit",
    width: 140,
  },
  {
    title: "名单基准时间",
    dataIndex: "as_of",
    width: 170,
    render: (value: string) => formatRefreshDateTime(value),
  },
  {
    title: "操作",
    key: "action",
    width: 110,
    fixed: "right",
    render: (_, queue) => (
      <Button
        type="link"
        href={`/refresh-queues/${encodeURIComponent(queue.id)}`}
      >
        查看详情
      </Button>
    ),
  },
];

export function RefreshQueueList({
  role,
  departments,
  currentDepartmentId,
}: {
  role: RefreshQueueRole;
  departments: DepartmentOption[];
  currentDepartmentId: string;
}) {
  const router = useRouter();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [createOpen, setCreateOpen] = useState(false);
  const listQuery = useRefreshQueueList((page - 1) * pageSize, pageSize);
  const canCreate = role !== "viewer";

  return (
    <section className="refresh-queue-workspace" aria-label="数据更新">
      <PageHeader
        title="数据更新"
        description="集中管理需要重新采集的达人数据名单。"
        extra={
          canCreate ? (
            <Button
              type="primary"
              icon={<PlusOutlined aria-hidden="true" />}
              onClick={() => setCreateOpen(true)}
            >
              创建更新名单
            </Button>
          ) : null
        }
      />

      <Card variant="borderless" className="refresh-queue-list-card">
        {listQuery.isPending ? (
          <div className="refresh-queue-state">
            <AppLoading label="正在加载数据更新列表" />
          </div>
        ) : listQuery.isError ? (
          <Alert
            type="error"
            showIcon
            title="数据更新列表加载失败"
            description="请检查网络连接后重新加载。"
            action={
              <Button onClick={() => void listQuery.refetch()}>重新加载</Button>
            }
          />
        ) : listQuery.data?.items.length === 0 ? (
          <AppEmpty description="暂无数据更新名单。" />
        ) : listQuery.data ? (
          <Space orientation="vertical" size="middle" className="full-width">
            <Table<RefreshQueue>
              className="refresh-queue-table"
              rowKey="id"
              columns={columns}
              dataSource={listQuery.data.items}
              pagination={false}
              scroll={{ x: 1050 }}
              size="middle"
            />
            <div className="refresh-queue-pagination">
              <Pagination
                current={page}
                pageSize={pageSize}
                total={listQuery.data.total}
                showSizeChanger
                pageSizeOptions={[20, 50, 100, 200]}
                showTotal={(total) => `共 ${total} 个名单`}
                onChange={(nextPage, nextPageSize) => {
                  setPage(nextPageSize === pageSize ? nextPage : 1);
                  setPageSize(nextPageSize);
                }}
              />
            </div>
          </Space>
        ) : null}
      </Card>

      {canCreate ? (
        <CreateRefreshQueueModal
          open={createOpen}
          role={role}
          departments={departments}
          currentDepartmentId={currentDepartmentId}
          onClose={() => setCreateOpen(false)}
          onCreated={(detail) => {
            setCreateOpen(false);
            router.push(
              `/refresh-queues/${encodeURIComponent(detail.queue.id)}`,
            );
          }}
        />
      ) : null}
    </section>
  );
}
