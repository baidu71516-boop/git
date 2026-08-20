"use client";

import { Button, Skeleton, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";

import { StatusBadge } from "@/components/ui/status-badge";

import {
  campaignStatusPresentation,
  formatCampaignDateTime,
  isDisabledCampaignOwner,
} from "../formatters";
import type { Campaign } from "../types";

const { Text } = Typography;

export function CampaignTable({
  items,
  loading,
}: {
  items?: Campaign[];
  loading?: boolean;
}) {
  if (loading) {
    return (
      <div aria-label="正在加载拓客活动">
        {Array.from({ length: 6 }, (_, index) => (
          <Skeleton active paragraph={{ rows: 1 }} title={false} key={index} />
        ))}
      </div>
    );
  }

  const columns: ColumnsType<Campaign> = [
    { title: "活动名称", dataIndex: "name", key: "name", width: 260 },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 120,
      render: (status: Campaign["status"]) => {
        const presentation = campaignStatusPresentation(status);
        return (
          <StatusBadge tone={presentation.tone}>
            {presentation.label}
          </StatusBadge>
        );
      },
    },
    {
      title: "负责人",
      dataIndex: "owner",
      key: "owner",
      width: 180,
      render: (owner: Campaign["owner"]) => (
        <div>
          <div>{owner.name}</div>
          {isDisabledCampaignOwner(owner) ? (
            <Text type="secondary" className="campaign-owner-disabled">
              已停用
            </Text>
          ) : null}
        </div>
      ),
    },
    {
      title: "更新时间",
      dataIndex: "updated_at",
      key: "updated_at",
      width: 180,
      render: (value: string) => formatCampaignDateTime(value),
    },
    {
      title: "操作",
      key: "action",
      width: 120,
      fixed: "right",
      render: (_, campaign) => (
        <Button
          type="link"
          href={`/campaigns/${encodeURIComponent(campaign.id)}`}
        >
          查看详情
        </Button>
      ),
    },
  ];

  return (
    <Table<Campaign>
      className="campaign-table"
      rowKey="id"
      columns={columns}
      dataSource={items ?? []}
      pagination={false}
      scroll={{ x: 850 }}
    />
  );
}
