import { DownOutlined } from "@ant-design/icons";
import {
  Button,
  Dropdown,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";

import {
  formatBulkDateTime,
  getBulkFileIssueTone,
  formatBulkFileRowCount,
  formatBulkFileSize,
  getBulkFileIssueMessage,
  getBulkFileStatusPresentation,
} from "../formatters";
import type { ImportJobFilePublic } from "../types";

const { Text } = Typography;

function MoreActionsMenu({
  file,
  canEditTime,
  canExclude,
  disabled,
  onEditAcquisitionTime,
  onExclude,
}: {
  file: ImportJobFilePublic;
  canEditTime: boolean;
  canExclude: boolean;
  disabled: boolean;
  onEditAcquisitionTime: (file: ImportJobFilePublic) => void;
  onExclude: (file: ImportJobFilePublic) => void;
}) {
  return (
    <div className="bulk-file-more-menu">
      {canEditTime ? (
        <Button
          type="text"
          size="small"
          disabled={disabled}
          onClick={() => onEditAcquisitionTime(file)}
          data-file-id={file.id}
        >
          修改时间
        </Button>
      ) : null}
      {canExclude ? (
        <Popconfirm
          title="确认排除这个文件？"
          description="排除后，该文件不会参与本次数据预览。"
          okText="确认排除"
          cancelText="取消"
          disabled={disabled}
          onConfirm={() => onExclude(file)}
        >
          <Button
            type="text"
            size="small"
            danger
            disabled={disabled}
            data-file-id={file.id}
          >
            排除
          </Button>
        </Popconfirm>
      ) : null}
    </div>
  );
}

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
      width: 320,
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
        <Space orientation="vertical" size={2} className="bulk-file-time-cell">
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
      render: (_, file) => {
        const tone = getBulkFileIssueTone(file);
        return (
          <Text
            className={
              tone === "danger"
                ? "bulk-file-issue-danger"
                : tone === "warning"
                  ? "bulk-file-issue-warning"
                  : "bulk-file-issue-secondary"
            }
            type={tone === "secondary" ? undefined : tone}
          >
            {getBulkFileIssueMessage(file)}
          </Text>
        );
      },
    },
    {
      title: "操作",
      key: "actions",
      width: 210,
      fixed: "right",
      className: "bulk-file-actions-column",
      render: (_, file) => {
        const busy = busyFileId === file.id;
        const actionDisabled = readOnly || frozen || busyFileId !== null;
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

        const hasSecondaryActions = canEditTime || canExclude;

        return (
          <div className="bulk-file-actions-cell">
            <Space
              size="small"
              align="center"
              className="bulk-file-primary-actions"
            >
              {file.status === "mapping_required" ? (
                <Button
                  type="link"
                  size="small"
                  disabled={actionDisabled}
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
                  disabled={actionDisabled}
                  onClick={() => onRetry(file)}
                >
                  重试
                </Button>
              ) : null}

              {hasSecondaryActions ? (
                <Dropdown
                  trigger={["click"]}
                  placement="bottomRight"
                  popupRender={() => (
                    <MoreActionsMenu
                      file={file}
                      disabled={actionDisabled}
                      canEditTime={canEditTime}
                      canExclude={canExclude}
                      onEditAcquisitionTime={onEditAcquisitionTime}
                      onExclude={onExclude}
                    />
                  )}
                  disabled={actionDisabled}
                >
                  <Button type="link" size="small">
                    更多 <DownOutlined />
                  </Button>
                </Dropdown>
              ) : null}
            </Space>
          </div>
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
