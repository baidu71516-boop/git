"use client";

import { AppstoreOutlined, LeftOutlined } from "@ant-design/icons";
import { Drawer, Space, Tag, Typography } from "antd";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { PageHeader } from "@/components/ui/page-header";

import { crmStageDisplay, platformLabel } from "./formatters";
import { InfluencerDetail } from "./components/influencer-detail";
import { InfluencerPreviewShell } from "./influencer-preview-shell";
import type { InfluencerDetail as InfluencerDetailData } from "./types";

const { Title, Text } = Typography;

export function DevInfluencerDetailDrawerPreview({
  detail,
  backHref,
}: {
  detail: InfluencerDetailData;
  backHref: string;
}) {
  const router = useRouter();
  const stage = crmStageDisplay(detail.crm_stage);

  return (
    <InfluencerPreviewShell title="达人详情预览" description="仅用于界面预览">
      <section className="influencer-workspace influencer-detail-preview-workspace">
        <div className="influencer-page-heading">
          <PageHeader
            title={`开发环境预览 · ${detail.display_name}`}
            description="仅用于视觉验收，数据为虚构"
          />
        </div>
        <div className="detail-preview-links">
          <Link href={backHref}>
            <LeftOutlined /> 返回样例列表
          </Link>
        </div>

        <Drawer
          rootClassName="influencer-detail-drawer"
          open
          size="default"
          width={740}
          title={
            <div className="influencer-drawer-heading">
              <Title level={5} style={{ margin: 0 }}>
                {detail.display_name}
              </Title>
              <Text type="secondary">
                {detail.platform_accounts[0]
                  ? `${platformLabel(detail.platform_accounts[0].platform)} · ${detail.platform_accounts[0].account_name}`
                  : ""}
              </Text>
            </div>
          }
          extra={
            stage ? (
              <Tag color={stage.color || "default"} icon={<AppstoreOutlined />}>
                {stage.label}
              </Tag>
            ) : null
          }
          closable={{ "aria-label": "关闭详情抽屉", placement: "end" }}
          keyboard
          mask={{ closable: true }}
          onClose={() => void router.push(backHref)}
        >
          <div className="influencer-drawer-content influencer-detail-layout">
            <InfluencerDetail detail={detail} showTitle={false} />
          </div>
        </Drawer>

        <Space className="influencer-preview-footer" size="small">
          <span className="detail-secondary-text">仅开发环境可见</span>
        </Space>
      </section>
    </InfluencerPreviewShell>
  );
}
