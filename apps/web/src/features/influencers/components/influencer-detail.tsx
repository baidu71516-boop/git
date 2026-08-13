import { Card, Descriptions, Space, Tag, Typography } from "antd";

import { crmStageDisplay, formatShanghaiDate } from "../formatters";
import type { InfluencerDetail as InfluencerDetailData } from "../types";
import { ContactSection } from "./contact-section";
import { CurrentMetricsSection } from "./current-metrics-section";
import { PlatformAccountSection } from "./platform-account-section";
import { SourceProvenanceSection } from "./source-provenance-section";

const { Title } = Typography;

export function InfluencerDetail({
  detail,
  showTitle = true,
}: {
  detail: InfluencerDetailData;
  showTitle?: boolean;
}) {
  const stage = crmStageDisplay(detail.crm_stage);
  return (
    <Space orientation="vertical" size="large" className="full-width">
      <Card variant="borderless" className="influencer-heading-card">
        {showTitle ? <Title level={3}>{detail.display_name}</Title> : null}
        <Title level={4}>基本资料</Title>
        <Descriptions bordered column={{ xs: 1, md: 2 }}>
          <Descriptions.Item label="达人 ID">{detail.id}</Descriptions.Item>
          <Descriptions.Item label="状态">
            {detail.status === "active" ? "正常" : detail.status}
          </Descriptions.Item>
          <Descriptions.Item label="创建时间">
            {formatShanghaiDate(detail.created_at)}
          </Descriptions.Item>
          <Descriptions.Item label="更新时间">
            {formatShanghaiDate(detail.updated_at)}
          </Descriptions.Item>
        </Descriptions>
        <Title level={4} className="influencer-management-title">
          管理信息
        </Title>
        <Descriptions bordered column={{ xs: 1, md: 2 }}>
          <Descriptions.Item label="CRM 阶段">
            <Tag color={stage.color}>{stage.label}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="负责人">
            {detail.owner ? (
              <Space>
                <span>{detail.owner.name}</span>
                {detail.owner.status === "disabled" ? <Tag>已停用</Tag> : null}
              </Space>
            ) : (
              "未分配"
            )}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card variant="borderless" className="influencer-detail-card">
        <PlatformAccountSection accounts={detail.platform_accounts} />
      </Card>
      <Card variant="borderless" className="influencer-detail-card">
        <CurrentMetricsSection
          accounts={detail.platform_accounts}
          metrics={detail.current_metrics}
        />
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
    </Space>
  );
}
