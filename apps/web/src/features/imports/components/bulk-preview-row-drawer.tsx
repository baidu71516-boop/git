import { Drawer, Tag, Typography } from "antd";

import { presentPreviewRow } from "../preview-presenters";
import type { ImportJobFilePublic, ImportRowPublic } from "../types";

const { Text, Title } = Typography;

function tagColor(tone: string): string | undefined {
  if (tone === "success") return "success";
  if (tone === "warning") return "warning";
  if (tone === "danger") return "error";
  if (tone === "processing") return "processing";
  return undefined;
}

function ChangeValue({
  before,
  after,
}: {
  before: string | null;
  after: string | null;
}) {
  return (
    <div className="bulk-preview-change-values">
      <div className="bulk-preview-change-value-col">
        <Text type="secondary" className="bulk-preview-change-value-label">
          原值
        </Text>
        <Text>{before ?? "—"}</Text>
      </div>
      <div className="bulk-preview-change-value-col bulk-preview-change-value-col-new">
        <Text type="secondary" className="bulk-preview-change-value-label">
          新值
        </Text>
        <Text className="bulk-preview-change-value-new">{after ?? "—"}</Text>
      </div>
    </div>
  );
}

function SectionTitle({ title }: { title: string }) {
  return (
    <Title level={5} className="bulk-preview-section-title">
      {title}
    </Title>
  );
}

export type BulkPreviewRowDrawerProps = {
  open: boolean;
  row: ImportRowPublic | null;
  files: readonly ImportJobFilePublic[];
  onClose: () => void;
};

export function BulkPreviewRowDrawer({
  open,
  row,
  files,
  onClose,
}: BulkPreviewRowDrawerProps) {
  if (!row) return null;

  const presented = presentPreviewRow(row);
  const file = files.find(
    (candidate) => candidate.id === row.import_job_file_id,
  );
  const source = file?.original_filename ?? "来源文件";
  const changeBuckets = presented.changeSummary.filter(
    (bucket) => bucket.items.length > 0,
  );

  return (
    <Drawer
      rootClassName="bulk-preview-row-drawer"
      open={open}
      width={740}
      title={
        <div className="bulk-preview-drawer-title">
          <div className="bulk-preview-drawer-title-name">
            {presented.displayName ?? "预览数据详情"}
          </div>
          <Text type="secondary">
            {source} · 第 {row.row_number} 行
          </Text>
          <Text type="secondary">处理结果：{presented.action.label}</Text>
        </div>
      }
      closable={{ "aria-label": "关闭预览数据详情", placement: "end" }}
      keyboard
      mask={{ closable: true }}
      onClose={onClose}
    >
      <div className="bulk-preview-drawer-content">
        <section className="bulk-preview-drawer-section">
          <SectionTitle title="基本信息" />
          <dl className="bulk-preview-detail-list">
            {presented.displayName ? (
              <div>
                <dt>达人</dt>
                <dd>{presented.displayName}</dd>
              </div>
            ) : null}
            <div>
              <dt>来源文件</dt>
              <dd>{source}</dd>
            </div>
            {presented.batchDuplicate ? (
              <div>
                <dt>重复位置</dt>
                <dd>
                  {presented.batchDuplicate.ownerFilePosition === null
                    ? "同批次"
                    : `第 ${presented.batchDuplicate.ownerFilePosition} 个文件`}
                  · 第 {presented.batchDuplicate.ownerRowNumber} 行
                </dd>
              </div>
            ) : null}
          </dl>
        </section>

        {presented.screening ? (
          <section className="bulk-preview-drawer-section">
            <SectionTitle title="筛选结果" />
            <div className="bulk-preview-screening-detail-heading">
              <Tag color={tagColor(presented.screening.tone)}>
                {presented.screening.label}
              </Tag>
              <Text type="secondary">按筛选规则的当前判断结果。</Text>
            </div>
            {presented.screening.evidence.length > 0 ? (
              <div className="bulk-preview-screening-evidence">
                {presented.screening.evidence.map((evidence, index) => (
                  <div
                    className="bulk-preview-screening-evidence-item"
                    key={`${evidence.label}-${index}`}
                  >
                    <div>
                      <Text strong>{evidence.label}</Text>
                      <Tag color={tagColor(evidence.result.tone)}>
                        {evidence.result.label}
                      </Tag>
                    </div>
                    <dl>
                      <div>
                        <dt>筛选条件</dt>
                        <dd>{evidence.configured}</dd>
                      </div>
                      <div>
                        <dt>当前数据</dt>
                        <dd>{evidence.observed}</dd>
                      </div>
                    </dl>
                    {evidence.message ? (
                      <Text type="secondary">{evidence.message}</Text>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : null}
          </section>
        ) : null}

        {changeBuckets.length > 0 ? (
          <section className="bulk-preview-drawer-section">
            <SectionTitle title="变更摘要" />
            <div className="bulk-preview-change-buckets">
              {changeBuckets.map((bucket) => (
                <section
                  className={`bulk-preview-change-bucket is-${bucket.key}`}
                  key={bucket.key}
                >
                  <Text strong>{bucket.label}</Text>
                  <div className="bulk-preview-change-items">
                    {bucket.items.map((item, index) => (
                      <div
                        className="bulk-preview-change-item"
                        key={`${item.kind}-${item.label}-${index}`}
                      >
                        <Text strong>{item.label}</Text>
                        {item.kind === "field" ? (
                          <ChangeValue
                            before={item.before}
                            after={item.after}
                          />
                        ) : null}
                        {item.kind === "contact" ? (
                          <Text type="secondary">{item.message}</Text>
                        ) : null}
                        {item.message && item.kind === "field" ? (
                          <Text type="secondary">{item.message}</Text>
                        ) : null}
                        {item.incoming !== null &&
                        item.incoming !== item.after ? (
                          <Text type="secondary">
                            本次观察：{item.incoming}
                          </Text>
                        ) : null}
                        {item.added.length > 0 ? (
                          <Text>新增：{item.added.join("、")}</Text>
                        ) : null}
                        {item.removed.length > 0 ? (
                          <Text type="secondary">
                            移除：{item.removed.join("、")}
                          </Text>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          </section>
        ) : null}

        {presented.issues.length > 0 ? (
          <section className="bulk-preview-drawer-section">
            <SectionTitle title="问题" />
            <div className="bulk-preview-issues-list">
              {presented.issues.map((issue, index) => (
                <div
                  className={
                    issue.severity === "error"
                      ? "bulk-preview-issue-item is-error"
                      : "bulk-preview-issue-item is-warning"
                  }
                  key={`${issue.code ?? issue.message}-${index}`}
                >
                  <Tag color={issue.severity === "error" ? "error" : "warning"}>
                    {issue.severity === "error" ? "错误" : "需关注"}
                  </Tag>
                  <Text>{issue.message}</Text>
                </div>
              ))}
            </div>
          </section>
        ) : null}
      </div>
    </Drawer>
  );
}
