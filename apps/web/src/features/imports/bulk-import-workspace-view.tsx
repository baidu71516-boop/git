import { Alert, Button, Card, Empty, Space, Typography } from "antd";

import { getBulkJobStatusPresentation, getPreviewGate } from "./formatters";
import { BulkFileTable } from "./components/bulk-file-table";
import {
  BulkFileUploader,
  type BulkUploadItem,
} from "./components/bulk-file-uploader";
import { BulkJobSummary } from "./components/bulk-job-summary";
import type {
  CollectionJobPublic,
  ImportJobFilePublic,
  ImportJobPublic,
} from "./types";

const { Text, Title } = Typography;

function JobStateAlert({
  job,
  readOnly,
  busy,
  onRebuildPreview,
  onRetryJob,
}: {
  job: ImportJobPublic;
  readOnly: boolean;
  busy: boolean;
  onRebuildPreview: () => void;
  onRetryJob: () => void;
}) {
  const status = getBulkJobStatusPresentation(job.status);

  if (job.status === "draft") return null;
  if (job.status === "preview_ready") {
    return (
      <Alert
        type="success"
        showIcon
        title="数据预览已生成"
        description="文件检查已经完成。"
      />
    );
  }
  if (job.status === "preview_stale") {
    return (
      <Alert
        type="warning"
        showIcon
        title="数据预览需要重新生成"
        description="当前数据状态已变化，请重新生成数据预览。"
        action={
          readOnly ? null : (
            <Button size="small" loading={busy} onClick={onRebuildPreview}>
              重新生成数据预览
            </Button>
          )
        }
      />
    );
  }
  if (job.status === "failed") {
    return (
      <Alert
        type="error"
        showIcon
        title="处理失败"
        description="批量任务暂时无法继续，请使用系统支持的任务重试。"
        action={
          readOnly ? null : (
            <Button size="small" loading={busy} onClick={onRetryJob}>
              重试
            </Button>
          )
        }
      />
    );
  }

  const type = job.status === "completed" ? "success" : "info";
  const description =
    job.status === "previewing"
      ? "后台正在生成数据预览，页面会自动刷新。"
      : job.status === "confirm_queued"
        ? "任务正在准备导入，页面会自动刷新。"
        : job.status === "importing"
          ? "任务正在导入，页面会自动刷新。"
          : job.status === "completed"
            ? "批量导入已经完成。"
            : job.status === "cancelled"
              ? "当前任务已取消。"
              : "任务状态正在更新，请刷新后查看。";

  return (
    <Alert
      type={type}
      showIcon
      title={status.label}
      description={description}
    />
  );
}

export type BulkImportWorkspaceViewProps = {
  job: ImportJobPublic | null;
  collection: CollectionJobPublic | null;
  files: ImportJobFilePublic[];
  readOnly: boolean;
  uploadItems: readonly BulkUploadItem[];
  busyFileId: string | null;
  previewBusy: boolean;
  error: string | null;
  notice: string | null;
  onNewCollection: () => void;
  onSelectFiles: (files: File[]) => void;
  onRetryUpload: (item: BulkUploadItem) => void;
  onEditAcquisitionTime: (file: ImportJobFilePublic) => void;
  onEditMapping: (file: ImportJobFilePublic) => void;
  onRetryFile: (file: ImportJobFilePublic) => void;
  onExcludeFile: (file: ImportJobFilePublic) => void;
  onRequestPreview: () => void;
  onRebuildPreview: () => void;
  onRetryJob: () => void;
};

export function BulkImportWorkspaceView({
  job,
  collection,
  files,
  readOnly,
  uploadItems,
  busyFileId,
  previewBusy,
  error,
  notice,
  onNewCollection,
  onSelectFiles,
  onRetryUpload,
  onEditAcquisitionTime,
  onEditMapping,
  onRetryFile,
  onExcludeFile,
  onRequestPreview,
  onRebuildPreview,
  onRetryJob,
}: BulkImportWorkspaceViewProps) {
  if (!job) {
    return (
      <Card variant="borderless" className="bulk-workspace-card">
        {error ? <Alert type="error" showIcon title={error} /> : null}
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Space orientation="vertical" size={2}>
              <Text strong>还没有批量采集任务</Text>
              <Text type="secondary">
                创建采集任务后，即可添加并处理多个达人数据文件。
              </Text>
            </Space>
          }
        >
          {readOnly ? null : (
            <Button type="primary" onClick={onNewCollection}>
              新建采集任务
            </Button>
          )}
        </Empty>
      </Card>
    );
  }

  const frozen = job.preview_revision > 0 || job.status !== "draft";
  const uploadBusy = uploadItems.some(
    (item) => item.status === "queued" || item.status === "uploading",
  );
  const previewGate = getPreviewGate(files);
  const previewDisabled =
    readOnly ||
    !previewGate.allowed ||
    previewBusy ||
    uploadBusy ||
    busyFileId !== null;
  const previewReason = readOnly
    ? "只读角色不能生成数据预览。"
    : previewBusy
      ? "正在提交数据预览请求。"
      : uploadBusy
        ? "请等待当前文件上传完成。"
        : busyFileId !== null
          ? "请等待当前文件操作完成。"
          : previewGate.reason;

  return (
    <Card variant="borderless" className="bulk-workspace-card">
      <Space orientation="vertical" size="middle" className="full-width">
        <BulkJobSummary
          collection={collection}
          readOnly={readOnly}
          onNewCollection={onNewCollection}
        />

        {readOnly ? (
          <Alert
            type="info"
            showIcon
            title="只读角色可以查看批量任务，但不能修改文件或生成数据预览。"
          />
        ) : null}
        {error ? <Alert type="error" showIcon title={error} closable /> : null}
        {notice ? (
          <Alert type="success" showIcon title={notice} closable />
        ) : null}

        <JobStateAlert
          job={job}
          readOnly={readOnly}
          busy={previewBusy}
          onRebuildPreview={onRebuildPreview}
          onRetryJob={onRetryJob}
        />

        <section
          className="bulk-file-section"
          aria-labelledby="bulk-files-title"
        >
          <div className="bulk-file-section-heading">
            <div>
              <Title level={5} id="bulk-files-title">
                文件处理
              </Title>
              <Text type="secondary">
                逐个完成文件检查、字段映射与数据取得时间确认。
              </Text>
            </div>
            <BulkFileUploader
              items={uploadItems}
              disabled={readOnly || frozen || uploadBusy}
              onSelectFiles={onSelectFiles}
              onRetry={onRetryUpload}
            />
          </div>

          {files.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <Space orientation="vertical" size={2}>
                  <Text strong>还没有文件</Text>
                  <Text type="secondary">添加 CSV / XLSX 文件开始处理。</Text>
                </Space>
              }
            />
          ) : (
            <BulkFileTable
              files={files}
              readOnly={readOnly}
              frozen={frozen}
              busyFileId={busyFileId}
              onEditAcquisitionTime={onEditAcquisitionTime}
              onEditMapping={onEditMapping}
              onRetry={onRetryFile}
              onExclude={onExcludeFile}
            />
          )}

          {job.status === "draft" ? (
            <div className="bulk-preview-footer">
              <Text type={previewGate.allowed ? "secondary" : "warning"}>
                {previewReason}
              </Text>
              <Button
                type="primary"
                disabled={previewDisabled}
                loading={previewBusy}
                onClick={onRequestPreview}
              >
                生成数据预览
              </Button>
            </div>
          ) : null}
        </section>
      </Space>
    </Card>
  );
}
