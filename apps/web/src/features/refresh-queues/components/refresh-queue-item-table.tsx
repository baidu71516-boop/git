import { Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";

import {
  formatRefreshDateTime,
  freshnessLabel,
  platformLabel,
  priorityReasonLabel,
  snapshotFreshnessFromReasons,
} from "../formatters";
import type { RefreshQueueItem } from "../types";
import { RefreshQueueItemStatusBadge } from "./refresh-queue-status";

const { Text } = Typography;

const columns: ColumnsType<RefreshQueueItem> = [
  {
    title: "平台账号",
    key: "account",
    width: 250,
    render: (_, item) => {
      const snapshot = item.identity_snapshot;
      const locator =
        snapshot.account_handle ??
        snapshot.platform_account_id ??
        snapshot.external_source_id ??
        "—";
      return (
        <div className="refresh-account-cell">
          <Text strong>{snapshot.account_name ?? locator}</Text>
          <Space size={6} wrap>
            <Text type="secondary" className="refresh-queue-account-meta">
              {platformLabel(snapshot.platform)} · {locator}
            </Text>
          </Space>
        </div>
      );
    },
  },
  {
    title: "创建时数据时效",
    key: "freshness",
    width: 140,
    render: (_, item) => (
      <Text>
        {freshnessLabel(snapshotFreshnessFromReasons(item.priority_reasons))}
      </Text>
    ),
  },
  {
    title: "优先原因",
    dataIndex: "priority_reasons",
    width: 220,
    render: (reasons: RefreshQueueItem["priority_reasons"]) => (
      <Space size={[4, 4]} wrap>
        {reasons.map((reason) => (
          <Tag key={reason} color="default" className="refresh-queue-meta-tag">
            {priorityReasonLabel(reason)}
          </Tag>
        ))}
      </Space>
    ),
  },
  {
    title: "最近可靠采集",
    dataIndex: "baseline_last_observed_at",
    width: 170,
    render: (value: string | null) => formatRefreshDateTime(value),
  },
  {
    title: "回流状态",
    dataIndex: "status",
    width: 170,
    render: (status: RefreshQueueItem["status"]) => (
      <RefreshQueueItemStatusBadge status={status} />
    ),
  },
];

export function RefreshQueueItemTable({
  items,
}: {
  items: RefreshQueueItem[];
}) {
  return (
    <Table<RefreshQueueItem>
      className="refresh-queue-table"
      rowKey="id"
      columns={columns}
      dataSource={items}
      pagination={false}
      scroll={{ x: 950 }}
      size="middle"
    />
  );
}
