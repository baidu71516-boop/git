import { Alert, Button, Modal, Spin, Typography } from "antd";

import {
  AMBIGUOUS_BULK_CONFIRM_MESSAGE,
  formatBulkDateTime,
} from "../formatters";
import type {
  ImportJobFilePublic,
  ImportJobPublic,
  ImportRowCategory,
  ImportRowPublic,
  ImportRowsPage,
} from "../types";
import { BulkPreviewRowDrawer } from "./bulk-preview-row-drawer";
import { BulkPreviewSummary } from "./bulk-preview-summary";
import { BulkPreviewTable } from "./bulk-preview-table";

const { Text, Title } = Typography;

const integerFormatter = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 0,
});

const PREVIEW_PAGE_SIZE = 50;

function StateNotice({ job }: { job: ImportJobPublic }) {
  if (job.status === "preview_stale") {
    return (
      <Alert
        className="bulk-preview-state-notice"
        type="warning"
        showIcon
        title="数据预览需要重新生成"
        description={
          job.confirmed_revision === null
            ? "文件、筛选规则或相关数据已经发生变化，请重新生成数据预览后再确认导入。"
            : "数据预览已失效，本次未写入达人库。请重新生成数据预览后再确认导入。"
        }
      />
    );
  }

  if (job.status === "failed") {
    return (
      <Alert
        className="bulk-preview-state-notice"
        type="error"
        showIcon
        title="任务处理失败"
        description="系统未能完成当前处理，请使用任务重试后继续。"
      />
    );
  }

  if (job.status === "previewing") {
    return (
      <Alert
        className="bulk-preview-state-notice"
        type="info"
        showIcon
        title="正在生成数据预览"
        description="文件正在后台处理，完成后会显示预览结果。"
      />
    );
  }

  if (job.status === "confirm_queued" || job.status === "importing") {
    return (
      <Alert
        className="bulk-preview-state-notice"
        type="info"
        showIcon
        title={job.status === "confirm_queued" ? "正在准备导入" : "正在导入"}
        description="系统正在处理当前任务，无需重复提交。"
        action={<Spin size="small" />}
      />
    );
  }

  if (job.status === "completed") {
    return (
      <Alert
        className="bulk-preview-state-notice"
        type="success"
        showIcon
        title="导入完成"
        description={
          job.completed_at
            ? `完成时间：${formatBulkDateTime(job.completed_at)}`
            : "本批数据已经完成导入。"
        }
      />
    );
  }

  return null;
}

function CompletedResult({ job }: { job: ImportJobPublic }) {
  if (!job.result) return null;
  const result = job.result;
  const metrics = [
    { label: "新增", value: result.created_rows },
    { label: "更新", value: result.updated_rows },
    { label: "无需变更", value: result.no_change_rows },
    { label: "跳过", value: result.skipped_rows },
    { label: "需人工处理", value: result.manual_review_rows },
    { label: "错误", value: result.error_rows },
  ];
  return (
    <section
      className="bulk-preview-completed-result"
      aria-labelledby="bulk-preview-result-title"
    >
      <div className="bulk-preview-result-heading">
        <div>
          <Title level={5} id="bulk-preview-result-title">
            导入结果
          </Title>
          <Text type="secondary">以下为本次实际完成的处理结果。</Text>
        </div>
      </div>
      <dl className="bulk-preview-result-band">
        {metrics.map((metric) => (
          <div key={metric.label}>
            <dt>{metric.label}</dt>
            <dd>{integerFormatter.format(metric.value)}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export type BulkPreviewWorkspaceViewProps = {
  job: ImportJobPublic;
  files: readonly ImportJobFilePublic[];
  rowsPage: ImportRowsPage | null;
  selectedRow: ImportRowPublic | null;
  category: ImportRowCategory;
  offset: number;
  loadingRows: boolean;
  rowsError: string | null;
  readOnly: boolean;
  confirmBusy: boolean;
  confirmAmbiguous: boolean;
  confirmError: string | null;
  confirmOpen: boolean;
  onCategoryChange: (category: ImportRowCategory) => void;
  onOffsetChange: (offset: number) => void;
  onSelectRow: (row: ImportRowPublic) => void;
  onCloseRow: () => void;
  onReloadRows: () => void;
  onRequestConfirm: () => void;
  onCancelConfirm: () => void;
  onConfirm: () => void;
  onRebuildPreview: () => void;
  onRetryJob: () => void;
};

export function BulkPreviewWorkspaceView({
  job,
  files,
  rowsPage,
  selectedRow,
  category,
  offset,
  loadingRows,
  rowsError,
  readOnly,
  confirmBusy,
  confirmAmbiguous,
  confirmError,
  confirmOpen,
  onCategoryChange,
  onOffsetChange,
  onSelectRow,
  onCloseRow,
  onReloadRows,
  onRequestConfirm,
  onCancelConfirm,
  onConfirm,
  onRebuildPreview,
  onRetryJob,
}: BulkPreviewWorkspaceViewProps) {
  const summary = job.preview_summary;
  const confirmAvailable =
    job.status === "preview_ready" &&
    job.preview_revision > 0 &&
    summary !== null &&
    !readOnly &&
    !confirmAmbiguous;
  const showPreview = summary !== null;
  const oldPreview =
    job.status === "preview_stale" ||
    job.status === "failed" ||
    job.status === "previewing";

  const headerAction =
    job.status === "preview_ready" ? (
      readOnly ? null : (
        <Button
          type="primary"
          disabled={!confirmAvailable || confirmBusy}
          loading={confirmBusy}
          onClick={onRequestConfirm}
        >
          确认导入
        </Button>
      )
    ) : job.status === "preview_stale" ? (
      readOnly ? null : (
        <Button loading={confirmBusy} onClick={onRebuildPreview}>
          重新生成数据预览
        </Button>
      )
    ) : job.status === "failed" ? (
      readOnly ? null : (
        <Button loading={confirmBusy} onClick={onRetryJob}>
          重试
        </Button>
      )
    ) : job.status === "completed" ? (
      <Button href="/influencers">查看达人库</Button>
    ) : null;

  const previewContent = showPreview ? (
    <div
      className={`bulk-preview-review${oldPreview ? " is-expired" : ""}`}
      aria-label={oldPreview ? "已过期的数据预览，只读参考" : "当前数据预览"}
    >
      {oldPreview ? (
        <div className="bulk-preview-expired-label">
          <Text strong>已有数据预览（只读参考）</Text>
          <Text type="secondary">此预览不能用于确认导入。</Text>
        </div>
      ) : null}
      <BulkPreviewSummary summary={summary} />
      <BulkPreviewTable
        rows={rowsPage?.items ?? []}
        files={files}
        summary={summary}
        total={rowsPage?.total ?? 0}
        offset={rowsPage?.offset ?? offset}
        limit={rowsPage?.limit ?? PREVIEW_PAGE_SIZE}
        category={category}
        loading={loadingRows}
        error={rowsError}
        onCategoryChange={onCategoryChange}
        onOffsetChange={onOffsetChange}
        onSelectRow={onSelectRow}
        onReload={onReloadRows}
      />
    </div>
  ) : null;

  return (
    <section
      className="bulk-preview-workspace"
      aria-labelledby="bulk-preview-workspace-title"
    >
      <header className="bulk-preview-workspace-heading">
        <div>
          <Title level={4} id="bulk-preview-workspace-title">
            数据预览
          </Title>
          <Text type="secondary">
            检查新增、变更、筛选结果和需要关注的数据。
          </Text>
        </div>
        {headerAction}
      </header>

      <StateNotice job={job} />

      {confirmAmbiguous ? (
        <Alert
          className="bulk-preview-state-notice"
          type="warning"
          showIcon
          title="正在核对导入状态"
          description={AMBIGUOUS_BULK_CONFIRM_MESSAGE}
        />
      ) : null}
      {confirmError ? (
        <Alert
          className="bulk-preview-state-notice"
          type="error"
          showIcon
          title="确认导入失败"
          description={confirmError}
        />
      ) : null}

      {job.status === "completed" ? <CompletedResult job={job} /> : null}
      {job.status === "completed" && previewContent ? (
        <details className="bulk-preview-completed-preview">
          <summary>查看导入前的数据预览</summary>
          {previewContent}
        </details>
      ) : (
        previewContent
      )}

      <BulkPreviewRowDrawer
        open={selectedRow !== null}
        row={selectedRow}
        files={files}
        onClose={onCloseRow}
      />

      <Modal
        className="bulk-preview-confirm-modal"
        open={confirmOpen}
        title="确认导入这批数据？"
        okText="确认导入"
        cancelText="取消"
        confirmLoading={confirmBusy}
        okButtonProps={{ disabled: !confirmAvailable || confirmBusy }}
        cancelButtonProps={{ disabled: confirmBusy }}
        mask={{ closable: !confirmBusy }}
        keyboard={!confirmBusy}
        onOk={onConfirm}
        onCancel={onCancelConfirm}
      >
        {summary ? (
          <dl className="bulk-preview-confirm-summary">
            <div>
              <dt>新增</dt>
              <dd>{integerFormatter.format(summary.created_rows)}</dd>
            </div>
            <div>
              <dt>更新</dt>
              <dd>{integerFormatter.format(summary.updated_rows)}</dd>
            </div>
            <div>
              <dt>无需变更</dt>
              <dd>{integerFormatter.format(summary.no_change_rows)}</dd>
            </div>
            <div>
              <dt>跳过</dt>
              <dd>{integerFormatter.format(summary.skipped_rows)}</dd>
            </div>
            <div>
              <dt>需人工处理</dt>
              <dd>{integerFormatter.format(summary.manual_review_rows)}</dd>
            </div>
            <div>
              <dt>错误</dt>
              <dd>{integerFormatter.format(summary.error_rows)}</dd>
            </div>
          </dl>
        ) : null}
        <Text>确认后，系统会按当前数据预览结果写入达人库。</Text>
        <Text type="secondary" className="bulk-preview-confirm-note">
          如果导入前检测到数据或规则变化，本次导入会停止，并要求重新生成数据预览。
        </Text>
        {confirmError ? (
          <Alert
            className="bulk-preview-confirm-error"
            type="error"
            showIcon
            title={confirmError}
          />
        ) : null}
      </Modal>
    </section>
  );
}
