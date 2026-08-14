import { Tag, Typography } from "antd";

import { getScreeningPresentation } from "../preview-presenters";
import type { ScreeningResult, UnifiedPreviewSummary } from "../types";

const { Text, Title } = Typography;

const integerFormatter = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 0,
});

const coreMetrics: ReadonlyArray<{
  key: keyof UnifiedPreviewSummary;
  label: string;
}> = [
  { key: "raw_rows", label: "总数据" },
  { key: "new_rows", label: "新增" },
  { key: "changed_rows", label: "有变更" },
  { key: "no_change_rows", label: "无需变更" },
  { key: "manual_review_rows", label: "需人工处理" },
  { key: "error_rows", label: "错误" },
];

const secondaryMetrics: ReadonlyArray<{
  key: keyof UnifiedPreviewSummary;
  label: string;
}> = [
  { key: "file_count", label: "参与文件" },
  { key: "excluded_file_count", label: "排除文件" },
  { key: "unique_rows", label: "去重后" },
  { key: "internal_duplicate_rows", label: "内部重复" },
  { key: "existing_rows", label: "已有数据" },
  { key: "warning_rows", label: "有警告" },
  {
    key: "possible_duplicate_contact_rows",
    label: "疑似重复联系方式",
  },
];

const screeningMetrics: ReadonlyArray<{
  result: ScreeningResult;
  key: keyof UnifiedPreviewSummary;
}> = [
  { result: "MATCH", key: "screening_match_rows" },
  { result: "NOT_MATCH", key: "screening_not_match_rows" },
  { result: "UNKNOWN", key: "screening_unknown_rows" },
];

function metricValue(
  summary: UnifiedPreviewSummary,
  key: keyof UnifiedPreviewSummary,
): string {
  const value = summary[key];
  return typeof value === "number" ? integerFormatter.format(value) : "—";
}

function screeningTone(tone: string): string {
  if (tone === "success") return "success";
  if (tone === "warning") return "warning";
  return "default";
}

export function BulkPreviewSummary({
  summary,
}: {
  summary: UnifiedPreviewSummary;
}) {
  return (
    <section
      className="bulk-preview-summary"
      aria-labelledby="bulk-preview-summary-title"
    >
      <Title level={5} id="bulk-preview-summary-title">
        核心结果
      </Title>

      <dl className="bulk-preview-summary-band">
        {coreMetrics.map((metric) => (
          <div className="bulk-preview-summary-metric" key={metric.key}>
            <dt>{metric.label}</dt>
            <dd>{metricValue(summary, metric.key)}</dd>
          </div>
        ))}
      </dl>

      <div className="bulk-preview-screening-summary">
        <div className="bulk-preview-screening-summary-meta">
          <Text strong>筛选结果</Text>
          <Text type="secondary">后端已按当前筛选规则完成判断</Text>
        </div>
        <div className="bulk-preview-screening-counts" role="list">
          {screeningMetrics.map(({ result, key }) => {
            const presentation = getScreeningPresentation(result);
            if (!presentation) return null;
            return (
              <Tag color={screeningTone(presentation.tone)} key={result}>
                {presentation.label} {metricValue(summary, key)}
              </Tag>
            );
          })}
        </div>
      </div>

      <details className="bulk-preview-more-stats">
        <summary>更多统计</summary>
        <dl>
          {secondaryMetrics.map((metric) => (
            <div key={metric.key}>
              <dt>{metric.label}</dt>
              <dd>{metricValue(summary, metric.key)}</dd>
            </div>
          ))}
        </dl>
      </details>
    </section>
  );
}
