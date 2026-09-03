import { Space, Tag, Tooltip, Typography } from "antd";
import type { ReactNode } from "react";

import { AppTooltip } from "@/components/ui/app-tooltip";

import {
  contactTypeLabel,
  crmStageDisplay,
  contentActivityCoverageLabel,
  formatExactFollowers,
  formatFollowers,
  formatMetricsTimestamp,
  contentActivityLatestAttemptLabel,
  contentActivityTrustedResultLabel,
  platformLabel,
} from "../formatters";
import type { InfluencerDetail as InfluencerDetailData } from "../types";
import { ContentActivityStatusText } from "./content-activity-status-text";
import { FreshnessStatusText } from "./freshness-status-text";

const { Title } = Typography;

const detailDateMinuteFormatter = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const detailDateSecondFormatter = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

function unique(values: string[]): string[] {
  return Array.from(new Set(values));
}

function rowItem(
  label: string,
  value: string | number | ReactNode | null,
  key?: string,
) {
  return (
    <div className="detail-row-item" key={key || label}>
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

function mapDataSourceLabel(source: string): string {
  if (source === "huitun") {
    return "灰豚";
  }
  if (source === "manual") {
    return "手动";
  }
  if (source === "generic") {
    return "通用";
  }
  return source || "—";
}

function formatDateForDetailField(value: string | null): ReactNode {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  const full = detailDateSecondFormatter.format(date).replaceAll("/", "-");
  const minute = detailDateMinuteFormatter.format(date).replaceAll("/", "-");
  return (
    <Tooltip title={full}>
      <span>{minute}</span>
    </Tooltip>
  );
}

function contactSourceLabel(source: string): string {
  if (source === "huitun") {
    return "灰豚";
  }
  if (source === "manual") {
    return "手动";
  }
  if (source === "generic") {
    return "通用";
  }
  return source || "—";
}

function contactValidationLabel(status: string | null | undefined): string {
  if (status === "unverified") {
    return "未验证";
  }
  if (status === "valid" || status === "verified") {
    return "已验证";
  }
  return status || "—";
}

function PlatformMetricGrid({
  account,
  detail,
  index,
}: {
  account: InfluencerDetailData["platform_accounts"][number];
  detail: InfluencerDetailData;
  index: number;
}) {
  const metric = metricForAccount(detail, account.id);
  const followersCount = metricFollowers(metric);
  const followers =
    followersCount === null ? "—" : formatFollowers(followersCount);
  const followersTooltip =
    followersCount === null ? null : formatExactFollowers(followersCount);
  const isXhs = account.platform === "xiaohongshu";
  const hasActivityAttempt = Boolean(
    account.content_activity_latest_attempt_observed_at ||
    account.content_activity_trusted_observed_at,
  );
  const useTrustedCoverage = ["current", "last_known", "stale"].includes(
    account.content_activity_state ?? "not_checked",
  );
  const activityCoverage = useTrustedCoverage
    ? account.content_activity_trusted_coverage_status
    : account.content_activity_latest_attempt_coverage_status;
  const trustedResult = useTrustedCoverage
    ? account.content_activity_trusted_result
    : null;

  return (
    <div
      className="detail-platform-item"
      style={index > 0 ? { paddingTop: "12px" } : undefined}
    >
      <div className="detail-platform-subtitle">
        {platformLabel(account.platform)} · {account.account_name}
      </div>
      <div className="detail-platform-grid detail-two-column-grid">
        <div className="detail-row-item" key={`${account.id}-followers`}>
          <span className="detail-row-label">粉丝</span>
          <span className="detail-row-value">
            {followersTooltip ? (
              <AppTooltip title={followersTooltip}>
                <span>{followers}</span>
              </AppTooltip>
            ) : (
              followers
            )}
          </span>
        </div>
        {rowItem(
          "数据时效",
          <FreshnessStatusText
            status={account.freshness_status}
            requiresRefresh={account.requires_refresh}
          />,
          `${account.id}-freshness`,
        )}
        {rowItem(
          "最近可靠采集时间",
          formatDateForDetailField(account.last_huitun_observed_at),
          `${account.id}-observed`,
        )}
        {rowItem(
          "最近导入时间",
          formatDateForDetailField(account.last_huitun_imported_at),
          `${account.id}-imported`,
        )}
        {account.freshness_age_days !== null
          ? rowItem(
              "距上次采集",
              `${account.freshness_age_days} 天`,
              `${account.id}-freshness-age`,
            )
          : null}
        {rowItem(
          "是否需要更新",
          account.requires_refresh ? "是" : "否",
          `${account.id}-requires-refresh`,
        )}
        {isXhs
          ? rowItem(
              "内容活跃度",
              <ContentActivityStatusText
                account={account}
                showLastPublication={false}
              />,
              `${account.id}-content-activity-state`,
            )
          : null}
        {isXhs
          ? rowItem(
              "可信结果",
              contentActivityTrustedResultLabel(trustedResult),
              `${account.id}-content-activity-trusted-result`,
            )
          : null}
        {isXhs
          ? rowItem(
              "最后公开作品",
              formatDateForDetailField(
                account.content_activity_last_publication_at ?? null,
              ),
              `${account.id}-content-activity-last-publication`,
            )
          : null}
        {isXhs
          ? rowItem(
              "断更天数",
              account.content_activity_inactive_days !== null &&
                account.content_activity_inactive_days !== undefined
                ? `${account.content_activity_inactive_days} 天`
                : "—",
              `${account.id}-content-activity-inactive-days`,
            )
          : null}
        {isXhs
          ? rowItem(
              "最后可信检测",
              formatDateForDetailField(
                account.content_activity_trusted_observed_at ?? null,
              ),
              `${account.id}-content-activity-trusted-observed`,
            )
          : null}
        {isXhs
          ? rowItem(
              "最新检测状态",
              contentActivityLatestAttemptLabel(
                account.content_activity_latest_attempt_observation_status,
                account.content_activity_latest_attempt_result,
              ),
              `${account.id}-content-activity-latest-attempt`,
            )
          : null}
        {isXhs
          ? rowItem(
              "覆盖范围",
              contentActivityCoverageLabel(activityCoverage),
              `${account.id}-content-activity-coverage`,
            )
          : null}
        {isXhs
          ? rowItem(
              "内容数据来源",
              hasActivityAttempt ? "小红书公开作品可信检测" : "—",
              `${account.id}-content-activity-source`,
            )
          : null}
        {rowItem(
          metric?.source === "huitun" ? "灰豚来源更新时间" : "来源数据更新时间",
          metric?.source_updated_at
            ? formatMetricsTimestamp(metric.source_updated_at)
            : "—",
          `${account.id}-updated`,
        )}
      </div>
    </div>
  );
}

function SinglePlatformMetric({
  account,
  detail,
  index,
}: {
  account: InfluencerDetailData["platform_accounts"][number];
  detail: InfluencerDetailData;
  index: number;
}) {
  return <PlatformMetricGrid account={account} detail={detail} index={index} />;
}

function MultiPlatformMetric({
  account,
  detail,
  index,
}: {
  account: InfluencerDetailData["platform_accounts"][number];
  detail: InfluencerDetailData;
  index: number;
}) {
  return <PlatformMetricGrid account={account} detail={detail} index={index} />;
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
  const creatorClassifications = unique(
    detail.source_states.flatMap(
      (state) => state.creator_classification_tags || [],
    ),
  );
  const visibleTags = tags.slice(0, 2);
  const extraTags = tags.length - visibleTags.length;
  const allSourcesText =
    allSources(detail).map(mapDataSourceLabel).join("、") || "—";
  const isMultiPlatform = detail.platform_accounts.length > 1;

  return (
    <section className="influencer-detail-panel">
      {showTitle ? <Title level={2}>{detail.display_name}</Title> : null}

      <section aria-labelledby="basic-section-title" className="detail-section">
        <Title level={5} id="basic-section-title">
          基本资料
        </Title>
        <div className="detail-field-grid detail-two-column-grid">
          {rowItem("负责人", detail.owner ? detail.owner.name : "未分配")}
          {rowItem("CRM 阶段", stage.label)}
          {rowItem(
            "平台",
            activeAccount ? platformLabel(activeAccount.platform) : "—",
          )}
          {rowItem("账号名", activeAccount ? activeAccount.account_name : "—")}
        </div>
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
        <div className="detail-label-row">
          <span className="detail-row-label">当前达人分类</span>
          <span className="detail-tag-line">
            {creatorClassifications.length ? (
              <Space size={[4, 4]} wrap>
                {creatorClassifications.map((classification) => (
                  <Tag key={classification} className="source-tag">
                    {classification}
                  </Tag>
                ))}
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
        ) : (
          <div className="detail-platform-list">
            {detail.platform_accounts.map((account, index) =>
              isMultiPlatform ? (
                <MultiPlatformMetric
                  key={account.id}
                  account={account}
                  detail={detail}
                  index={index}
                />
              ) : (
                <SinglePlatformMetric
                  key={account.id}
                  account={account}
                  detail={detail}
                  index={index}
                />
              ),
            )}
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
                  来源：{contactSourceLabel(contact.source)} · 状态：
                  {contactValidationLabel(contact.validation_status)}
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
        <div className="detail-field-grid detail-two-column-grid">
          {rowItem("数据来源", allSourcesText)}
          {rowItem("创建时间", formatDateForDetailField(detail.created_at))}
          {rowItem("资料更新时间", formatDateForDetailField(detail.updated_at))}
        </div>
      </section>
    </section>
  );
}
