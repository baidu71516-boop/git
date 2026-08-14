"use client";

import { Segmented, Space, Typography } from "antd";
import { useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";

import { BulkImportWorkspaceView } from "./bulk-import-workspace-view";
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
  const scenario =
    scenarios.find((candidate) => candidate.key === scenarioKey) ??
    scenarios[0];

  if (!scenario) return null;

  const rowsPage = getBulkPreviewRowsPage(
    scenario,
    category,
    offset,
    previewPageSize,
  );

  function changeScenario(value: BulkPreviewScenarioKey) {
    const nextScenario = scenarios.find((candidate) => candidate.key === value);
    setScenarioKey(value);
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
            readOnly={false}
            uploadItems={[]}
            busyFileId={null}
            previewBusy={false}
            error={null}
            notice={null}
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
        </Space>
      </section>
    </AppShell>
  );
}
