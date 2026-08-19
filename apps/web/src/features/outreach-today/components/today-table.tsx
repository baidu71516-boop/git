"use client";

import { Alert, Skeleton, Table, Tag, Tooltip, Typography } from "antd";
import Link from "next/link";

import {
  channelLabel,
  contactSummary,
  formatShanghaiDateTime,
  formatTodayDueAt,
  preferredAccountSecondary,
  priorityLabel,
  taskKindLabel,
} from "../formatters";
import type { OperatorOption, TodayItem } from "../types";

const { Text } = Typography;

export function TodayTable({
  items,
  businessDate,
  asOf,
  operators,
  loading,
}: {
  items?: TodayItem[];
  businessDate?: string;
  asOf?: string;
  operators?: OperatorOption[];
  loading?: boolean;
}) {
  if (loading) {
    return (
      <div className="today-table-skeleton" aria-label="正在加载今日触达">
        {Array.from({ length: 6 }, (_, index) => (
          <Skeleton active paragraph={{ rows: 1 }} title={false} key={index} />
        ))}
      </div>
    );
  }
  const operatorNames = new Map(
    (operators ?? []).map((operator) => [operator.id, operator.name]),
  );
  return (
    <div className="today-table-scroll">
      <Table<TodayItem>
        className="today-table"
        rowKey="task_id"
        pagination={false}
        dataSource={items ?? []}
        columns={[
          {
            title: "达人账号",
            key: "account",
            width: 180,
            render: (_, item) => (
              <Link
                className="today-account-link"
                href={`/influencers/${item.influencer_id}`}
              >
                <span className="today-account-name">
                  {item.preferred_platform_account.account_name}
                </span>
                <span className="today-account-secondary">
                  {preferredAccountSecondary(item)}
                </span>
              </Link>
            ),
          },
          {
            title: "Campaign",
            dataIndex: "campaign_name",
            key: "campaign",
            width: 160,
            render: (value: string) => (
              <Tooltip title={value}>
                <Text className="today-campaign-name">{value}</Text>
              </Tooltip>
            ),
          },
          {
            title: "当前任务",
            key: "task",
            width: 130,
            render: (_, item) => (
              <div className="today-task-cell">
                <span>{taskKindLabel(item.kind)}</span>
                {item.history_warning ? (
                  <Tooltip
                    title={`曾在 ${channelLabel(item.history_warning.channel)} 有触达记录 · ${formatShanghaiDateTime(item.history_warning.last_sent_at)}`}
                  >
                    <span className="today-history-warning">曾有历史触达</span>
                  </Tooltip>
                ) : null}
              </div>
            ),
          },
          {
            title: "渠道",
            dataIndex: "channel",
            key: "channel",
            width: 105,
            render: (value: TodayItem["channel"]) => channelLabel(value),
          },
          {
            title: "到期时间",
            dataIndex: "due_at",
            key: "due_at",
            width: 155,
            render: (value: string) => {
              const due = formatTodayDueAt(
                value,
                businessDate ?? "",
                asOf ?? "",
              );
              return (
                <span
                  className={due.overdue ? "today-due-overdue" : undefined}
                  title={due.title}
                >
                  {due.label}
                </span>
              );
            },
          },
          {
            title: "联系方式",
            key: "contact",
            width: 115,
            render: (_, item) => contactSummary(item),
          },
          {
            title: "优先级",
            dataIndex: "priority",
            key: "priority",
            width: 85,
            render: (value: TodayItem["priority"]) => (
              <Tag
                className={
                  value === "HIGH"
                    ? "today-priority-high"
                    : "today-priority-normal"
                }
              >
                {priorityLabel(value)}
              </Tag>
            ),
          },
          {
            title: "负责人",
            dataIndex: "assigned_operator_id",
            key: "owner",
            width: 105,
            render: (value: string | null) =>
              value ? (operatorNames.get(value) ?? "—") : "—",
          },
        ]}
      />
    </div>
  );
}

export function TodayTableError({ onRetry }: { onRetry: () => void }) {
  return (
    <Alert
      type="error"
      showIcon
      message="今日触达加载失败"
      description="请稍后重试。"
      action={
        <button className="today-inline-retry" type="button" onClick={onRetry}>
          重新加载
        </button>
      }
    />
  );
}
