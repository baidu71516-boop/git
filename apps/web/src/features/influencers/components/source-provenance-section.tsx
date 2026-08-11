import { Card, Descriptions, Empty, Space, Tag, Typography } from "antd";

import { formatShanghaiDate } from "../formatters";
import type {
  PlatformAccountDetail,
  SourceIdentityDetail,
  SourceStateDetail,
} from "../types";

const { Title } = Typography;

function accountName(accounts: PlatformAccountDetail[], accountId: string) {
  return (
    accounts.find((account) => account.id === accountId)?.account_name ?? "—"
  );
}

export function SourceProvenanceSection({
  accounts,
  sourceStates,
  sourceIdentities,
}: {
  accounts: PlatformAccountDetail[];
  sourceStates: SourceStateDetail[];
  sourceIdentities: SourceIdentityDetail[];
}) {
  return (
    <section aria-labelledby="source-provenance-title">
      <Title level={4} id="source-provenance-title">
        来源与追溯
      </Title>
      {sourceStates.length === 0 && sourceIdentities.length === 0 ? (
        <Empty description="暂无来源追溯" />
      ) : (
        <div className="influencer-detail-grid">
          {sourceStates.map((state) => (
            <Card
              key={`${state.platform_account_id}-${state.source}`}
              size="small"
              title={`${accountName(accounts, state.platform_account_id)} · ${state.source}`}
            >
              <Descriptions column={1} size="small">
                <Descriptions.Item label="来源更新时间">
                  {formatShanghaiDate(state.source_updated_at)}
                </Descriptions.Item>
                <Descriptions.Item label="状态版本">
                  {state.state_version}
                </Descriptions.Item>
                <Descriptions.Item label="来源 creator_tags">
                  {state.creator_tags.length ? (
                    <Space wrap size={[4, 4]}>
                      {state.creator_tags.map((tag) => (
                        <Tag key={tag}>{tag}</Tag>
                      ))}
                    </Space>
                  ) : (
                    "—"
                  )}
                </Descriptions.Item>
                <Descriptions.Item label="最近记录的 Import Job">
                  {state.last_import_job_id}
                </Descriptions.Item>
                <Descriptions.Item label="最近记录的 Import Row">
                  {state.last_import_row_id}
                </Descriptions.Item>
              </Descriptions>
            </Card>
          ))}
          {sourceIdentities.map((identity) => (
            <Card
              key={identity.id}
              size="small"
              title={`来源身份 · ${identity.source}`}
            >
              <Descriptions column={1} size="small">
                <Descriptions.Item label="账号">
                  {accountName(accounts, identity.platform_account_id)}
                </Descriptions.Item>
                <Descriptions.Item label="平台">
                  {identity.platform}
                </Descriptions.Item>
                <Descriptions.Item label="外部账号 ID">
                  {identity.external_account_id}
                </Descriptions.Item>
                <Descriptions.Item label="首次 Import Job / Row">
                  {identity.first_import_job_id} /{" "}
                  {identity.first_import_row_id}
                </Descriptions.Item>
                <Descriptions.Item label="最近记录的 Import Job">
                  {identity.last_import_job_id}
                </Descriptions.Item>
                <Descriptions.Item label="最近记录的 Import Row">
                  {identity.last_import_row_id}
                </Descriptions.Item>
              </Descriptions>
            </Card>
          ))}
        </div>
      )}
    </section>
  );
}
