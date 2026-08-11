"use client";

import { LinkOutlined } from "@ant-design/icons";
import { Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import Link from "next/link";

import type {
  CurrentMetricsSummary,
  InfluencerListItem,
  PlatformAccountSummary,
} from "../types";

const { Text } = Typography;

function accountLabel(
  accounts: PlatformAccountSummary[],
  accountId: string,
): string {
  return (
    accounts.find((account) => account.id === accountId)?.account_name ?? "—"
  );
}

function metricText(
  metric: CurrentMetricsSummary,
  accounts: PlatformAccountSummary[],
) {
  return (
    <div key={`${metric.platform_account_id}-${metric.source}`}>
      <Text type="secondary">
        {accountLabel(accounts, metric.platform_account_id)} · {metric.source}
      </Text>
      <br />
      <Text>
        {metric.followers_count === null ? "—" : metric.followers_count}
      </Text>
    </div>
  );
}

const columns: ColumnsType<InfluencerListItem> = [
  {
    title: "达人",
    key: "influencer",
    width: 190,
    render: (_, item) => (
      <Space orientation="vertical" size={2}>
        <Link href={`/influencers/${item.id}`}>{item.display_name}</Link>
        {item.platform_accounts.length ? (
          item.platform_accounts.map((account) => (
            <Text type="secondary" key={account.id}>
              {account.account_name}
            </Text>
          ))
        ) : (
          <Text type="secondary">—</Text>
        )}
      </Space>
    ),
  },
  {
    title: "平台账号",
    key: "accounts",
    width: 230,
    render: (_, item) =>
      item.platform_accounts.length ? (
        <Space orientation="vertical" size="small">
          {item.platform_accounts.map((account) => (
            <div key={account.id}>
              <Text>{account.platform}</Text>
              <br />
              <Text type="secondary">
                {account.account_name}
                {account.account_handle ? ` · ${account.account_handle}` : ""}
              </Text>
              {account.profile_url ? (
                <>
                  <br />
                  <a
                    href={account.profile_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <LinkOutlined /> 主页
                  </a>
                </>
              ) : null}
            </div>
          ))}
        </Space>
      ) : (
        "—"
      ),
  },
  {
    title: "赛道/标签",
    key: "tags",
    width: 180,
    render: (_, item) => {
      const tags = item.platform_accounts.flatMap(
        (account) => account.source_tags,
      );
      return tags.length ? (
        <Space wrap size={[4, 4]}>
          {tags.map((tag, index) => (
            <Tag key={`${tag}-${index}`} title={tag} className="source-tag">
              {tag}
            </Tag>
          ))}
        </Space>
      ) : (
        "—"
      );
    },
  },
  {
    title: "粉丝",
    key: "followers",
    width: 170,
    render: (_, item) =>
      item.current_metrics.length ? (
        <Space orientation="vertical" size="small">
          {item.current_metrics.map((metric) =>
            metricText(metric, item.platform_accounts),
          )}
        </Space>
      ) : (
        "—"
      ),
  },
  {
    title: "联系方式",
    key: "contacts",
    width: 210,
    render: (_, item) =>
      item.current_contacts.length ? (
        <Space orientation="vertical" size="small">
          {item.current_contacts.map((contact) => (
            <div key={contact.id}>
              <Text>
                {contact.type} · {contact.display_value}
              </Text>
              {contact.possible_duplicate_contact ? (
                <div>
                  <Tag color="warning">疑似重复联系方式</Tag>
                </div>
              ) : null}
            </div>
          ))}
        </Space>
      ) : (
        "—"
      ),
  },
  {
    title: "CRM Stage",
    dataIndex: "crm_stage",
    key: "crm_stage",
    width: 130,
  },
  {
    title: "Owner",
    key: "owner",
    width: 150,
    render: (_, item) =>
      item.owner ? (
        <Space orientation="vertical" size={2}>
          <Text>{item.owner.name}</Text>
          {item.owner.status === "disabled" ? <Tag>已停用</Tag> : null}
        </Space>
      ) : (
        "未分配"
      ),
  },
];

export function InfluencerTable({ items }: { items: InfluencerListItem[] }) {
  return (
    <Table
      rowKey="id"
      columns={columns}
      dataSource={items}
      pagination={false}
      scroll={{ x: 1250 }}
      onRow={() => ({ className: "influencer-row" })}
    />
  );
}
