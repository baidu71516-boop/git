import { Card, Descriptions, Empty, Typography } from "antd";

import {
  formatShanghaiDate,
  metricLabel,
  renderJsonValue,
} from "../formatters";
import type { CurrentMetricsDetail, PlatformAccountDetail } from "../types";

const { Title } = Typography;

function accountName(accounts: PlatformAccountDetail[], accountId: string) {
  return (
    accounts.find((account) => account.id === accountId)?.account_name ?? "—"
  );
}

export function CurrentMetricsSection({
  accounts,
  metrics,
}: {
  accounts: PlatformAccountDetail[];
  metrics: CurrentMetricsDetail[];
}) {
  return (
    <section aria-label="当前指标">
      <Title level={4}>当前指标</Title>
      {metrics.length === 0 ? (
        <Empty description="暂无当前指标" />
      ) : (
        <div className="influencer-detail-grid">
          {metrics.map((metric) => (
            <Card
              key={`${metric.platform_account_id}-${metric.source}`}
              size="small"
              title={`${accountName(accounts, metric.platform_account_id)} · ${metric.source}`}
            >
              <Descriptions column={1} size="small">
                <Descriptions.Item label="来源更新时间">
                  {formatShanghaiDate(metric.source_updated_at)}
                </Descriptions.Item>
                {Object.entries(metric.metrics).map(([key, value]) => (
                  <Descriptions.Item key={key} label={metricLabel(key)}>
                    {renderJsonValue(value, key)}
                  </Descriptions.Item>
                ))}
                <Descriptions.Item label="最近记录的 Import Job">
                  {metric.last_import_job_id}
                </Descriptions.Item>
                <Descriptions.Item label="最近记录的 Import Row">
                  {metric.last_import_row_id}
                </Descriptions.Item>
              </Descriptions>
            </Card>
          ))}
        </div>
      )}
    </section>
  );
}
