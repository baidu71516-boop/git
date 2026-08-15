"use client";

import { Segmented, Space, Typography } from "antd";
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
          <Segmented
            block
            value={scenarioKey}
            options={scenarios.map(({ key, label }) => ({
              label,
              value: key,
            }))}
            onChange={(value) =>
              changeScenario(value as BulkPreviewScenarioKey)
            }
          />
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
