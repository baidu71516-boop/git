import { Alert, Button, Card, Empty, Space, Spin, Typography } from "antd";
import type { UseQueryResult } from "@tanstack/react-query";

import {
  formatShanghaiDate,
  metricLabel,
  renderJsonValue,
} from "../formatters";
import type { MetricSnapshotPage, PlatformAccountDetail } from "../types";

const { Paragraph, Text, Title } = Typography;

function accountName(accounts: PlatformAccountDetail[], accountId: string) {
  return (
    accounts.find((account) => account.id === accountId)?.account_name ?? "—"
  );
}

export function MetricSnapshotList({
  accounts,
  page,
  pageSize,
  query,
  onPageChange,
}: {
  accounts: PlatformAccountDetail[];
  page: number;
  pageSize: number;
  query: UseQueryResult<MetricSnapshotPage, Error>;
  onPageChange: (page: number) => void;
}) {
  return (
    <section aria-label="指标历史">
      <Title level={4}>指标历史</Title>
      <Paragraph type="secondary">
        历史快照记录的是当次输入，不等于当前合并指标。
      </Paragraph>
      {query.isPending ? (
        <div className="influencer-list-state">
          <Spin />
          <span>正在加载指标历史</span>
        </div>
      ) : query.isError ? (
        <Alert
          type="error"
          showIcon
          message="指标历史加载失败"
          action={
            <Button onClick={() => void query.refetch()}>重试指标历史</Button>
          }
        />
      ) : (
        <Space orientation="vertical" size="middle" className="full-width">
          {query.data.items.length ? (
            query.data.items.map((snapshot) => (
              <Card
                key={snapshot.id}
                size="small"
                title={`${accountName(accounts, snapshot.platform_account_id)} · ${snapshot.source}`}
              >
                <p>捕获时间：{formatShanghaiDate(snapshot.captured_at)}</p>
                <p>
                  来源更新时间：{formatShanghaiDate(snapshot.source_updated_at)}
                </p>
                <dl className="snapshot-metrics">
                  {Object.entries(snapshot.metrics).map(([key, value]) => (
                    <div key={key}>
                      <dt>{metricLabel(key)}</dt>
                      <dd>
                        {renderJsonValue(
                          value,
                          `snapshot-${snapshot.id}-${key}`,
                        )}
                      </dd>
                    </div>
                  ))}
                </dl>
                <p>
                  导入任务 / 数据行：{snapshot.import_job_id} /{" "}
                  {snapshot.import_row_id}
                </p>
              </Card>
            ))
          ) : (
            <Empty description="暂无指标历史" />
          )}
          <div className="influencer-pagination">
            <Text>共 {query.data.total} 条历史快照</Text>
            <Space>
              <Button
                aria-label="上一页历史快照"
                disabled={page <= 1}
                onClick={() => onPageChange(page - 1)}
              >
                上一页
              </Button>
              <Text>第 {page} 页</Text>
              <Button
                aria-label="下一页历史快照"
                disabled={page * pageSize >= query.data.total}
                onClick={() => onPageChange(page + 1)}
              >
                下一页
              </Button>
            </Space>
          </div>
        </Space>
      )}
    </section>
  );
}
