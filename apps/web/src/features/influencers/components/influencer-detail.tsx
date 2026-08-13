import { Divider, Space, Tag, Typography } from "antd";

import { AppTooltip } from "@/components/ui/app-tooltip";

import {
  contactTypeLabel,
  crmStageDisplay,
  formatExactFollowers,
  formatFollowers,
  formatMetricsTimestamp,
  formatShanghaiDate,
  platformLabel,
} from "../formatters";
import type { InfluencerDetail as InfluencerDetailData } from "../types";

const { Title } = Typography;

function unique(values: string[]): string[] {
  return Array.from(new Set(values));
}

function rowItem(label: string, value: string | null) {
  return (
    <div className="detail-row-item" key={label}>
      <span className="detail-row-label">{label}</span>
      <span className="detail-row-value">{value}</span>
    </div>
  );
}

function metricForAccount(
  detail: InfluencerDetailData,
  accountId: string | null,
) {
  return detail.current_metrics.find(
    (metric) => metric.platform_account_id === accountId,
  );
}

function metricFollowers(
  metric: ReturnType<typeof metricForAccount>,
): number | null {
  if (!metric) {
    return null;
  }
  const followers = metric.metrics?.followers_count;
  if (typeof followers !== "number") {
    return null;
  }
  return followers;
}

function allSources(detail: InfluencerDetailData): string[] {
  return unique([
    ...detail.platform_accounts.map((account) => account.source),
    ...detail.current_metrics.map((metric) => metric.source),
    ...detail.source_states.map((state) => state.source),
    ...detail.source_identities.map((identity) => identity.source),
  ]);
}

export function InfluencerDetail({
  detail,
  showTitle = true,
}: {
  detail: InfluencerDetailData;
  showTitle?: boolean;
}) {
  const stage = crmStageDisplay(detail.crm_stage);
  const activeAccount = detail.platform_accounts.find(
    (account) => account.is_active,
  );
  const tags = unique(
    detail.platform_accounts.flatMap((account) => account.source_tags || []),
  );
  const visibleTags = tags.slice(0, 2);
  const extraTags = tags.length - visibleTags.length;
  const stageNode =
    detail.crm_stage !== "" ? (
      <Tag color={stage.color || "default"}>{stage.label}</Tag>
    ) : null;
  const allSourcesText = allSources(detail).join("、") || "—";

  return (
    <section className="influencer-detail-panel">
      {showTitle ? <Title level={2}>{detail.display_name}</Title> : null}

      <section aria-labelledby="basic-section-title" className="detail-section">
        <Title level={5} id="basic-section-title">
          基本资料
        </Title>
        <div className="detail-field-grid">
          {rowItem("达人名称", detail.display_name)}
          {rowItem("负责人", detail.owner ? detail.owner.name : "未分配")}
          {rowItem("CRM 阶段", stage.label)}
          {rowItem(
            "平台",
            activeAccount ? platformLabel(activeAccount.platform) : "—",
          )}
          {rowItem("账号", activeAccount ? activeAccount.account_name : "—")}
        </div>
        <Divider />
        <div className="detail-label-row">
          <span className="detail-row-label">标签</span>
          <span className="detail-tag-line">
            {visibleTags.length ? (
              <Space size={[4, 4]} wrap>
                {visibleTags.map((tag) => (
                  <Tag key={tag} className="source-tag">
                    {tag}
                  </Tag>
                ))}
                {extraTags > 0 ? (
                  <Tag className="source-tag-more">+{extraTags}</Tag>
                ) : null}
              </Space>
            ) : (
              <span>—</span>
            )}
          </span>
        </div>
      </section>

      <section
        aria-labelledby="platform-metrics-title"
        className="detail-section"
      >
        <Title level={5} id="platform-metrics-title">
          平台与指标
        </Title>
        {detail.platform_accounts.length === 0 ? (
          <span className="detail-empty-state">暂无平台账号与指标</span>
        ) : detail.platform_accounts.length === 1 ? (
          <div className="detail-single-platform">
            {detail.platform_accounts.map((account) => {
              const metric = metricForAccount(detail, account.id);
              const followersCount = metricFollowers(metric);
              const followers =
                followersCount === null ? "—" : formatFollowers(followersCount);
              const tooltip =
                followersCount === null
                  ? null
                  : formatExactFollowers(followersCount);
              return (
                <div className="detail-inline-rows" key={account.id}>
                  {rowItem("平台", platformLabel(account.platform))}
                  {rowItem("账号名", account.account_name)}
                  <div className="detail-row-item">
                    <span className="detail-row-label">粉丝</span>
                    <span className="detail-row-value">
                      {tooltip ? (
                        <AppTooltip title={tooltip}>
                          <span>{followers}</span>
                        </AppTooltip>
                      ) : (
                        followers
                      )}
                    </span>
                  </div>
                  {rowItem(
                    "指标更新时间",
                    metric?.source_updated_at
                      ? formatMetricsTimestamp(metric.source_updated_at)
                      : "—",
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="detail-platform-list">
            {detail.platform_accounts.map((account) => {
              const metric = metricForAccount(detail, account.id);
              const followersCount = metricFollowers(metric);
              const followers =
                followersCount === null ? "—" : formatFollowers(followersCount);
              const followersTooltip =
                followersCount === null
                  ? null
                  : formatExactFollowers(followersCount);
              return (
                <div className="detail-platform-item" key={account.id}>
                  <div className="detail-platform-item-title">
                    {platformLabel(account.platform)} · {account.account_name}
                  </div>
                  <div className="detail-row-grid">
                    <div className="detail-row-item">
                      <span className="detail-row-label">粉丝</span>
                      <span className="detail-row-value">
                        {followersTooltip && metric?.source_updated_at ? (
                          <AppTooltip title={followersTooltip}>
                            <span>{followers}</span>
                          </AppTooltip>
                        ) : (
                          followers
                        )}
                      </span>
                    </div>
                    {rowItem(
                      "指标更新时间",
                      metric?.source_updated_at
                        ? formatMetricsTimestamp(metric.source_updated_at)
                        : "—",
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      <section aria-labelledby="contact-title" className="detail-section">
        <Title level={5} id="contact-title">
          联系方式
        </Title>
        {detail.contacts.length === 0 ? (
          <span className="detail-empty-state">—</span>
        ) : (
          <div className="detail-contact-grid">
            {detail.contacts.map((contact) => (
              <div key={contact.id} className="detail-contact-item">
                <span className="detail-row-label">
                  {contactTypeLabel(contact.type)}
                </span>
                <span className="detail-row-value">
                  {contact.display_value}
                </span>
                <span className="detail-secondary-text">
                  来源：{contact.source} · 验证：{contact.validation_status}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section aria-labelledby="owner-title" className="detail-section">
        <Title level={5} id="owner-title">
          管理信息
        </Title>
        <div className="detail-field-grid">
          {rowItem("负责人", detail.owner ? detail.owner.name : "未分配")}
          {rowItem("CRM 阶段", stage.label)}
          {rowItem(
            "负责人状态",
            detail.owner
              ? detail.owner.status === "disabled"
                ? "已停用"
                : "正常"
              : "—",
          )}
          {rowItem("数据来源", allSourcesText)}
          {rowItem("创建时间", formatShanghaiDate(detail.created_at))}
          {rowItem("达人记录更新时间", formatShanghaiDate(detail.updated_at))}
        </div>
        <div className="detail-row-item detail-tag-row">
          <span className="detail-row-label">CRM 阶段徽标</span>
          {stageNode}
        </div>
      </section>
    </section>
  );
}
