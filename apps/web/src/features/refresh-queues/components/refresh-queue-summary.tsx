import { Typography } from "antd";

import {
  freshnessLabel,
  itemStatusPresentation,
  priorityReasonLabel,
} from "../formatters";
import type {
  RefreshFreshnessStatus,
  RefreshPriorityReason,
  RefreshQueueItemStatus,
  RefreshQueueSummary as Summary,
} from "../types";

const { Text } = Typography;

const freshnessOrder: RefreshFreshnessStatus[] = [
  "fresh",
  "aging",
  "stale",
  "very_stale",
  "unknown",
];
const itemStatusOrder: RefreshQueueItemStatus[] = [
  "pending",
  "fulfilled_changed",
  "fulfilled_no_change",
  "stale_return",
  "unresolved",
  "cancelled",
];
const tierReasons: Record<string, RefreshPriorityReason> = {
  "1": "FRESHNESS_UNKNOWN",
  "2": "VERY_STALE",
  "3": "STALE",
  "4": "AGING",
  "5": "FOLLOWERS_MISSING",
};

function Distribution({
  label,
  values,
}: {
  label: string;
  values: {
    key: string;
    label: string;
    count: number;
    textClass?: string;
  }[];
}) {
  return (
    <div className="refresh-queue-distribution">
      <Text type="secondary">{label}</Text>
      <div className="refresh-queue-distribution-list">
        {values.map((value) => (
          <span key={value.key} className="refresh-queue-distribution-item">
            <Text className={value.textClass}>{value.label}</Text>
            <Text type="secondary">{value.count}</Text>
          </span>
        ))}
      </div>
    </div>
  );
}

export function RefreshQueueSummary({ summary }: { summary: Summary }) {
  return (
    <section className="refresh-queue-summary" aria-label="名单汇总">
      <div className="refresh-queue-summary-band">
        <div>
          <Text type="secondary">请求数量</Text>
          <strong>{summary.requested}</strong>
        </div>
        <div>
          <Text type="secondary">实际选中</Text>
          <strong>{summary.selected}</strong>
        </div>
        <div>
          <Text type="secondary">涉及达人</Text>
          <strong>{summary.unique_influencers}</strong>
        </div>
      </div>
      <div className="refresh-queue-distributions">
        <Distribution
          label="创建时数据时效"
          values={freshnessOrder.map((status) => ({
            key: status,
            label: freshnessLabel(status),
            count: summary.freshness_breakdown[status] ?? 0,
            textClass: `freshness-status freshness-status-${status}`,
          }))}
        />
        <Distribution
          label="名单优先级"
          values={Object.entries(tierReasons).map(([tier, reason]) => ({
            key: tier,
            label: priorityReasonLabel(reason),
            count:
              summary.priority_breakdown[
                tier as keyof typeof summary.priority_breakdown
              ] ?? 0,
            textClass: "refresh-queue-muted-label",
          }))}
        />
        <Distribution
          label="回流状态"
          values={itemStatusOrder.map((status) => ({
            key: status,
            label: itemStatusPresentation(status).label,
            count: summary.status_breakdown[status] ?? 0,
            textClass: "refresh-queue-muted-label",
          }))}
        />
      </div>
    </section>
  );
}
