import { Button, Space, Typography } from "antd";

import { getCollectionJobStatusPresentation } from "../formatters";
import type { CollectionJobPublic } from "../types";

const { Text, Title } = Typography;

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
            <Space size="small" wrap>
              <Text strong>筛选规则摘要</Text>
              {onOpenScreeningRules ? (
                <Button type="link" size="small" onClick={onOpenScreeningRules}>
                  {readOnly ? "查看筛选规则" : "编辑筛选规则"}
                </Button>
              ) : null}
            </Space>
            <Space size={[16, 4]} wrap>
              <Text type="secondary">
                平台：
                {collection.screening_rules.platforms.includes("xiaohongshu")
                  ? "小红书"
                  : "未设置"}
              </Text>
              <Text type="secondary">
                来源标签：
                {collection.screening_rules.source_tags_exact_any.length > 0
                  ? collection.screening_rules.source_tags_exact_any.join("、")
                  : "未设置"}
              </Text>
              <Text type="secondary">
                粉丝范围：
                {collection.follower_min === null &&
                collection.follower_max === null
                  ? "不限"
                  : `${collection.follower_min?.toLocaleString("zh-CN") ?? "不限"} – ${collection.follower_max?.toLocaleString("zh-CN") ?? "不限"}`}
              </Text>
              <Text type="secondary">
                规则版本：{collection.screening_rules_revision}
              </Text>
            </Space>
          </div>
        ) : null}
      </div>
      <Button onClick={onNewCollection} disabled={readOnly}>
        新建采集任务
      </Button>
    </div>
  );
}
