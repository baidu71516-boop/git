"use client";

import { LeftOutlined } from "@ant-design/icons";
import { Drawer, Space, Tag, Typography } from "antd";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { PageHeader } from "@/components/ui/page-header";
import { InfluencerAvatar } from "./components/influencer-avatar";

import { crmStageDisplay, platformLabel } from "./formatters";
import { InfluencerDetail } from "./components/influencer-detail";
import { InfluencerPreviewShell } from "./influencer-preview-shell";
import type { InfluencerDetail as InfluencerDetailData } from "./types";

const { Title, Text } = Typography;

export function DevInfluencerDetailDrawer({
  detail,
  open,
  onClose,
}: {
  detail: InfluencerDetailData;
  open: boolean;
  onClose: () => void;
}) {
  const stage = crmStageDisplay(detail.crm_stage);
  const activeAccount =
    detail.platform_accounts.find((account) => account.is_active) ??
    detail.platform_accounts[0];
  const activeSubtitle = activeAccount
    ? `${platformLabel(activeAccount.platform)} · ${activeAccount.account_name}`
    : "";

  return (
    <Drawer
      rootClassName="influencer-detail-drawer"
      open={open}
      size="default"
      width={740}
      title={
        <div className="influencer-detail-drawer-title">
          <InfluencerAvatar
            size={56}
            name={detail.display_name}
            avatarUrl={detail.avatar_url ?? null}
            square={false}
          />
          <div className="influencer-detail-drawer-title-main">
            <Title level={4} style={{ margin: 0 }}>
              {detail.display_name}
            </Title>
            <Text
              type="secondary"
              className="influencer-detail-drawer-subtitle"
            >
              {activeSubtitle}
            </Text>
          </div>
        </div>
      }
      extra={
        stage.label ? (
          <Tag color={stage.color || "default"}>{stage.label}</Tag>
        ) : null
      }
      closable={{ "aria-label": "关闭详情抽屉", placement: "end" }}
      keyboard
      mask={{ closable: true }}
      onClose={onClose}
    >
      <div className="influencer-drawer-content influencer-detail-layout">
        <InfluencerDetail detail={detail} showTitle={false} />
      </div>
    </Drawer>
  );
}

export function DevInfluencerDetailDrawerPreview({
  detail,
  backHref,
}: {
  detail: InfluencerDetailData;
  backHref: string;
}) {
  const router = useRouter();

  return (
    <InfluencerPreviewShell title="达人详情预览" description="仅开发环境可见">
      <section className="influencer-workspace influencer-detail-preview-workspace">
        <div className="influencer-page-heading">
          <PageHeader
            title={`达人详情预览 · ${detail.display_name}`}
            description="仅开发环境可见"
          />
        </div>
        <div className="detail-preview-links">
          <Link href={backHref}>
            <LeftOutlined /> 返回样例列表
          </Link>
        </div>
        <DevInfluencerDetailDrawer
          detail={detail}
          open
          onClose={() => {
            void router.push(backHref);
          }}
        />
        <Space className="influencer-preview-footer" size="small">
          <span className="detail-secondary-text">仅开发环境可见</span>
        </Space>
      </section>
    </InfluencerPreviewShell>
  );
}
