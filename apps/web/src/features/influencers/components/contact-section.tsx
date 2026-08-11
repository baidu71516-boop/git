import { Card, Descriptions, Empty, Space, Tag, Typography } from "antd";

import { formatShanghaiDate } from "../formatters";
import type { InfluencerContactDetail } from "../types";

const { Title } = Typography;

export function ContactSection({
  contacts,
}: {
  contacts: InfluencerContactDetail[];
}) {
  return (
    <section aria-labelledby="contacts-title">
      <Title level={4} id="contacts-title">
        联系方式
      </Title>
      {contacts.length === 0 ? (
        <Empty description="—" />
      ) : (
        <div className="influencer-detail-grid">
          {contacts.map((contact) => (
            <Card
              key={contact.id}
              size="small"
              title={`${contact.type} · ${contact.display_value}`}
            >
              <Space wrap className="detail-tag-row">
                <Tag>{contact.source}</Tag>
                <Tag>{contact.validation_status}</Tag>
                {contact.possible_duplicate_contact ? (
                  <Tag color="warning">疑似重复联系方式</Tag>
                ) : null}
              </Space>
              <Descriptions column={1} size="small">
                <Descriptions.Item label="平台账号 ID">
                  {contact.platform_account_id ?? "—"}
                </Descriptions.Item>
                <Descriptions.Item label="首次记录时间">
                  {formatShanghaiDate(contact.first_seen_at)}
                </Descriptions.Item>
                <Descriptions.Item label="最近记录时间">
                  {formatShanghaiDate(contact.last_seen_at)}
                </Descriptions.Item>
                <Descriptions.Item label="来源更新时间">
                  {formatShanghaiDate(contact.source_updated_at)}
                </Descriptions.Item>
                <Descriptions.Item label="首次 Import Job / Row">
                  {contact.first_import_job_id ?? "—"} /{" "}
                  {contact.first_import_row_id ?? "—"}
                </Descriptions.Item>
                <Descriptions.Item label="最近 Import Job / Row">
                  {contact.last_import_job_id ?? "—"} /{" "}
                  {contact.last_import_row_id ?? "—"}
                </Descriptions.Item>
              </Descriptions>
            </Card>
          ))}
        </div>
      )}
    </section>
  );
}
