import { Button, Space, Typography } from "antd";

import { getCollectionJobStatusPresentation } from "../formatters";
import type { CollectionJobPublic } from "../types";

const { Text, Title } = Typography;

function formatFollowerBoundary(value: number | null): string {
  if (value === null) {
    return "不限";
  }
  if (value < 10_000) {
    return value.toLocaleString("zh-CN");
  }
  const scaled = value / 10_000;
  return `${scaled % 1 === 0 ? scaled.toFixed(0) : scaled.toFixed(1).replace(/\.0$/, "")}万`;
}

function formatFollowerRange(min: number | null, max: number | null): string {
  if (min === null && max === null) {
    return "不限";
  }
  return `${formatFollowerBoundary(min)}–${formatFollowerBoundary(max)}`;
}

export function BulkJobSummary({
  collection,
  readOnly,
  onNewCollection,
  onOpenScreeningRules,
}: {
  collection: CollectionJobPublic | null;
  readOnly: boolean;
  onNewCollection: () => void;
  onOpenScreeningRules?: () => void;
}) {
  const collectionStatus = collection
    ? getCollectionJobStatusPresentation(collection.status)
    : null;
  const tagCount =
    collection?.screening_rules.source_tags_exact_any.length ?? 0;
  const platformText = collection?.screening_rules.platforms.includes(
    "xiaohongshu",
  )
    ? "小红书"
    : "未设置";
  const followerText = collection
    ? formatFollowerRange(collection.follower_min, collection.follower_max)
    : "不限";
  return (
    <div className="bulk-job-summary">
      <div className="bulk-job-summary-copy">
        <Text type="secondary">当前采集任务</Text>
        <Title level={4}>{collection?.name ?? "正在读取采集任务"}</Title>
        <Space size="small" wrap>
          {collection ? <Text>行业：{collection.industry}</Text> : null}
          {collectionStatus ? (
            <Text type="secondary">任务状态：{collectionStatus.label}</Text>
          ) : null}
        </Space>
        {collection ? (
          <div className="bulk-screening-rule-summary">
            <Space
              size="small"
              wrap
              className="bulk-screening-rule-summary-header"
            >
              <Text type="secondary">筛选规则</Text>
              {onOpenScreeningRules ? (
                <Button type="link" size="small" onClick={onOpenScreeningRules}>
                  {readOnly ? "查看筛选规则" : "编辑筛选规则"}
                </Button>
              ) : null}
            </Space>
            <Text type="secondary" className="bulk-screening-rule-summary-line">
              {platformText} · 来源标签 {tagCount} 个 · 粉丝 {followerText} ·
              规则版本 {collection.screening_rules_revision}
            </Text>
          </div>
        ) : null}
      </div>
      <Button onClick={onNewCollection} disabled={readOnly}>
        新建采集任务
      </Button>
    </div>
  );
}
