import { Button, Popconfirm, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";

import {
  formatBulkDateTime,
  formatBulkFileRowCount,
  formatBulkFileSize,
  getBulkFileIssueMessage,
  getBulkFileStatusPresentation,
} from "../formatters";
import type { ImportJobFilePublic } from "../types";

const { Text } = Typography;

export function BulkFileTable({
  files,
  readOnly,
  frozen,
  busyFileId,
  onEditAcquisitionTime,
  onEditMapping,
  onRetry,
  onExclude,
}: {
  files: ImportJobFilePublic[];
  readOnly: boolean;
  frozen: boolean;
  busyFileId: string | null;
  onEditAcquisitionTime: (file: ImportJobFilePublic) => void;
  onEditMapping: (file: ImportJobFilePublic) => void;
  onRetry: (file: ImportJobFilePublic) => void;
  onExclude: (file: ImportJobFilePublic) => void;
}) {
  const columns: ColumnsType<ImportJobFilePublic> = [
    {
      title: "文件",
      dataIndex: "original_filename",
      key: "file",
      width: 230,
      render: (_, file) => (
        <div className="bulk-file-name-cell">
          <Text strong ellipsis={{ tooltip: file.original_filename }}>
            {file.original_filename}
          </Text>
          <Text type="secondary">{formatBulkFileSize(file.file_size)}</Text>
        </div>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 140,
      render: (_, file) => {
        const status = getBulkFileStatusPresentation(file.status);
        const color =
          status.tone === "default"
            ? undefined
            : status.tone === "danger"
              ? "error"
              : status.tone;
        return <Tag color={color}>{status.label}</Tag>;
      },
    },
    {
      title: "行数",
      key: "rows",
      width: 86,
      render: (_, file) => formatBulkFileRowCount(file),
    },
    {
      title: "数据取得时间",
      key: "source_acquired_at",
      width: 190,
      render: (_, file) => (
        <Space orientation="vertical" size={2}>
          <Text>{formatBulkDateTime(file.source_acquired_at)}</Text>
          {file.source_acquired_at_confirmation_required ? (
            <Tag color="warning">待确认</Tag>
          ) : null}
        </Space>
      ),
    },
    {
      title: "问题",
      key: "issue",
      width: 210,
      render: (_, file) => (
        <Text type={file.status === "failed" ? "danger" : "secondary"}>
          {getBulkFileIssueMessage(file)}
        </Text>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 260,
      fixed: "right",
      render: (_, file) => {
        const busy = busyFileId === file.id;
        const mutationDisabled = readOnly || frozen || busyFileId !== null;
        const canEditTime = ["ready", "mapping_required", "failed"].includes(
          file.status,
        );
        const canExclude = [
          "uploaded",
          "ready",
          "mapping_required",
          "failed",
        ].includes(file.status);

        if (file.status === "parsing" || file.status === "excluded") {
          return <Text type="secondary">—</Text>;
        }

        return (
          <Space size="small" wrap>
            {file.status === "mapping_required" ? (
              <Button
                type="link"
                size="small"
                disabled={mutationDisabled}
                onClick={() => onEditMapping(file)}
              >
                处理字段映射
              </Button>
            ) : null}
            {file.status === "failed" ? (
              <Button
                type="link"
                size="small"
                loading={busy}
                disabled={mutationDisabled}
                onClick={() => onRetry(file)}
              >
                重试
              </Button>
            ) : null}
            {canEditTime ? (
              <Button
                className="bulk-file-time-action"
                type="link"
                size="small"
                disabled={mutationDisabled}
                onClick={() => onEditAcquisitionTime(file)}
              >
                {file.source_acquired_at_confirmation_required
                  ? "确认时间"
                  : "修改数据取得时间"}
              </Button>
            ) : null}
            {canExclude ? (
              <Popconfirm
                title="确认排除这个文件？"
                description="排除后，该文件不会参与本次数据预览。"
                okText="确认排除"
                cancelText="取消"
                disabled={mutationDisabled}
                onConfirm={() => onExclude(file)}
              >
                <Button
                  type="link"
                  size="small"
                  danger
                  loading={busy}
                  disabled={mutationDisabled}
                >
                  排除
                </Button>
              </Popconfirm>
            ) : null}
          </Space>
        );
      },
    },
  ];

  return (
    <Table<ImportJobFilePublic>
      className="bulk-file-table"
      rowKey="id"
      columns={columns}
      dataSource={files}
      pagination={false}
      scroll={{ x: 1120 }}
    />
  );
}
