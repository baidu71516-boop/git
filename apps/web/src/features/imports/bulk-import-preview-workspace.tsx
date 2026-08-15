"use client";

import { Select, Space, Typography } from "antd";
import { useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";

import { BulkImportWorkspaceView } from "./bulk-import-workspace-view";
import { ScreeningRuleEditorModal } from "./components/screening-rule-editor-modal";
import {
  createBulkPreviewScenarios,
  getBulkPreviewRowsPage,
  type BulkPreviewScenarioKey,
} from "./preview-fixtures";
import type { ImportRowCategory, ImportRowPublic } from "./types";

const { Text } = Typography;
const noOperation = () => undefined;
const previewPageSize = 50;
const previewStatePlaceholder = "选择开发预览场景";

function getPreviewScenarioLabel(key: BulkPreviewScenarioKey): string {
  switch (key) {
    case "screening_rules_editable":
      return "筛选规则 · 可编辑";
    case "screening_rules_conflict":
      return "筛选规则 · 规则冲突";
    case "screening_rules_readonly":
      return "筛选规则 · 只读";
    case "preview_ready":
      return "筛选规则 · 已有数据预览";
    case "no_job":
      return "无批量任务";
    case "no_files":
      return "任务无文件";
    case "mixed_files":
      return "多文件处理中";
    case "preview_stale":
      return "数据预览需重建";
    case "confirm_queued":
      return "确认任务排队中";
    case "importing":
      return "导入中";
    case "completed":
      return "导入完成";
    case "refresh_preview_ready":
      return "更新回流预览";
    case "refresh_completed":
      return "更新回流完成";
    case "failed":
      return "任务失败";
    default:
      return key;
  }
}

export function BulkImportPreviewWorkspace() {
  const scenarios = useMemo(() => createBulkPreviewScenarios(), []);
  const [scenarioKey, setScenarioKey] =
    useState<BulkPreviewScenarioKey>("preview_ready");
  const [category, setCategory] = useState<ImportRowCategory>("all");
  const [offset, setOffset] = useState(0);
  const [selectedRow, setSelectedRow] = useState<ImportRowPublic | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [screeningRuleModalOpen, setScreeningRuleModalOpen] = useState(false);
  const [screeningRuleConflict, setScreeningRuleConflict] = useState(false);
  const [screeningRuleReloading, setScreeningRuleReloading] = useState(false);
  const scenario =
    scenarios.find((candidate) => candidate.key === scenarioKey) ??
    scenarios[0]!;

  const isReadOnlyScenario = scenario.key === "screening_rules_readonly";
  const isConflictScenario = scenario.key === "screening_rules_conflict";

  const rowsPage = getBulkPreviewRowsPage(
    scenario,
    category,
    offset,
    previewPageSize,
  );

  function changeScenario(value: BulkPreviewScenarioKey) {
    const nextScenario = scenarios.find((candidate) => candidate.key === value);
    setScenarioKey(value);
    setScreeningRuleConflict(value === "screening_rules_conflict");
    if (!nextScenario?.job?.preview_summary) setCategory("all");
    setOffset(0);
    setSelectedRow(null);
    setConfirmOpen(false);
  }

  function changeCategory(value: ImportRowCategory) {
    setCategory(value);
    setOffset(0);
    setSelectedRow(null);
  }

  const canShowPreviewRuleHint =
    Boolean(
      scenario.job?.preview_revision && scenario.job.preview_revision > 0,
    ) && !isConflictScenario;

  function openScreeningRules() {
    setScreeningRuleModalOpen(true);
  }

  function closeScreeningRules() {
    setScreeningRuleModalOpen(false);
  }

  async function reloadLatestScreeningRules() {
    setScreeningRuleReloading(true);
    await new Promise((resolve) => setTimeout(resolve, 180));
    setScreeningRuleConflict(false);
    setScreeningRuleReloading(false);
  }

  return (
    <AppShell
      title="数据采集"
      description="上传达人数据，完成文件检查后生成数据预览。"
      department="界面预览"
      operator="预览用户"
      role="仅展示"
      onLogout={noOperation}
    >
      <section
        className="data-collection-preview"
        aria-label="数据采集视觉预览"
      >
        <div className="data-collection-preview-controls">
          <Text strong>开发预览状态</Text>
          <Space direction="vertical" size="small" style={{ minWidth: 0 }}>
            <Text type="secondary">预览场景</Text>
            <Select
              style={{ width: 300, maxWidth: "100%" }}
              value={scenarioKey}
              options={scenarios.map(({ key }) => ({
                value: key,
                label: getPreviewScenarioLabel(key),
              }))}
              onChange={(value) => changeScenario(value)}
              showSearch
              optionFilterProp="label"
              filterOption={(input, option) => {
                const label = String(option?.label ?? "").toLocaleLowerCase();
                const keyword = input.trim().toLocaleLowerCase();
                return label.includes(keyword);
              }}
              placeholder={previewStatePlaceholder}
            />
          </Space>
        </div>

        <Space orientation="vertical" size="middle" className="full-width">
          <BulkImportWorkspaceView
            job={scenario.job}
            collection={scenario.collection}
            files={scenario.files}
            readOnly={isReadOnlyScenario}
            uploadItems={[]}
            busyFileId={null}
            previewBusy={false}
            error={null}
            notice={null}
            refreshContext={
              scenario.job?.refresh_queue_id
                ? {
                    queueId: scenario.job.refresh_queue_id,
                    detail: scenario.refreshQueueDetail ?? null,
                    loading: false,
                    error: null,
                    canExit: false,
                    showViewLink: scenario.job.status !== "completed",
                    onExit: noOperation,
                    onReload: noOperation,
                  }
                : null
            }
            onNewCollection={noOperation}
            onSelectFiles={noOperation}
            onRetryUpload={noOperation}
            onEditAcquisitionTime={noOperation}
            onEditMapping={noOperation}
            onRetryFile={noOperation}
            onExcludeFile={noOperation}
            onRequestPreview={noOperation}
            onRebuildPreview={noOperation}
            onRetryJob={noOperation}
            onOpenScreeningRules={
              scenario.collection ? openScreeningRules : undefined
            }
            preview={{
              rowsPage,
              selectedRow,
              category,
              offset,
              loadingRows: false,
              rowsError: null,
              confirmBusy: false,
              confirmAmbiguous: false,
              confirmError: null,
              confirmOpen,
              onCategoryChange: changeCategory,
              onOffsetChange: (nextOffset) => {
                setOffset(nextOffset);
                setSelectedRow(null);
              },
              onSelectRow: setSelectedRow,
              onCloseRow: () => setSelectedRow(null),
              onReloadRows: noOperation,
              onRequestConfirm: () => setConfirmOpen(true),
              onCancelConfirm: () => setConfirmOpen(false),
              onConfirm: () => setConfirmOpen(false),
            }}
          />
          {scenario.collection ? (
            <ScreeningRuleEditorModal
              open={screeningRuleModalOpen}
              collection={scenario.collection}
              readOnly={isReadOnlyScenario}
              previewReady={canShowPreviewRuleHint}
              saving={false}
              reloading={screeningRuleReloading}
              conflict={screeningRuleConflict}
              error={null}
              onCancel={closeScreeningRules}
              onSave={() => {
                setScreeningRuleModalOpen(false);
              }}
              onReloadLatest={() => void reloadLatestScreeningRules()}
            />
          ) : null}
        </Space>
      </section>
    </AppShell>
  );
}
