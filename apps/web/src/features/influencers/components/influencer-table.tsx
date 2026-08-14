"use client";

import { Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import Link from "next/link";
import type { ReactNode } from "react";

import { AppTooltip } from "@/components/ui/app-tooltip";

import {
  contactTypeLabel,
  crmStageDisplay,
  formatExactFollowers,
  formatFollowers,
  formatMetricsTimestamp,
  formatMetricsTimestampTooltip,
  latestMetricsTimestamp,
  platformLabel,
} from "../formatters";
import type { InfluencerListItem, PlatformAccountSummary } from "../types";
import { FreshnessStatusText } from "./freshness-status-text";

const { Text } = Typography;

function unique(values: string[]): string[] {
  return Array.from(new Set(values));
}

function normalizeDetailHref(prefix: string): string {
  return prefix.endsWith("/") ? prefix.slice(0, -1) : prefix;
}

function buildColumns(
  detailHrefPrefix: string,
  onOpenDetail?: (id: string) => void,
): ColumnsType<InfluencerListItem> {
  const detailBaseHref = normalizeDetailHref(detailHrefPrefix);
  const renderLinkOrButton = ({
    id,
    className,
    children,
  }: {
    id: string;
    className: string;
    children: ReactNode;
  }) => {
    if (onOpenDetail) {
      return (
        <button
          type="button"
          className={`${className} influencer-detail-trigger`}
          onClick={() => {
            onOpenDetail(id);
          }}
        >
          {children}
        </button>
      );
    }

    return (
      <Link className={className} href={`${detailBaseHref}/${id}`}>
        {children}
      </Link>
    );
  };

  return [
    {
      title: "达人",
      key: "influencer",
      width: 210,
      render: (_, item) => {
        const account = item.platform_accounts[0];
        return (
          <div className="influencer-primary-cell">
            {renderLinkOrButton({
              id: item.id,
              className: "influencer-name-link",
              children: item.display_name,
            })}
            {account ? (
              <AppTooltip title={accountTooltip(item)}>
                <Text className="influencer-account-line" type="secondary">
                  {accountSummary(item, account)}
                </Text>
              </AppTooltip>
            ) : (
              <Text type="secondary">—</Text>
            )}
          </div>
        );
      },
    },
    {
      title: "平台 / 标签",
      key: "platform_tags",
      width: 196,
      render: (_, item) => {
        const platforms = unique(
          item.platform_accounts.map((account) => account.platform),
        );
        const tags = unique(
          item.platform_accounts.flatMap((account) => account.source_tags),
        );
        const visibleTags = tags.slice(0, 2);
        const hiddenCount = tags.length - visibleTags.length;
        return (
          <div className="influencer-platform-tags">
            <div className="influencer-tag-line">
              {platforms.length ? (
                platforms.map((platform) => (
                  <Tag
                    className={`platform-tag ${platformTagClass(platform)}`}
                    key={platform}
                  >
                    {platformLabel(platform)}
                  </Tag>
                ))
              ) : (
                <Text type="secondary">—</Text>
              )}
            </div>
            <div className="influencer-tag-line">
              {visibleTags.length ? (
                <>
                  {visibleTags.map((tag) => (
                    <AppTooltip title={tag} key={tag}>
                      <Tag className="source-tag">{tag}</Tag>
                    </AppTooltip>
                  ))}
                  {hiddenCount > 0 ? (
                    <AppTooltip title={tags.join("、")}>
                      <Tag className="source-tag-more">+{hiddenCount}</Tag>
                    </AppTooltip>
                  ) : null}
                </>
              ) : (
                <Text type="secondary">—</Text>
              )}
            </div>
          </div>
        );
      },
    },
    {
      title: "粉丝",
      key: "followers",
      width: 100,
      align: "right",
      render: (_, item) => {
        const value = followerValue(item);
        return value === null ? (
          "—"
        ) : (
          <AppTooltip title={formatExactFollowers(value)}>
            <Text className="influencer-follower-value" strong>
              {formatFollowers(value)}
            </Text>
          </AppTooltip>
        );
      },
    },
    {
      title: "CRM 阶段",
      key: "crm_stage",
      width: 120,
      render: (_, item) => {
        const stage = crmStageDisplay(item.crm_stage);
        return <Tag color={stage.color}>{stage.label}</Tag>;
      },
    },
    {
      title: "数据时效",
      key: "freshness",
      width: 136,
      render: (_, item) => {
        const account = item.platform_accounts[0];
        return (
          <FreshnessStatusText
            status={account?.freshness_status ?? item.freshness_status}
            requiresRefresh={account?.requires_refresh ?? item.requires_refresh}
            freshnessAgeDays={account?.freshness_age_days ?? null}
          />
        );
      },
    },
    {
      title: "联系方式",
      key: "contacts",
      width: 124,
      render: (_, item) => (
        <div className="influencer-contact-cell">
          <Text>{contactSummary(item)}</Text>
          {item.possible_duplicate_contact ? (
            <Text type="warning">需核对</Text>
          ) : null}
        </div>
      ),
    },
    {
      title: "负责人",
      key: "owner",
      width: 120,
      render: (_, item) =>
        item.owner ? (
          <div className="influencer-owner-cell">
            <Text>{item.owner.name}</Text>
            {item.owner.status === "disabled" ? <Tag>已停用</Tag> : null}
          </div>
        ) : (
          "未分配"
        ),
    },
    {
      title: "指标更新时间",
      key: "metrics_updated_at",
      width: 140,
      render: (_, item) => {
        const timestamp = latestMetricsTimestamp(item.current_metrics);
        return timestamp ? (
          <AppTooltip title={formatMetricsTimestampTooltip(timestamp)}>
            <Text className="influencer-metrics-time">
              {formatMetricsTimestamp(timestamp)}
            </Text>
          </AppTooltip>
        ) : (
          "—"
        );
      },
    },
    {
      title: "操作",
      key: "actions",
      width: 70,
      fixed: "right",
      render: (_, item) => {
        return renderLinkOrButton({
          id: item.id,
          className: "influencer-view-link",
          children: "查看",
        });
      },
    },
  ];
}

function accountSummary(
  item: InfluencerListItem,
  account: PlatformAccountSummary,
): string {
  const platform = platformLabel(account.platform);
  const accountName = account.account_name.trim();
  const primary =
    accountName && accountName !== item.display_name
      ? `${accountName} · ${platform}`
      : platform;
  const remaining = item.platform_accounts.length - 1;
  return remaining > 0 ? `${primary} · +${remaining} 个账号` : primary;
}

function accountTooltip(item: InfluencerListItem): string {
  return item.platform_accounts
    .map(
      (account) =>
        `${account.account_name} · ${platformLabel(account.platform)}`,
    )
    .join("\n");
}

function platformTagClass(platform: string) {
  return `platform-tag-${platform.toLowerCase().replace(/[^a-z0-9]/g, "-")}`;
}

function followerValue(item: InfluencerListItem): number | null {
  const values = item.current_metrics.flatMap((metric) =>
    metric.followers_count === null ? [] : [metric.followers_count],
  );
  return values.length ? Math.max(...values) : null;
}

function contactSummary(item: InfluencerListItem): string {
  const types = unique(
    item.current_contacts.map((contact) => contactTypeLabel(contact.type)),
  );
  return types.length ? types.join(" · ") : "—";
}

export function InfluencerTable({
  items,
  detailHrefPrefix = "/influencers",
  onOpenDetail,
}: {
  items: InfluencerListItem[];
  detailHrefPrefix?: string;
  onOpenDetail?: (id: string) => void;
}) {
  const columns = buildColumns(detailHrefPrefix, onOpenDetail);

  return (
    <Table
      rowKey="id"
      columns={columns}
      dataSource={items}
      pagination={false}
      scroll={{ x: 1212 }}
      onRow={() => ({ className: "influencer-row" })}
    />
  );
}
