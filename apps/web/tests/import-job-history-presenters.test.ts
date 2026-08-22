import {
  formatPersistedImportResult,
  getImportJobStatusPresentation,
  getImportSourceLabel,
  getPersistedImportResult,
} from "@/features/imports/import-job-history-presenters";
import type {
  ImportConfirmResult,
  ImportJobStatus,
} from "@/features/imports/types";

const completedResult: ImportConfirmResult = {
  import_job_id: "job-completed",
  preview_revision: 3,
  created_rows: 7,
  updated_rows: 5,
  no_change_rows: 3,
  skipped_rows: 2,
  error_rows: 1,
  manual_review_rows: 4,
};

describe("Import Job history presenters", () => {
  it("reuses the UI-4 presenter for all 12 Job status translations", () => {
    const expected: Record<ImportJobStatus, string> = {
      draft: "文件准备中",
      uploaded: "已上传",
      parsing: "处理中",
      mapping_required: "需要字段映射",
      previewing: "正在生成数据预览",
      preview_ready: "数据预览已生成",
      preview_stale: "数据预览需要重新生成",
      confirm_queued: "正在准备导入",
      importing: "正在导入",
      completed: "导入完成",
      failed: "处理失败",
      cancelled: "已取消",
    };

    for (const [status, label] of Object.entries(expected) as Array<
      [ImportJobStatus, string]
    >) {
      expect(getImportJobStatusPresentation(status).label).toBe(label);
    }
  });

  it("maps only the two Backend source types", () => {
    expect(getImportSourceLabel("manual_huitun_export")).toBe("灰豚导出");
    expect(getImportSourceLabel("generic_csv")).toBe("通用 CSV");
  });

  it("never presents preview counters as a non-final result", () => {
    const job = { status: "importing" as const, result: completedResult };

    expect(getPersistedImportResult(job)).toBeNull();
    expect(formatPersistedImportResult(job)).toBe("—");
  });

  it("presents the persisted completed result without recomputing it", () => {
    const job = { status: "completed" as const, result: completedResult };

    expect(getPersistedImportResult(job)).toBe(completedResult);
    expect(formatPersistedImportResult(job)).toBe(
      "新增 7，更新 5，无变化 3，跳过 2，错误 1，人工复核 4",
    );
  });
});
