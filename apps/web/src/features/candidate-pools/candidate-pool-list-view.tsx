"use client";

import { ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Skeleton, Table, Typography } from "antd";
import Link from "next/link";
import { useMemo } from "react";

import { AppEmpty } from "@/components/ui/app-empty";
import { StatusBadge } from "@/components/ui/status-badge";

import {
  candidateDateTime,
  candidatePoolKindLabel,
  candidatePoolStatus,
} from "./formatters";
import { useCandidatePoolList } from "./queries";
import type { CandidatePool } from "./types";

const { Text } = Typography;

function Owner({ pool }: { pool: CandidatePool }) {
  return (
    <span>
      {pool.owner.name}
      {pool.owner.status === "disabled" ? (
        <Text type="secondary"> · 已停用</Text>
      ) : null}
    </span>
  );
}

export function CandidatePoolListView({
  previewState,
  previewMode = false,
}: {
  previewState?: "empty" | "error";
  previewMode?: boolean;
}) {
  const query = useCandidatePoolList();
  const pools = useMemo(
    () => query.data?.pages.flatMap((page) => page.items) ?? [],
    [query.data],
  );
  const columns = [
    { title: "候选池名称", dataIndex: "name", key: "name", width: 250 },
    {
      title: "类型",
      key: "kind",
      width: 128,
      render: (_: unknown, pool: CandidatePool) =>
        candidatePoolKindLabel(pool.kind),
    },
    {
      title: "状态",
      key: "status",
      width: 110,
      render: (_: unknown, pool: CandidatePool) => {
        const status = candidatePoolStatus(pool.status);
        return <StatusBadge tone={status.tone}>{status.label}</StatusBadge>;
      },
    },
    {
      title: "负责人",
      key: "owner",
      width: 160,
      render: (_: unknown, pool: CandidatePool) => <Owner pool={pool} />,
    },
    {
      title: "更新时间",
      key: "updated_at",
      width: 168,
      render: (_: unknown, pool: CandidatePool) =>
        candidateDateTime(pool.updated_at),
    },
    {
      title: "操作",
      key: "action",
      width: 104,
      render: (_: unknown, pool: CandidatePool) => (
        <Link
          className="candidate-table-action"
          href={`/candidate-pools/${encodeURIComponent(pool.id)}`}
        >
          查看详情
        </Link>
      ),
    },
  ];
  return (
    <section
      className="candidate-pool-workspace campaign-workspace"
      aria-label="候选池"
    >
      <div>
        <h2 className="page-title">候选池</h2>
        <Text type="secondary">查看用于筛选目标达人的候选池。</Text>
      </div>
      <Card
        className="candidate-pool-list-card campaign-list-card"
        variant="borderless"
      >
        {query.isPending ? (
          <Skeleton active paragraph={{ rows: 7 }} />
        ) : query.isError || previewState === "error" ? (
          <Alert
            type="error"
            showIcon
            title="候选池加载失败"
            description="请稍后重试。"
            action={
              <Button onClick={() => void query.refetch()}>重新加载</Button>
            }
          />
        ) : previewState === "empty" || pools.length === 0 ? (
          <div className="campaign-empty-state">
            <AppEmpty description="暂无候选池" />
            <Text type="secondary">当前没有可查看的候选池。</Text>
          </div>
        ) : (
          <>
            <div className="campaign-table-shell">
              <Table<CandidatePool>
                className="candidate-pool-table campaign-table"
                rowKey="id"
                columns={columns}
                dataSource={pools}
                pagination={false}
              />
            </div>
            <div className="campaign-pagination-footer">
              {query.hasNextPage ? (
                <Button
                  icon={<ReloadOutlined aria-hidden="true" />}
                  loading={previewMode ? false : query.isFetchingNextPage}
                  onClick={() => {
                    if (!previewMode) void query.fetchNextPage();
                  }}
                >
                  加载更多
                </Button>
              ) : null}
            </div>
          </>
        )}
      </Card>
    </section>
  );
}
