import {
  Alert,
  Button,
  Pagination,
  Segmented,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import type { MouseEvent } from "react";

import {
  getPreviewCategoryLabel,
  presentPreviewRow,
} from "../preview-presenters";
import { presentRefreshReturnEvidence } from "../refresh-return-presenters";
import type {
  ImportJobFilePublic,
  ImportRowCategory,
  ImportRowPublic,
  UnifiedPreviewSummary,
} from "../types";

const { Paragraph, Text } = Typography;

const categories: readonly ImportRowCategory[] = [
  "all",
  "attention",
  "error",
  "manual_review",
  "warning",
  "changed",
  "new",
  "no_change",
  "duplicate",
];

const integerFormatter = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 0,
});

function actionTagColor(tone: string): string | undefined {
  if (tone === "success") return "blue";
  if (tone === "processing") return "purple";
  if (tone === "warning") return "warning";
  if (tone === "danger") return "error";
  return undefined;
}

function screeningTagColor(tone: string): string | undefined {
  if (tone === "success") return "success";
  if (tone === "warning") return "warning";
  return undefined;
}

function refreshReturnTagColor(tone: string): string | undefined {
  if (tone === "success") return "success";
  if (tone === "warning") return "warning";
  if (tone === "danger") return "error";
  if (tone === "processing") return "processing";
  return undefined;
}

function categoryCount(
  summary: UnifiedPreviewSummary | null,
  category: ImportRowCategory,
): number | null {
  if (!summary) return null;
  const counts: Partial<Record<ImportRowCategory, number>> = {
    all: summary.raw_rows,
    error: summary.error_rows,
    manual_review: summary.manual_review_rows,
    warning: summary.warning_rows,
    changed: summary.changed_rows,
    new: summary.new_rows,
    no_change: summary.no_change_rows,
    duplicate: summary.internal_duplicate_rows,
  };
  return counts[category] ?? null;
}

export type BulkPreviewTableProps = {
  rows: readonly ImportRowPublic[];
  files: readonly ImportJobFilePublic[];
  summary: UnifiedPreviewSummary | null;
  total: number;
  offset: number;
  limit: number;
  category: ImportRowCategory;
  loading: boolean;
  error: string | null;
  refreshReturn?: boolean;
  onCategoryChange: (category: ImportRowCategory) => void;
  onOffsetChange: (offset: number) => void;
  onSelectRow: (row: ImportRowPublic) => void;
  onReload: () => void;
};

export function BulkPreviewTable({
  rows,
  files,
  summary,
  total,
  offset,
  limit,
  category,
  loading,
  error,
  refreshReturn = false,
  onCategoryChange,
  onOffsetChange,
  onSelectRow,
  onReload,
}: BulkPreviewTableProps) {
  const columns: ColumnsType<ImportRowPublic> = [
    {
      title: "达人 / 来源",
      key: "creator",
      className: "bulk-preview-creator-col",
      width: 300,
      render: (_, row) => {
        const presented = presentPreviewRow(row);
        const source =
          files.find((file) => file.id === row.import_job_file_id)
            ?.original_filename ?? "来源文件";
        return (
          <div className="bulk-preview-creator-cell">
            <Text strong ellipsis={{ tooltip: presented.displayName ?? false }}>
              {presented.displayName ?? "—"}
            </Text>
            <Text type="secondary" ellipsis>
              {source} · 第 {row.row_number} 行
            </Text>
          </div>
        );
      },
    },
    {
      title: "处理结果",
      key: "action",
      width: 112,
      render: (_, row) => {
        const action = presentPreviewRow(row).action;
        return <Tag color={actionTagColor(action.tone)}>{action.label}</Tag>;
      },
    },
    {
      title: "筛选结果",
      key: "screening",
      width: 112,
      render: (_, row) => {
        const screening = presentPreviewRow(row).screening;
        return screening ? (
          <Tag color={screeningTagColor(screening.tone)}>{screening.label}</Tag>
        ) : (
          <Text type="secondary">—</Text>
        );
      },
    },
    {
      title: "变更",
      key: "changes",
      width: 130,
      render: (_, row) => {
        const count = presentPreviewRow(row).changeCount;
        return count > 0 ? (
          <Text>{integerFormatter.format(count)} 项</Text>
        ) : (
          <Text type="secondary">—</Text>
        );
      },
    },
    {
      title: "问题",
      key: "issues",
      width: 240,
      render: (_, row) => {
        const issues = presentPreviewRow(row).issues;
        if (issues.length === 0) return <Text type="secondary">—</Text>;
        const first = issues[0];
        if (!first) return <Text type="secondary">—</Text>;
        return (
          <div className="bulk-preview-issue-cell">
            <Paragraph
              className={
                first.severity === "error"
                  ? "bulk-preview-issue-error"
                  : "bulk-preview-issue-warning"
              }
              ellipsis={{ tooltip: first.message, rows: 2 }}
            >
              {first.message}
            </Paragraph>
            {issues.length > 1 ? (
              <Text type="secondary">另有 {issues.length - 1} 项</Text>
            ) : null}
          </div>
        );
      },
    },
    {
      title: "操作",
      key: "operation",
      width: 86,
      fixed: "right",
      render: (_, row) => (
        <Button
          type="link"
          size="small"
          onClick={(event: MouseEvent<HTMLElement>) => {
            event.stopPropagation();
            onSelectRow(row);
          }}
        >
          查看
        </Button>
      ),
    },
  ];

  if (refreshReturn) {
    columns.splice(3, 0, {
      title: "回流预览",
      key: "refresh-return",
      width: 156,
      render: (_, row) => {
        const presentation = presentRefreshReturnEvidence(
          row.merge_plan?.refresh_return,
        );
        return presentation ? (
          <Tag color={refreshReturnTagColor(presentation.tone)}>
            {presentation.label}
          </Tag>
        ) : (
          <Text type="secondary">—</Text>
        );
      },
    });
  }

  const categoryOptions = categories.map((value) => {
    const count = categoryCount(summary, value);
    return {
      value,
      label: (
        <span className="bulk-preview-category-label">
          {getPreviewCategoryLabel(value)}
          {count === null ? null : (
            <span aria-label={`${integerFormatter.format(count)} 条`}>
              {integerFormatter.format(count)}
            </span>
          )}
        </span>
      ),
    };
  });

  const emptyText = (
    <div className="bulk-preview-empty-state">
      <Text strong>当前分类没有数据</Text>
      {category === "all" ? null : (
        <Button type="link" onClick={() => onCategoryChange("all")}>
          查看全部
        </Button>
      )}
    </div>
  );

  return (
    <section
      className="bulk-preview-table-section"
      aria-labelledby="bulk-preview-table-title"
    >
      <div className="bulk-preview-table-heading">
        <div>
          <Text strong id="bulk-preview-table-title">
            分类筛选
          </Text>
          <Text type="secondary">先锁定目标类目，再看该类目中的每行结果。</Text>
        </div>
        <div className="bulk-preview-filter-bar">
          <Segmented<ImportRowCategory>
            className="bulk-preview-category-filter"
            value={category}
            options={categoryOptions}
            onChange={onCategoryChange}
          />
        </div>
      </div>

      {error ? (
        <Alert
          className="bulk-preview-rows-error"
          type="error"
          showIcon
          title="数据预览加载失败"
          description={error}
          action={
            <Button size="small" onClick={onReload}>
              重新加载
            </Button>
          }
        />
      ) : null}

      <Table<ImportRowPublic>
        className="bulk-preview-table"
        rowKey="id"
        columns={columns}
        dataSource={[...rows]}
        loading={loading}
        pagination={false}
        locale={{ emptyText }}
        scroll={{ x: refreshReturn ? 1200 : 1040 }}
        onRow={(row) => ({
          onClick: () => onSelectRow(row),
          className: "bulk-preview-table-row",
        })}
      />

      <div className="bulk-preview-pagination">
        <Text type="secondary">共 {integerFormatter.format(total)} 条</Text>
        <Pagination
          current={Math.floor(offset / Math.max(limit, 1)) + 1}
          pageSize={limit}
          total={total}
          showSizeChanger={false}
          hideOnSinglePage={total <= limit}
          onChange={(page) => onOffsetChange((page - 1) * limit)}
        />
      </div>
    </section>
  );
}
