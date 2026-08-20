"use client";

import { Button, Skeleton, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import Link from "next/link";

import {
  formatShanghaiDate,
  platformLabel,
} from "@/features/influencers/formatters";

import type { CampaignMember } from "../types";

const { Text } = Typography;

function MemberIdentity({ member }: { member: CampaignMember }) {
  const identity = member.influencer;
  if (identity.status === "active") {
    return (
      <Link href={`/influencers/${encodeURIComponent(identity.id)}`}>
        {identity.display_name}
      </Link>
    );
  }
  return (
    <div>
      <div>{identity.display_name}</div>
      <Text type="secondary">已停用</Text>
    </div>
  );
}

function PreferredAccount({ member }: { member: CampaignMember }) {
  const account = member.preferred_platform_account;
  return (
    <div className="campaign-member-account">
      <div>{`${platformLabel(account.platform)} · ${account.account_name}`}</div>
      {account.account_handle ? (
        <Text type="secondary">@{account.account_handle}</Text>
      ) : null}
      {!account.is_active ? <Text type="secondary">账号已停用</Text> : null}
    </div>
  );
}

export function CampaignMemberTable({
  items,
  loading,
  canRemove,
  onRemove,
}: {
  items?: CampaignMember[];
  loading?: boolean;
  canRemove?: boolean;
  onRemove?: (member: CampaignMember) => void;
}) {
  if (loading) {
    return (
      <div aria-label="正在加载活动达人">
        {Array.from({ length: 5 }, (_, index) => (
          <Skeleton active paragraph={{ rows: 1 }} title={false} key={index} />
        ))}
      </div>
    );
  }

  const columns: ColumnsType<CampaignMember> = [
    {
      title: "达人",
      key: "influencer",
      width: 270,
      render: (_, member) => <MemberIdentity member={member} />,
    },
    {
      title: "活动账号",
      key: "preferred_platform_account",
      width: 300,
      render: (_, member) => <PreferredAccount member={member} />,
    },
    {
      title: "首次加入时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 190,
      render: (value: string) => formatShanghaiDate(value),
    },
  ];

  if (canRemove) {
    columns.push({
      title: "操作",
      key: "action",
      width: 120,
      fixed: "right",
      render: (_, member) => (
        <Button type="link" danger onClick={() => onRemove?.(member)}>
          移出活动
        </Button>
      ),
    });
  }

  return (
    <Table<CampaignMember>
      className="campaign-member-table"
      rowKey="id"
      columns={columns}
      dataSource={items ?? []}
      pagination={false}
      scroll={{ x: canRemove ? 880 : 760 }}
    />
  );
}
