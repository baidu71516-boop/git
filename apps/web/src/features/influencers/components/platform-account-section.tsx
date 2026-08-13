import { LinkOutlined } from "@ant-design/icons";
import { Card, Descriptions, Empty, Space, Tag, Typography } from "antd";

import { displayValue, platformLabel } from "../formatters";
import type { PlatformAccountDetail } from "../types";

const { Paragraph, Title } = Typography;

export function PlatformAccountSection({
  accounts,
}: {
  accounts: PlatformAccountDetail[];
}) {
  return (
    <section aria-labelledby="platform-accounts-title">
      <Title level={4} id="platform-accounts-title">
        平台账号
      </Title>
      {accounts.length === 0 ? (
        <Empty description="暂无平台账号" />
      ) : (
        <div className="influencer-detail-grid">
          {accounts.map((account) => (
            <Card key={account.id} size="small" title={account.account_name}>
              <Descriptions column={1} size="small">
                <Descriptions.Item label="平台">
                  {platformLabel(account.platform)}
                </Descriptions.Item>
                <Descriptions.Item label="平台账号 ID">
                  {displayValue(account.platform_account_id)}
                </Descriptions.Item>
                <Descriptions.Item label="账号标识">
                  {displayValue(account.account_handle)}
                </Descriptions.Item>
                <Descriptions.Item label="创建来源">
                  {account.source}
                </Descriptions.Item>
                <Descriptions.Item label="性别">
                  {displayValue(account.gender)}
                </Descriptions.Item>
                <Descriptions.Item label="地区">
                  {displayValue(account.region_raw)}
                </Descriptions.Item>
                <Descriptions.Item label="认证">
                  {displayValue(account.verification_info)}
                </Descriptions.Item>
                <Descriptions.Item label="MCN">
                  {displayValue(account.mcn_name)}
                </Descriptions.Item>
                <Descriptions.Item label="创作者等级">
                  {displayValue(account.creator_level)}
                </Descriptions.Item>
                <Descriptions.Item label="品牌合作人">
                  {displayValue(account.is_brand_partner)}
                </Descriptions.Item>
                <Descriptions.Item label="赛道/标签">
                  {account.source_tags.length ? (
                    <Space wrap size={[4, 4]}>
                      {account.source_tags.map((tag) => (
                        <Tag key={tag} className="source-tag" title={tag}>
                          {tag}
                        </Tag>
                      ))}
                    </Space>
                  ) : (
                    "—"
                  )}
                </Descriptions.Item>
              </Descriptions>
              <Paragraph className="detail-bio">
                {displayValue(account.bio)}
              </Paragraph>
              {account.profile_url ? (
                <a href={account.profile_url} target="_blank" rel="noreferrer">
                  <LinkOutlined /> 打开平台主页
                </a>
              ) : null}
            </Card>
          ))}
        </div>
      )}
    </section>
  );
}
