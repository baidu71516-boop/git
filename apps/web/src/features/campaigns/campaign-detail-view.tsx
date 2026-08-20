"use client";

import {
  Alert,
  Button,
  Card,
  Descriptions,
  Skeleton,
  Tabs,
  Tooltip,
  Typography,
  message,
} from "antd";
import { useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { PageHeader } from "@/components/ui/page-header";
import { StatusBadge } from "@/components/ui/status-badge";
import { ApiClientError } from "@/lib/api/client";

import { EditCampaignModal } from "./components/edit-campaign-modal";
import { CampaignMembersSection } from "./components/campaign-members-section";
import {
  campaignStatusPresentation,
  formatCampaignDateTime,
  isDisabledCampaignOwner,
} from "./formatters";
import { useCampaignDetail } from "./queries";
import type { Campaign, CampaignRole, CampaignScope } from "./types";
import type { CampaignDetailPreview } from "./preview-types";

const { Text } = Typography;

function ownerContent(campaign: Campaign) {
  return (
    <span>
      {campaign.owner.name}
      {isDisabledCampaignOwner(campaign.owner) ? (
        <Text type="secondary"> · 已停用</Text>
      ) : null}
    </span>
  );
}

type CampaignDetailViewProps = {
  campaignId: string;
  role: CampaignRole;
  hasSelectedOperator: boolean;
  scope?: CampaignScope;
  preview?: CampaignDetailPreview;
};

function RemoteCampaignDetailView({
  campaignId,
  role,
  hasSelectedOperator,
  scope,
}: {
  campaignId: string;
  role: CampaignRole;
  hasSelectedOperator: boolean;
  scope?: CampaignScope;
}) {
  const [messageApi, contextHolder] = message.useMessage();
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [editingCampaign, setEditingCampaign] = useState<Campaign | null>(null);
  const detailQuery = useCampaignDetail(campaignId, scope);
  const campaign = detailQuery.data;
  const canWrite = role !== "viewer";
  const canMutate = canWrite && hasSelectedOperator;
  const canEdit = Boolean(campaign && campaign.status !== "CLOSED" && canWrite);

  async function openEdit() {
    const result = await detailQuery.refetch();
    if (!result.isError && result.data) setEditingCampaign(result.data);
  }

  async function reloadAfterConflict() {
    setEditingCampaign(null);
    await detailQuery.refetch();
  }

  if (detailQuery.isPending) {
    return (
      <section className="campaign-workspace" aria-label="拓客活动详情">
        <div className="campaign-detail-header-skeleton">
          <Skeleton.Input active size="small" />
          <Skeleton.Input active size="small" />
        </div>
        <Card className="campaign-detail-card" variant="borderless">
          <Skeleton active paragraph={{ rows: 5 }} />
        </Card>
      </section>
    );
  }

  if (detailQuery.isError) {
    const error = detailQuery.error;
    const notFound = error instanceof ApiClientError && error.status === 404;
    const forbidden = error instanceof ApiClientError && error.status === 403;
    return (
      <section className="campaign-workspace" aria-label="拓客活动详情">
        <Alert
          type="error"
          showIcon
          title={
            notFound
              ? "拓客活动不存在或不可访问"
              : forbidden
                ? "无法查看该拓客活动"
                : "拓客活动加载失败"
          }
          description={
            notFound
              ? "该活动可能不存在，或当前账号无法查看。"
              : forbidden
                ? "你没有查看此活动的权限。"
                : "请稍后重试。"
          }
          action={
            notFound || forbidden ? (
              <Button href="/campaigns">返回拓客活动</Button>
            ) : (
              <Button onClick={() => void detailQuery.refetch()}>
                重新加载
              </Button>
            )
          }
        />
      </section>
    );
  }

  if (!campaign) return null;
  const activeTab = searchParams.get("tab") === "members" ? "members" : "basic";
  const status = campaignStatusPresentation(campaign.status);
  const editAction = canEdit ? (
    canMutate ? (
      <Button type="primary" onClick={() => void openEdit()}>
        编辑活动
      </Button>
    ) : (
      <Tooltip title="请先选择当前操作人">
        <Button type="primary" disabled>
          编辑活动
        </Button>
      </Tooltip>
    )
  ) : null;

  return (
    <section className="campaign-workspace" aria-label="拓客活动详情">
      {contextHolder}
      <PageHeader title={campaign.name} extra={editAction} />
      <div className="campaign-detail-summary">
        <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
        <Text>负责人：{ownerContent(campaign)}</Text>
        <Text type="secondary">
          更新于：{formatCampaignDateTime(campaign.updated_at)}
        </Text>
      </div>
      <Tabs
        activeKey={activeTab}
        items={[
          {
            key: "basic",
            label: "基本信息",
            children: (
              <Card className="campaign-detail-card" variant="borderless">
                <Descriptions column={1} size="middle">
                  <Descriptions.Item label="活动名称">
                    {campaign.name}
                  </Descriptions.Item>
                  <Descriptions.Item label="状态">
                    <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
                  </Descriptions.Item>
                  <Descriptions.Item label="负责人">
                    {ownerContent(campaign)}
                  </Descriptions.Item>
                  <Descriptions.Item label="创建时间">
                    {formatCampaignDateTime(campaign.created_at)}
                  </Descriptions.Item>
                  <Descriptions.Item label="更新时间">
                    {formatCampaignDateTime(campaign.updated_at)}
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            ),
          },
          {
            key: "members",
            label: "活动达人",
            children: (
              <CampaignMembersSection
                campaign={campaign}
                role={role}
                hasSelectedOperator={hasSelectedOperator}
                scope={scope}
              />
            ),
          },
        ]}
        onChange={(key) =>
          router.push(key === "members" ? `${pathname}?tab=members` : pathname)
        }
      />
      {editingCampaign ? (
        <EditCampaignModal
          open
          campaign={editingCampaign}
          canMutate={canMutate}
          scope={scope}
          onClose={() => setEditingCampaign(null)}
          onUpdated={() => {
            setEditingCampaign(null);
            void messageApi.success("拓客活动已更新");
          }}
          onVersionConflict={() => {
            void messageApi.error(
              "活动信息已被其他人更新。请重新加载最新信息后再继续编辑。",
            );
            void reloadAfterConflict();
          }}
          onCampaignClosed={() => {
            void messageApi.error("该拓客活动已关闭，无法继续编辑。");
            void reloadAfterConflict();
          }}
        />
      ) : null}
    </section>
  );
}

function PreviewCampaignDetailView({
  role,
  hasSelectedOperator,
  preview,
}: {
  role: CampaignRole;
  hasSelectedOperator: boolean;
  preview: CampaignDetailPreview;
}) {
  const [activeTab, setActiveTab] = useState(preview.activeTab ?? "members");
  const campaign = preview.campaign;
  const status = campaignStatusPresentation(campaign.status);

  return (
    <section className="campaign-workspace" aria-label="拓客活动详情">
      <PageHeader title={campaign.name} />
      <div className="campaign-detail-summary">
        <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
        <Text>负责人：{ownerContent(campaign)}</Text>
        <Text type="secondary">
          更新于：{formatCampaignDateTime(campaign.updated_at)}
        </Text>
      </div>
      <Tabs
        activeKey={activeTab}
        items={[
          {
            key: "basic",
            label: "基本信息",
            children: (
              <Card className="campaign-detail-card" variant="borderless">
                <Descriptions column={1} size="middle">
                  <Descriptions.Item label="活动名称">
                    {campaign.name}
                  </Descriptions.Item>
                  <Descriptions.Item label="状态">
                    <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
                  </Descriptions.Item>
                  <Descriptions.Item label="负责人">
                    {ownerContent(campaign)}
                  </Descriptions.Item>
                  <Descriptions.Item label="创建时间">
                    {formatCampaignDateTime(campaign.created_at)}
                  </Descriptions.Item>
                  <Descriptions.Item label="更新时间">
                    {formatCampaignDateTime(campaign.updated_at)}
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            ),
          },
          {
            key: "members",
            label: "活动达人",
            children: (
              <CampaignMembersSection
                campaign={campaign}
                role={role}
                hasSelectedOperator={hasSelectedOperator}
                preview={preview.members}
              />
            ),
          },
        ]}
        onChange={(key) => setActiveTab(key as "basic" | "members")}
      />
    </section>
  );
}

export function CampaignDetailView(props: CampaignDetailViewProps) {
  if (props.preview) {
    return <PreviewCampaignDetailView {...props} preview={props.preview} />;
  }
  return <RemoteCampaignDetailView {...props} />;
}
