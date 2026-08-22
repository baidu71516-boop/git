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
  detailHref,
  onViewCampaign,
}: {
  items?: Campaign[];
  loading?: boolean;
  detailHref?: string | ((campaign: Campaign) => string);
  onViewCampaign?: (campaign: Campaign) => void;
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
    {
      title: "活动名称",
      dataIndex: "name",
      key: "name",
      width: 340,
      render: (name: string) => (
        <div className="campaign-table-name-cell">
          <span className="campaign-table-name-content" title={name}>
            {name}
          </span>
        </div>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 106,
      render: (status: Campaign["status"]) => {
        const presentation = campaignStatusPresentation(status);
        return (
          <StatusBadge
            tone={presentation.tone}
            className="campaign-table-status-badge"
          >
            {presentation.label}
          </StatusBadge>
        );
      },
    },
    {
      title: "负责人",
      dataIndex: "owner",
      key: "owner",
      width: 150,
      render: (owner: Campaign["owner"]) => (
        <div className="campaign-owner-cell">
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
      width: 150,
      render: (value: string) => formatCampaignDateTime(value),
    },
    {
      title: "操作",
      key: "action",
      width: 102,
      fixed: "right",
      render: (_, campaign) => (
        <Button
          type="link"
          href={(() => {
            if (onViewCampaign) return undefined;
            if (typeof detailHref === "function") {
              return detailHref(campaign);
            }
            if (detailHref) {
              return `${detailHref.replace(/\/$/, "")}/${encodeURIComponent(
                campaign.id,
              )}`;
            }
            return `/campaigns/${encodeURIComponent(campaign.id)}`;
          })()}
          onClick={() => {
            if (onViewCampaign) {
              onViewCampaign(campaign);
            }
          }}
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
