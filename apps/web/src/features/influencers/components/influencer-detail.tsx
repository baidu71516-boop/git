import { Card, Descriptions, Space, Tag, Typography } from "antd";

import { formatShanghaiDate } from "../formatters";
import type { InfluencerDetail as InfluencerDetailData } from "../types";
import { ContactSection } from "./contact-section";
import { CurrentMetricsSection } from "./current-metrics-section";
import { PlatformAccountSection } from "./platform-account-section";
import { SourceProvenanceSection } from "./source-provenance-section";

const { Title } = Typography;

export function InfluencerDetail({ detail }: { detail: InfluencerDetailData }) {
  return (
    <Space orientation="vertical" size="large" className="full-width">
      <Card variant="borderless" className="influencer-heading-card">
        <Title level={3}>{detail.display_name}</Title>
        <Descriptions bordered column={{ xs: 1, md: 2 }}>
          <Descriptions.Item label="主体 ID">{detail.id}</Descriptions.Item>
          <Descriptions.Item label="状态">{detail.status}</Descriptions.Item>
          <Descriptions.Item label="CRM Stage">
            {detail.crm_stage}
          </Descriptions.Item>
          <Descriptions.Item label="Owner">
            {detail.owner ? (
              <Space>
                <span>{detail.owner.name}</span>
                {detail.owner.status === "disabled" ? <Tag>已停用</Tag> : null}
              </Space>
            ) : (
              "未分配"
            )}
          </Descriptions.Item>
          <Descriptions.Item label="创建时间">
            {formatShanghaiDate(detail.created_at)}
          </Descriptions.Item>
          <Descriptions.Item label="更新时间">
            {formatShanghaiDate(detail.updated_at)}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card variant="borderless" className="influencer-detail-card">
        <PlatformAccountSection accounts={detail.platform_accounts} />
      </Card>
      <Card variant="borderless" className="influencer-detail-card">
        <ContactSection contacts={detail.contacts} />
      </Card>
      <Card variant="borderless" className="influencer-detail-card">
        <SourceProvenanceSection
          accounts={detail.platform_accounts}
          sourceStates={detail.source_states}
          sourceIdentities={detail.source_identities}
        />
      </Card>
      <Card variant="borderless" className="influencer-detail-card">
        <CurrentMetricsSection
          accounts={detail.platform_accounts}
          metrics={detail.current_metrics}
        />
      </Card>
    </Space>
  );
}
