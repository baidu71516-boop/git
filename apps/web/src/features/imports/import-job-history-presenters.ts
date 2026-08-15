import { getBulkJobStatusPresentation } from "./formatters";
import type {
  ImportConfirmResult,
  ImportJobPublic,
  ImportSourceType,
} from "./types";

const integerFormatter = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 0,
});

const sourceLabels: Record<ImportSourceType, string> = {
  manual_huitun_export: "灰豚导出",
  generic_csv: "通用 CSV",
};

export const importResultFields = [
  ["created_rows", "新增"],
  ["updated_rows", "更新"],
  ["no_change_rows", "无变化"],
  ["skipped_rows", "跳过"],
  ["error_rows", "错误"],
  ["manual_review_rows", "人工复核"],
] as const satisfies ReadonlyArray<
  readonly [keyof ImportConfirmResult, string]
>;

export function getImportJobStatusPresentation(
  status: ImportJobPublic["status"],
) {
  return getBulkJobStatusPresentation(status);
}

export function getImportSourceLabel(sourceType: ImportSourceType): string {
  return sourceLabels[sourceType];
}

export function formatImportCount(value: number): string {
  return integerFormatter.format(value);
}

export function getPersistedImportResult(
  job: Pick<ImportJobPublic, "status" | "result">,
): ImportConfirmResult | null {
  return job.status === "completed" ? job.result : null;
}

export function formatPersistedImportResult(
  job: Pick<ImportJobPublic, "status" | "result">,
): string {
  const result = getPersistedImportResult(job);
  if (!result) return "—";
  return importResultFields
    .map(
      ([field, label]) =>
        `${label} ${formatImportCount(result[field] as number)}`,
    )
    .join("，");
}
