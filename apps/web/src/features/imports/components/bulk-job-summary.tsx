import { Button, Space, Typography } from "antd";

import { getCollectionJobStatusPresentation } from "../formatters";
import type { CollectionJobPublic } from "../types";

const { Text, Title } = Typography;

export function BulkJobSummary({
  collection,
  readOnly,
  onNewCollection,
}: {
  collection: CollectionJobPublic | null;
  readOnly: boolean;
  onNewCollection: () => void;
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
      </div>
      <Button onClick={onNewCollection} disabled={readOnly}>
        新建采集任务
      </Button>
    </div>
  );
}
