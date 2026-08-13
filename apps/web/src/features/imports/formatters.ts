import { ApiClientError } from "@/lib/api/client";

import type {
  CollectionJobStatus,
  ImportJobFilePublic,
  ImportJobFileStatus,
  ImportJobStatus,
} from "./types";

export type StatusTone =
  "default" | "success" | "warning" | "danger" | "processing";

export type StatusPresentation = {
  label: string;
  tone: StatusTone;
};

export type PreviewGate = {
  allowed: boolean;
  reason: string;
};

export type IssueTone = "secondary" | "warning" | "danger";

export const AMBIGUOUS_BULK_CREATE_MESSAGE =
  "采集任务已创建，但系统无法确认批量文件处理任务是否创建成功。为避免重复创建，系统不会自动重试。请保留当前页面并联系管理员核对。";

export const AMBIGUOUS_BULK_CONFIRM_MESSAGE =
  "无法确认导入请求是否已受理，请勿重复提交。系统将继续检查当前任务状态。";

const collectionStatusPresentation: Record<
  CollectionJobStatus,
  StatusPresentation
> = {
  draft: { label: "草稿", tone: "default" },
  active: { label: "进行中", tone: "processing" },
  completed: { label: "已完成", tone: "success" },
  cancelled: { label: "已取消", tone: "default" },
};

const jobStatusPresentation: Record<ImportJobStatus, StatusPresentation> = {
  draft: { label: "文件准备中", tone: "default" },
  uploaded: { label: "已上传", tone: "processing" },
  parsing: { label: "处理中", tone: "processing" },
  mapping_required: { label: "需要字段映射", tone: "warning" },
  previewing: { label: "正在生成数据预览", tone: "processing" },
  preview_ready: { label: "数据预览已生成", tone: "success" },
  preview_stale: { label: "数据预览需要重新生成", tone: "warning" },
  confirm_queued: { label: "正在准备导入", tone: "processing" },
  importing: { label: "正在导入", tone: "processing" },
  completed: { label: "导入完成", tone: "success" },
  failed: { label: "处理失败", tone: "danger" },
  cancelled: { label: "已取消", tone: "default" },
};

const fileStatusPresentation: Record<ImportJobFileStatus, StatusPresentation> =
  {
    uploaded: { label: "已上传", tone: "processing" },
    parsing: { label: "处理中", tone: "processing" },
    mapping_required: { label: "需要字段映射", tone: "warning" },
    ready: { label: "已就绪", tone: "success" },
    failed: { label: "处理失败", tone: "danger" },
    excluded: { label: "已排除", tone: "default" },
  };

const safeErrorMessages: Readonly<Record<string, string>> = {
  AUTH_REQUIRED: "登录状态已失效，请重新登录。",
  CSRF_FAILED: "安全校验失败，请刷新页面后重试。",
  PERMISSION_DENIED: "当前账号没有执行此操作的权限。",
  OPERATOR_REQUIRED: "请先选择当前操作人。",
  COLLECTION_JOB_NOT_FOUND: "采集任务不存在或当前账号无法访问。",
  COLLECTION_JOB_NOT_ACTIVE: "当前采集任务不可用于批量文件处理。",
  IMPORT_JOB_NOT_FOUND: "批量文件处理任务不存在或当前账号无法访问。",
  IMPORT_JOB_FILE_NOT_FOUND: "文件不存在或当前账号无法访问。",
  IMPORT_BATCH_FROZEN: "已生成过数据预览，当前批次不能再修改文件。",
  IMPORT_BATCH_FILE_LIMIT: "文件数量已达到当前批次上限。",
  IMPORT_BATCH_SIZE_LIMIT: "文件总大小已超过当前批次上限。",
  IMPORT_BATCH_ROW_LIMIT: "文件总行数已超过当前批次上限。",
  INVALID_FILE_EXTENSION: "仅支持 CSV / XLSX 文件，请重新选择文件。",
  MIME_MISMATCH: "文件内容与格式不匹配，请检查后重新上传。",
  INVALID_CSV_ENCODING: "CSV 编码无法识别，请转换编码后重新上传。",
  INVALID_CSV: "CSV 文件格式不正确或包含不安全内容，请检查后重试。",
  INVALID_XLSX: "XLSX 文件格式不正确，请检查后重试。",
  UNSAFE_XLSX: "XLSX 文件包含不支持的内容，请处理后重试。",
  EMPTY_FILE: "文件内容为空，请选择包含达人数据的文件。",
  FILE_TOO_LARGE: "文件过大，请缩小后重新上传。",
  FILE_INTEGRITY_FAILED: "文件完整性校验失败，请重新上传。",
  IDEMPOTENCY_CONFLICT: "同一文件标识对应了不同内容，请重新选择文件。",
  INVALID_CLIENT_FILE_ID: "文件请求标识无效，请重新选择文件。",
  INVALID_SOURCE_ACQUIRED_AT: "数据取得时间无效，请检查时间和时区。",
  INVALID_SOURCE_TIME: "数据取得时间无效，请检查后重试。",
  INVALID_FILE_STATE: "文件当前状态不支持此操作，请刷新后重试。",
  IMPORT_FILE_BUSY: "文件正在处理中，请稍后再试。",
  MAPPING_UNAVAILABLE: "文件字段尚未识别，暂时无法处理映射。",
  MAPPING_INVALID: "字段映射无效，请检查后重试。",
  MAPPING_REQUIRED: "请先完成文件字段映射。",
  IMPORT_PREVIEW_EMPTY: "请先添加至少一个可用文件。",
  IMPORT_PREVIEW_BLOCKED: "仍有文件未就绪，请处理后再生成数据预览。",
  SOURCE_ACQUIRED_AT_CONFIRMATION_REQUIRED: "请先确认所有文件的数据取得时间。",
  INVALID_PREVIEW_REQUEST: "数据预览请求无效，请刷新后重试。",
  PREVIEW_STALE: "数据预览已失效，请重新生成后再确认导入。",
  INVALID_STATE_TRANSITION: "任务状态已变化，请刷新后重试。",
  VALIDATION_ERROR: "提交内容有误，请检查后重试。",
  INTERNAL_ERROR: "系统暂时无法完成操作，请稍后重试。",
};

const fileErrorMessages: Readonly<Record<string, string>> = {
  INVALID_HEADER: "文件表头无法识别，请检查字段后重试。",
  DUPLICATE_HEADER: "文件包含重复表头，请修改后重试。",
  NO_DATA_ROWS: "文件中没有可处理的数据行。",
  INVALID_ROW_WIDTH: "文件部分行的列数不一致，请检查后重试。",
  FILE_COMPLEXITY_LIMIT: "文件内容过于复杂，请精简后重试。",
  ...safeErrorMessages,
};

const integerFormatter = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 0,
});

const byteFormatter = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 1,
});

const shanghaiDateTimeFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

const dateTimeNoTimezonePattern =
  /^(\d{4}-\d{2}-\d{2})[T\s](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?$/;

function formatNaiveDateTime(value: string): string | null {
  const match = value.match(dateTimeNoTimezonePattern);
  if (!match) return null;
  const [, date, hour, minute] = match;
  return `${date} ${hour}:${minute}`;
}

export function getCollectionJobStatusPresentation(
  status: CollectionJobStatus,
): StatusPresentation {
  return collectionStatusPresentation[status];
}

export function getBulkJobStatusPresentation(
  status: ImportJobStatus,
): StatusPresentation {
  return jobStatusPresentation[status];
}

export function getBulkFileStatusPresentation(
  status: ImportJobFileStatus,
): StatusPresentation {
  return fileStatusPresentation[status];
}

export function formatBulkFileRowCount(file: ImportJobFilePublic): string {
  if (file.raw_rows === 0 && file.detected_fields === null) return "—";
  return integerFormatter.format(file.raw_rows);
}

export function formatBulkDateTime(value: string | null): string {
  if (!value) return "—";
  const trimmed = value.trim();
  const naive = formatNaiveDateTime(trimmed);
  if (naive !== null) return naive;

  const date = new Date(trimmed);
  if (Number.isNaN(date.getTime())) return "—";
  const values = Object.fromEntries(
    shanghaiDateTimeFormatter
      .formatToParts(date)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}`;
}

export function formatBulkFileAcquisitionTime(
  file: ImportJobFilePublic,
): string {
  if (file.source_acquired_at_confirmation_required) return "待确认";
  return formatBulkDateTime(file.source_acquired_at);
}

export function formatBulkFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1_024) return `${integerFormatter.format(bytes)} B`;
  if (bytes < 1_024 * 1_024) {
    return `${byteFormatter.format(bytes / 1_024)} KB`;
  }
  return `${byteFormatter.format(bytes / (1_024 * 1_024))} MB`;
}

export function getBulkFileIssueMessage(file: ImportJobFilePublic): string {
  if (file.error_code) {
    return (
      fileErrorMessages[file.error_code] ?? "文件处理失败，请重试或排除该文件。"
    );
  }
  if (file.status === "failed") {
    return "文件处理失败，请重试或排除该文件。";
  }
  if (file.status === "mapping_required") {
    return "需要处理字段映射";
  }
  if (file.status === "excluded") {
    return "不计入本次数据预览";
  }
  const issues: string[] = [];
  if (file.error_rows > 0) {
    issues.push(`${integerFormatter.format(file.error_rows)} 行错误`);
  }
  if (file.warning_rows > 0) {
    issues.push(`${integerFormatter.format(file.warning_rows)} 行提醒`);
  }
  return issues.length > 0 ? issues.join("，") : "—";
}

export function getBulkFileIssueTone(file: ImportJobFilePublic): IssueTone {
  if (file.error_code || file.status === "failed") {
    return "danger";
  }

  if (file.status === "mapping_required") {
    return "warning";
  }

  if (file.warning_rows > 0 || file.error_rows > 0) {
    return "warning";
  }

  return "secondary";
}

export function getBulkErrorMessage(
  error: unknown,
  fallback = "操作失败，请稍后重试。",
): string {
  if (!(error instanceof ApiClientError)) return fallback;
  if (error.code && safeErrorMessages[error.code]) {
    return safeErrorMessages[error.code];
  }
  if (error.status === 401) return safeErrorMessages.AUTH_REQUIRED;
  if (error.status === 403) return safeErrorMessages.PERMISSION_DENIED;
  if (error.status >= 500) return "系统暂时无法完成操作，请稍后重试。";
  return fallback;
}

export function isAmbiguousBulkCreateError(error: unknown): boolean {
  if (!(error instanceof ApiClientError)) return true;
  return (
    error.code === "INVALID_RESPONSE" ||
    error.status < 400 ||
    error.status === 408 ||
    error.status >= 500
  );
}

export function isAmbiguousBulkConfirmError(error: unknown): boolean {
  if (!(error instanceof ApiClientError)) return true;
  return (
    error.code === "INVALID_RESPONSE" ||
    error.status < 400 ||
    error.status === 408 ||
    error.status >= 500
  );
}

export function getPreviewGate(
  files: readonly ImportJobFilePublic[],
): PreviewGate {
  const included = files.filter((file) => file.status !== "excluded");
  if (included.length === 0) {
    return { allowed: false, reason: "请先添加至少一个可用文件。" };
  }

  const failedCount = included.filter(
    (file) => file.status === "failed",
  ).length;
  if (failedCount > 0) {
    return {
      allowed: false,
      reason: `还有 ${failedCount} 个文件处理失败，请重试或排除后再生成数据预览。`,
    };
  }

  const mappingCount = included.filter(
    (file) => file.status === "mapping_required",
  ).length;
  if (mappingCount > 0) {
    return {
      allowed: false,
      reason: `还有 ${mappingCount} 个文件需要处理字段映射。`,
    };
  }

  const parsingCount = included.filter(
    (file) => file.status === "parsing",
  ).length;
  if (parsingCount > 0) {
    return {
      allowed: false,
      reason: `还有 ${parsingCount} 个文件正在处理，请稍后再试。`,
    };
  }

  const notReadyCount = included.filter(
    (file) => file.status !== "ready",
  ).length;
  if (notReadyCount > 0) {
    return {
      allowed: false,
      reason: `还有 ${notReadyCount} 个文件尚未就绪，处理完成后可生成数据预览。`,
    };
  }

  const confirmationCount = included.filter(
    (file) => file.source_acquired_at_confirmation_required,
  ).length;
  if (confirmationCount > 0) {
    return {
      allowed: false,
      reason: `还有 ${confirmationCount} 个文件待确认数据取得时间。`,
    };
  }

  return { allowed: true, reason: "文件已就绪，可以生成数据预览。" };
}
