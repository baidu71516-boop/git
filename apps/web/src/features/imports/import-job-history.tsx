"use client";

import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Skeleton,
  Space,
  Table,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { PageHeader } from "@/components/ui/page-header";
import { StatusBadge } from "@/components/ui/status-badge";

import { formatBulkDateTime } from "./formatters";
import {
  formatImportCount,
  formatPersistedImportResult,
  getImportJobStatusPresentation,
  getImportSourceLabel,
  getPersistedImportResult,
  importResultFields,
} from "./import-job-history-presenters";
import {
  useImportJobHistory,
  useImportJobHistoryDetail,
} from "./import-job-history-queries";
import type { ImportJobPublic } from "./types";

const { Paragraph, Text, Title } = Typography;

export const IMPORT_JOB_HISTORY_LIMIT = 50;

export function parseImportJobHistoryOffset(value: string | null): number {
  if (!value || !/^\d+$/.test(value)) return 0;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : 0;
}

function JobStatus({ job }: { job: Pick<ImportJobPublic, "status"> }) {
  const presentation = getImportJobStatusPresentation(job.status);
  return (
    <StatusBadge tone={presentation.tone}>{presentation.label}</StatusBadge>
  );
}

function historyColumns(
  onView: (job: ImportJobPublic) => void,
): ColumnsType<ImportJobPublic> {
  return [
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 168,
      render: (value: string) => formatBulkDateTime(value),
    },
    {
      title: "来源",
      dataIndex: "source_type",
      width: 118,
      render: (sourceType: ImportJobPublic["source_type"]) =>
        getImportSourceLabel(sourceType),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 190,
      render: (_, job) => <JobStatus job={job} />,
    },
    {
      title: "数据量",
      dataIndex: "total_rows",
      width: 110,
      align: "right",
      render: (value: number) => `${formatImportCount(value)} 行`,
    },
    {
      title: "处理结果",
      key: "result",
      width: 370,
      render: (_, job) => (
        <Text type="secondary" className="import-job-result-summary">
          {formatPersistedImportResult(job)}
        </Text>
      ),
    },
    {
      title: "完成时间",
      dataIndex: "completed_at",
      width: 168,
      render: (value: string | null) => formatBulkDateTime(value),
    },
    {
      title: "操作",
      key: "action",
      width: 96,
      fixed: "right",
      render: (_, job) => (
        <Button type="link" onClick={() => onView(job)}>
          查看详情
        </Button>
      ),
    },
  ];
}

type SkeletonRow = { id: string };

function SkeletonCell() {
  return <Skeleton.Input active size="small" block />;
}

const skeletonColumns: ColumnsType<SkeletonRow> = [
  { title: "创建时间", width: 168, render: SkeletonCell },
  { title: "来源", width: 118, render: SkeletonCell },
  { title: "状态", width: 190, render: SkeletonCell },
  { title: "数据量", width: 110, render: SkeletonCell },
  { title: "处理结果", width: 370, render: SkeletonCell },
  { title: "完成时间", width: 168, render: SkeletonCell },
  { title: "操作", width: 96, fixed: "right", render: SkeletonCell },
];

function ImportJobHistorySkeleton() {
  return (
    <div role="status" aria-label="导入记录加载中">
      <Table<SkeletonRow>
        className="import-job-history-table"
        rowKey="id"
        columns={skeletonColumns}
        dataSource={Array.from({ length: 6 }, (_, index) => ({
          id: `skeleton-${index}`,
        }))}
        pagination={false}
        scroll={{ x: 1_220 }}
        size="middle"
      />
    </div>
  );
}

function ImportResultStatistics({ job }: { job: ImportJobPublic }) {
  const result = getPersistedImportResult(job);
  if (!result) {
    return <Text type="secondary">暂无最终处理统计。</Text>;
  }

  return (
    <div className="import-job-result-grid">
      {importResultFields.map(([field, label]) => (
        <div key={field}>
          <Text type="secondary">{label}</Text>
          <strong>{formatImportCount(result[field] as number)}</strong>
        </div>
      ))}
    </div>
  );
}

function ImportJobDetailDrawer({
  importJobId,
  onClose,
}: {
  importJobId: string | null;
  onClose: () => void;
}) {
  const detailQuery = useImportJobHistoryDetail(
    importJobId ?? "",
    importJobId !== null,
  );
  const job = detailQuery.data;

  return (
    <Drawer
      className="import-job-history-drawer"
      title="导入任务详情"
      placement="right"
      size={640}
      open={importJobId !== null}
      onClose={onClose}
    >
      {detailQuery.isPending ? (
        <Skeleton active paragraph={{ rows: 10 }} />
      ) : detailQuery.isError || !job ? (
        <Alert
          type="error"
          showIcon
          title="导入任务详情加载失败"
          description="请稍后重试。"
          action={
            <Button onClick={() => void detailQuery.refetch()}>重新加载</Button>
          }
        />
      ) : (
        <div className="import-job-drawer-content">
          <Descriptions column={2} size="small" layout="vertical">
            <Descriptions.Item label="状态">
              <JobStatus job={job} />
            </Descriptions.Item>
            <Descriptions.Item label="来源">
              {getImportSourceLabel(job.source_type)}
            </Descriptions.Item>
            <Descriptions.Item label="创建时间">
              {formatBulkDateTime(job.created_at)}
            </Descriptions.Item>
            <Descriptions.Item label="完成时间">
              {formatBulkDateTime(job.completed_at)}
            </Descriptions.Item>
          </Descriptions>

          <section className="import-job-drawer-section" aria-label="处理统计">
            <Title level={5}>处理结果</Title>
            <ImportResultStatistics job={job} />
          </section>

          <section className="import-job-drawer-section" aria-label="错误信息">
            <Title level={5}>错误信息</Title>
            {job.error_code || job.error_message ? (
              <Alert
                type="error"
                showIcon
                title={job.error_message ?? "任务处理失败"}
                description={
                  job.error_code ? <Text code>{job.error_code}</Text> : null
                }
              />
            ) : (
              <Text type="secondary">无</Text>
            )}
          </section>

          <section
            className="import-job-drawer-section import-job-id-section"
            aria-label="任务标识"
          >
            <Text type="secondary">任务 ID</Text>
            <Paragraph type="secondary" copyable={{ text: job.id }}>
              {job.id}
            </Paragraph>
          </section>
        </div>
      )}
    </Drawer>
  );
}

export function ImportJobHistory() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const offset = parseImportJobHistoryOffset(searchParams.get("offset"));
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const listQuery = useImportJobHistory(offset, IMPORT_JOB_HISTORY_LIMIT);

  function navigateToOffset(nextOffset: number) {
    const normalizedOffset = Math.max(0, nextOffset);
    const nextParams = new URLSearchParams(searchParams.toString());
    if (normalizedOffset === 0) {
      nextParams.delete("offset");
    } else {
      nextParams.set("offset", String(normalizedOffset));
    }
    const query = nextParams.toString();
    router.replace(query ? `/import-jobs?${query}` : "/import-jobs", {
      scroll: false,
    });
  }

  const data = listQuery.data;
  const hasPreviousPage = offset > 0;
  const hasNextPage = data
    ? offset + IMPORT_JOB_HISTORY_LIMIT < data.total
    : false;

  return (
    <section className="import-job-history" aria-label="导入记录">
      <PageHeader
        title="导入记录"
        description="查看导入任务的当前状态和最终处理结果。"
      />

      <Card variant="borderless" className="import-job-history-card">
        {listQuery.isPending ? (
          <ImportJobHistorySkeleton />
        ) : listQuery.isError ? (
          <Alert
            type="error"
            showIcon
            title="导入记录加载失败"
            description="请稍后重试。"
            action={
              <Button onClick={() => void listQuery.refetch()}>重新加载</Button>
            }
          />
        ) : data?.total === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Space orientation="vertical" size={2}>
                <Text>暂无导入记录</Text>
                <Text type="secondary">
                  完成数据采集后，相关导入任务会显示在这里。
                </Text>
              </Space>
            }
          >
            <Button type="primary" href="/">
              前往数据采集
            </Button>
          </Empty>
        ) : data ? (
          <>
            <Table<ImportJobPublic>
              className="import-job-history-table"
              rowKey="id"
              columns={historyColumns((job) => setSelectedJobId(job.id))}
              dataSource={data.items}
              pagination={false}
              scroll={{ x: 1_220 }}
              size="middle"
            />
            <div className="import-job-history-pagination">
              <Text type="secondary">共 {data.total} 条记录</Text>
              <Space>
                <Button
                  disabled={!hasPreviousPage}
                  onClick={() =>
                    navigateToOffset(offset - IMPORT_JOB_HISTORY_LIMIT)
                  }
                >
                  上一页
                </Button>
                <Button
                  disabled={!hasNextPage}
                  onClick={() =>
                    navigateToOffset(offset + IMPORT_JOB_HISTORY_LIMIT)
                  }
                >
                  下一页
                </Button>
              </Space>
            </div>
          </>
        ) : null}
      </Card>

      <ImportJobDetailDrawer
        importJobId={selectedJobId}
        onClose={() => setSelectedJobId(null)}
      />
    </section>
  );
}
